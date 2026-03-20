"""Team Room UI helpers for study_ai.

MVP goals:
- Event log (JSONL)
- Agent state board
- Mission board (simple)

We keep this in a separate module to avoid app.py becoming unmaintainable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def utc_ts() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class TeamPaths:
    history_dir: Path

    @property
    def event_log(self) -> Path:
        return self.history_dir / "team_room.jsonl"

    @property
    def agent_state(self) -> Path:
        return self.history_dir / "agent_state.json"

    @property
    def mission_board(self) -> Path:
        return self.history_dir / "mission_board.json"

    @property
    def task_queue(self) -> Path:
        return self.history_dir / "task_queue.jsonl"

    @property
    def worker_state(self) -> Path:
        return self.history_dir / "worker_state.json"


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def init_agent_state(role_ids: list[str], roles: dict) -> dict:
    # role_ids includes coordinator/coder/reviewer/integrator
    state: dict[str, dict] = {}
    for rid in role_ids:
        state[rid] = {
            "role_id": rid,
            "name": roles[rid]["name"],
            "status": "Idle",
            "task": "",
            "last": "",
            "updated_at": None,
            "round": None,
        }
    return state


def update_agent_state(paths: TeamPaths, role_id: str, **patch: Any) -> dict:
    cur = load_json(paths.agent_state, default={})
    if role_id not in cur:
        cur[role_id] = {"role_id": role_id}
    cur[role_id].update(patch)
    cur[role_id]["updated_at"] = utc_ts()
    save_json(paths.agent_state, cur)
    return cur


def mission_set(paths: TeamPaths, mission_id: str, **patch: Any) -> dict:
    board = load_json(paths.mission_board, default={"missions": {}})
    missions = board.setdefault("missions", {})
    m = missions.get(mission_id) or {"id": mission_id}
    m.update(patch)

    # Derive elapsed seconds if possible
    try:
        from datetime import datetime

        started_at = m.get("started_at")
        finished_at = m.get("finished_at")

        if started_at:
            s = datetime.fromisoformat(str(started_at))
            if finished_at:
                f = datetime.fromisoformat(str(finished_at))
                m["elapsed_sec"] = max(0.0, (f - s).total_seconds())
            else:
                # Running: compute live elapsed
                m["elapsed_sec_live"] = max(0.0, (datetime.utcnow() - s).total_seconds())
    except Exception:
        pass

    missions[mission_id] = m
    save_json(paths.mission_board, board)
    return board


EventCallback = Callable[[dict], None]


def emit_event(
    paths: TeamPaths,
    *,
    speaker: str,
    speaker_name: str,
    type: str,
    content: str,
    round: int | None = None,
    meta: dict | None = None,
    cb: EventCallback | None = None,
) -> dict:
    evt = {
        "ts": utc_ts(),
        "speaker": speaker,
        "speaker_name": speaker_name,
        "type": type,
        "content": content,
        "round": round,
        "meta": meta or {},
    }
    append_jsonl(paths.event_log, evt)
    if cb:
        cb(evt)
    return evt


# --- Task queue / worker ---

def enqueue_task(paths: TeamPaths, task: dict) -> None:
    """Append a task to the queue (JSONL)."""
    append_jsonl(paths.task_queue, task)


def read_queue(paths: TeamPaths, limit: int | None = None) -> list[dict]:
    return read_jsonl(paths.task_queue, limit=limit)


def take_next_task(paths: TeamPaths) -> dict | None:
    """Pop the next pending task.

    We implement a simple queue using JSONL rewriting.
    For demo purposes this is sufficient.
    """

    q = read_jsonl(paths.task_queue, limit=None)
    if not q:
        return None

    # Find first task with status=queued
    idx = None
    for i, t in enumerate(q):
        if isinstance(t, dict) and t.get("status") in (None, "queued"):
            idx = i
            break
    if idx is None:
        return None

    task = q[idx]
    task["status"] = "taken"
    task["taken_at"] = utc_ts()

    # Rewrite file atomically
    tmp = paths.task_queue.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for j, obj in enumerate(q):
            if j == idx:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
            else:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    tmp.replace(paths.task_queue)
    return task


def load_worker_state(paths: TeamPaths) -> dict:
    return load_json(paths.worker_state, default={"status": "Idle", "current": None, "updated_at": None})


def set_worker_state(paths: TeamPaths, **patch: Any) -> dict:
    cur = load_worker_state(paths)
    cur.update(patch)
    cur["updated_at"] = utc_ts()
    save_json(paths.worker_state, cur)
    return cur


def set_paused(paths: TeamPaths, paused: bool) -> dict:
    return set_worker_state(paths, paused=bool(paused))


def is_paused(paths: TeamPaths) -> bool:
    ws = load_worker_state(paths)
    return bool(ws.get("paused"))


def cancel_queued_task(paths: TeamPaths, mission_id: str) -> bool:
    """Mark a queued task as canceled by mission_id."""
    if not mission_id:
        return False
    q = read_jsonl(paths.task_queue, limit=None)
    if not q:
        return False
    changed = False
    for obj in q:
        if isinstance(obj, dict) and obj.get("mission_id") == mission_id and obj.get("status") in (None, "queued"):
            obj["status"] = "canceled"
            obj["canceled_at"] = utc_ts()
            changed = True
    if not changed:
        return False
    tmp = paths.task_queue.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for obj in q:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    tmp.replace(paths.task_queue)
    return True


def reorder_queue(paths: TeamPaths, mission_id: str, direction: str) -> bool:
    """Move a queued task up/down in the queue.

    direction: 'up' or 'down'
    Only affects tasks with status queued.
    """
    if not mission_id:
        return False
    q = read_jsonl(paths.task_queue, limit=None)
    if not q:
        return False

    # collect indices of queued tasks
    queued_idx = [i for i, t in enumerate(q) if isinstance(t, dict) and t.get("status") in (None, "queued")]
    # find mission index among queued
    pos = None
    for j, i in enumerate(queued_idx):
        if q[i].get("mission_id") == mission_id:
            pos = j
            break
    if pos is None:
        return False

    if direction == "up" and pos > 0:
        i1, i2 = queued_idx[pos], queued_idx[pos - 1]
        q[i1], q[i2] = q[i2], q[i1]
    elif direction == "down" and pos < len(queued_idx) - 1:
        i1, i2 = queued_idx[pos], queued_idx[pos + 1]
        q[i1], q[i2] = q[i2], q[i1]
    else:
        return False

    tmp = paths.task_queue.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for obj in q:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    tmp.replace(paths.task_queue)
    return True
