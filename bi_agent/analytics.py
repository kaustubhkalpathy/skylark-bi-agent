"""Business-intelligence computations over the cleaned DataFrames.

These are pure functions returning JSON-serializable dicts, so they can be
exposed directly as Gemini tool results. Every function is defensive: missing
columns or empty frames yield an explanatory message rather than an exception.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# Deal statuses considered "open pipeline" vs closed.
OPEN_STATUSES = {"Open", "On Hold"}
WON_STATUSES = {"Won"}
LOST_STATUSES = {"Dead"}


def _fmt_inr(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"INR {value:,.0f}"


def _quarter_bounds(quarter: str | None, fy_start_month: int = 4):
    """Return (start, end) Timestamps for a fiscal quarter label like 'Q3 FY26'.

    Skylark appears to run an Apr-Mar fiscal year (Indian standard), so we default
    to that. Returns (None, None) if not parseable.
    """
    if not quarter:
        return None, None
    import re

    m = re.search(r"q([1-4]).*?(\d{2,4})", quarter.lower())
    if not m:
        return None, None
    q = int(m.group(1))
    yr = int(m.group(2))
    if yr < 100:
        yr += 2000
    # FY label names the year the fiscal year ENDS. For an Apr-Mar FY,
    # FY26 spans Apr-2025 .. Mar-2026, so the FY starts in calendar year yr-1.
    # Q1 = months 0-2 after fy_start_month, Q2 = 3-5, etc.
    fy_first_year = yr - 1
    abs_month = fy_start_month + (q - 1) * 3  # 1-based month index from FY start
    s_year = fy_first_year + (abs_month - 1) // 12
    s_month = (abs_month - 1) % 12 + 1
    start = pd.Timestamp(year=s_year, month=s_month, day=1)
    end = start + pd.DateOffset(months=3)
    return start, end


def pipeline_summary(deals: pd.DataFrame, sector: str | None = None,
                     quarter: str | None = None) -> dict[str, Any]:
    """Open-pipeline health, optionally filtered by sector and/or quarter."""
    if deals.empty or "status" not in deals.columns:
        return {"error": "No deals data available."}

    df = deals.copy()
    notes: list[str] = []

    if sector:
        df = df[df["sector"].str.lower() == sector.lower()]
        if df.empty:
            return {"message": f"No deals found for sector '{sector}'.",
                    "available_sectors": sorted(deals["sector"].dropna().unique().tolist())}

    if quarter:
        start, end = _quarter_bounds(quarter)
        if start is not None and "tentative_close_date" in df.columns:
            mask = df["tentative_close_date"].between(start, end)
            filtered = df[mask]
            notes.append(
                f"Filtered by expected close date in {quarter} "
                f"({start.date()} to {(end - pd.Timedelta(days=1)).date()}); "
                f"{int(mask.sum())} deal(s) matched. Deals without a close date are excluded."
            )
            df = filtered

    open_df = df[df["status"].isin(OPEN_STATUSES)]
    won_df = df[df["status"].isin(WON_STATUSES)]
    lost_df = df[df["status"].isin(LOST_STATUSES)]

    open_value = open_df["deal_value"].sum(min_count=1) if "deal_value" in open_df else None
    won_value = won_df["deal_value"].sum(min_count=1) if "deal_value" in won_df else None

    # Weighted pipeline using probability if present.
    weighted = None
    if "probability" in open_df.columns and "deal_value" in open_df.columns:
        weights = {"high": 0.75, "medium": 0.5, "low": 0.25}
        w = open_df.apply(
            lambda r: (r["deal_value"] or 0) * weights.get(str(r["probability"]).lower(), 0.0)
            if pd.notna(r["deal_value"]) else 0,
            axis=1,
        )
        weighted = float(w.sum())

    result = {
        "scope": {"sector": sector or "all", "quarter": quarter or "all-time"},
        "open_deals": int(len(open_df)),
        "open_pipeline_value": _fmt_inr(open_value),
        "open_pipeline_value_raw": None if open_value is None else float(open_value),
        "weighted_pipeline_value": _fmt_inr(weighted) if weighted is not None else "N/A",
        "won_deals": int(len(won_df)),
        "won_value": _fmt_inr(won_value),
        "lost_deals": int(len(lost_df)),
        "notes": notes,
    }
    # Stage breakdown for open deals.
    if "stage_label" in open_df.columns and not open_df.empty:
        stage_counts = open_df["stage_label"].value_counts().to_dict()
        result["open_by_stage"] = {k: int(v) for k, v in stage_counts.items()}
    return result


def revenue_by_sector(deals: pd.DataFrame, status: str = "Won") -> dict[str, Any]:
    """Sum deal value by sector for a given status (default Won)."""
    if deals.empty or "sector" not in deals.columns:
        return {"error": "No deals data available."}
    df = deals
    if status and "status" in df.columns:
        df = df[df["status"].str.lower() == status.lower()]
    if df.empty:
        return {"message": f"No deals with status '{status}'."}
    if "deal_value" not in df.columns:
        return {"error": "Deal value column not available."}
    grouped = (
        df.groupby("sector")["deal_value"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    )
    out = []
    for sector, row in grouped.iterrows():
        out.append(
            {
                "sector": sector,
                "total_value": _fmt_inr(row["sum"]),
                "total_value_raw": None if pd.isna(row["sum"]) else float(row["sum"]),
                "deal_count": int(row["count"]),
            }
        )
    return {"status_filter": status, "by_sector": out}


def sector_performance(deals: pd.DataFrame) -> dict[str, Any]:
    """Win/loss/open counts and win-rate per sector."""
    if deals.empty or "sector" not in deals.columns or "status" not in deals.columns:
        return {"error": "No deals data available."}
    rows = []
    for sector, g in deals.groupby("sector"):
        won = int(g["status"].isin(WON_STATUSES).sum())
        lost = int(g["status"].isin(LOST_STATUSES).sum())
        openc = int(g["status"].isin(OPEN_STATUSES).sum())
        decided = won + lost
        win_rate = f"{(won / decided * 100):.0f}%" if decided else "N/A"
        rows.append(
            {
                "sector": sector,
                "won": won,
                "lost": lost,
                "open": openc,
                "win_rate": win_rate,
            }
        )
    rows.sort(key=lambda r: r["won"], reverse=True)
    return {"by_sector": rows}


def operational_metrics(work_orders: pd.DataFrame, sector: str | None = None) -> dict[str, Any]:
    """Execution + billing + collection health from work orders."""
    if work_orders.empty:
        return {"error": "No work-order data available."}
    df = work_orders.copy()
    if sector and "sector" in df.columns:
        df = df[df["sector"].str.lower() == sector.lower()]
        if df.empty:
            return {"message": f"No work orders for sector '{sector}'."}

    out: dict[str, Any] = {"scope": {"sector": sector or "all"}, "total_work_orders": int(len(df))}

    if "execution_status" in df.columns:
        out["by_execution_status"] = {
            k: int(v) for k, v in df["execution_status"].fillna("Unknown").value_counts().items()
        }
    if "billing_status" in df.columns:
        out["by_billing_status"] = {
            k: int(v) for k, v in df["billing_status"].fillna("Unknown").value_counts().items()
        }
    if "collected_amount" in df.columns:
        collected = df["collected_amount"].sum(min_count=1)
        out["total_collected"] = _fmt_inr(collected)
    if "amount_receivable" in df.columns:
        receivable = df["amount_receivable"].sum(min_count=1)
        out["total_receivable"] = _fmt_inr(receivable)
    if "billed_incl_gst" in df.columns:
        billed = df["billed_incl_gst"].sum(min_count=1)
        out["total_billed"] = _fmt_inr(billed)
    return out


def leadership_update(deals: pd.DataFrame, work_orders: pd.DataFrame) -> dict[str, Any]:
    """A compact 'leadership update' snapshot combining both boards.

    Interpretation (documented in Decision Log): a leadership update is a concise
    executive briefing - top-line pipeline, wins, sector highlights, and
    operational/collections health - suitable for pasting into a weekly review.
    """
    snapshot: dict[str, Any] = {}
    snapshot["pipeline"] = pipeline_summary(deals)
    snapshot["revenue_won_by_sector"] = revenue_by_sector(deals, status="Won")
    snapshot["sector_performance"] = sector_performance(deals)
    snapshot["operations"] = operational_metrics(work_orders)
    return snapshot


def list_dimensions(deals: pd.DataFrame, work_orders: pd.DataFrame) -> dict[str, Any]:
    """Expose the distinct values the agent can filter on (helps it answer accurately)."""
    dims: dict[str, Any] = {}
    if not deals.empty:
        if "sector" in deals.columns:
            dims["deal_sectors"] = sorted(deals["sector"].dropna().unique().tolist())
        if "status" in deals.columns:
            dims["deal_statuses"] = sorted(deals["status"].dropna().unique().tolist())
        if "stage_label" in deals.columns:
            dims["deal_stages"] = sorted(deals["stage_label"].dropna().unique().tolist())
    if not work_orders.empty and "sector" in work_orders.columns:
        dims["work_order_sectors"] = sorted(work_orders["sector"].dropna().unique().tolist())
    return dims
