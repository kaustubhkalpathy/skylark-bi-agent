"""Skylark Drones - monday.com Business Intelligence Agent (chat UI).

Run locally:   streamlit run streamlit_app.py
Deployed on:   Streamlit Community Cloud (secrets set in the dashboard).
"""
from __future__ import annotations

import streamlit as st

from bi_agent.config import settings

st.set_page_config(page_title="Skylark BI Agent", page_icon="🛩️", layout="centered")

st.title("🛩️ Skylark Drones — Business Intelligence Agent")
st.caption(
    "Ask founder-level questions about the sales pipeline (Deals) and project "
    "execution (Work Orders). Data is read live from monday.com and cleaned on the fly."
)

# ---- configuration guard ----
if not settings.is_configured:
    st.error(
        "The agent is not fully configured. Missing: "
        + ", ".join(settings.missing_keys())
    )
    st.info(
        "Set these as environment variables (local `.env`) or as Streamlit "
        "secrets (deployed). See `.env.example`."
    )
    st.stop()


@st.cache_resource(show_spinner="Connecting to monday.com and loading data…")
def get_agent():
    from bi_agent.agent import BIAgent

    agent = BIAgent()
    agent.refresh_data()
    return agent


try:
    agent = get_agent()
except Exception as exc:  # noqa: BLE001
    st.error(f"Failed to start the agent: {type(exc).__name__}: {exc}")
    st.stop()

# ---- sidebar: data status + controls ----
with st.sidebar:
    st.subheader("Data status")
    try:
        st.write(f"**monday.com account:** {agent.account_name()}")
    except Exception:
        st.write("**monday.com account:** (unavailable)")
    meta = agent.dataset.meta
    st.write(
        f"**Deals:** {meta.get('deals_clean', 0)} rows "
        f"(from {meta.get('deals_raw', 0)} raw)"
    )
    st.write(
        f"**Work Orders:** {meta.get('work_orders_clean', 0)} rows "
        f"(from {meta.get('work_orders_raw', 0)} raw)"
    )

    if st.button("🔄 Refresh data from monday.com"):
        with st.spinner("Refreshing…"):
            agent.refresh_data(force=True)
        st.rerun()

    with st.expander("Data-quality caveats"):
        caveats = agent.dataset.caveats
        if caveats:
            for c in caveats:
                st.markdown(f"- {c}")
        else:
            st.write("No caveats detected.")

    st.divider()
    st.markdown("**Try asking:**")
    st.markdown(
        "- How's our pipeline looking for the energy sector this quarter?\n"
        "- Which sectors are driving the most won revenue?\n"
        "- What's our win rate by sector?\n"
        "- How healthy are collections on work orders?\n"
        "- Give me a leadership update."
    )

# ---- chat state ----
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Hi! I'm your BI agent. Ask me about pipeline, revenue, sector "
                "performance, or operations — or just say **'Give me a leadership update.'**"
            ),
        }
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask a business question…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            answer = agent.ask(prompt)
        st.markdown(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
