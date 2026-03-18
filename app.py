import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from openai import OpenAI

ROOT = Path(__file__).parent
HISTORY_DIR = ROOT / "history"
HISTORY_DIR.mkdir(exist_ok=True)


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


def get_client() -> OpenAI:
    # OpenAI python SDK reads OPENAI_API_KEY from env by default.
    return OpenAI()


st.set_page_config(page_title="AI 开发团队管理台", layout="wide")
st.title("AI 开发团队管理台")

roles = load_roles()
role_ids = list(roles.keys())

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
    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    st.write("OPENAI_API_KEY:", "✅ 已设置" if api_key_present else "❌ 未设置")
    model = st.text_input("模型名", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    st.caption("如需更换模型，可设置环境变量 OPENAI_MODEL 或在这里改。")

    st.divider()
    if st.button("清空当前角色对话记录", type="secondary"):
        clear_history(role_id)
        st.rerun()


role = roles[role_id]
st.subheader(f"当前角色：{role['name']}")

if not api_key_present:
    st.warning(
        "还没有设置 OPENAI_API_KEY。请在虚拟机上先执行：\n\n"
        "export OPENAI_API_KEY='你的key'\n\n"
        "然后重新启动本页面。",
        icon="⚠️",
    )

history = read_history(role_id)

# Render history
for m in history:
    with st.chat_message(m.get("role", "assistant")):
        st.markdown(m.get("content", ""))

prompt = st.chat_input("给这个角色发消息（例如：给你一个任务……）")

if prompt:
    user_msg = {"role": "user", "content": prompt, "ts": datetime.utcnow().isoformat()}
    append_history(role_id, user_msg)

    with st.chat_message("user"):
        st.markdown(prompt)

    if api_key_present:
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
            st.markdown("（未设置 OPENAI_API_KEY，无法调用模型）")
