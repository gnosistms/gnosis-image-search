import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import server
import search_pipeline as pipeline
import search_ranking
import semantic_embeddings as embeddings
import visual_similarity
from provider_cursor import ProviderCursor
from search_runtime import WorkContext, WorkExpired, work_context, network_timeout


def item(source, n, score=.7):
    value = server.normalize_result(dict(source=source, source_id=str(n),
        title=f"Item {source} {n}", image_url=f"https://images.test/{source}/{n}.jpg",
        width=1000, height=1000), n)
    value["test_score"] = score
    return value


def group(source, offset, items, exhausted=False):
    return dict(source=source, offset=offset, results=items, count=len(items),
                exhausted=exhausted, error="")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.releases = []
        self.patches = [
            patch.object(server, "PREVIEW_BATCH_SIZES", (10,)),
            patch.object(server, "prepare_search_results", lambda q, items: (copy.deepcopy(items), ({}, {}, {}))),
            patch.object(server, "finish_search_results", self.score),
            patch.object(search_ranking, "cached_image_vectors", return_value={}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for event in self.releases:
            event.set()
        for session in self.sessions:
            session.cancel()
        deadline = time.monotonic()+3
        while any(s.stream_running for s in self.sessions) and time.monotonic() < deadline:
            time.sleep(.01)
        for p in reversed(self.patches):
            p.stop()

    @staticmethod
    def score(query, prepared):
        items = prepared[0]
        for value in items:
            if value.get("test_score") is not None:
                value.update(pamela_score=value["test_score"], pamela_rerank=True)
            value["scoring_complete"] = True
        return items

    def start(self, sources, fetch):
        session = server.SearchSession("Krishna", sources)
        self.sessions.append(session)
        events = []
        thread = threading.Thread(target=lambda: events.extend(server.stream_search_round(session, fetch)))
        thread.start()
        return session, events, thread

    def test_all_eighteen_start_before_any_finishes(self):
        release = threading.Event()
        self.releases.append(release)
        started = set()
        lock = threading.Lock()
        all_started = threading.Event()
        def fetch(name, q, offset, count, **kw):
            with lock:
                started.add(name)
                if len(started) == 18:
                    all_started.set()
            release.wait(3)
            return group(name, offset, [], True)
        session, events, thread = self.start(list(server.SOURCE_LABELS), fetch)
        self.assertTrue(all_started.wait(2))
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(session.lifecycle, "complete")

    def test_scored_source_resumes_without_waiting_for_slow_collection(self):
        release = threading.Event()
        self.releases.append(release)
        next_batch = threading.Event()
        def fetch(name, q, offset, count, **kw):
            if name == "met":
                release.wait(3)
                return group(name, offset, [], True)
            if offset:
                next_batch.set()
                return group(name, offset, [], True)
            return group(name, offset, [item(name, n, .95) for n in range(10)])
        session, events, thread = self.start(["commons", "met"], fetch)
        self.assertTrue(next_batch.wait(2))
        self.assertFalse(release.is_set())
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any(e["snapshot"].get("results") for e in events))

    def test_metadata_publishes_while_scoring_is_blocked(self):
        release = threading.Event()
        self.releases.append(release)
        def prepare(q, values):
            release.wait(3)
            return values, ({}, {}, {})
        with patch.object(server, "prepare_search_results", prepare):
            session, events, thread = self.start(["met"], lambda n,q,o,c,**kw: group(n,o,[item(n,0)],True))
            deadline = time.monotonic()+2
            while not session.results and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(len(session.results), 1)
            self.assertEqual(session.continuation_policy()["met"]["stage"], "scoring")
            release.set()
            thread.join(3)

    def test_incomplete_scoring_is_not_a_quality_stop_and_can_retry(self):
        calls = []
        def fetch(n,q,o,c,**kw):
            calls.append(o)
            return group(n,o,[item(n,i,None) for i in range(10)])
        session, events, thread = self.start(["met"], fetch)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(session.continuation_policy()["met"]["stage"], "incomplete")
        self.assertEqual(calls, [0])
        for value in session.all_results.values():
            value["test_score"] = .7
        pipeline.retry_sources(session, ["met"])
        def next_fetch(n,q,o,c,**kw):
            self.assertEqual(o, 10)
            return group(n,o,[],True)
        list(server.stream_search_round(session, next_fetch))
        self.assertEqual(session.continuation_policy()["met"]["scored"], 10)
        self.assertEqual(session.lifecycle, "complete")

    def test_ten_image_decisions_use_only_final_scores(self):
        session = server.SearchSession("Krishna", ["met", "commons"])
        session.async_ranking = True
        values = [item("met", n, .95) for n in range(50)] + [item("commons", n, .1) for n in range(10)]
        values = self.score("", (values, None))
        session.all_results = {v["id"]:v for v in values}
        ranked, families, quality, pending, affected = search_ranking.rank_snapshot("Krishna", values)
        session.results = {v["id"]:v for v in ranked}
        session.family_by_id = {v["id"]:r for r,m in families.items() for v in m}
        session.quality_positions = quality
        state = session.source_states["commons"]
        state.update(fetched=10, rounds=1, logical_ids=[v["id"] for v in values[-10:]], scoring_done=True, stage="idle")
        self.assertEqual(session.continuation_policy()["commons"]["stage"], "paused")
        session.quality_positions[values[-1]["id"]] = 1
        self.assertTrue(session.continuation_policy()["commons"]["continue"])
        state["scoring_done"] = False
        self.assertFalse(session.continuation_policy()["commons"]["continue"])

    def test_failed_first_preview_does_not_skip_the_remaining_nine(self):
        calls = []
        def fetch(n,q,o,c,**kw):
            calls.append((o,c))
            return group(n,o,[item(n,i,None if i == 0 else .8) for i in range(o,o+c)],o>=10)
        with patch.object(server,"PREVIEW_BATCH_SIZES",(1,9)):
            session,events,thread=self.start(["met"],fetch)
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls,[(0,1),(1,9),(10,10)])

    def test_deadline_checked_while_other_work_completes(self):
        release = threading.Event()
        self.releases.append(release)
        def fetch(n,q,o,c,**kw):
            if n == "met":
                release.wait(2)
            return group(n,o,[],True)
        with patch.object(server, "SOURCE_BATCH_TIMEOUT_SECONDS", .08):
            session, events, thread = self.start(["met", "commons"], fetch)
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertIn("deadline",session.source_errors["met"])
            release.set()

    def test_two_subscribers_do_not_duplicate_provider_work(self):
        release = threading.Event()
        self.releases.append(release)
        calls=[]
        def fetch(n,q,o,c,**kw):
            calls.append(n)
            release.wait(2)
            return group(n,o,[],True)
        session, events, thread = self.start(["met"], fetch)
        second = threading.Thread(target=lambda:list(server.stream_search_round(session,fetch)))
        second.start()
        time.sleep(.05)
        release.set()
        thread.join(2); second.join(2)
        self.assertEqual(calls,["met"])

    def test_stale_ranking_is_discarded_and_status_reads_do_not_wait_for_it(self):
        ranking_started=threading.Event(); release=threading.Event(); self.releases.append(release)
        real_rank=pipeline.rank_snapshot
        first=[True]
        def rank(*args):
            if first[0]:
                first[0]=False
                ranking_started.set()
                release.wait(3)
            return real_rank(*args)
        def fetch(n,q,o,c,**kw):
            if n == "commons":
                ranking_started.wait(2)
            return group(n,o,[item(n,0)],True)
        with patch.object(pipeline,"rank_snapshot",rank):
            session,events,thread=self.start(["met","commons"],fetch)
            self.assertTrue(ranking_started.wait(2))
            started=time.monotonic()
            session.snapshot()
            self.assertLess(time.monotonic()-started,.2)
            deadline=time.monotonic()+2
            while len(session.all_results)<2 and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(len(session.all_results),2)
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(session.results),2)
        self.assertGreaterEqual(session.trace.snapshot().get("stale_rankings",0),1)

    def test_blocked_duplicate_download_does_not_block_another_source(self):
        started=threading.Event(); release=threading.Event(); advanced=threading.Event()
        self.releases.append(release)
        real_rank=pipeline.rank_snapshot
        target=item("met",0)["id"]
        def rank(query,items,evidence):
            result=list(real_rank(query,items,evidence))
            if any(i["id"]==target for i in items) and target not in evidence:
                result[3]={target}; result[4]={target}
            return tuple(result)
        def feature(value):
            started.set(); release.wait(3); return None
        def fetch(n,q,o,c,**kw):
            if n == "commons" and o:
                advanced.set()
                return group(n,o,[],True)
            return group(n,o,[item(n,i) for i in range(c)],n=="met")
        with patch.object(pipeline,"rank_snapshot",rank), patch.object(pipeline,"_item_feature",feature):
            session,events,thread=self.start(["met","commons"],fetch)
            self.assertTrue(started.wait(2))
            self.assertTrue(advanced.wait(2))
            self.assertFalse(release.is_set())
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_hidden_source_retains_late_results_without_fetching_again(self):
        started=threading.Event(); release=threading.Event(); self.releases.append(release)
        calls=[]
        def fetch(n,q,o,c,**kw):
            calls.append((n,o))
            if n=="met":
                started.set(); release.wait(3)
            return group(n,o,[item(n,i) for i in range(c)],n!="met")
        session,events,thread=self.start(["met","commons"],fetch)
        self.assertTrue(started.wait(2))
        session.update_sources(["commons"])
        release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any(i['source']=='met' for i in session.all_results.values()))
        self.assertEqual({i['source'] for i in session.results.values()},{'commons'})
        self.assertEqual([o for n,o in calls if n=='met'],[0])

    def test_hiding_strong_collection_resumes_previously_paused_source(self):
        session = server.SearchSession("Krishna", ["met", "commons"])
        self.sessions.append(session)
        session.async_ranking = True
        values = self.score("", ([item("met", n, .95) for n in range(50)]
                                 + [item("commons", n, .1) for n in range(10)], None))
        session.all_results = {v["id"]: v for v in values}
        for name in session.source_states:
            ids = [v["id"] for v in values if v["source"] == name]
            session.source_states[name].update(fetched=len(ids), rounds=1, logical_ids=ids[-10:],
                                               scoring_done=True, stage="idle")
        session.lifecycle = "complete"
        session.update_sources(["commons"])
        calls = []
        def fetch(n,q,o,c,**kw):
            calls.append((n,o))
            return group(n,o,[],True)
        list(server.stream_search_round(session,fetch))
        self.assertEqual(calls,[("commons",10)])
        self.assertEqual({v["source"] for v in session.results.values()},{"commons"})

    def test_deeper_pool_top_fifty_survives_completion_order_permutations(self):
        names = list(server.SOURCE_LABELS)
        pool = {name: [item(name,n,.98-index*.025-n*.0001) for n in range(30)]
                for index,name in enumerate(names)}
        all_scored = self.score("", ([copy.deepcopy(v) for rows in pool.values() for v in rows],None))
        expected = list(search_ranking.rank_snapshot("Krishna",all_scored)[2])[:50]
        for order in (names, list(reversed(names)), names[9:]+names[:9]):
            def fetch(n,q,o,c,**kw):
                time.sleep(order.index(n)*.0004)
                rows=copy.deepcopy(pool[n][o:o+c])
                return group(n,o,rows,o+c>=len(pool[n]))
            session,events,thread=self.start(names,fetch)
            thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertEqual(list(session.quality_positions)[:50],expected)

    def test_rejected_exact_candidates_expand_without_repeating_empty_batch(self):
        calls=[]
        def adapter(q,need):
            calls.append(need)
            values=[dict(source='met',source_id=str(i),title='wrong',image_url=f'https://image.test/{i}')
                    for i in range(need)]
            if need>=160:
                values[80]['title']='required phrase'
            return values
        result=server.search_batch('met','required phrase',0,1,adapters={'met':adapter},
                                   resolve_dimensions=False,exact_phrases=('required phrase',))
        self.assertEqual(calls,[40,80,160])
        self.assertEqual(result['count'],1)

    def test_krishna_fixture_records_premature_commons_decision(self):
        fixture=json.loads(Path("test/fixtures/krishna-search.json").read_text())
        first,second=fixture["snapshots"][:2]
        self.assertFalse(first["policy"]["commons"]["continue"])
        self.assertEqual(second["policy"]["commons"]["top_50_hits"],6)


