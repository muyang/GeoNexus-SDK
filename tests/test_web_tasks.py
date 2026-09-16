"""Tests for the Web-layer TaskManager (geonexus.web.tasks)."""

from __future__ import annotations

import threading
import time

import pytest

from geonexus.web.tasks import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    TaskManager,
    TaskNotCancellableError,
    TaskNotFoundError,
)


class TestTaskManager:
    def test_successful_task(self) -> None:
        with TaskManager() as tm:
            tid = tm.submit(lambda: {"answer": 42})
            state = tm.wait(tid, timeout=10)
            assert state["status"] == DONE
            assert state["result"] == {"answer": 42}

    def test_failed_task_records_error(self) -> None:
        def boom() -> None:
            raise ValueError("bad input")

        with TaskManager() as tm:
            tid = tm.submit(boom)
            state = tm.wait(tid, timeout=10)
            assert state["status"] == FAILED
            assert "bad input" in (state["error"] or "")
            assert "result" not in state

    def test_cancel_queued_task(self) -> None:
        with TaskManager() as tm:
            # A task that blocks on an event; cancel it while queued/running.
            import threading

            gate = threading.Event()

            def worker() -> str:
                gate.wait(10)
                return "done"

            tid = tm.submit(worker)
            # Give the pool a moment to pick it up.
            time.sleep(0.1)
            state = tm.cancel(tid)
            assert state["cancelled"] is True
            gate.set()
            final = tm.wait(tid, timeout=10)
            assert final["status"] in (CANCELLED, DONE)

    def test_cancel_unknown_task(self) -> None:
        with TaskManager() as tm, pytest.raises(TaskNotFoundError):
            tm.cancel("nope")

    def test_cancel_terminal_task_rejected(self) -> None:
        with TaskManager() as tm:
            tid = tm.submit(lambda: 1)
            tm.wait(tid, timeout=10)
            with pytest.raises(TaskNotCancellableError):
                tm.cancel(tid)

    def test_get_unknown_task(self) -> None:
        with TaskManager() as tm, pytest.raises(TaskNotFoundError):
            tm.get("nope")

    def test_progress_updates(self) -> None:
        events: list[dict] = []

        def worker(task_id: str) -> str:
            tm.update_progress(task_id, 0.5, "halfway")
            time.sleep(0.05)
            tm.update_progress(task_id, 1.0, "done step")
            return "ok"

        # max_workers=1 plus a gate task holds `worker` in QUEUED until we have
        # observed that state, so the assertion cannot race the worker pool.
        gate = threading.Event()
        with TaskManager(max_workers=1, persist=events.append) as tm:
            blocking = tm.submit(lambda: gate.wait(10))
            tid = tm.submit(worker, message="start")
            assert tm.get(tid)["status"] == QUEUED
            gate.set()
            tm.wait(blocking, timeout=10)
            state = tm.wait(tid, timeout=10)

        assert state["status"] == DONE
        assert state["result"] == "ok"
        assert state["progress"] == 1.0
        assert state["message"] == "done step"

        # Every transition is reported, including the intermediate progress
        # update. Ordering is not asserted: it is not guaranteed across threads.
        seen = [e for e in events if e["id"] == tid]
        assert any(e["status"] == QUEUED for e in seen)
        assert any(e["progress"] == 0.5 for e in seen)
        assert any(e["status"] == DONE for e in seen)

    def test_should_cancel_cooperation(self) -> None:
        def worker(task_id: str) -> str:
            for _ in range(50):
                if tm.should_cancel(task_id):
                    return "cancelled-cooperatively"
                time.sleep(0.01)
            return "finished"

        with TaskManager() as tm:
            tid = tm.submit(worker)
            time.sleep(0.1)
            tm.cancel(tid)
            state = tm.wait(tid, timeout=10)
            assert state["result"] == "cancelled-cooperatively"

    def test_list_sorted_newest_first(self) -> None:
        with TaskManager() as tm:
            tm.submit(lambda: 1)
            time.sleep(0.01)
            tm.submit(lambda: 2)
            tasks = tm.list()
            assert tasks[0]["created_at"] >= tasks[1]["created_at"]
            assert "result" not in tasks[0]

    def test_persist_callback_receives_events(self) -> None:
        events: list[dict] = []
        with TaskManager(persist=lambda data: events.append(data)) as tm:
            tid = tm.submit(lambda: "x")
            tm.wait(tid, timeout=10)
        assert any(e["id"] == tid and e["status"] == QUEUED for e in events)
        assert any(e["id"] == tid and e["status"] == DONE for e in events)

    def test_queued_status_before_run(self) -> None:
        with TaskManager(max_workers=1) as tm:
            first = tm.submit(lambda: time.sleep(0.3) or "a")
            second = tm.submit(lambda: "b")
            # The second task is queued behind the first.
            assert tm.get(second)["status"] == QUEUED
            tm.wait(first, timeout=10)
            tm.wait(second, timeout=10)
