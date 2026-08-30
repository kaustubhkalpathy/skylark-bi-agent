"""Skylark Drones - monday.com Business Intelligence Agent (chat UI + dashboard).

Run locally:   streamlit run streamlit_app.py
Deployed on:   Streamlit Community Cloud (secrets set in the dashboard).
"""
from __future__ import annotations

import re

import altair as alt
import pandas as pd
import streamlit as st

from bi_agent import analytics
from bi_agent.config import settings

st.set_page_config(page_title="Skylark BI Agent", page_icon="🛩️", layout="wide")

# ---- light styling (explainable CSS: a gradient header band + card look) ----
st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1200px; }
      .skylark-hero {
        background: linear-gradient(90deg, #1E3A8A 0%, #2563EB 55%, #38BDF8 100%);
        color: #fff; padding: 22px 28px; border-radius: 14px; margin-bottom: 18px;
        box-shadow: 0 6px 20px rgba(37,99,235,0.25);
      }
      .skylark-hero h1 { margin: 0; font-size: 1.75rem; font-weight: 700; color: #FFFFFF !important; }
      .skylark-hero p  { margin: 6px 0 0; opacity: .95; font-size: .95rem; color: #F1F5F9 !important; }
      /* Metric cards */
      div[data-testid="stMetric"] {
        background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px;
        padding: 14px 16px; box-shadow: 0 1px 3px rgba(15,23,42,0.06);
      }
      div[data-testid="stMetricLabel"] p { color: #475569; font-weight: 600; }
      /* Suggestion buttons */
      div.stButton > button {
        border-radius: 999px; border: 1px solid #CBD5E1; background: #F8FAFC;
        font-size: .85rem;
      }
      div.stButton > button:hover { border-color: #2563EB; color: #2563EB; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- header ----
st.markdown(
    """
    <div class="skylark-hero">
      <h1>🛩️ Skylark Drones — Business Intelligence Agent</h1>
      <p>Founder-level answers over live monday.com data (Deals + Work Orders),
      cleaned on the fly with transparent data-quality caveats.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---- configuration guard ----
if not settings.is_configured:
    st.error("The agent is not fully configured. Missing: " + ", ".join(settings.missing_keys()))
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

deals = agent.dataset.deals
work_orders = agent.dataset.work_orders


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _inr_compact(raw: float | None) -> str:
    """Format a rupee amount using Indian Cr / L units."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "N/A"
    if abs(raw) >= 1e7:
        return f"₹{raw / 1e7:.1f} Cr"
    if abs(raw) >= 1e5:
        return f"₹{raw / 1e5:.1f} L"
    return f"₹{raw:,.0f}"


def _parse_inr_string(text: str | None) -> float | None:
    """analytics returns pre-formatted 'INR 1,234' strings; recover the number."""
    if not text or not isinstance(text, str):
        return None
    digits = re.sub(r"[^0-9.]", "", text)
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _collection_rate() -> str:
    if work_orders.empty:
        return "N/A"
    billed = work_orders.get("billed_incl_gst")
    collected = work_orders.get("collected_amount")
    if billed is None or collected is None:
        return "N/A"
    b = billed.sum(min_count=1)
    c = collected.sum(min_count=1)
    if not b or pd.isna(b) or b == 0:
        return "N/A"
    return f"{(c / b) * 100:.0f}%"


# --------------------------------------------------------------------------- #
#  Sidebar: data status + controls
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.subheader("Data status")
    try:
        st.write(f"**monday.com account:** {agent.account_name()}")
    except Exception:
        st.write("**monday.com account:** (unavailable)")
    meta = agent.dataset.meta
    st.write(f"**Deals:** {meta.get('deals_clean', 0)} rows (from {meta.get('deals_raw', 0)} raw)")
    st.write(
        f"**Work Orders:** {meta.get('work_orders_clean', 0)} rows "
        f"(from {meta.get('work_orders_raw', 0)} raw)"
    )

    if st.button("🔄 Refresh data from monday.com", use_container_width=True):
        with st.spinner("Refreshing…"):
            agent.refresh_data(force=True)
        st.rerun()

# --------------------------------------------------------------------------- #
#  Precompute dashboard data (direct analytics calls, no LLM)
# --------------------------------------------------------------------------- #
pipe = analytics.pipeline_summary(deals)
won_rev = analytics.revenue_by_sector(deals, status="Won")

won_total = None
sector_rows = []
if isinstance(won_rev, dict) and "by_sector" in won_rev:
    sector_rows = won_rev["by_sector"]
    won_total = sum((row.get("total_value_raw") or 0) for row in sector_rows)

open_raw = pipe.get("open_pipeline_value_raw") if isinstance(pipe, dict) else None
weighted_raw = _parse_inr_string(pipe.get("weighted_pipeline_value")) if isinstance(pipe, dict) else None

# Canonical order of pipeline stages (for the funnel).
STAGE_ORDER = [
    "Lead Generated", "Sales Qualified Leads", "Demo Done", "Feasibility",
    "Proposal/Commercials Sent", "Negotiations", "POC", "Work Order Received",
]

SUGGESTIONS = [
    "Give me a leadership update.",
    "Which sectors drive the most won revenue?",
    "What's our win rate by sector?",
    "Of the deals we won, how many are being executed and billed?",
    "How healthy are collections on work orders?",
]

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Hi! I'm your BI agent. Ask me about pipeline, revenue, sector "
                "performance, or operations — or tap a suggestion below."
            ),
        }
    ]

tab_overview, tab_ask = st.tabs(["📊 Overview", "💬 Ask the agent"])

# --------------------------------------------------------------------------- #
#  Overview tab: metric cards + charts
# --------------------------------------------------------------------------- #
with tab_overview:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open pipeline", _inr_compact(open_raw), help=f"{pipe.get('open_deals', 0)} open deals")
    c2.metric("Weighted pipeline", _inr_compact(weighted_raw), help="Probability-weighted open pipeline")
    c3.metric("Won revenue", _inr_compact(won_total), help=f"{pipe.get('won_deals', 0)} won deals")
    c4.metric("Collection rate", _collection_rate(), help="Collected / billed across work orders")

    st.write("")
    left, right = st.columns(2)

    # ---- Won revenue by sector (Altair bar with labels) ----
    with left:
        st.markdown("##### 💰 Won revenue by sector")
        if sector_rows:
            sdf = pd.DataFrame(
                [
                    {"Sector": r["sector"], "Won": r.get("total_value_raw") or 0}
                    for r in sector_rows
                    if (r.get("total_value_raw") or 0) > 0
                ]
            )
            if not sdf.empty:
                sdf["Won (Cr)"] = sdf["Won"] / 1e7
                chart = (
                    alt.Chart(sdf)
                    .mark_bar(cornerRadiusEnd=4, color="#2563EB")
                    .encode(
                        x=alt.X("Won (Cr):Q", title="Won value (₹ Cr)"),
                        y=alt.Y("Sector:N", sort="-x", title=None),
                        tooltip=[
                            "Sector",
                            alt.Tooltip("Won (Cr):Q", title="₹ Cr", format=".2f"),
                        ],
                    )
                    .properties(height=280)
                )
                labels = chart.mark_text(align="left", dx=3, color="#334155").encode(
                    text=alt.Text("Won (Cr):Q", format=".1f")
                )
                st.altair_chart(chart + labels, use_container_width=True)
        else:
            st.info("No won-revenue data available.")

    # ---- Open pipeline funnel by stage ----
    with right:
        st.markdown("##### 🫙 Open pipeline by stage")
        stage_counts = pipe.get("open_by_stage", {}) if isinstance(pipe, dict) else {}
        if stage_counts:
            def _stage_rank(name: str) -> int:
                for i, s in enumerate(STAGE_ORDER):
                    if s.lower() in name.lower():
                        return i
                return len(STAGE_ORDER)

            fdf = pd.DataFrame(
                [{"Stage": k, "Deals": v} for k, v in stage_counts.items()]
            )
            fdf["rank"] = fdf["Stage"].map(_stage_rank)
            fdf = fdf.sort_values("rank")
            funnel = (
                alt.Chart(fdf)
                .mark_bar(cornerRadiusEnd=4, color="#38BDF8")
                .encode(
                    x=alt.X("Deals:Q", title="Open deals"),
                    y=alt.Y("Stage:N", sort=fdf["Stage"].tolist(), title=None),
                    tooltip=["Stage", "Deals"],
                )
                .properties(height=280)
            )
            flabels = funnel.mark_text(align="left", dx=3, color="#334155").encode(
                text="Deals:Q"
            )
            st.altair_chart(funnel + flabels, use_container_width=True)
        else:
            st.info("No open-pipeline stage data available.")

    with st.expander("⚠️ Data-quality caveats", expanded=False):
        caveats = agent.dataset.caveats
        if caveats:
            for c in caveats:
                st.markdown(f"- {c}")
        else:
            st.write("No caveats detected.")

# --------------------------------------------------------------------------- #
#  Ask tab: conversational agent
# --------------------------------------------------------------------------- #
with tab_ask:
    st.markdown("**Try asking:**")
    cols = st.columns(len(SUGGESTIONS))
    clicked_suggestion = None
    for col, text in zip(cols, SUGGESTIONS):
        if col.button(text, use_container_width=True):
            clicked_suggestion = text

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    typed = st.chat_input("Ask a business question…")
    prompt = typed or clicked_suggestion

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                answer = agent.ask(prompt)
            st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
