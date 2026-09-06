"""Cooperative deadlines, inherited worker context, and nonblocking search traces."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import queue
import threading
import time


class WorkExpired(TimeoutError):
    pass


_local = threading.local()
_events = queue.Queue(maxsize=8192)
_writer_lock = threading.Lock()
_writer_started = False


def _write_events(path):
    with open(path, "a", buffering=1) as output:
        while True:
            output.write(json.dumps(_events.get(), ensure_ascii=False) + "\n")


class SearchTrace:
    def __init__(self, session):
        self.session = session
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.metrics = {}
        path = os.environ.get("SEARCH_TRACE_FILE")
        self.enabled = bool(path)
        global _writer_started
        if path:
            with _writer_lock:
                if not _writer_started:
                    threading.Thread(target=_write_events, args=(path,), daemon=True).start()
                    _writer_started = True

    def count(self, name, value=1):
        with self.lock:
            self.metrics[name] = self.metrics.get(name, 0) + value

    def event(self, event, **fields):
        if self.enabled:
            try:
                _events.put_nowait(dict(session=self.session, event=event,
                                       elapsed=round(time.monotonic()-self.started, 4), **fields))
            except queue.Full:
                self.count("trace_events_dropped")

    def snapshot(self):
        with self.lock:
            return dict(self.metrics)


@dataclass
class WorkContext:
    deadline: float
    cancelled: object = None
    trace: object = None
    failures: int = 0
    failure_reason: str = ""


def note_failure(reason=None):
    context = getattr(_local, "context", None)
    if context:
        context.failures += 1
        context.failure_reason = reason or context.failure_reason or "Collection requests failed"
    count("provider_failures")


def failed_requests():
    context = getattr(_local, "context", None)
    return context.failures if context else 0


def request_failure_reason():
    context = getattr(_local, "context", None)
    return context.failure_reason if context else "Collection requests failed"


@contextmanager
def work_context(context):
    previous = getattr(_local, "context", None)
    _local.context = context
    try:
        yield
    finally:
        _local.context = previous


def check_work():
    context = getattr(_local, "context", None)
    if context and (time.monotonic() >= context.deadline
                    or (context.cancelled and context.cancelled())):
        raise WorkExpired("Search work cancelled or deadline exceeded")


def network_timeout(default):
    check_work()
    context = getattr(_local, "context", None)
    return max(.01, min(default, context.deadline-time.monotonic())) if context else default


def count(name, value=1):
    context = getattr(_local, "context", None)
    if context and context.trace:
        context.trace.count(name, value)


def pause(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check_work()
        time.sleep(min(.1, max(0, end-time.monotonic())))


def submit(pool, function, *args, **kwargs):
    context = getattr(_local, "context", None)
    def run():
        with work_context(context):
            check_work()
            return function(*args, **kwargs)
    return pool.submit(run)


def map_work(pool, function, values):
    futures = [submit(pool, function, value) for value in values]
    return (future.result() for future in futures)
