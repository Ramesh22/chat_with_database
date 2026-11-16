"""Streamlit chat UI for Chat with Your Database."""

from __future__ import annotations

import json
import os
from urllib import error, request

import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = 90


def ask_database(question: str) -> dict:
    body = json.dumps({"question": question}).encode("utf-8")
    http_request = request.Request(
        f"{API_URL}/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        if isinstance(detail, dict):
            detail = detail.get("message", json.dumps(detail))
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach the API at {API_URL}: {exc.reason}") from exc


def render_answer(answer: dict) -> None:
    count = answer.get("row_count", 0)
    st.markdown(f"**{count}** row(s) returned.")
    st.code(answer.get("sql", ""), language="sql")
    rows = answer.get("rows") or []
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if answer.get("truncated"):
        st.warning(f"Results truncated to the first {count} rows.")


st.set_page_config(page_title="Chat with Your Database", layout="wide")
st.title("Chat with Your Database")
st.caption(
    "Ask a question in plain English. The model writes a read-only SQL query "
    "against the demo `users` and `orders` tables."
)

if "history" not in st.session_state:
    st.session_state.history = []

for item in st.session_state.history:
    with st.chat_message(item["role"]):
        if item["role"] == "user":
            st.markdown(item["content"])
        elif item.get("answer"):
            render_answer(item["answer"])
        else:
            st.error(item["content"])

if prompt := st.chat_input("e.g. Which countries have the most orders?"):
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Generating and running SQL..."):
            try:
                answer = ask_database(prompt)
            except RuntimeError as exc:
                st.error(str(exc))
                st.session_state.history.append(
                    {"role": "assistant", "content": str(exc), "answer": None}
                )
                st.stop()
        render_answer(answer)

    st.session_state.history.append(
        {"role": "assistant", "content": "", "answer": answer}
    )
