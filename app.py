import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import streamlit as st
import streamlit.components.v1 as components
from openai import DefaultHttpxClient, OpenAI

from team_ui import (
    TeamPaths,
    cancel_queued_task,
    emit_event,
    enqueue_task,
    init_agent_state,
    is_paused,
    load_json,
    load_worker_state,
    mission_set,
    read_jsonl,
    read_queue,
    save_json,
    set_paused,
    set_worker_state,
    take_next_task,
    update_agent_state,
)

# This VM has proxy envs (including socks://) that can break httpx/OpenAI SDK.
# For this app, we explicitly bypass env proxies and talk to the local Gateway.
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])

ROOT = Path(__file__).parent
REPO_ROOT = ROOT
HISTORY_DIR = ROOT / "history"
HISTORY_DIR.mkdir(exist_ok=True)

ORCH_STATE_PATH = HISTORY_DIR / "orchestrator_state.json"
ORCH_MAX_GOAL_CHARS = 4000
ORCH_MAX_TRANSCRIPT_CHARS = 20000

TEAMS_ROOT = HISTORY_DIR / "teams"
REGISTRY_PATH = TEAMS_ROOT / "registry.json"
TEAM_EVENTS_LIMIT = 400


def team_paths(team_id: str) -> TeamPaths:
    team_id = (team_id or "team_default").strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", team_id)
    return TeamPaths(TEAMS_ROOT / safe)


