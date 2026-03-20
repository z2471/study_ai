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
    missions[mission_id] = m
    save_json(paths.mission_board, board)
    return board


EventCallback = Callable[[dict], None]


def emit_event(paths: TeamPaths, *, speaker: str, speaker_name: str, type: str, content: str, round: int | None = None, meta: dict | None = None, cb: EventCallback | None = None) -> dict:
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
