"""Central configuration. Reads from environment (.env locally, host secrets in prod).

We support two ways of providing secrets so the same code runs locally and on
Streamlit Community Cloud:
  1. Environment variables / a local .env file (python-dotenv).
  2. st.secrets when running inside Streamlit (checked lazily to avoid a hard
     dependency on a Streamlit runtime for the backend modules).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # loads .env if present; no-op in production hosts that inject env vars


def _get(key: str, default: str | None = None) -> str | None:
    """Fetch a setting from env first, then Streamlit secrets if available."""
    val = os.environ.get(key)
    if val:
        return val
    try:
        import streamlit as st  # type: ignore

        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        # Streamlit not installed / not running / no secrets file: fine.
        pass
    return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    monday_api_token: str | None
    deals_board_id: str | None
    work_orders_board_id: str | None
    cache_ttl: int

    @property
    def is_configured(self) -> bool:
        return bool(
            self.gemini_api_key
            and self.monday_api_token
            and self.deals_board_id
            and self.work_orders_board_id
        )

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.monday_api_token:
            missing.append("MONDAY_API_TOKEN")
        if not self.deals_board_id:
            missing.append("MONDAY_DEALS_BOARD_ID")
        if not self.work_orders_board_id:
            missing.append("MONDAY_WORK_ORDERS_BOARD_ID")
        return missing


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash",
        monday_api_token=_get("MONDAY_API_TOKEN"),
        deals_board_id=_get("MONDAY_DEALS_BOARD_ID"),
        work_orders_board_id=_get("MONDAY_WORK_ORDERS_BOARD_ID"),
        cache_ttl=int(_get("DATA_CACHE_TTL", "300") or "300"),
    )


settings = load_settings()
