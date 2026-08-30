"""Gemini tool-calling Business Intelligence agent.

We use google-generativeai's *automatic function calling*: we register plain
Python functions as tools; Gemini decides which to call, the SDK executes them,
feeds results back, and loops until it produces a natural-language answer.

The tools close over a freshly-loaded Dataset (cleaned monday.com data) so the
model always reasons over live, normalized data - never hardcoded CSVs.
"""
from __future__ import annotations

from typing import Any, Callable

import google.generativeai as genai

from . import analytics
from .config import settings
from .data_store import DataStore, Dataset

SYSTEM_INSTRUCTION = """
You are the Skylark Drones Business Intelligence Agent. You answer founder- and
executive-level questions about the company's sales pipeline (Deals) and project
execution (Work Orders), using ONLY the tools provided. The underlying data comes
live from monday.com and has been cleaned, but it is real-world messy.

Rules of engagement:
- ALWAYS get numbers from the tools. Never invent figures.
- When a question is ambiguous (e.g. "this quarter", an unclear sector name),
  ask ONE concise clarifying question OR state the assumption you are making and
  proceed. Prefer proceeding with a clearly-stated assumption when reasonable.
- If a tool reports data-quality caveats or excluded rows, surface them briefly
  so the user trusts the number.
- Give insight, not just raw figures: point out the notable driver, risk, or
  trend behind the numbers. Keep answers tight and executive-friendly.
- Currency is INR. Values are masked/relative, so frame them as indicative.
- If asked for a "leadership update", use the leadership_update tool and format
  the result as a short briefing with clear sections.
- Use list_dimensions when unsure which sector/status/stage names exist.
""".strip()


def _build_tools(ds: Dataset) -> list[Callable[..., Any]]:
    """Create tool functions bound to the current dataset."""

    def get_pipeline_summary(sector: str = "", quarter: str = "") -> dict:
        """Get open-pipeline health (open/won/lost counts, pipeline value, weighted
        value, stage breakdown). Optionally filter by sector and/or fiscal quarter
        like 'Q3 FY26'. Leave args empty for company-wide all-time."""
        return analytics.pipeline_summary(ds.deals, sector or None, quarter or None)

    def get_revenue_by_sector(status: str = "Won") -> dict:
        """Total deal value grouped by sector for a given deal status
        (Won, Open, Dead, On Hold). Defaults to Won."""
        return analytics.revenue_by_sector(ds.deals, status=status or "Won")

    def get_sector_performance() -> dict:
        """Won/lost/open counts and win-rate for every sector."""
        return analytics.sector_performance(ds.deals)

    def get_operational_metrics(sector: str = "") -> dict:
        """Work-order execution, billing and collection health. Optional sector filter."""
        return analytics.operational_metrics(ds.work_orders, sector or None)

    def get_leadership_update() -> dict:
        """Compact executive briefing combining pipeline, won revenue by sector,
        sector performance and operations/collections health."""
        return analytics.leadership_update(ds.deals, ds.work_orders)

    def get_won_deals_execution() -> dict:
        """Cross-board join. Of the deals we WON, how many are actually being
        executed and billed (i.e. have matching Work Orders)? Reports execution
        coverage plus execution/billing status of the matched work orders.
        Use for questions linking sales wins to delivery/billing."""
        return analytics.won_deals_execution(ds.deals, ds.work_orders)

    def list_dimensions() -> dict:
        """List the distinct sector/status/stage values present in the data so
        answers use real category names."""
        return analytics.list_dimensions(ds.deals, ds.work_orders)

    def get_data_quality_report() -> dict:
        """Report row counts and the data-quality caveats detected during cleaning."""
        return {"row_counts": ds.meta, "caveats": ds.caveats}

    return [
        get_pipeline_summary,
        get_revenue_by_sector,
        get_sector_performance,
        get_operational_metrics,
        get_leadership_update,
        get_won_deals_execution,
        list_dimensions,
        get_data_quality_report,
    ]


class BIAgent:
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured.")
        genai.configure(api_key=settings.gemini_api_key)
        self._store = DataStore()
        self._dataset: Dataset | None = None
        self._chat = None
        self._model_name = self._resolve_model(settings.gemini_model)

    @staticmethod
    def _resolve_model(preferred: str) -> str:
        """Pick a usable model.

        Google periodically retires model names. We try the configured model
        first, then a stable fallback list, then whatever the account can list
        that supports content generation. This keeps the app working even if a
        specific model has been deprecated.
        """
        candidates = [
            preferred,
            "gemini-3.6-flash",
            "gemini-flash-latest",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
        ]
        try:
            available = {
                m.name.split("/")[-1]
                for m in genai.list_models()
                if "generateContent" in getattr(m, "supported_generation_methods", [])
            }
        except Exception:
            # If listing fails (e.g. transient), just trust the preferred name.
            return preferred

        for name in candidates:
            if name in available:
                return name
        # Last resort: any available flash model, else any available model.
        flash = sorted(n for n in available if "flash" in n)
        if flash:
            return flash[-1]
        return next(iter(sorted(available)), preferred)

    # ---- data ----
    def refresh_data(self, *, force: bool = False) -> Dataset:
        self._dataset = self._store.load(force_refresh=force)
        # Rebuild model+chat so tools bind to the fresh dataset.
        self._start_chat()
        return self._dataset

    @property
    def dataset(self) -> Dataset:
        if self._dataset is None:
            self.refresh_data()
        return self._dataset  # type: ignore[return-value]

    def account_name(self) -> str:
        return self._store.account_name()

    # ---- chat ----
    def _start_chat(self) -> None:
        # refresh_data() always sets self._dataset before calling this.
        assert self._dataset is not None, "_start_chat called before data was loaded"
        tools = _build_tools(self._dataset)
        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=SYSTEM_INSTRUCTION,
            tools=tools,
        )
        self._chat = model.start_chat(enable_automatic_function_calling=True)

    def ask(self, message: str) -> str:
        if self._chat is None:
            self.refresh_data()
        try:
            resp = self._chat.send_message(message)
            return (resp.text or "").strip() or "(No answer produced.)"
        except Exception as exc:  # surface a friendly error to the UI
            return (
                "I hit an error answering that: "
                f"{type(exc).__name__}: {exc}. "
                "You can try rephrasing, or click 'Refresh data' if this persists."
            )
