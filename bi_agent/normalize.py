"""Data resilience / normalization layer.

The source data is real-world messy. This module turns raw monday.com board
records into clean pandas DataFrames and, importantly, produces a list of
human-readable *data-quality caveats* so the agent can be transparent with the
user about what was cleaned or is missing.

Design goal: NEVER throw away a row silently. We normalize what we can, flag the
rest, and keep the original text available for auditing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from dateutil import parser as date_parser

# ----- canonical column mappings (fuzzy: lowercased, punctuation-stripped) -----
# We map many possible source titles to a single canonical name so the agent can
# rely on stable field names regardless of monday column titling.

DEALS_FIELD_MAP = {
    "deal name": "deal_name",
    "owner code": "owner",
    "client code": "client",
    "deal status": "status",
    "close date a": "close_date",
    "closure probability": "probability",
    "masked deal value": "deal_value",
    "tentative close date": "tentative_close_date",
    "deal stage": "stage",
    "product deal": "product",
    "sector service": "sector",
    "created date": "created_date",
}

WORK_ORDER_FIELD_MAP = {
    "deal name masked": "deal_name",
    "customer name code": "customer",
    "serial": "serial",
    "nature of work": "nature_of_work",
    "last executed month of recurring project": "last_executed_month",
    "execution status": "execution_status",
    "data delivery date": "data_delivery_date",
    "date of po loi": "po_date",
    "document type": "document_type",
    "probable start date": "start_date",
    "probable end date": "end_date",
    "bd kam personnel code": "owner",
    "sector": "sector",
    "type of work": "type_of_work",
    "last invoice date": "last_invoice_date",
    "latest invoice no": "invoice_no",
    "amount in rupees excl of gst masked": "amount_excl_gst",
    "amount in rupees incl of gst masked": "amount_incl_gst",
    "billed value in rupees excl of gst masked": "billed_excl_gst",
    "billed value in rupees incl of gst masked": "billed_incl_gst",
    "collected amount in rupees incl of gst masked": "collected_amount",
    "amount to be billed in rs excl of gst masked": "to_be_billed_excl_gst",
    "amount to be billed in rs incl of gst masked": "to_be_billed_incl_gst",
    "amount receivable masked": "amount_receivable",
    "invoice status": "invoice_status",
    "wo status billed": "wo_status",
    "collection status": "collection_status",
    "collection date": "collection_date",
    "billing status": "billing_status",
}

# Values that should be treated as "missing".
NULL_TOKENS = {"", "nan", "none", "null", "n/a", "na", "-", "none none"}


@dataclass
class CleanResult:
    df: pd.DataFrame
    caveats: list[str] = field(default_factory=list)
    row_count_raw: int = 0
    row_count_clean: int = 0


def _slug(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace - for fuzzy matching."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _map_columns(df: pd.DataFrame, field_map: dict[str, str]) -> pd.DataFrame:
    rename = {}
    for col in df.columns:
        canon = field_map.get(_slug(str(col)))
        if canon:
            rename[col] = canon
    return df.rename(columns=rename)


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip().lower() in NULL_TOKENS


def _clean_str(value: Any) -> str | None:
    if _is_null(value):
        return None
    return str(value).strip()


def _clean_number(value: Any) -> float | None:
    """Parse messy currency/number text: strip commas, symbols, spaces."""
    if _is_null(value):
        return None
    s = str(value).strip()
    s = re.sub(r"[^0-9.\-]", "", s)  # drop currency symbols, commas, etc.
    if s in {"", "-", ".", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean_date(value: Any):
    """Parse a wide variety of date formats into a pandas Timestamp (or None)."""
    if _is_null(value):
        return None
    s = str(value).strip()
    # monday often returns ISO like 2025-09-27 or with a time component.
    try:
        return pd.Timestamp(date_parser.parse(s, dayfirst=False))
    except (ValueError, OverflowError, TypeError):
        return None


# Month tokens appear as "Dec", "November", "June" etc. Normalize to a number.
_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def _clean_month(value: Any) -> str | None:
    s = _clean_str(value)
    if not s:
        return None
    num = _MONTHS.get(s.lower()) or _MONTHS.get(s.lower()[:3])
    if num:
        return f"{num:02d}"
    return s  # leave as-is if unrecognized; caveat handled elsewhere


def _title_case(value: Any) -> str | None:
    # Guard against non-strings (e.g. float NaN, which is truthy) that pandas
    # may hand us. Coerce to a clean string first.
    cleaned = _clean_str(value)
    if not cleaned:
        return cleaned
    return " ".join(w.capitalize() for w in cleaned.split())


def _drop_repeated_headers(df: pd.DataFrame, key_col: str, header_tokens: set[str]) -> tuple[pd.DataFrame, int]:
    """The Deals sheet embeds header rows mid-data (e.g. a row where status == 'Deal Status').

    Detect and drop rows that are clearly repeated header lines.
    """
    if key_col not in df.columns:
        return df, 0
    mask = df[key_col].astype(str).str.strip().str.lower().isin(header_tokens)
    dropped = int(mask.sum())
    return df[~mask].copy(), dropped


# --------------------------------------------------------------------------- #
#  DEALS
# --------------------------------------------------------------------------- #
def clean_deals(records: list[dict[str, Any]]) -> CleanResult:
    caveats: list[str] = []
    raw = pd.DataFrame(records)
    raw_count = len(raw)
    if raw.empty:
        return CleanResult(df=raw, caveats=["Deals board returned no rows."], row_count_raw=0)

    df = _map_columns(raw, DEALS_FIELD_MAP)

    # Drop monday helper columns we don't need.
    df = df.drop(columns=[c for c in ("item_id", "item_name") if c in df.columns], errors="ignore")

    # Remove embedded header rows (status literally equals a header label).
    df, hdr = _drop_repeated_headers(
        df, "status", {"deal status"}
    )
    if hdr:
        caveats.append(f"Removed {hdr} embedded header row(s) found inside the Deals data.")

    # Clean text fields.
    for col in ("deal_name", "owner", "client", "status", "stage", "product", "sector", "probability"):
        if col in df.columns:
            df[col] = df[col].map(_clean_str)

    # Normalize status casing/spelling.
    if "status" in df.columns:
        df["status"] = df["status"].map(lambda v: _title_case(v) if v else v)

    # Stage: values look like "E. Proposal/Commercials Sent" -> keep letter + label,
    # but also expose a clean stage_label without the leading "X." prefix.
    if "stage" in df.columns:
        df["stage_label"] = df["stage"].map(
            lambda v: re.sub(r"^[A-Za-z]\.\s*", "", v) if v else v
        )

    # Numbers and dates.
    if "deal_value" in df.columns:
        df["deal_value"] = df["deal_value"].map(_clean_number)
    for dcol in ("close_date", "tentative_close_date", "created_date"):
        if dcol in df.columns:
            df[dcol] = df[dcol].map(_clean_date)

    # Standardize sector spelling.
    if "sector" in df.columns:
        df["sector"] = df["sector"].map(_normalize_sector)

    # Deduplicate exact duplicate rows (the data has many).
    before = len(df)
    df = df.drop_duplicates()
    dupes = before - len(df)
    if dupes:
        caveats.append(f"Removed {dupes} exact duplicate deal row(s).")

    # Data-quality caveats.
    if "deal_value" in df.columns:
        missing_val = int(df["deal_value"].isna().sum())
        if missing_val:
            caveats.append(
                f"{missing_val} of {len(df)} deals have no deal value; revenue/pipeline "
                "figures exclude those rows."
            )
    if "sector" in df.columns:
        missing_sector = int(df["sector"].isna().sum())
        if missing_sector:
            caveats.append(f"{missing_sector} deal(s) have no sector and are grouped as 'Unknown'.")
        df["sector"] = df["sector"].fillna("Unknown")

    df = df.reset_index(drop=True)
    return CleanResult(df=df, caveats=caveats, row_count_raw=raw_count, row_count_clean=len(df))


# --------------------------------------------------------------------------- #
#  WORK ORDERS
# --------------------------------------------------------------------------- #
def clean_work_orders(records: list[dict[str, Any]]) -> CleanResult:
    caveats: list[str] = []
    raw = pd.DataFrame(records)
    raw_count = len(raw)
    if raw.empty:
        return CleanResult(df=raw, caveats=["Work Orders board returned no rows."], row_count_raw=0)

    df = _map_columns(raw, WORK_ORDER_FIELD_MAP)
    df = df.drop(columns=[c for c in ("item_id", "item_name") if c in df.columns], errors="ignore")

    text_cols = (
        "deal_name", "customer", "serial", "nature_of_work", "execution_status",
        "document_type", "owner", "sector", "type_of_work", "invoice_no",
        "invoice_status", "wo_status", "collection_status", "billing_status",
    )
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].map(_clean_str)

    # Normalize obvious typos / casing in status fields.
    if "billing_status" in df.columns:
        df["billing_status"] = df["billing_status"].map(_normalize_billing_status)
    if "execution_status" in df.columns:
        df["execution_status"] = df["execution_status"].map(lambda v: _title_case(v) if v else v)

    # Sector normalization.
    if "sector" in df.columns:
        df["sector"] = df["sector"].map(_normalize_sector)

    # Recurring-project month normalization.
    if "last_executed_month" in df.columns:
        df["last_executed_month"] = df["last_executed_month"].map(_clean_month)

    # Numeric (masked) amount columns.
    amount_cols = (
        "amount_excl_gst", "amount_incl_gst", "billed_excl_gst", "billed_incl_gst",
        "collected_amount", "to_be_billed_excl_gst", "to_be_billed_incl_gst",
        "amount_receivable",
    )
    for col in amount_cols:
        if col in df.columns:
            df[col] = df[col].map(_clean_number)

    # Date columns.
    date_cols = (
        "data_delivery_date", "po_date", "start_date", "end_date",
        "last_invoice_date", "collection_date",
    )
    for col in date_cols:
        if col in df.columns:
            df[col] = df[col].map(_clean_date)

    before = len(df)
    df = df.drop_duplicates()
    dupes = before - len(df)
    if dupes:
        caveats.append(f"Removed {dupes} exact duplicate work-order row(s).")

    if "sector" in df.columns:
        missing_sector = int(df["sector"].isna().sum())
        if missing_sector:
            caveats.append(f"{missing_sector} work order(s) have no sector and are grouped as 'Unknown'.")
        df["sector"] = df["sector"].fillna("Unknown")

    if "amount_incl_gst" in df.columns:
        missing_amt = int(df["amount_incl_gst"].isna().sum())
        if missing_amt:
            caveats.append(
                f"{missing_amt} of {len(df)} work orders have no PO amount; billing totals "
                "exclude those rows."
            )

    df = df.reset_index(drop=True)
    return CleanResult(df=df, caveats=caveats, row_count_raw=raw_count, row_count_clean=len(df))


# --------------------------------------------------------------------------- #
#  Shared value normalizers
# --------------------------------------------------------------------------- #
_SECTOR_ALIASES = {
    "renewables": "Renewables",
    "renewable": "Renewables",
    "mining": "Mining",
    "powerline": "Powerline",
    "power line": "Powerline",
    "railways": "Railways",
    "railway": "Railways",
    "dsp": "DSP",
    "tender": "Tender",
    "construction": "Construction",
    "aviation": "Aviation",
    "manufacturing": "Manufacturing",
    "security and surveillance": "Security & Surveillance",
    "others": "Others",
    "other": "Others",
}


def _normalize_sector(value: Any) -> str | None:
    s = _clean_str(value)
    if not s:
        return None
    return _SECTOR_ALIASES.get(s.lower(), _title_case(s))


def _normalize_billing_status(value: Any) -> str | None:
    s = _clean_str(value)
    if not s:
        return None
    low = s.lower()
    if low in {"billed", "bılled"}:  # handles the "BIlled" typo
        return "Billed"
    if "partial" in low:
        return "Partially Billed"
    if "not billed" in low or "not bılled" in low:
        return "Not Billed"
    if "update" in low:
        return "Update Required"
    return _title_case(s)