def ensure_registry_and_migrate_default() -> None:
    """Create teams registry, and migrate legacy single-team files into team_default.

    Legacy files live directly under HISTORY_DIR (history/*.jsonl, team_room.jsonl, mission_board.json, etc.).
    We COPY them into history/teams/team_default on first run to avoid destructive moves.
    """

    TEAMS_ROOT.mkdir(parents=True, exist_ok=True)

    if not REGISTRY_PATH.exists():
        data = {
            "teams": [
                {
                    "id": "team_default",
                    "name": "study_ai 默认团队",
                    "created_at": datetime.utcnow().isoformat(),
                }
            ]
        }
        REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Migrate if legacy markers exist.
    legacy_markers = [
        HISTORY_DIR / "team_room.jsonl",
        HISTORY_DIR / "mission_board.json",
        HISTORY_DIR / "task_queue.jsonl",
        HISTORY_DIR / "agent_state.json",
        HISTORY_DIR / "worker_state.json",
    ]
    if any(p.exists() for p in legacy_markers):
        dst = team_paths("team_default").history_dir
        dst.mkdir(parents=True, exist_ok=True)
        # Copy only known team-related files
        for p in legacy_markers:
            if p.exists():
                (dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        # Also copy per-role histories (coordinator/coder/reviewer/integrator)
        for p in HISTORY_DIR.glob("*.jsonl"):
            if p.name in ("team_room.jsonl", "task_queue.jsonl"):
                continue
            (dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        # Keep legacy files in place (non-destructive).


def load_roles(path: Path | None = None) -> dict:
    p = path or (ROOT / "roles.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def roles_path_for_team(tp: TeamPaths) -> Path:
    p = tp.history_dir / "roles.json"
    return p if p.exists() else (ROOT / "roles.json")


def history_path(role_id: str, base_dir: Path = HISTORY_DIR) -> Path:
    return base_dir / f"{role_id}.jsonl"


def read_history(role_id: str, base_dir: Path = HISTORY_DIR) -> list[dict]:
    p = history_path(role_id, base_dir=base_dir)
    if not p.exists():
        return []
    msgs: list[dict] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msgs.append(json.loads(line))
    return msgs


def append_history(role_id: str, msg: dict, base_dir: Path = HISTORY_DIR) -> None:
    p = history_path(role_id, base_dir=base_dir)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def clear_history(role_id: str, base_dir: Path = HISTORY_DIR) -> None:
    p = history_path(role_id, base_dir=base_dir)
    if p.exists():
        p.unlink()


def _safe_repo_path(rel_path: str) -> Path:
    # Prevent writing outside repo.
    rel_path = rel_path.strip().lstrip("/")
    p = (REPO_ROOT / rel_path).resolve()
    if not str(p).startswith(str(REPO_ROOT.resolve()) + os.sep):
        raise ValueError(f"Unsafe path outside repo: {rel_path}")
    return p


def get_client() -> OpenAI:
    """Return an OpenAI client.

    Two modes:
    - Gateway proxy mode (preferred when you don't have an OpenAI key):
      set OPENCLAW_GATEWAY_TOKEN and optionally OPENCLAW_GATEWAY_URL.
    - Direct OpenAI mode: set OPENAI_API_KEY.

    We always bypass env proxies (trust_env=False) to avoid socks proxy issues.
    """

    http_client = DefaultHttpxClient(trust_env=False)

    gateway_token = os.getenv("OPENCLAW_GATEWAY_TOKEN")
    if gateway_token:
        base_url = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789/v1").rstrip("/")
        agent_id = os.getenv("OPENCLAW_AGENT_ID", "main")
        return OpenAI(
            base_url=base_url,
            api_key=gateway_token,
            default_headers={"x-openclaw-agent-id": agent_id},
            http_client=http_client,
        )

    return OpenAI(http_client=http_client)


def extract_json_block(text: str) -> Any | None:
    """Try to extract JSON from a fenced code block, or from the whole text."""
    if not text:
        return None

    # Prefer ```json ... ```
    m = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.S)
    if not m:
        m = re.search(r"```\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.S)
    candidate = m.group(1) if m else text.strip()

    try:
        return json.loads(candidate)
    except Exception:
        return None


def should_autorun_orchestrator(text: str) -> bool:
    """Heuristic: decide whether a coordinator chat message is a build/execute request.

    - Explicit: starts with /run
    - Implicit: contains strong implementation intent keywords.

    You can still discuss normally; those messages won't trigger orchestration.
    """

    if not text:
        return False
    t = text.strip()
    if t.lower().startswith("/run"):
        return True

    keywords = [
        "写一个",
        "做一个",
        "实现",
        "开发",
        "生成",
        "帮我写",
        "帮我做",
        "程序",
        "脚本",
        "工具",
        "项目",
        "pytest",
        "ci",
    ]
    score = sum(1 for k in keywords if k in t.lower())
    # Require a minimum signal to reduce accidental triggers.
    return score >= 2


def run_cmd(cmd: str, cwd: Path = REPO_ROOT, timeout: int = 120) -> tuple[int, str]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout


def _truncate(s: str, limit: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= limit else s[:limit] + "\n...<truncated>..."


def load_orchestrator_state() -> dict:
    if not ORCH_STATE_PATH.exists():
        return {"goal": "", "transcript": "", "updated_at": None}
    try:
        return json.loads(ORCH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"goal": "", "transcript": "", "updated_at": None}


def save_orchestrator_state(goal: str, transcript: str) -> None:
    data = {
        "goal": _truncate(goal or "", ORCH_MAX_GOAL_CHARS),
        "transcript": _truncate(transcript or "", ORCH_MAX_TRANSCRIPT_CHARS),
        "updated_at": datetime.utcnow().isoformat(),
    }
    ORCH_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def repo_snapshot(max_chars: int = 5000) -> str:
    parts: list[str] = []
    for c in [
        "git status --porcelain",
        "git log -1 --oneline",
        "ls -la",
    ]:
        code, out = run_cmd(c, timeout=30)
        parts.append(f"$ {c}\n{out.strip()}\n(exit={code})")
    snap = "\n\n".join(parts)
    if len(snap) > max_chars:
        snap = snap[: max_chars - 200] + "\n...<truncated>..."
    return snap


@dataclass
class RoleCall:
    to: str
    task: str


def call_role(role_id: str, roles: dict, history: list[dict], user_content: str, model: str) -> str:
    client = get_client()

    messages = [{"role": "system", "content": roles[role_id]["system"]}]
    for m in history:
        if m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m.get("content", "")})
    messages.append({"role": "user", "content": user_content})

    resp = client.chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content or ""


def run_orchestrator(
    user_goal: str,
    roles: dict,
    model: str,
    allow_write: bool,
    allow_commit: bool,
    max_rounds: int = 10,
    *,
    team_paths: TeamPaths | None = None,
    mission_id: str = "mission:main",
    on_event: Callable[[dict], None] | None = None,
) -> str:
    """Run multi-agent orchestration and optionally apply changes.

    Returns a markdown transcript.
    If team_paths is provided, also emits a structured event stream for the Team Room UI.
    """

    transcript: list[str] = []
    latest_snap = repo_snapshot()

    if team_paths is not None:
        # Ensure initial state exists.
        cur_state = load_json(team_paths.agent_state, default=None)
        if not isinstance(cur_state, dict) or not cur_state:
            save_json(team_paths.agent_state, init_agent_state(list(roles.keys()), roles))
        mission_set(
            team_paths,
            mission_id=mission_id,
            title=user_goal.strip()[:120] or "(no title)",
            status="Running",
            started_at=datetime.utcnow().isoformat(),
            type="main",
        )
        emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_START", content=f"任务开始：{user_goal.strip()}", cb=on_event)

    # Allow dynamic role ids per team. coordinator is the orchestrator; others are callable roles.
    callable_roles = [rid for rid in roles.keys() if rid != "coordinator"]
    coordinator_instruction = (
        "你要作为 Orchestrator 自动调度团队完成用户目标。\n"
        "你可以在每一轮输出一个 JSON 指令块（必须是严格 JSON），格式如下：\n\n"
        "{\n"
        "  \"round_goal\": \"本轮要达成什么\",\n"
        "  \"calls\": [\n"
        "    {\"to\": \"<ROLE_ID>\", \"task\": \"给该角色的具体指令\"}\n"
        "  ],\n"
        "  \"need_changes\": true|false,\n"
        "  \"done\": true|false\n"
        "}\n\n"
        "可用角色 ROLE_ID 列表：\n- "
        + "\n- ".join(callable_roles)
        + "\n\n"
        "规则：\n"
        "- done=true 表示已经达到验收标准并停止。\n"
        "- need_changes=true 表示下一步需要产出文件变更（如需要写入仓库文件，通常让交付/集成角色产出变更包）。\n"
        "- 你必须以 ```json ...``` 包裹 JSON。\n"
        "- 如果某个角色需要写入仓库文件，请让它输出一个严格 JSON：\n"
        "  {\"files\":[{\"path\":\"相对路径\",\"content\":\"全文\"}],\"commands\":[...],\"commit_message\":\"...\"}\n"
        "- 所有路径必须是仓库内相对路径，不允许删除文件。\n"
        "- 每轮要参考最新 repo 快照和上一轮角色反馈，避免反复。\n"
    )

    loop_notes: list[str] = []
    loop_notes.append(f"用户目标：\n{user_goal.strip()}")
    loop_notes.append(f"初始仓库快照：\n{latest_snap}")

    for r in range(1, max_rounds + 1):
        # Soft-stop: allow user to request stop for the running mission.
        if team_paths is not None:
            board = load_json(team_paths.mission_board, default={"missions": {}})
            m = (board.get("missions") or {}).get(mission_id) or {}
            if m.get("stop_requested"):
                mission_set(team_paths, mission_id=mission_id, status="Stopped", finished_at=datetime.utcnow().isoformat())
                emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_STOP_REQUESTED", content="检测到 stop_requested=true，已停止当前任务。", round=r, meta={"mission_id": mission_id}, cb=on_event)
                return "\n\n".join(transcript)

        coord_history = read_history("coordinator", base_dir=team_paths.history_dir if team_paths is not None else HISTORY_DIR)[-20:]

        coord_user = (
            f"用户目标：\n{user_goal.strip()}\n\n"
            f"当前仓库快照：\n{latest_snap}\n\n"
            f"已知信息/上轮反馈：\n" + "\n".join(loop_notes[-20:])
        )

        if team_paths is not None:
            update_agent_state(team_paths, "coordinator", status="Planning", task=f"Round {r}: 规划本轮调度", round=r)
        coord_reply = call_role(
            "coordinator",
            roles,
            coord_history,
            coordinator_instruction + "\n\n" + coord_user,
            model=model,
        )
        transcript.append(f"#### Round {r} - 总指挥\n{coord_reply}")
        if team_paths is not None:
            emit_event(team_paths, speaker="coordinator", speaker_name=roles["coordinator"]["name"], type="PLAN", content=coord_reply, round=r, cb=on_event)
            update_agent_state(team_paths, "coordinator", status="Idle", last=coord_reply[-400:], round=r)

        plan = extract_json_block(coord_reply)
        if not isinstance(plan, dict):
            loop_notes.append(f"Round {r}: 总指挥输出无法解析为 JSON，停止。")
            break

        calls_raw = plan.get("calls") or []
        calls: list[RoleCall] = []
        for i, c in enumerate(calls_raw):
            if not isinstance(c, dict):
                continue
            to = str(c.get("to", "")).strip()
            task = str(c.get("task", "")).strip()
            if to in roles and task:
                calls.append(RoleCall(to=to, task=task))
                if team_paths is not None:
                    mid = f"{mission_id}:r{r}:{i}:{to}"
                    mission_set(
                        team_paths,
                        mission_id=mid,
                        title=(task[:120] if task else f"Round {r} - {to}"),
                        status="Backlog",
                        owner=to,
                        round=r,
                        created_at=datetime.utcnow().isoformat(),
                        type="subtask",
                    )

        for i, call in enumerate(calls):
            mid = f"{mission_id}:r{r}:{i}:{call.to}"
            if team_paths is not None:
                mission_set(team_paths, mission_id=mid, status="Running", started_at=datetime.utcnow().isoformat())
                emit_event(team_paths, speaker="coordinator", speaker_name=roles["coordinator"]["name"], type="CALL", content=f"→ {roles[call.to]['name']}：{call.task}", round=r, meta={"mission_id": mid}, cb=on_event)
                update_agent_state(team_paths, call.to, status="Working", task=call.task, round=r)
                emit_event(team_paths, speaker=call.to, speaker_name=roles[call.to]["name"], type="START", content="开始执行。", round=r, meta={"mission_id": mid}, cb=on_event)

            h = read_history(call.to, base_dir=team_paths.history_dir if team_paths is not None else HISTORY_DIR)[-30:]
            reply = call_role(call.to, roles, h, call.task, model=model)
            transcript.append(f"**→ {roles[call.to]['name']}**\n\n{reply}")
            loop_notes.append(f"Round {r} {call.to} reply:\n{reply}")

            if team_paths is not None:
                emit_event(team_paths, speaker=call.to, speaker_name=roles[call.to]["name"], type="RESULT", content=reply, round=r, meta={"mission_id": mid}, cb=on_event)
                mission_set(team_paths, mission_id=mid, status="Completed", finished_at=datetime.utcnow().isoformat())
                update_agent_state(team_paths, call.to, status="Idle", last=reply[-400:], round=r)

            if call.to == "integrator" and allow_write:
                bundle = extract_json_block(reply)
                if isinstance(bundle, dict) and isinstance(bundle.get("files"), list):
                    files_payload = bundle.get("files", [])
                    if team_paths is not None:
                        emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="WRITE_INTENT", content=json.dumps({"files": [f.get("path") for f in files_payload if isinstance(f, dict)]}, ensure_ascii=False), round=r, cb=on_event)

                    changed = apply_file_writes(files_payload)
                    loop_notes.append(f"Applied file writes: {changed}")
                    if team_paths is not None:
                        emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="WRITE_FILES", content="\n".join(changed) or "(no files)", round=r, cb=on_event)
                        # Persist changed files into mission metadata
                        board0 = load_json(team_paths.mission_board, default={"missions": {}})
                        m0 = (board0.get("missions") or {}).get(mission_id) or {}
                        prev = m0.get("changed_files") or []
                        if not isinstance(prev, list):
                            prev = []
                        merged = list(dict.fromkeys([*prev, *changed]))
                        mission_set(team_paths, mission_id=mission_id, changed_files=merged)

                    cmd_logs: list[str] = []
                    for cmd in bundle.get("commands", []) or []:
                        if not isinstance(cmd, str) or not cmd.strip():
                            continue
                        code, out = run_cmd(cmd.strip(), timeout=300)
                        log = f"$ {cmd}\n{out}\n(exit={code})"
                        cmd_logs.append(log)
                        if team_paths is not None:
                            emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="RUN_CMD", content=log, round=r, cb=on_event)
                            if code != 0:
                                emit_event(team_paths, speaker="system", speaker_name="系统", type="ERROR", content=f"命令执行失败：{cmd} (exit={code})", round=r, cb=on_event)
                                mission_set(team_paths, mission_id=mission_id, status="Failed", error_summary=f"命令失败: {cmd} (exit={code})")
                    if cmd_logs:
                        loop_notes.append("Command logs:\n" + "\n\n".join(cmd_logs))

                    latest_snap = repo_snapshot()
                    if team_paths is not None:
                        emit_event(team_paths, speaker="system", speaker_name="系统", type="REPO_SNAPSHOT", content=latest_snap, round=r, cb=on_event)

                    if allow_commit:
                        msg = str(bundle.get("commit_message") or f"auto: round {r}").strip()
                        run_cmd("git add -A", timeout=60)
                        code, out = run_cmd(f"git commit -m {json.dumps(msg)}", timeout=60)
                        loop_notes.append(f"git commit: exit={code}\n{out}")
                        commit_hash = None
                        try:
                            mm = re.search(r"\[[^\]]+\s+([0-9a-f]{7,40})\]", out)
                            if mm:
                                commit_hash = mm.group(1)
                        except Exception:
                            commit_hash = None
                        if team_paths is not None:
                            emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="COMMIT", content=f"{msg}\n{out}\n(exit={code})", round=r, cb=on_event)
                            if code == 0 and commit_hash:
                                mission_set(team_paths, mission_id=mission_id, commit_hash=commit_hash, commit_message=msg)
                        latest_snap = repo_snapshot()

        if bool(plan.get("done")):
            loop_notes.append(f"Round {r}: done=true，停止。")
            if team_paths is not None:
                mission_set(team_paths, mission_id=mission_id, status="Completed", finished_at=datetime.utcnow().isoformat())
                emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_DONE", content="done=true，任务结束。", round=r, cb=on_event)
            break

    if team_paths is not None:
        # If we exited due to max rounds, mark as partial.
        board = load_json(team_paths.mission_board, default={"missions": {}})
        status = board.get("missions", {}).get(mission_id, {}).get("status")
        if status == "Running":
            mission_set(team_paths, mission_id=mission_id, status="Stopped", finished_at=datetime.utcnow().isoformat())
            emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_STOP", content=f"达到最大轮数 {max_rounds}，停止。", cb=on_event)

    return "\n\n".join(transcript)


