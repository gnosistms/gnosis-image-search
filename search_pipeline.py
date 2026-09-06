"""One persistent coordinator per search; bounded workers and scored decisions."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
import sys
import threading
import time

from search_runtime import WorkContext, work_context
from search_ranking import rank_snapshot
from visual_similarity import _item_feature


SEARCH_WORKERS = max(1, int(os.environ.get("SEARCH_SOURCE_CONCURRENCY", "18")))
SEARCH_POOL = ThreadPoolExecutor(max_workers=SEARCH_WORKERS, thread_name_prefix="collection")
PREPARE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="image-prepare")
SCORE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-score")
FEATURE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="image-feature")
RANK_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gallery-rank")
SCORE_TIMEOUT = float(os.environ.get("SEARCH_SCORING_TIMEOUT", "90"))
FEATURE_TIMEOUT = float(os.environ.get("SEARCH_FEATURE_TIMEOUT", "20"))
HEARTBEAT_SECONDS = 5


def process_peak_rss_bytes():
    try:
        import resource
    except ImportError:  # Not available on Windows.
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def continuation_policy(session):
    with session.lock:
        policy = {}
        scored_counts = {}
        for item in session.all_results.values():
            if isinstance(item.get("pamela_score"), (int, float)):
                scored_counts[item["source"]] = scored_counts.get(item["source"], 0) + 1
        for name, state in session.source_states.items():
            selected = name in session.selected_sources
            stage = state.get("stage", "queued")
            ids = state.get("logical_ids", state["last_ids"])
            representatives = {session.family_by_id.get(i, i) for i in ids}
            hits = sum(session.quality_positions.get(i, 10**9) <= 50 for i in representatives)
            failed_scores = sum(not isinstance(session.all_results.get(i, {}).get("pamela_score"),
                                                (int, float)) for i in ids)
            more, reason = False, ""
            if not selected:
                stage, reason = "hidden", "collection hidden"
            elif state.get("stop_reason"):
                stage, reason = "failed", state["stop_reason"]
            elif stage in {"fetching", "scoring", "evaluating"}:
                reason = {"fetching": "retrieving collection images", "scoring": "scoring images",
                          "evaluating": "updating aggregate ranking"}[stage]
            elif not state["rounds"]:
                more, stage, reason = True, "queued", "awaiting first batch"
            elif state["exhausted"] and state.get("scoring_done") and failed_scores:
                stage, reason = "incomplete", "image scoring incomplete; retry available"
            elif state["exhausted"]:
                stage, reason = "finished", "source exhausted"
            elif not state.get("scoring_done") or session.rank_dirty:
                stage, reason = "evaluating", "waiting for scored ranking"
            elif set(ids) & session.pending_features:
                stage, reason = "evaluating", "comparing possible duplicate images"
            elif len(ids) < 10:
                more, stage, reason = True, "queued", "completing first ten images"
            elif failed_scores and hits == 0:
                stage, reason = "incomplete", "image scoring incomplete; retry available"
            elif hits:
                more, stage, reason = True, "queued", f"{hits} of latest ten rank in aggregate top 50"
            else:
                stage = "finished" if session.lifecycle == "complete" else "paused"
                reason = "latest ten scored images produced no aggregate top-50 result"
            policy[name] = dict(selected=selected, continue_=more, reason=reason,
                                fetched=state["fetched"], rounds=state["rounds"], top_50_hits=hits,
                                stage=stage, scored=scored_counts.get(name, 0),
                                scoring_failures=failed_scores if state.get("scoring_done") else 0,
                                batch_id=state.get("batch_id"),
                                progress_age_seconds=round(time.monotonic()-state.get("last_progress", session.created_monotonic), 1),
                                retryable=stage in {"failed", "incomplete"})
            policy[name]["continue"] = policy[name].pop("continue_")
        return policy


def retry_sources(session, names):
    with session.lock:
        for name in names:
            if name not in session.selected_sources:
                continue
            state = session.source_states[name]
            if not continuation_policy(session)[name]["retryable"]:
                continue
            state["stop_reason"] = ""
            session.source_errors.pop(name, None)
            session.source_cancel_events[name].clear()
            state["stage"] = "idle"
            if state.get("logical_ids") and not state.get("scoring_done", False):
                state["needs_scoring"] = True
            elif state.get("logical_ids") and any(
                not isinstance(session.all_results.get(i, {}).get("pamela_score"), (int, float))
                for i in state["logical_ids"]
            ):
                state["scoring_done"] = False
                state["needs_scoring"] = True
            else:
                state["exhausted"] = False
                state["stage"] = "queued"
                state["retry_fetch"] = True
        session.lifecycle = "idle" if not session.stream_running else "running"
        session.last_progress = time.monotonic()
    return session.snapshot()


def stream_session(session, batch_search):
    """Subscribe without taking ownership of worker lifetime or duplicating work."""
    last = None
    heartbeat = 0
    while True:
        with session.lock:
            session.async_ranking = True
            if not session.stream_running and session.lifecycle == "idle":
                session.stream_running = True
                session.lifecycle = "running"
                threading.Thread(target=_run, args=(session, batch_search), daemon=True,
                                 name=f"search-{session.id[:8]}").start()
        snapshot = session.snapshot()
        signature = (snapshot["revision"], snapshot["lifecycle"],
                     tuple((n, p["stage"], p["fetched"], p["scored"])
                           for n, p in snapshot["source_policy"].items()))
        done = not snapshot["stream_running"]
        if signature != last or time.monotonic()-heartbeat >= HEARTBEAT_SECONDS or done:
            kind = "complete" if done else "snapshot" if signature != last else "heartbeat"
            if kind == "heartbeat":
                snapshot.pop("results", None)
            yield {"type": kind, "snapshot": snapshot}
            last, heartbeat = signature, time.monotonic()
        if done:
            break
        time.sleep(.05)


def _run(session, batch_search):
    import server
    jobs, prepared, pending_prepare = {}, {}, {}
    requested_features = set()
    decisions = {}
    last_top_fifty = None
    from provider_cursor import ProviderCursor
    cursors = session.provider_cursors

    def stage(name, value):
        state = session.source_states[name]
        state["stage"] = value
        state["last_progress"] = session.last_progress = time.monotonic()

    def launch(pool, kind, function, args, seconds, **details):
        started = time.monotonic()
        expired = threading.Event()
        context = WorkContext(started+seconds, lambda: expired.is_set() or session.cancelled,
                              session.trace)
        def run():
            session.trace.event(kind+"_started", queue_seconds=round(time.monotonic()-started, 4), **details)
            with work_context(context):
                from search_runtime import check_work
                check_work()
                work_started = time.monotonic()
                try:
                    return function(*args)
                finally:
                    if kind == "rank":
                        session.trace.count("ranking_seconds", time.monotonic()-work_started)
        future = pool.submit(run)
        jobs[future] = dict(kind=kind, started=started, deadline=started+seconds,
                            expired=expired, **details)
        session.trace.event(kind+"_queued", **details)

    def schedule_fetch(name):
        state = session.source_states[name]
        logical = state.setdefault("logical_ids", [])
        if len(logical) >= 10:
            state["logical_ids"] = []
        count = 10-len(state["logical_ids"])
        if not state["fetched"] and server.PREVIEW_BATCH_SIZES[0] == 1:
            count = 1
        state["batch_id"] = f"{name}:{state['fetched']}:{time.monotonic_ns()}"
        state["scoring_done"] = False
        state["retry_fetch"] = False
        stage(name, "fetching")
        offset = state["fetched"]
        def fetch():
            return batch_search(name, session.retrieval_query, offset, count,
                                cancelled=lambda: session.batch_cancelled(name),
                                resolve_dimensions=False,
                                **({"cursor": cursors.setdefault(name, ProviderCursor())}
                                   if batch_search is server.search_batch else {}),
                                **({"exact_phrases": session.exact_phrases} if session.exact_phrases else {}))
        launch(SEARCH_POOL, "fetch", fetch, (), server.SOURCE_BATCH_TIMEOUT_SECONDS,
               source=name, batch_id=state["batch_id"], offset=state["fetched"], count=count)

    def fail(details, reason):
        name = details.get("source")
        with session.lock:
            if name:
                state = session.source_states[name]
                state["stop_reason"] = reason
                session.source_errors[name] = reason
                pending_prepare.pop(name, None)
                dispose_prepared(prepared.pop(name, (None,))[0])
                stage(name, "failed")
        session.trace.event("failure", kind=details["kind"], source=name, reason=reason)

    try:
        while not session.cancelled:
            now = time.monotonic()
            # Check deadlines even when another future keeps producing results.
            for future, details in list(jobs.items()):
                if not future.done() and now >= details["deadline"]:
                    jobs.pop(future)
                    details["expired"].set()
                    future.cancel()
                    if details["kind"] == "prepare":
                        future.add_done_callback(dispose_late_preparation)
                    if details["kind"] == "feature":
                        with session.lock:
                            session.feature_evidence[details["item_id"]] = (True, None)
                            session.touch()
                    elif details["kind"] == "rank":
                        raise TimeoutError("Ranking deadline exceeded")
                    else:
                        fail(details, f"{details['kind']} deadline exceeded")
                    session.trace.count("timeouts")

            for future in [f for f in jobs if f.done()]:
                details = jobs.pop(future)
                kind, name = details["kind"], details.get("source")
                session.trace.event(kind+"_finished", seconds=round(time.monotonic()-details["started"], 4),
                                    source=name, batch_id=details.get("batch_id"))
                try:
                    value = future.result()
                except Exception as exc:
                    if kind == "feature":
                        value = None
                    elif kind == "rank":
                        raise
                    else:
                        fail(details, f"{kind} failed ({type(exc).__name__})")
                        continue
                with session.lock:
                    if name and session.source_states[name].get("batch_id") != details["batch_id"]:
                        continue
                    if kind == "fetch":
                        state = session.source_states[name]
                        old_ids = list(state.get("logical_ids", []))
                        session.merge_batch(value, score_results=False, retain_superseded_batch=True)
                        state["logical_ids"] = old_ids + [i["id"] for i in value["results"]]
                        state["last_ids"] = state["logical_ids"]
                        state["last_batch_count"] = len(state["logical_ids"])
                        if value.get("error"):
                            stage(name, "failed")
                        elif value["results"]:
                            pending_prepare[name] = (value["results"], 0, time.monotonic()+SCORE_TIMEOUT)
                            stage(name, "scoring")
                        else:
                            state["scoring_done"] = True
                            stage(name, "idle")
                    elif kind == "prepare":
                        prepared[name] = (value, details["attempt"], details["score_deadline"])
                    elif kind == "score":
                        state = session.source_states[name]
                        session.merge_scored_results(value)
                        missing = [i for i in value if not isinstance(i.get("pamela_score"), (int, float))]
                        if missing and details["attempt"] == 0 and time.monotonic() < details["score_deadline"]:
                            pending_prepare[name] = (missing, 1, details["score_deadline"])
                        else:
                            state["scoring_done"] = True
                            stage(name, "evaluating")
                    elif kind == "feature":
                        session.feature_evidence[details["item_id"]] = (True, value)
                        session.touch()
                    elif kind == "rank":
                        generation = details["generation"]
                        if generation != session.data_revision:
                            session.trace.count("stale_rankings")
                            continue
                        ranked, families, quality, requests, affected = value
                        session.results = {i["id"]: i for i in ranked}
                        session.families = families
                        session.family_by_id = {i["id"]: r for r, members in families.items() for i in members}
                        session.quality_positions = quality
                        session.pending_features = affected
                        requested_features = requests
                        session.rank_dirty = False
                        session.ranked_data_revision = generation
                        session.revision += 1
                        session.last_progress = time.monotonic()
                        session.trace.event("ranking_published", revision=session.revision,
                                            results=len(ranked), scored=len(quality), pending_features=len(requests))
                        with session.trace.lock:
                            for metric, reached in (("first_visible_seconds", bool(ranked)),
                                                    ("first_scored_seconds", bool(quality)),
                                                    ("fifty_scored_seconds", len(quality) >= 50)):
                                if reached:
                                    session.trace.metrics.setdefault(metric, time.monotonic()-session.trace.started)
                            top_fifty = tuple(sorted(quality, key=quality.get)[:50])
                            if top_fifty != last_top_fifty:
                                last_top_fifty = top_fifty
                                session.trace.metrics["settled_top_fifty_seconds"] = time.monotonic()-session.trace.started
                        for state in session.source_states.values():
                            if state.get("stage") == "evaluating" and state.get("scoring_done"):
                                state["stage"] = "idle"

            with session.lock:
                with session.trace.lock:
                    metrics = session.trace.metrics
                    metrics["peak_queued_jobs"] = max(metrics.get("peak_queued_jobs", 0), len(jobs))
                    metrics["peak_pending_images"] = max(metrics.get("peak_pending_images", 0),
                                                         sum(len(v[0]) for v in pending_prepare.values()))
                # Queue only a bounded number of prepared image batches, and
                # give initial samples priority over subsequent batches.
                occupied = sum(d["kind"] in {"prepare", "score"} for d in jobs.values()) + len(prepared)
                for name in sorted(pending_prepare, key=lambda n: session.source_states[n]["fetched"]):
                    if name not in session.selected_sources:
                        pending_prepare.pop(name)
                        session.source_states[name]["needs_scoring"] = True
                        continue
                    if time.monotonic() >= pending_prepare[name][2]:
                        fail(dict(kind="scoring", source=name), "scoring queue deadline exceeded")
                        continue
                    if occupied >= 4:
                        break
                    items, attempt, deadline = pending_prepare.pop(name)
                    state = session.source_states[name]
                    launch(PREPARE_POOL, "prepare", server.prepare_search_results, (session.query, items),
                           max(.01, deadline-time.monotonic()), source=name, batch_id=state["batch_id"],
                           attempt=attempt, score_deadline=deadline)
                    occupied += 1
                for name in list(prepared):
                    if name not in session.selected_sources:
                        dispose_prepared(prepared.pop(name)[0])
                        session.source_states[name]["needs_scoring"] = True
                if prepared and not any(d["kind"] == "score" for d in jobs.values()):
                    name = min(prepared, key=lambda n: session.source_states[n]["fetched"])
                    value, attempt, deadline = prepared.pop(name)
                    launch(SCORE_POOL, "score", server.finish_search_results, (session.query, value),
                           max(.01, deadline-time.monotonic()), source=name,
                           batch_id=session.source_states[name]["batch_id"], attempt=attempt, score_deadline=deadline)
                active_features = {d["item_id"] for d in jobs.values() if d["kind"] == "feature"}
                for item_id in requested_features - active_features:
                    if len(active_features) >= 4:
                        break
                    if item_id in session.feature_evidence:
                        continue
                    item = session.all_results.get(item_id)
                    if item:
                        launch(FEATURE_POOL, "feature", _item_feature, (item,), FEATURE_TIMEOUT, item_id=item_id)
                        active_features.add(item_id)
                if session.rank_dirty and not any(d["kind"] == "rank" for d in jobs.values()):
                    items = [dict(i) for i in session.all_results.values() if i["source"] in session.selected_sources]
                    launch(RANK_POOL, "rank", rank_snapshot,
                           (session.query, items, dict(session.feature_evidence)), 60,
                           generation=session.data_revision)
                active_sources = {d.get("source") for d in jobs.values()} | pending_prepare.keys() | prepared.keys()
                for name, policy in continuation_policy(session).items():
                    if not policy["selected"] or name in active_sources:
                        continue
                    state = session.source_states[name]
                    if state.pop("needs_scoring", False):
                        state["batch_id"] = f"retry:{name}:{time.monotonic_ns()}"
                        pending_prepare[name] = ([session.all_results[i] for i in state["logical_ids"]],
                                                 0, time.monotonic()+SCORE_TIMEOUT)
                        stage(name, "scoring")
                    elif policy["continue"] or state.get("retry_fetch"):
                        schedule_fetch(name)
                    if decisions.get(name) != policy["reason"]:
                        decisions[name] = policy["reason"]
                        session.trace.event("decision", source=name, reason=policy["reason"],
                                            revision=session.revision, top_50_hits=policy["top_50_hits"])
                if not jobs and not pending_prepare and not prepared:
                    session.lifecycle = "complete"
                    # Process-wide high-water mark, including model/cache memory.
                    peak_rss = process_peak_rss_bytes()
                    if peak_rss is not None:
                        with session.trace.lock:
                            session.trace.metrics["process_peak_rss_bytes"] = peak_rss
                    session.trace.event("complete", results=len(session.results), metrics=session.trace.snapshot())
                    break
            if jobs:
                wait(list(jobs), timeout=.05, return_when=FIRST_COMPLETED)
            else:
                time.sleep(.01)
    except Exception as exc:
        with session.lock:
            session.lifecycle = "failed"
            for name in session.selected_sources:
                session.source_states[name]["stop_reason"] = f"Search coordinator failed ({type(exc).__name__})"
                session.source_errors[name] = session.source_states[name]["stop_reason"]
        session.trace.event("coordinator_failed", error_type=type(exc).__name__)
        import traceback
        traceback.print_exc()
    finally:
        for future, details in jobs.items():
            details["expired"].set()
            future.cancel()
            if details["kind"] == "prepare":
                future.add_done_callback(dispose_late_preparation)
        for value, _attempt, _deadline in prepared.values():
            dispose_prepared(value)
        with session.lock:
            session.stream_running = False


def dispose_prepared(value):
    if value:
        for image in value[1][2].values():
            image.close()


def dispose_late_preparation(future):
    if not future.cancelled() and future.exception() is None:
        dispose_prepared(future.result())
