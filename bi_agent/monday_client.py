"""Read-only monday.com GraphQL API client.

Fetches board metadata (columns) and all items with cursor-based pagination.
The agent NEVER hardcodes CSV data - it always queries monday.com dynamically,
as required by the assignment.

monday.com API reference: https://developer.monday.com/api-reference/
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

API_URL = "https://api.monday.com/v2"
API_VERSION = "2024-10"  # pin a stable API version
PAGE_LIMIT = 100
REQUEST_TIMEOUT = 30


class MondayError(RuntimeError):
    """Raised when the monday.com API returns an error or is unreachable."""


@dataclass
class BoardData:
    """Raw (uncleaned) representation of a monday.com board."""

    board_id: str
    name: str
    columns: list[dict[str, Any]]  # [{id, title, type}]
    items: list[dict[str, Any]] = field(default_factory=list)
    # each item: {"id", "name", "column_values": [{"id","title","text","value","type"}]}

    def to_records(self) -> list[dict[str, Any]]:
        """Flatten items into row dicts keyed by column *title* (human friendly).

        Uses the column's displayed ``text`` value, which is what a human sees in
        monday. This keeps the downstream cleaning layer decoupled from monday's
        internal column-id scheme.
        """
        records = []
        for item in self.items:
            row: dict[str, Any] = {"item_id": item.get("id"), "item_name": item.get("name")}
            for cv in item.get("column_values", []):
                title = cv.get("title") or cv.get("id")
                row[title] = cv.get("text")
            records.append(row)
        return records


class MondayClient:
    def __init__(self, api_token: str, *, session: requests.Session | None = None):
        if not api_token:
            raise MondayError("monday.com API token is missing.")
        self._token = api_token
        self._session = session or requests.Session()

    # ---- low level ----
    def _query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
            "API-Version": API_VERSION,
        }
        try:
            resp = self._session.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MondayError(f"Network error contacting monday.com: {exc}") from exc

        if resp.status_code == 401:
            raise MondayError("Authentication failed (401). Check MONDAY_API_TOKEN.")
        if resp.status_code == 429:
            raise MondayError("Rate limited by monday.com (429). Try again shortly.")
        if resp.status_code >= 400:
            raise MondayError(f"monday.com HTTP {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        if "errors" in payload and payload["errors"]:
            msg = "; ".join(e.get("message", str(e)) for e in payload["errors"])
            raise MondayError(f"monday.com GraphQL error: {msg}")
        return payload.get("data", {})

    # ---- public ----
    def verify_connection(self) -> str:
        """Return the authenticated account name; raises MondayError on failure."""
        data = self._query("query { me { name email } }")
        me = data.get("me") or {}
        return me.get("name") or me.get("email") or "unknown"

    def fetch_board(self, board_id: str) -> BoardData:
        """Fetch a board's columns and ALL items (paginated)."""
        meta_query = """
        query ($ids: [ID!]) {
          boards (ids: $ids) {
            id
            name
            columns { id title type }
          }
        }
        """
        meta = self._query(meta_query, {"ids": [str(board_id)]})
        boards = meta.get("boards") or []
        if not boards:
            raise MondayError(
                f"Board {board_id} not found or not accessible with this token."
            )
        board = boards[0]

        col_titles = {c["id"]: c["title"] for c in board.get("columns", [])}
        item_fields = """
          items {
            id
            name
            column_values { id text type ... on MirrorValue { display_value } }
          }
        """

        # First page: nested items_page inside the board.
        first_query = f"""
        query ($ids: [ID!], $limit: Int!) {{
          boards (ids: $ids) {{
            items_page (limit: $limit) {{
              cursor
              {item_fields}
            }}
          }}
        }}
        """
        # Subsequent pages: top-level next_items_page using the cursor.
        next_query = f"""
        query ($limit: Int!, $cursor: String!) {{
          next_items_page (limit: $limit, cursor: $cursor) {{
            cursor
            {item_fields}
          }}
        }}
        """

        items: list[dict[str, Any]] = []

        def _absorb(page: dict[str, Any]) -> str | None:
            for it in page.get("items", []):
                cvs = []
                for cv in it.get("column_values", []):
                    text = cv.get("text")
                    if not text and cv.get("display_value"):
                        text = cv.get("display_value")
                    cvs.append(
                        {
                            "id": cv.get("id"),
                            "title": col_titles.get(cv.get("id"), cv.get("id")),
                            "text": text,
                            "type": cv.get("type"),
                        }
                    )
                items.append({"id": it.get("id"), "name": it.get("name"), "column_values": cvs})
            return page.get("cursor")

        data = self._query(first_query, {"ids": [str(board_id)], "limit": PAGE_LIMIT})
        page = (data.get("boards") or [{}])[0].get("items_page") or {}
        cursor = _absorb(page)

        while cursor:
            data = self._query(next_query, {"limit": PAGE_LIMIT, "cursor": cursor})
            page = data.get("next_items_page") or {}
            cursor = _absorb(page)

        return BoardData(
            board_id=str(board.get("id")),
            name=board.get("name", f"board_{board_id}"),
            columns=board.get("columns", []),
            items=items,
        )


# ---- simple in-process TTL cache to avoid hammering the API on every chat turn ----
_CACHE: dict[str, tuple[float, BoardData]] = {}


def fetch_board_cached(client: MondayClient, board_id: str, ttl: int) -> BoardData:
    now = time.time()
    hit = _CACHE.get(board_id)
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    data = client.fetch_board(board_id)
    _CACHE[board_id] = (now, data)
    return data


def clear_cache() -> None:
    _CACHE.clear()
