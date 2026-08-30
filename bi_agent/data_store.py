"""Bridges monday.com (raw) -> normalized DataFrames, with caching.

The agent's tools call into this module. It hides all monday/pandas details
behind a small, stable surface.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import settings
from .monday_client import MondayClient, fetch_board_cached, clear_cache
from .normalize import CleanResult, clean_deals, clean_work_orders


@dataclass
class Dataset:
    deals: pd.DataFrame
    work_orders: pd.DataFrame
    caveats: list[str]
    meta: dict[str, int]


class DataStore:
    def __init__(self) -> None:
        if not settings.monday_api_token:
            raise RuntimeError("MONDAY_API_TOKEN not configured.")
        self.client = MondayClient(settings.monday_api_token)

    def account_name(self) -> str:
        return self.client.verify_connection()

    def load(self, *, force_refresh: bool = False) -> Dataset:
        if force_refresh:
            clear_cache()

        deals_board = fetch_board_cached(
            self.client, settings.deals_board_id, settings.cache_ttl
        )
        wo_board = fetch_board_cached(
            self.client, settings.work_orders_board_id, settings.cache_ttl
        )

        deals_clean: CleanResult = clean_deals(deals_board.to_records())
        wo_clean: CleanResult = clean_work_orders(wo_board.to_records())

        caveats = (
            [f"Deals: {c}" for c in deals_clean.caveats]
            + [f"Work Orders: {c}" for c in wo_clean.caveats]
        )
        meta = {
            "deals_raw": deals_clean.row_count_raw,
            "deals_clean": deals_clean.row_count_clean,
            "work_orders_raw": wo_clean.row_count_raw,
            "work_orders_clean": wo_clean.row_count_clean,
        }
        return Dataset(
            deals=deals_clean.df,
            work_orders=wo_clean.df,
            caveats=caveats,
            meta=meta,
        )
