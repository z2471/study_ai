import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import streamlit as st
from openai import DefaultHttpxClient, OpenAI

from team_ui import TeamPaths, emit_event, init_agent_state, load_json, mission_set, read_jsonl, save_json, update_agent_state

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

TEAM_PATHS = TeamPaths(HISTORY_DIR)
TEAM_EVENTS_LIMIT = 400


def load_roles() -> dict:
    with open(ROOT / "roles.json", "r", encoding="utf-8") as f:
        return json.load(f)


def history_path(role_id: str) -> Path:
    return HISTORY_DIR / f"{role_id}.jsonl"


def read_history(role_id: str) -> list[dict]:
    p = history_path(role_id)
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


def append_history(role_id: str, msg: dict) -> None:
    p = history_path(role_id)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def clear_history(role_id: str) -> None:
    p = history_path(role_id)
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
        mission_set(team_paths, mission_id="current", title=user_goal.strip()[:80] or "(no title)", status="Running", started_at=datetime.utcnow().isoformat())
        emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_START", content=f"任务开始：{user_goal.strip()}", cb=on_event)

    coordinator_instruction = (
        "你要作为 Orchestrator 自动调度团队完成用户目标。\n"
        "你可以在每一轮输出一个 JSON 指令块（必须是严格 JSON），格式如下：\n\n"
        "{\n"
        "  \"round_goal\": \"本轮要达成什么\",\n"
        "  \"calls\": [\n"
        "    {\"to\": \"coder\"|\"reviewer\"|\"integrator\", \"task\": \"给该角色的具体指令\"}\n"
        "  ],\n"
        "  \"need_changes\": true|false,\n"
        "  \"done\": true|false\n"
        "}\n\n"
        "规则：\n"
        "- done=true 表示已经达到验收标准并停止。\n"
        "- need_changes=true 表示下一步需要产出代码/文件变更（通常让 integrator 产出变更包）。\n"
        "- 你必须以 ```json ...``` 包裹 JSON。\n"
        "- 在给 integrator 的 task 里，要求它如果要改代码，输出一个严格 JSON：\n"
        "  {\"files\":[{\"path\":\"相对路径\",\"content\":\"全文\"}],\"commands\":[...],\"commit_message\":\"...\"}\n"
        "- 所有路径必须是仓库内相对路径，不允许删除文件。\n"
        "- 每轮要参考最新 repo 快照和上一轮角色反馈，避免反复。\n"
    )

    loop_notes: list[str] = []
    loop_notes.append(f"用户目标：\n{user_goal.strip()}")
    loop_notes.append(f"初始仓库快照：\n{latest_snap}")

    for r in range(1, max_rounds + 1):
        coord_history = read_history("coordinator")[-20:]

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
        for c in calls_raw:
            if not isinstance(c, dict):
                continue
            to = str(c.get("to", "")).strip()
            task = str(c.get("task", "")).strip()
            if to in roles and task:
                calls.append(RoleCall(to=to, task=task))

        for call in calls:
            if team_paths is not None:
                emit_event(team_paths, speaker="coordinator", speaker_name=roles["coordinator"]["name"], type="CALL", content=f"→ {roles[call.to]['name']}：{call.task}", round=r, cb=on_event)
                update_agent_state(team_paths, call.to, status="Working", task=call.task, round=r)
                emit_event(team_paths, speaker=call.to, speaker_name=roles[call.to]["name"], type="START", content="开始执行。", round=r, cb=on_event)

            h = read_history(call.to)[-30:]
            reply = call_role(call.to, roles, h, call.task, model=model)
            transcript.append(f"**→ {roles[call.to]['name']}**\n\n{reply}")
            loop_notes.append(f"Round {r} {call.to} reply:\n{reply}")

            if team_paths is not None:
                emit_event(team_paths, speaker=call.to, speaker_name=roles[call.to]["name"], type="RESULT", content=reply, round=r, cb=on_event)
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

                    cmd_logs: list[str] = []
                    for cmd in bundle.get("commands", []) or []:
                        if not isinstance(cmd, str) or not cmd.strip():
                            continue
                        code, out = run_cmd(cmd.strip(), timeout=300)
                        log = f"$ {cmd}\n{out}\n(exit={code})"
                        cmd_logs.append(log)
                        if team_paths is not None:
                            emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="RUN_CMD", content=log, round=r, cb=on_event)
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
                        if team_paths is not None:
                            emit_event(team_paths, speaker="integrator", speaker_name=roles["integrator"]["name"], type="COMMIT", content=f"{msg}\n{out}\n(exit={code})", round=r, cb=on_event)
                        latest_snap = repo_snapshot()

        if bool(plan.get("done")):
            loop_notes.append(f"Round {r}: done=true，停止。")
            if team_paths is not None:
                mission_set(team_paths, mission_id="current", status="Completed", finished_at=datetime.utcnow().isoformat())
                emit_event(team_paths, speaker="system", speaker_name="系统", type="MISSION_DONE", content="done=true，任务结束。", round=r, cb=on_event)
            break

    if team_paths is not None:
        # If we exited due to max rounds, mark as partial.
        board = load_json(team_paths.mission_board, default={"missions": {}})
        status = board.get("missions", {}).get("current", {}).get("status")
        if status == "Running":
            mission_set(team_paths, mission_id="current", status="Stopped", finished_at=datetime.utcnow().isoformat())
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

