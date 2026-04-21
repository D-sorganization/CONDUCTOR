"""TaskStore — durable task persistence in SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from maxwell_daemon.core.task_store import TaskStore
from maxwell_daemon.daemon.runner import Task, TaskKind, TaskStatus


def _fresh_task(**overrides: object) -> Task:
    defaults = {
        "id": uuid.uuid4().hex[:12],
        "prompt": "hello",
        "kind": TaskKind.PROMPT,
        "repo": None,
        "backend": None,
        "model": None,
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


class TestSaveAndGet:
    def test_roundtrip(self, store: TaskStore) -> None:
        task = _fresh_task(prompt="do the thing")
        store.save(task)
        loaded = store.get(task.id)
        assert loaded is not None
        assert loaded.id == task.id
        assert loaded.prompt == "do the thing"
        assert loaded.kind is TaskKind.PROMPT
        assert loaded.status is TaskStatus.QUEUED

    def test_get_missing_returns_none(self, store: TaskStore) -> None:
        assert store.get("nope") is None

    def test_save_rejects_empty_id(self, store: TaskStore) -> None:
        from maxwell_daemon.contracts import PreconditionError

        task = _fresh_task(id="")
        with pytest.raises(PreconditionError):
            store.save(task)

    def test_upsert_updates_existing(self, store: TaskStore) -> None:
        task = _fresh_task(prompt="v1")
        store.save(task)
        task.prompt = "v2"
        store.save(task)
        loaded = store.get(task.id)
        assert loaded.prompt == "v2"


class TestUpdateStatus:
    def test_transitions_recorded(self, store: TaskStore) -> None:
        task = _fresh_task()
        store.save(task)
        store.update_status(task.id, TaskStatus.RUNNING, started_at=datetime.now(timezone.utc))
        loaded = store.get(task.id)
        assert loaded.status is TaskStatus.RUNNING
        assert loaded.started_at is not None

    def test_missing_id_raises(self, store: TaskStore) -> None:
        with pytest.raises(KeyError):
            store.update_status("ghost", TaskStatus.COMPLETED)


class TestList:
    def test_lists_newest_first(self, store: TaskStore) -> None:
        a = _fresh_task()
        store.save(a)
        b = _fresh_task()
        store.save(b)
        listed = store.list_tasks(limit=10)
        assert listed[0].id == b.id
        assert listed[1].id == a.id

    def test_respects_limit(self, store: TaskStore) -> None:
        for _ in range(5):
            store.save(_fresh_task())
        assert len(store.list_tasks(limit=3)) == 3

    def test_filter_by_status(self, store: TaskStore) -> None:
        a = _fresh_task()
        b = _fresh_task()
        store.save(a)
        store.save(b)
        store.update_status(a.id, TaskStatus.COMPLETED)
        completed = store.list_tasks(limit=10, status=TaskStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].id == a.id


class TestRecoverPending:
    def test_recovers_queued(self, store: TaskStore) -> None:
        queued = _fresh_task()
        done = _fresh_task()
        store.save(queued)
        store.save(done)
        store.update_status(done.id, TaskStatus.COMPLETED)

        recovered = store.recover_pending()
        ids = {t.id for t in recovered}
        assert queued.id in ids
        assert done.id not in ids

    def test_marks_stale_running_as_failed(self, store: TaskStore) -> None:
        running = _fresh_task()
        store.save(running)
        store.update_status(running.id, TaskStatus.RUNNING)

        store.recover_pending()
        loaded = store.get(running.id)
        assert loaded.status is TaskStatus.FAILED
        assert loaded.error is not None
        assert "crashed" in loaded.error.lower()


class TestIssueFields:
    def test_preserves_issue_metadata(self, store: TaskStore) -> None:
        task = _fresh_task(
            kind=TaskKind.ISSUE,
            issue_repo="o/r",
            issue_number=42,
            issue_mode="implement",
        )
        store.save(task)
        loaded = store.get(task.id)
        assert loaded.issue_repo == "o/r"
        assert loaded.issue_number == 42
        assert loaded.issue_mode == "implement"


class TestSchemaMigration:
    def test_create_if_not_exists(self, tmp_path: Path) -> None:
        """Opening an existing DB that already has a tasks table must not error."""
        db = tmp_path / "t.db"
        s1 = TaskStore(db)
        s1.save(_fresh_task(prompt="x"))
        s2 = TaskStore(db)  # second open should be a no-op, not an error
        assert s2.list_tasks(limit=10)[0].prompt == "x"


class TestPrune:
    def test_prune_deletes_old_completed(self, store: TaskStore) -> None:
        old_task = _fresh_task(
            kind=TaskKind.ISSUE,
        )
        store.save(old_task)
        store.update_status(old_task.id, TaskStatus.COMPLETED)
        # Backdating created_at requires direct DB update.
        from datetime import timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        import sqlite3
        with sqlite3.connect(store._path) as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old_ts, old_task.id))
        deleted = store.prune(older_than_days=30)
        assert deleted == 1
        assert store.get(old_task.id) is None

    def test_prune_keeps_recent_tasks(self, store: TaskStore) -> None:
        recent = _fresh_task(status=TaskStatus.COMPLETED)
        store.save(recent)
        store.update_status(recent.id, TaskStatus.COMPLETED)
        deleted = store.prune(older_than_days=30)
        assert deleted == 0
        assert store.get(recent.id) is not None

    def test_prune_keeps_queued_tasks(self, store: TaskStore) -> None:
        task = _fresh_task(status=TaskStatus.QUEUED)
        store.save(task)
        import sqlite3
        from datetime import timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        with sqlite3.connect(store._path) as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old_ts, task.id))
        deleted = store.prune(older_than_days=30)
        assert deleted == 0  # QUEUED tasks are never pruned
        assert store.get(task.id) is not None


class TestDispatchedIssueNumbers:
    def test_returns_dispatched_numbers(self, store: TaskStore) -> None:
        task = _fresh_task(
            kind=TaskKind.ISSUE,
            issue_repo="owner/repo",
            issue_number=42,
            status=TaskStatus.COMPLETED,
        )
        store.save(task)
        store.update_status(task.id, TaskStatus.COMPLETED)
        nums = store.dispatched_issue_numbers("owner/repo")
        assert 42 in nums

    def test_excludes_failed_tasks(self, store: TaskStore) -> None:
        task = _fresh_task(
            kind=TaskKind.ISSUE,
            issue_repo="owner/repo",
            issue_number=99,
            status=TaskStatus.FAILED,
        )
        store.save(task)
        store.update_status(task.id, TaskStatus.FAILED)
        nums = store.dispatched_issue_numbers("owner/repo")
        assert 99 not in nums

    def test_excludes_other_repos(self, store: TaskStore) -> None:
        task = _fresh_task(
            kind=TaskKind.ISSUE,
            issue_repo="other/repo",
            issue_number=7,
            status=TaskStatus.COMPLETED,
        )
        store.save(task)
        store.update_status(task.id, TaskStatus.COMPLETED)
        assert 7 not in store.dispatched_issue_numbers("owner/repo")