class RankingPreparationTests(unittest.TestCase):
    def test_bulk_lookup_uses_one_connection_and_does_not_cache_misses_forever(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(embeddings,"CACHE_PATH",Path(folder)/"vectors.db"):
            values=[item("met",n) for n in range(12)]
            with patch.object(embeddings,"_MEMORY_CACHE",{}):
                embeddings.CACHE_PATH.touch()
                connect=sqlite3.connect
                with patch.object(sqlite3,"connect", wraps=connect) as spy:
                    self.assertEqual(embeddings.cached_image_vectors(values),{})
                    self.assertEqual(spy.call_count,1)
                # Store outside the empty mocked memory cache (which is not an LRU).
            embeddings._store_vectors({values[0]["image_url"]:np.array([1.,0.],dtype="float32")})
            self.assertIn(values[0]["id"],embeddings.cached_image_vectors(values))

    def test_missing_perceptual_evidence_stays_pending_without_download(self):
        values=[item("met",0),item("commons",0)]
        vectors={v["id"]:np.array([1.,0.]) for v in values}
        with patch.object(search_ranking,"cached_image_vectors",return_value=vectors), \
             patch.object(search_ranking,"cached_item_feature",return_value=(False,None)), \
             patch.object(visual_similarity,"_download_feature",side_effect=AssertionError("network in rank")):
            ranked,families,quality,requests,affected=search_ranking.rank_snapshot("",values)
            self.assertEqual(len(ranked),2)
            self.assertEqual(requests,set(vectors))
            ranked,*_=search_ranking.rank_snapshot("",values,{v["id"]:(True,None) for v in values})
            self.assertEqual(len(ranked),1)

    def test_cursor_reuses_overfetched_records(self):
        cursor=ProviderCursor(); calls=[]
        def fetch(n):
            calls.append(n)
            return [dict(source="met",source_id=str(i)) for i in range(n*2)]
        self.assertEqual(len(cursor.fetch(10,fetch)),20)
        self.assertEqual(len(cursor.fetch(20,fetch)),20)
        cursor.fetch(30,fetch)
        self.assertEqual(calls,[10,30])

    def test_native_cleveland_cursor_consumes_pages_once_with_unchanged_metadata(self):
        import urllib.parse
        rows = [dict(id=i, title=f"Krishna {i}", share_license_status="CC0",
                     images={"web": {"url": f"https://image.test/{i}", "width": 500, "height": 600}})
                for i in range(45)]
        rows[4]["images"] = {}  # Raw offsets must include rejected records.
        calls = []
        def get(url, source):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            offset = int(params.get("skip", [0])[0]); limit = int(params["limit"][0])
            calls.append(offset)
            return {"data": rows[offset:offset+limit], "info": {"total": len(rows)}}
        with patch.object(server.sources, "_get_json", get):
            expected = server.sources.cleveland("Krishna", 30)
            calls.clear()
            cursor = ProviderCursor()
            for requested in (10, 20, 30, 50):
                actual = cursor.fetch_pages(requested, lambda offset, limit:
                    server.sources.cleveland_page("Krishna", offset, limit))
        self.assertEqual(actual, expected)
        self.assertEqual(calls, [0, 20, 40])
        self.assertTrue(cursor.exhausted)

    def test_overlapping_image_downloads_share_network_but_own_image_copies(self):
        from concurrent.futures import ThreadPoolExecutor
        from PIL import Image
        entered = threading.Event(); release = threading.Event()
        def download(url):
            entered.set(); release.wait(2)
            return Image.new("RGB", (8, 8), "red")
        with patch.object(embeddings, "_download_image_once", side_effect=download) as spy, \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(embeddings._download_image, "https://image.test/shared")
            self.assertTrue(entered.wait(1))
            second = pool.submit(embeddings._download_image, "https://image.test/shared")
            deadline = time.monotonic()+1
            while time.monotonic() < deadline:
                with embeddings._DOWNLOAD_LOCK:
                    if embeddings._DOWNLOAD_INFLIGHT["https://image.test/shared"][1] == 2:
                        break
                time.sleep(.005)
            release.set()
            a, b = first.result(), second.result()
        self.assertEqual(spy.call_count, 1)
        a.close()
        self.assertEqual(b.getpixel((0, 0)), (255, 0, 0))
        b.close()
        self.assertFalse(embeddings._DOWNLOAD_INFLIGHT)

    def test_completed_perceptual_evidence_preserves_original_ranking_and_families(self):
        from PIL import Image
        from ranker import rank_result_groups
        values = PipelineTests.score("", ([item("met", 0), item("commons", 0), item("cleveland", 1)], None))
        features = [visual_similarity.compute_feature(Image.new("RGB", (32, 32), color))
                    for color in ("red", "red", "blue")]
        vectors = {v["id"]: np.array([1., 0.]) for v in values}
        evidence = {v["id"]: (True, f) for v, f in zip(values, features)}
        def perceptual(a, b):
            return visual_similarity.feature_similarity(evidence[a["id"]][1], evidence[b["id"]][1])
        with patch.object(visual_similarity, "result_feature_similarity", perceptual):
            expected, families = rank_result_groups("Krishna", values,
                lambda a,b: visual_similarity.likely_same_image(a,b,float(vectors[a["id"]] @ vectors[b["id"]])))
        with patch.object(search_ranking, "cached_image_vectors", return_value=vectors):
            actual, actual_families, quality, pending, affected = search_ranking.rank_snapshot("Krishna", values, evidence)
        self.assertEqual(actual, expected)
        self.assertEqual(actual_families, families)
        self.assertEqual(len(actual), 2)
        self.assertFalse(pending)

    def test_harvard_scoring_uses_the_same_image_access_as_gallery(self):
        import io
        import harvard_images
        from PIL import Image
        output=io.BytesIO()
        Image.new("RGB",(12,16),"red").save(output,format="JPEG")
        value=item("harvard",0)
        with patch.object(harvard_images,"fetch_harvard_preview",return_value=(output.getvalue(),"image/jpeg")) as fetch, \
             patch.object(embeddings,"_download_image",side_effect=AssertionError("bypassed Harvard access")):
            image=embeddings._download_item_image(value)
        self.assertEqual(image.size,(12,16))
        image.close()
        fetch.assert_called_once_with(value,attempts=2)
        self.assertTrue(visual_similarity.cached_item_feature(value)[0])

    def test_provider_access_failure_preserves_specific_reason(self):
        import urllib.error
        with work_context(WorkContext(time.monotonic()+5)), \
             patch.object(server.sources,"DELAY",0), \
             patch.object(server.sources.urllib.request,"urlopen",
                 side_effect=urllib.error.HTTPError("https://source.test",403,"Forbidden",{},None)):
            value=server.search_batch("yale","Krishna",0,10,adapters={"yale":lambda q,n:
                server.sources._get_json("https://source.test","yale",ttl_ok=False) or []})
        self.assertIn("HTTP 403",value["error"])
        session=server.SearchSession("Krishna",["yale"])
        session.merge_batch(value,score_results=False)
        self.assertIn("HTTP 403",session.source_states["yale"]["stop_reason"])

    def test_successful_empty_retry_is_not_reported_as_a_provider_failure(self):
        import io, urllib.error
        response=io.BytesIO(b"[]")
        with tempfile.TemporaryDirectory() as folder, work_context(WorkContext(time.monotonic()+5)), \
             patch.object(server.sources,"CACHE",folder), patch.object(server.sources,"DELAY",0), \
             patch.object(server.sources,"pause"), \
             patch.object(server.sources.urllib.request,"urlopen",side_effect=[
                 urllib.error.HTTPError("https://source.test",429,"Rate limit",{},None), response]):
            value=server.search_batch("yale","Krishna",0,10,adapters={"yale":lambda q,n:
                server.sources._get_json("https://source.test","yale",ttl_ok=False) or []})
        self.assertFalse(value["error"])
        self.assertTrue(value["exhausted"])

    def test_harvard_missing_key_is_not_cached_as_an_empty_collection(self):
        import additional_sources
        with patch.object(additional_sources,"_harvard_key",return_value=""):
            value=server.search_batch("harvard","Krishna",0,10,
                                     adapters={"harvard":additional_sources.harvard})
        self.assertIn("not configured",value["error"])

    def test_packaged_credentials_include_configured_harvard_without_logging_secrets(self):
        import contextlib, io, runpy, sys
        import keys
        package=runpy.run_path("scripts/package-europeana-key.py")
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(sys,"argv",["package",folder]), \
             patch.object(keys,"get_key",side_effect=lambda n: {"europeana":"test-eu","harvard":"test-harvard"}.get(n)), \
             patch.object(keys,"write_encrypted") as write, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(package["main"](),0)
        self.assertEqual(write.call_args.args[0],{"europeana":"test-eu","harvard":"test-harvard"})
        self.assertNotIn("test-harvard",output.getvalue())

    def test_deadline_propagates_to_network_timeout(self):
        with work_context(WorkContext(time.monotonic()-.01)):
            with self.assertRaises(WorkExpired):
                network_timeout(45)


if __name__ == "__main__":
    unittest.main()