roles = load_roles()
role_ids = list(roles.keys())

# Sidebar
with st.sidebar:
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
        clear_history(role_id)
        st.rerun()


def render_team_room() -> None:
    st.subheader("团队聊天室（Team Room）")

    # Controls
    cols = st.columns(3)
    with cols[0]:
        allow_write = st.checkbox("允许自动写入仓库文件（仅限本 repo）", value=False, key="team_allow_write")
    with cols[1]:
        allow_commit = st.checkbox("允许自动 git add/commit（不会 push）", value=False, key="team_allow_commit")
    with cols[2]:
        if st.button("清空团队事件/状态", type="secondary"):
            # Clear files
            if TEAM_PATHS.event_log.exists():
                TEAM_PATHS.event_log.unlink()
            if TEAM_PATHS.agent_state.exists():
                TEAM_PATHS.agent_state.unlink()
            if TEAM_PATHS.mission_board.exists():
                TEAM_PATHS.mission_board.unlink()
            st.rerun()

    # Right side: Office + Mission
    left, right = st.columns([2, 1])

    with right:
        st.markdown("### 办公室大屏")
        agent_state = load_json(TEAM_PATHS.agent_state, default=init_agent_state(role_ids, roles))
        # Render as cards
        for rid in ["coordinator", "coder", "reviewer", "integrator"]:
            s = agent_state.get(rid) or {"name": roles[rid]["name"], "status": "(unknown)", "task": "", "last": ""}
            with st.container(border=True):
                st.markdown(f"**{s.get('name', rid)}**")
                st.caption(f"状态：{s.get('status','')}  |  Round: {s.get('round','-')}")
                if s.get("task"):
                    st.markdown(f"任务：{s['task']}")
                if s.get("last"):
                    st.caption("最近输出：")
                    st.code(str(s.get("last"))[-600:], language="markdown")

        st.markdown("### Mission 看板")
        board = load_json(TEAM_PATHS.mission_board, default={"missions": {}})
        current = (board.get("missions") or {}).get("current") or {}
        with st.container(border=True):
            st.markdown(f"**当前任务**：{current.get('title','(none)')}")
            st.caption(f"状态：{current.get('status','(none)')}  | 开始：{current.get('started_at','-')}  | 结束：{current.get('finished_at','-')}")

    with left:
        st.markdown("### 时间线（完整日志）")
        timeline_box = st.container(height=520)

        def render_timeline() -> None:
            evts = read_jsonl(TEAM_PATHS.event_log, limit=TEAM_EVENTS_LIMIT)
            with timeline_box:
                for e in evts:
                    speaker_name = e.get("speaker_name") or e.get("speaker")
                    typ = e.get("type")
                    ts = e.get("ts")
                    rnd = e.get("round")
                    header = f"[{typ}] {speaker_name}  (round {rnd})\n{ts}" if rnd else f"[{typ}] {speaker_name}\n{ts}"
                    with st.chat_message("assistant"):
                        st.caption(header)
                        # full content
                        st.markdown(e.get("content", ""))

        render_timeline()

        prompt = st.chat_input("在这里给团队布置任务（由总指挥分解并调度）", key="team_prompt")
        if prompt:
            if not (gateway_token_present or api_key_present):
                st.warning("未检测到模型凭证（OPENCLAW_GATEWAY_TOKEN 或 OPENAI_API_KEY），无法执行。")
                return

            # Emit user instruction as an event.
            emit_event(TEAM_PATHS, speaker="user", speaker_name="你", type="USER", content=prompt, cb=None)

            # Live callback: refresh right panel state + timeline
            def on_event(evt: dict) -> None:
                # When events come in, re-render timeline and right side panels by rerun.
                # Streamlit doesn't support partial refresh reliably across columns without rerun.
                # We keep it simple: write a small placeholder log line and rely on the final rerun.
                pass

            with st.spinner("总指挥正在调度执行（最多10轮）..."):
                transcript_md = run_orchestrator(
                    user_goal=prompt.strip(),
                    roles=roles,
                    model=model,
                    allow_write=bool(allow_write),
                    allow_commit=bool(allow_commit),
                    max_rounds=10,
                    team_paths=TEAM_PATHS,
                    on_event=None,  # MVP: write to file; UI will refresh at end
                )
                # Persist orchestrator transcript as well (optional)
                save_orchestrator_state(prompt.strip(), transcript_md)

            st.rerun()


def render_single_role() -> None:
    role = roles[role_id]
    st.subheader(f"当前角色：{role['name']}")


if view_mode == "team_room":
    render_team_room()
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

history = read_history(role_id)

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
    append_history(role_id, user_msg)

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
            append_history(role_id, assistant_msg)
        else:
            client = get_client()

            # Compose messages: system + role history (without ts)
            messages = [{"role": "system", "content": role["system"]}]
            for m in read_history(role_id):
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
            append_history(role_id, assistant_msg)
