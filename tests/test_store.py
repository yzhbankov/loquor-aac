"""Persistent task state."""

from loquor.messages import TaskState
from loquor.store import TaskStore


def test_upsert_merges_data_and_updates_state(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.db"))
    store.upsert("t1", role="server", state=TaskState.IN_PROGRESS, peer_id="pump-01",
                 capability="diag", data={"observations": {"v": 8.2}})
    store.upsert("t1", role="server", state=TaskState.AWAITING_INFO,
                 data={"measurements": {"current": 14.9}})

    task = store.get("t1")
    assert task["state"] is TaskState.AWAITING_INFO
    assert task["data"] == {"observations": {"v": 8.2}, "measurements": {"current": 14.9}}
    assert task["peer_id"] == "pump-01"
    store.close()


def test_state_survives_reopen(tmp_path):
    path = str(tmp_path / "tasks.db")
    store = TaskStore(path)
    store.upsert("t1", role="requester", state=TaskState.INTERRUPTED, peer_id="inspector-01",
                 capability="diag", data={"observations": {"v": 1}})
    store.close()

    reopened = TaskStore(path)
    interrupted = reopened.by_state("requester", TaskState.INTERRUPTED)
    assert [t["task_id"] for t in interrupted] == ["t1"]
    reopened.close()


def test_by_state_filters_role_and_state():
    store = TaskStore(":memory:")
    store.upsert("a", role="requester", state=TaskState.COMPLETED)
    store.upsert("b", role="requester", state=TaskState.INTERRUPTED)
    store.upsert("c", role="server", state=TaskState.INTERRUPTED)
    found = store.by_state("requester", TaskState.INTERRUPTED, TaskState.AWAITING_INFO)
    assert [t["task_id"] for t in found] == ["b"]
    store.close()
