import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from openai import DefaultHttpxClient, OpenAI

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
    st.header("团队角色")
    role_id = st.radio(
        "选择角色",
        role_ids,
        format_func=lambda rid: roles[rid]["name"],
        label_visibility="collapsed",
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


role = roles[role_id]
st.subheader(f"当前角色：{role['name']}")

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
    st.markdown("### 自动调度（B 模式：最多 10 轮 + 可自动写文件/提交）")
    st.caption(
        "说明：你只对总指挥说需求；总指挥会调度写代码/审查/集成。\n"
        "为了避免跑飞：最多 10 轮；写文件和 git commit 需要你勾选允许。"
    )

    allow_write = st.checkbox("允许自动写入仓库文件（仅限本 repo）", value=False)
    allow_commit = st.checkbox("允许自动 git add/commit（不会 push）", value=False)

    # Persist across reruns + restarts (size-limited)
    persisted = load_orchestrator_state()
    if "coordinator_goal" not in st.session_state:
        st.session_state.coordinator_goal = persisted.get("goal", "") or ""
    if "orchestrator_transcript" not in st.session_state:
        st.session_state.orchestrator_transcript = persisted.get("transcript", "") or ""

    user_goal = st.text_area(
        "本次目标（自然语言）",
        placeholder="例如：做一个命令行工具，把某目录下的 .txt 统计词频并输出 JSON，同时加 pytest。",
        height=120,
        key="coordinator_goal",
    )

    if st.button("开始自动执行（最多10轮）", type="primary", disabled=not bool(user_goal.strip())):
        if not (gateway_token_present or api_key_present):
            st.error("没有可用模型凭证，无法执行。请先配置 OPENCLAW_GATEWAY_TOKEN 或 OPENAI_API_KEY。")
        else:
            with st.spinner("自动调度执行中（可能需要几十秒~几分钟）..."):
                transcript: list[str] = []

                # Make a compact working memory.
                latest_snap = repo_snapshot()

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

                # A minimal loop state we keep in coordinator prompt.
                loop_state = {
                    "user_goal": user_goal.strip(),
                    "repo_snapshot": latest_snap,
                    "notes": [],
                }

                for r in range(1, 11):
                    coord_history = read_history("coordinator")[-20:]

                    coord_user = (
                        f"用户目标：\n{loop_state['user_goal']}\n\n"
                        f"当前仓库快照：\n{loop_state['repo_snapshot']}\n\n"
                        f"已知信息/上轮反馈：\n" + "\n".join(loop_state["notes"][-20:])
                    )

                    coord_reply = call_role(
                        "coordinator",
                        roles,
                        coord_history,
                        coordinator_instruction + "\n\n" + coord_user,
                        model=model,
                    )
                    transcript.append(f"#### Round {r} - 总指挥\n{coord_reply}")

                    plan = extract_json_block(coord_reply)
                    if not isinstance(plan, dict):
                        loop_state["notes"].append(f"Round {r}: 总指挥输出无法解析为 JSON，停止。")
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

                    role_feedback: dict[str, str] = {}
                    for call in calls:
                        h = read_history(call.to)[-30:]
                        reply = call_role(call.to, roles, h, call.task, model=model)
                        role_feedback[call.to] = reply
                        transcript.append(f"**→ {roles[call.to]['name']}**\n\n{reply}")

                        loop_state["notes"].append(f"Round {r} {call.to} reply:\n{reply}")

                        # Apply changes only if integrator returns a change bundle.
                        if call.to == "integrator" and allow_write:
                            bundle = extract_json_block(reply)
                            if isinstance(bundle, dict) and isinstance(bundle.get("files"), list):
                                changed = apply_file_writes(bundle.get("files", []))
                                loop_state["notes"].append(f"Applied file writes: {changed}")

                                # Run optional commands
                                cmd_logs: list[str] = []
                                for cmd in bundle.get("commands", []) or []:
                                    if not isinstance(cmd, str) or not cmd.strip():
                                        continue
                                    code, out = run_cmd(cmd.strip(), timeout=300)
                                    cmd_logs.append(f"$ {cmd}\n{out}\n(exit={code})")
                                if cmd_logs:
                                    loop_state["notes"].append("Command logs:\n" + "\n\n".join(cmd_logs))

                                # Refresh snapshot after change
                                loop_state["repo_snapshot"] = repo_snapshot()

                                # Commit if allowed
                                if allow_commit:
                                    msg = str(bundle.get("commit_message") or f"auto: round {r}").strip()
                                    run_cmd("git add -A", timeout=60)
                                    code, out = run_cmd(f"git commit -m {json.dumps(msg)}", timeout=60)
                                    loop_state["notes"].append(f"git commit: exit={code}\n{out}")
                                    loop_state["repo_snapshot"] = repo_snapshot()

                    if bool(plan.get("done")):
                        loop_state["notes"].append(f"Round {r}: done=true，停止。")
                        break

                st.session_state.orchestrator_transcript = "\n\n".join(transcript)
                # Persist (bounded size)
                save_orchestrator_state(st.session_state.coordinator_goal, st.session_state.orchestrator_transcript)
                st.markdown(st.session_state.orchestrator_transcript)

    if st.session_state.orchestrator_transcript:
        st.divider()
        st.markdown("### 上一次自动调度输出（已持久化，大小受限）")
        st.markdown(st.session_state.orchestrator_transcript)

    cols = st.columns(2)
    with cols[0]:
        if st.button("保存当前目标/输出", type="secondary"):
            save_orchestrator_state(st.session_state.coordinator_goal, st.session_state.orchestrator_transcript)
            st.toast("已保存（大小受限）")
    with cols[1]:
        if st.button("清空已保存的目标/输出", type="secondary"):
            save_orchestrator_state("", "")
            st.session_state.coordinator_goal = ""
            st.session_state.orchestrator_transcript = ""
            st.rerun()


if prompt:
    user_msg = {"role": "user", "content": prompt, "ts": datetime.utcnow().isoformat()}
    append_history(role_id, user_msg)

    with st.chat_message("user"):
        st.markdown(prompt)

    if gateway_token_present or api_key_present:
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
    else:
        with st.chat_message("assistant"):
            st.markdown("（未设置 OPENCLAW_GATEWAY_TOKEN 或 OPENAI_API_KEY，无法调用模型）")