def apply_file_writes(files: list[dict]) -> list[str]:
    changed: list[str] = []
    for f in files:
        path = f.get("path")
        content = f.get("content")
        if not path or content is None:
            continue
        p = _safe_repo_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        changed.append(str(p.relative_to(REPO_ROOT)))
    return changed


st.set_page_config(page_title="AI 开发团队管理台", layout="wide")
st.title("AI 开发团队管理台")

# --- UI animations (offline) ---
# CSS badges + inline vendored lottie-web (no external network/static hosting).
st.markdown(
    """
<style>
@keyframes oc_pulse {0%{transform:scale(1);opacity:.6}50%{transform:scale(1.15);opacity:1}100%{transform:scale(1);opacity:.6}}
@keyframes oc_spin {0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
@keyframes oc_bounce {0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}
@keyframes oc_shake {0%{transform:translateX(0)}25%{transform:translateX(-2px)}50%{transform:translateX(2px)}75%{transform:translateX(-2px)}100%{transform:translateX(0)}}

.oc-badge{display:inline-flex;align-items:center;gap:8px}
.oc-dot{width:8px;height:8px;border-radius:999px;display:inline-block}
.oc-dot.working{background:#3b82f6;animation:oc_pulse 1s infinite}
.oc-dot.planning{background:#f59e0b;animation:oc_bounce .8s infinite}
.oc-dot.idle{background:#9ca3af}
.oc-dot.failed{background:#ef4444;animation:oc_shake .6s infinite}
.oc-dot.done{background:#22c55e}

/* Lottie containers */
.oc-lottie{width:44px;height:44px;display:inline-block;vertical-align:middle}
</style>
""",
    unsafe_allow_html=True,
)

@st.cache_data
def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _lottie_js() -> str:
    return _read_text(ROOT / "assets" / "lottie" / "lottie.min.js")


def _lottie_anim(state: str) -> str:
    return _read_text(ROOT / "assets" / "lottie" / f"{state}.json")

ensure_registry_and_migrate_default()

# Query params: team_id
qp = st.query_params
current_team = str(qp.get("team", "team_default"))

# Load roles for the currently selected team
_tp0 = team_paths(current_team)
roles = load_roles(roles_path_for_team(_tp0))
role_ids = list(roles.keys())

# Sidebar
with st.sidebar:
    st.header("页面")
    page = st.radio(
        "选择页面",
        ["hub", "team"],
        format_func=lambda v: "总控面板" if v == "hub" else "团队面板",
        label_visibility="collapsed",
        index=0 if str(qp.get("page", "hub")) == "hub" else 1,
    )

    st.divider()
    st.header("团队")
    # Read registry
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    teams = reg.get("teams") or []
    # Sort by created_at asc
    teams = sorted(teams, key=lambda x: x.get("created_at") or "")
    team_ids = [t.get("id") for t in teams if t.get("id")]

    if current_team not in team_ids and team_ids:
        current_team = team_ids[0]

    selected_team = st.selectbox(
        "选择团队",
        team_ids,
        index=team_ids.index(current_team) if current_team in team_ids else 0,
        format_func=lambda tid: next((t.get("name") for t in teams if t.get("id") == tid), tid),
    )

    if selected_team != current_team:
        st.query_params.update({"team": selected_team, "page": page})
        st.rerun()

    st.divider()
    st.header("视图")
    view_mode = st.radio(
        "选择视图",
        ["team_room", "single_role"],
        format_func=lambda v: "团队聊天室" if v == "team_room" else "单角色聊天",
        label_visibility="collapsed",
    )

    st.divider()
    st.header("团队角色")
    role_id = st.radio(
        "选择角色",
        role_ids,
        format_func=lambda rid: roles[rid]["name"],
        label_visibility="collapsed",
        disabled=(view_mode == "team_room"),
    )

    st.divider()
    st.subheader("连接状态")

    gateway_token_present = bool(os.getenv("OPENCLAW_GATEWAY_TOKEN"))
    api_key_present = bool(os.getenv("OPENAI_API_KEY"))

    if gateway_token_present:
        st.write("OpenClaw Gateway 代转:", "✅ 已启用")
        st.write("OPENCLAW_GATEWAY_URL:", os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789/v1"))
        st.write("OPENCLAW_AGENT_ID:", os.getenv("OPENCLAW_AGENT_ID", "main"))
        model = st.text_input("模型名", value=os.getenv("OPENAI_MODEL", "openclaw"))
        st.caption("此模式下不需要 OpenAI Key；模型名通常用 openclaw。")
    else:
        st.write("OpenClaw Gateway 代转:", "❌ 未启用")
        st.write("OPENAI_API_KEY:", "✅ 已设置" if api_key_present else "❌ 未设置")
        model = st.text_input("模型名", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        st.caption("如需更换模型，可设置环境变量 OPENAI_MODEL 或在这里改。")

    st.divider()
    if st.button("清空当前角色对话记录", type="secondary"):
        _tp_single = team_paths(selected_team)
        clear_history(role_id, base_dir=_tp_single.history_dir)
        st.rerun()


def render_hub(selected_team: str) -> None:
    st.subheader("总控面板（Team Hub）")
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    teams = reg.get("teams") or []
    teams = sorted(teams, key=lambda x: x.get("created_at") or "")

    cols = st.columns(4)
    for i, t in enumerate(teams):
        tid = t.get("id")
        if not tid:
            continue
        tp = team_paths(tid)
        ws = load_worker_state(tp)
        q = read_queue(tp, limit=500)
        queued = [x for x in q if isinstance(x, dict) and x.get("status") in (None, "queued")]
        board = load_json(tp.mission_board, default={"missions": {}})
        missions = list((board.get("missions") or {}).values())
        running = [m for m in missions if m.get("status") == "Running" and m.get("type") == "main"]
        failed = [m for m in missions if m.get("status") == "Failed" and m.get("type") == "main"]
        canceled = [m for m in missions if m.get("status") == "Canceled" and m.get("type") == "main"]
        stopped = [m for m in missions if m.get("status") == "Stopped" and m.get("type") == "main"]

        # last event summary
        last_evt = None
        evts = read_jsonl(tp.event_log, limit=1)
        if evts:
            last_evt = evts[-1]

        with cols[i % 4]:
            with st.container(border=True):
                st.markdown(f"**{t.get('name', tid)}**")
                st.caption(f"team_id: {tid}")
                st.caption(f"创建：{t.get('created_at','-')}")
                st.write(f"Worker: {ws.get('status','?')}")
                if ws.get("status") == "Running":
                    st.caption(f"当前：{ws.get('current')}")
                st.write(f"队列待执行：{len(queued)}")
                st.write(f"Running: {len(running)} | Failed: {len(failed)}")
                st.caption(f"Stopped: {len(stopped)} | Canceled: {len(canceled)}")
                if last_evt:
                    st.caption(f"最近：[{last_evt.get('type')}] {last_evt.get('speaker_name')}")
                    snippet = str(last_evt.get('content','')).strip().replace('\n',' ')
                    if len(snippet) > 80:
                        snippet = snippet[:80] + '…'
                    if snippet:
                        st.caption(snippet)
                    st.caption(last_evt.get('ts','-'))
                else:
                    st.caption("最近：-（暂无事件）")

                if st.button("进入团队", key=f"enter_{tid}"):
                    st.query_params.update({"page": "team", "team": tid})
                    st.rerun()


def render_team_room(team_id: str) -> None:
    tp = team_paths(team_id)
    # Team-specific roles
    roles = load_roles(roles_path_for_team(tp))
    role_ids = list(roles.keys())
    st.subheader(f"团队面板：{team_id}")

    # Controls
    cols = st.columns(4)
    with cols[0]:
        allow_write = st.checkbox("允许自动写入仓库文件（仅限本 repo）", value=False, key="team_allow_write")
    with cols[1]:
        allow_commit = st.checkbox("允许自动 git add/commit（不会 push）", value=False, key="team_allow_commit")
    with cols[2]:
        auto_refresh = st.checkbox("自动刷新（2秒）", value=True, key="team_auto_refresh")
    with cols[3]:
        if st.button("清空团队事件/状态", type="secondary"):
            for p in [tp.event_log, tp.agent_state, tp.mission_board, tp.task_queue, tp.worker_state]:
                if p.exists():
                    p.unlink()
            st.rerun()

    # Worker status bar (very visible)
    ws = load_worker_state(tp)
    w_status = ws.get("status", "?")
    w_current = ws.get("current")
    paused = bool(ws.get("paused"))
    q = read_queue(tp, limit=200)
    queued = [t for t in q if isinstance(t, dict) and t.get("status") in (None, "queued")]
    st.caption(f"队列：待执行 {len(queued)} 个")

    # Worker controls
    c1, c2, c3 = st.columns(3)
    with c1:
        if not paused:
            if st.button("暂停 Worker", key="pause_worker"):
                set_paused(tp, True)
                emit_event(tp, speaker="system", speaker_name="系统", type="WORKER", content="Worker 已暂停")
                st.rerun()
        else:
            if st.button("恢复 Worker", key="resume_worker"):
                set_paused(tp, False)
                emit_event(tp, speaker="system", speaker_name="系统", type="WORKER", content="Worker 已恢复")
                st.rerun()

    with c2:
        if w_status == "Running" and w_current:
            if st.button("停止当前任务", key="stop_current"):
                mission_set(tp, mission_id=w_current, stop_requested=True)
                emit_event(tp, speaker="system", speaker_name="系统", type="MISSION_STOP_REQUEST", content=f"已请求停止：{w_current}", meta={"mission_id": w_current})
                st.warning("已请求停止：将在下一轮开始前生效（软停止）。")

    with c3:
        st.write("")

    if paused:
        st.warning("Worker：Paused（暂停中）")
    elif w_status == "Running":
        st.info(f"Worker：Running | 当前任务：{w_current}")
    else:
        st.success("Worker：Idle（空闲）")

    # --- Background worker (queue executor) ---
    import threading
    import time

    def _worker_loop() -> None:
        while True:
            try:
                ws = load_worker_state(tp)
                if ws.get("paused"):
                    time.sleep(1.0)
                    continue
                if ws.get("status") in (None, "Idle"):
                    task = take_next_task(tp)
                    if task:
                        mission_id = str(task.get("mission_id") or "mission:main")
                        goal = str(task.get("goal") or "").strip()
                        allow_write_ = bool(task.get("allow_write"))
                        allow_commit_ = bool(task.get("allow_commit"))
                        model_ = str(task.get("model") or model)

                        set_worker_state(tp, status="Running", current=mission_id)
                        emit_event(tp, speaker="system", speaker_name="系统", type="WORKER", content=f"开始执行队列任务：{mission_id}")

                        try:
                            transcript_md = run_orchestrator(
                                user_goal=goal,
                                roles=roles,
                                model=model_,
                                allow_write=allow_write_,
                                allow_commit=allow_commit_,
                                max_rounds=10,
                                team_paths=tp,
                                mission_id=mission_id,
                                on_event=None,
                            )
                            save_orchestrator_state(goal, transcript_md)
                        except Exception as e:
                            emit_event(tp, speaker="system", speaker_name="系统", type="ERROR", content=f"Worker 执行异常：{e}")
                            mission_set(tp, mission_id=mission_id, status="Failed", error_summary=str(e))
                        finally:
                            set_worker_state(tp, status="Idle", current=None)
                time.sleep(1.0)
            except Exception:
                # Avoid worker dying.
                time.sleep(2.0)

    @st.cache_resource
    def _start_worker_once() -> bool:
        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()
        return True

    _start_worker_once()

    # Layout: Mission board large, Office as side panel, Timeline on the left.
    # Three-column layout works better for demo screens.
    left, mid, right = st.columns([2, 2, 1])

    # Selection state for "jump to timeline"
    if "team_selected_mission" not in st.session_state:
        st.session_state.team_selected_mission = None

    with right:
        st.markdown("### 办公室大屏")
        agent_state = load_json(tp.agent_state, default=init_agent_state(role_ids, roles))

        # Fixed display order for prototype team and other teams (if present)
        preferred = ["coordinator", "pm", "ux", "prototyper", "writer", "publisher", "coder", "reviewer", "integrator"]
        ordered = [rid for rid in preferred if rid in role_ids]
        ordered += [rid for rid in role_ids if rid not in ordered]

        for rid in ordered:
            if rid not in roles:
                continue
            s = agent_state.get(rid) or {"name": roles[rid]["name"], "status": "(unknown)", "task": "", "last": ""}
            with st.container(border=True):
                name = s.get('name', rid)
                status = s.get('status','')

                # Offline animated badge + vendored Lottie (best-effort).
                cls = 'idle'
                lottie_state = 'idle'
                if status == 'Working':
                    cls = 'working'
                    lottie_state = 'working'
                elif status == 'Planning':
                    cls = 'planning'
                    lottie_state = 'planning'
                elif status in ('Failed','Error'):
                    cls = 'failed'
                    lottie_state = 'failed'
                elif status in ('Done','Completed'):
                    cls = 'done'
                    lottie_state = 'done'

                # Use unique DOM id so multiple cards don't collide.
                dom_id = f"lottie_{rid}".replace(":", "_").replace("/", "_")

                # Inline vendored lottie library + animation json (offline)
                lottie_js = _lottie_js()
                anim_json = _lottie_anim(lottie_state)
                # NOTE: Streamlit does not reliably execute <script> in st.markdown.
                # Use components.html (iframe) to ensure JS runs.
                header_html = f"""
<!doctype html>
<html>
<head>
<meta charset='utf-8'/>
<style>
  body {{ margin:0; padding:0; background: transparent; }}
  .wrap {{ display:flex; align-items:center; gap:8px; font-family: sans-serif; }}
  .dot {{ width:8px;height:8px;border-radius:999px; background:#9ca3af; }}
  .dot.working {{ background:#3b82f6; }}
  .dot.planning {{ background:#f59e0b; }}
  .dot.failed {{ background:#ef4444; }}
  .dot.done {{ background:#22c55e; }}
  #anim {{ width:44px; height:44px; }}
  .name {{ font-weight:700; font-size:14px; }}
</style>
</head>
<body>
<div class='wrap'>
  <span class='dot {cls}'></span>
  <div id='anim'></div>
  <span class='name'>{name}</span>
</div>
<script>
{lottie_js}
var animData = {anim_json};
var el = document.getElementById('anim');
window.lottie.loadAnimation({{
  container: el,
  renderer: 'svg',
  loop: true,
  autoplay: true,
  animationData: animData
}});
</script>
</body>
</html>
"""
                components.html(header_html, height=54)
                st.caption(f"状态：{status}  |  Round: {s.get('round','-')}  | 更新：{s.get('updated_at','-')}")
                if s.get("task"):
                    st.markdown(f"任务：{s['task']}")
                if s.get("last"):
                    st.caption("最近输出：")
                    st.code(str(s.get("last"))[-600:], language="markdown")

    with mid:
        st.markdown("### Mission 看板")
        # Touch the missions to update live elapsed for running items.
        board = load_json(tp.mission_board, default={"missions": {}})
        for _mid, _m in (board.get("missions") or {}).items():
            try:
                if isinstance(_m, dict) and _m.get("status") == "Running" and _m.get("started_at") and not _m.get("finished_at"):
                    mission_set(tp, mission_id=_mid, started_at=_m.get("started_at"))
            except Exception:
                pass

        board = load_json(tp.mission_board, default={"missions": {}})
        missions = list((board.get("missions") or {}).values())
        # For backward compatibility, pick the newest main mission as "current".
        mains = [m for m in missions if m.get("type") == "main"]
        mains_sorted = sorted(mains, key=lambda x: x.get("created_at") or x.get("started_at") or "", reverse=True)
        main = mains_sorted[0] if mains_sorted else {}

        # Main mission header
        with st.container(border=True):
            st.markdown(f"**主任务**：{main.get('title','(none)')}")
            status = main.get("status", "(none)")
            elapsed = main.get("elapsed_sec")
            elapsed_live = main.get("elapsed_sec_live")
            if isinstance(elapsed, (int, float)):
                elapsed_txt = f" | 耗时：{int(elapsed)}s"
            elif isinstance(elapsed_live, (int, float)) and status == "Running":
                elapsed_txt = f" | 已运行：{int(elapsed_live)}s"
            else:
                elapsed_txt = ""
            st.caption(
                f"状态：{status}{elapsed_txt}  | 开始：{main.get('started_at','-')}  | 结束：{main.get('finished_at','-')}"
            )
            if status == "Failed" and main.get("error_summary"):
                st.error(f"失败原因：{main.get('error_summary')}")

            # Artifacts / rollback info & actions
            changed_files = main.get("changed_files") or []
            commit_hash = main.get("commit_hash")
            if changed_files or commit_hash:
                st.divider()
                st.markdown("**产物 / 回滚**")
                if changed_files:
                    st.caption("改动文件：")
                    st.code("\n".join(changed_files)[:1500], language="text")
                if commit_hash:
                    st.caption(f"commit: {commit_hash}  ({main.get('commit_message','')})")

                # Confirmed actions (destructive)
                if "rollback_confirm" not in st.session_state:
                    st.session_state.rollback_confirm = {}

                keybase = str(main.get("id") or "main")
                if commit_hash:
                    if st.button("回滚该 commit（git revert）", key=f"revert_btn_{keybase}"):
                        st.session_state.rollback_confirm[f"revert:{keybase}"] = True
                    if st.session_state.rollback_confirm.get(f"revert:{keybase}"):
                        st.warning("确认执行 git revert？这会生成一个反向提交。")
                        if st.button("确认回滚", key=f"revert_yes_{keybase}"):
                            cmd = f"git revert --no-edit {commit_hash}"
                            code, out = run_cmd(cmd, timeout=300)
                            emit_event(tp, speaker="integrator", speaker_name="集成提交", type="ROLLBACK", content=f"$ {cmd}\n{out}\n(exit={code})", meta={"mission_id": keybase})
                            st.session_state.rollback_confirm.pop(f"revert:{keybase}", None)
                            st.rerun()
                        if st.button("取消回滚", key=f"revert_no_{keybase}"):
                            st.session_state.rollback_confirm.pop(f"revert:{keybase}", None)
                            st.rerun()
                else:
                    # No commit: allow restoring working tree files
                    if changed_files:
                        if st.button("撤回未提交改动（git restore）", key=f"restore_btn_{keybase}"):
                            st.session_state.rollback_confirm[f"restore:{keybase}"] = True
                        if st.session_state.rollback_confirm.get(f"restore:{keybase}"):
                            st.warning("确认执行 git restore 以撤回这些未提交改动？")
                            if st.button("确认撤回", key=f"restore_yes_{keybase}"):
                                files = " ".join(json.dumps(f) for f in changed_files)
                                cmd = f"git restore -- {files}"
                                code, out = run_cmd(cmd, timeout=300)
                                emit_event(tp, speaker="integrator", speaker_name="集成提交", type="ROLLBACK", content=f"$ {cmd}\n{out}\n(exit={code})", meta={"mission_id": keybase})
                                st.session_state.rollback_confirm.pop(f"restore:{keybase}", None)
                                st.rerun()
                            if st.button("取消撤回", key=f"restore_no_{keybase}"):
                                st.session_state.rollback_confirm.pop(f"restore:{keybase}", None)
                                st.rerun()

        def _by_status(wanted: str) -> list[dict]:
            if wanted in ("Canceled", "Stopped"):
                return [m for m in missions if (m.get("status") == wanted and m.get("type") == "main")]
            return [m for m in missions if (m.get("status") == wanted and m.get("type") != "main")]

        lanes = [
            ("Backlog", "待办"),
            ("Running", "执行中"),
            ("Completed", "完成"),
            ("Failed", "失败"),
            ("Canceled", "已撤销"),
            ("Stopped", "已停止"),
        ]
        cols = st.columns(6)
        for col, (status, label) in zip(cols, lanes):
            with col:
                st.markdown(f"**{label}**")
                items = _by_status(status)
                if not items:
                    st.caption("（空）")
                for m in items[:30]:
                    title = m.get("title") or m.get("id")
                    owner = m.get("owner") or "-"
                    rnd = m.get("round") or "-"
                    mid_ = m.get("id")
                    with st.container(border=True):
                        badge = ""
                        if m.get("status") == "Running":
                            badge = "🟦 "
                        elif m.get("status") == "Completed":
                            badge = "✅ "
                        elif m.get("status") == "Failed":
                            badge = "🟥 "
                        st.markdown(badge + title)
                        # show elapsed if available
                        es = m.get("elapsed_sec")
                        esl = m.get("elapsed_sec_live")
                        if isinstance(es, (int, float)):
                            ttxt = f"{int(es)}s"
                        elif isinstance(esl, (int, float)) and m.get("status") == "Running":
                            ttxt = f"{int(esl)}s"
                        else:
                            ttxt = "-"
                        st.caption(f"owner: {owner} | round: {rnd} | t: {ttxt}")
                        if m.get("status") == "Failed" and m.get("error_summary"):
                            st.error(str(m.get("error_summary"))[:200])

                        # Cancel queued main missions
                        if m.get("type") == "main" and m.get("status") == "Backlog" and mid_:
                            if st.button("撤销", key=f"cancel_{mid_}"):
                                ok = cancel_queued_task(tp, mid_)
                                if ok:
                                    mission_set(tp, mission_id=mid_, status="Canceled", finished_at=datetime.utcnow().isoformat())
                                    emit_event(tp, speaker="system", speaker_name="系统", type="CANCEL", content=f"已撤销：{mid_}", meta={"mission_id": mid_})
                                    st.rerun()
                                else:
                                    st.warning("撤销失败：可能已开始执行或不在队列中")

                        # "Jump" behavior: filter/highlight related timeline events.
                        if mid_:
                            if st.button("定位到时间线", key=f"jump_{mid_}"):
                                st.session_state.team_selected_mission = mid_

    with left:
        st.markdown("### 时间线（完整日志）")
        timeline_box = st.container(height=520)

        def render_timeline() -> None:
            evts = read_jsonl(tp.event_log, limit=TEAM_EVENTS_LIMIT)
            selected_mid = st.session_state.get("team_selected_mission")

            with timeline_box:
                if selected_mid:
                    st.info(f"已定位：{selected_mid}（仅高亮/展示相关事件）")
                    if st.button("清除定位", key="clear_jump"):
                        st.session_state.team_selected_mission = None
                        st.rerun()

                for e in evts:
                    speaker_name = e.get("speaker_name") or e.get("speaker")
                    typ = e.get("type")
                    ts = e.get("ts")
                    rnd = e.get("round")
                    meta = e.get("meta") or {}
                    mid = meta.get("mission_id")

                    if selected_mid and mid != selected_mid:
                        # Show only related events when a mission is selected.
                        continue

                    header = f"[{typ}] {speaker_name}  (round {rnd})\n{ts}" if rnd else f"[{typ}] {speaker_name}\n{ts}"
                    with st.chat_message("assistant"):
                        st.caption(header)
                        st.markdown(e.get("content", ""))

        render_timeline()

        prompt = st.chat_input("在这里给团队布置任务（会进入队列，自动逐个执行）", key="team_prompt")
        if prompt:
            if not (gateway_token_present or api_key_present):
                st.warning("未检测到模型凭证（OPENCLAW_GATEWAY_TOKEN 或 OPENAI_API_KEY），无法执行。")
                return

            # Create a new mission id per task.
            mission_id = f"mission:{int(datetime.utcnow().timestamp())}"

            emit_event(tp, speaker="user", speaker_name="你", type="USER", content=prompt, meta={"mission_id": mission_id}, cb=None)
            mission_set(
                tp,
                mission_id=mission_id,
                title=prompt.strip()[:120] or "(no title)",
                status="Backlog",
                created_at=datetime.utcnow().isoformat(),
                type="main",
            )

            enqueue_task(
                tp,
                {
                    "mission_id": mission_id,
                    "goal": prompt.strip(),
                    "status": "queued",
                    "queued_at": datetime.utcnow().isoformat(),
                    "allow_write": bool(allow_write),
                    "allow_commit": bool(allow_commit),
                    "model": model,
                },
            )

            emit_event(tp, speaker="system", speaker_name="系统", type="QUEUE", content=f"已入队：{mission_id}", meta={"mission_id": mission_id}, cb=None)
            st.success(f"任务已入队：{mission_id}（正在执行的任务不会被打断）")
            st.rerun()


def render_single_role() -> None:
    role = roles[role_id]
    st.subheader(f"当前角色：{role['name']}")


if page == "hub":
    render_hub(selected_team)
    st.stop()

# page == team
if view_mode == "team_room":
    render_team_room(selected_team)
    st.stop()

render_single_role()

if not (gateway_token_present or api_key_present):
    st.warning(
        "未检测到任何可用的模型凭证。你有两种方式任选其一：\n\n"
        "1) 走 OpenClaw Gateway 代转（推荐，无需 OpenAI Key）：\n"
        "   export OPENCLAW_GATEWAY_TOKEN='你的gateway token'\n\n"
        "2) 直连 OpenAI：\n"
        "   export OPENAI_API_KEY='你的OpenAI key'\n\n"
        "设置后重启本页面即可。",
        icon="⚠️",
    )

# Single-role chat uses the selected team's history directory
_tp_single = team_paths(selected_team)
history = read_history(role_id, base_dir=_tp_single.history_dir)

# Render history
for m in history:
    with st.chat_message(m.get("role", "assistant")):
        st.markdown(m.get("content", ""))

prompt = st.chat_input("给这个角色发消息（例如：给你一个任务……）")

# --- Orchestrator UI (only shown in coordinator) ---
if role_id == "coordinator":
    st.divider()
    st.markdown("### 总指挥自动调度（单输入框）")
    st.caption(
        "你直接在下方聊天框跟总指挥说话。\n"
        "当总指挥判断你是在下达‘实现/写程序’类指令时，会自动调度写代码/审查/集成（最多10轮）。\n"
        "你也可以用 **/run 你的目标** 强制触发自动执行。"
    )

    st.checkbox("允许自动写入仓库文件（仅限本 repo）", value=False, key="orch_allow_write")
    st.checkbox("允许自动 git add/commit（不会 push）", value=False, key="orch_allow_commit")
    st.checkbox("自动判定是否触发执行（否则仅聊天）", value=True, key="orch_auto_from_chat")

    persisted = load_orchestrator_state()
    if "orchestrator_transcript" not in st.session_state:
        st.session_state.orchestrator_transcript = persisted.get("transcript", "") or ""

    if st.session_state.orchestrator_transcript:
        st.divider()
        st.markdown("### 上一次自动调度输出（已持久化，大小受限）")
        st.markdown(st.session_state.orchestrator_transcript)

    cols = st.columns(2)
    with cols[0]:
        if st.button("清空已保存的目标/输出", type="secondary"):
            save_orchestrator_state("", "")
            st.session_state.orchestrator_transcript = ""
            st.rerun()
    with cols[1]:
        st.write("")


if prompt:
    user_msg = {"role": "user", "content": prompt, "ts": datetime.utcnow().isoformat()}
    _tp_single = team_paths(selected_team)
    append_history(role_id, user_msg, base_dir=_tp_single.history_dir)

    with st.chat_message("user"):
        st.markdown(prompt)

    if not (gateway_token_present or api_key_present):
        with st.chat_message("assistant"):
            st.markdown("（未设置 OPENCLAW_GATEWAY_TOKEN 或 OPENAI_API_KEY，无法调用模型）")
    else:
        # Coordinator: optionally auto-run orchestration directly from the chat input.
        if role_id == "coordinator" and st.session_state.get("orch_auto_from_chat", True) and should_autorun_orchestrator(prompt):
            # Support explicit /run prefix.
            goal = prompt.strip()
            if goal.lower().startswith("/run"):
                goal = goal[4:].strip()

            with st.chat_message("assistant"):
                with st.spinner("总指挥正在自动调度执行（最多10轮）..."):
                    transcript_md = run_orchestrator(
                        user_goal=goal,
                        roles=roles,
                        model=model,
                        allow_write=bool(st.session_state.get("orch_allow_write", False)),
                        allow_commit=bool(st.session_state.get("orch_allow_commit", False)),
                        max_rounds=10,
                    )
                    st.session_state.orchestrator_transcript = transcript_md
                    save_orchestrator_state(goal, st.session_state.orchestrator_transcript)
                    st.markdown(transcript_md)

            assistant_msg = {
                "role": "assistant",
                "content": transcript_md,
                "ts": datetime.utcnow().isoformat(),
                "model": model,
            }
            _tp_single = team_paths(selected_team)
            append_history(role_id, assistant_msg, base_dir=_tp_single.history_dir)
        else:
            client = get_client()

            # Compose messages: system + role history (without ts)
            messages = [{"role": "system", "content": role["system"]}]
            _tp_single = team_paths(selected_team)
            for m in read_history(role_id, base_dir=_tp_single.history_dir):
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})

            with st.chat_message("assistant"):
                with st.spinner("思考中..."):
                    resp = client.chat.completions.create(
                        model=model,
                        messages=messages,
                    )
                    content = resp.choices[0].message.content or ""
                    st.markdown(content)

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "ts": datetime.utcnow().isoformat(),
                "model": model,
            }
            _tp_single = team_paths(selected_team)
            append_history(role_id, assistant_msg, base_dir=_tp_single.history_dir)
