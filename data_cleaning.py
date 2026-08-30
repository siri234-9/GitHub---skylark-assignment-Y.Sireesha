"""Generic, schema-agnostic cleaning helpers.

These operate on raw text values coming out of monday.com's `text` field on
each column (already human-rendered by monday, e.g. dates as "2024-03-15" or
"March 15 2024", numbers as "$12,500"). We do NOT assume specific column
names here -- the agent discovers columns dynamically -- but we do apply
type-aware cleaning once a column's monday.com type is known, plus a
best-effort fallback for ambiguous text.
"""
from __future__ import annotations

import re
from datetime import datetime

from dateutil import parser as dateparser

MISSING_TOKENS = {
    "", "-", "--", "n/a", "na", "null", "none", "tbd", "unknown", "?",
    "pending", "nil", "#n/a",
}


def is_missing(value) -> bool:
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in MISSING_TOKENS


def normalize_text(value) -> str | None:
    """Trim whitespace, collapse internal whitespace, fix casing drift for
    free-text / categorical fields (e.g. 'ENERGY ', ' energy', 'Energy')."""
    if is_missing(value):
        return None
    s = re.sub(r"\s+", " ", str(value).strip())
    return s


CANONICAL_SECTOR_ALIASES = {
    "energy": "Energy", "power": "Energy", "solar": "Energy",
    "oil & gas": "Energy", "oil and gas": "Energy",
    "mining": "Mining", "mines": "Mining",
    "infra": "Infrastructure", "infrastructure": "Infrastructure",
    "construction": "Infrastructure",
    "agri": "Agriculture", "agriculture": "Agriculture", "agritech": "Agriculture",
    "telecom": "Telecom", "telecommunications": "Telecom",
    "govt": "Government", "government": "Government", "public sector": "Government",
    "logistics": "Logistics", "defence": "Defense", "defense": "Defense",
}


def normalize_category(value, alias_map: dict | None = None) -> str | None:
    """Fold obvious spelling/case variants of a categorical value (e.g.
    sector, status) into a canonical label. Falls back to title-cased raw
    text if no alias is known, so unseen categories are preserved rather
    than dropped."""
    s = normalize_text(value)
    if s is None:
        return None
    key = s.lower().strip()
    m = alias_map if alias_map is not None else CANONICAL_SECTOR_ALIASES
    if key in m:
        return m[key]
    return s

_NUMERIC_STRIP = re.compile(r"[,\s]")
_CURRENCY_SYMBOLS = re.compile(r"[₹$€£]")
_SUFFIX_MULT = {"k": 1_000, "m": 1_000_000, "mm": 1_000_000, "cr": 10_000_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000}


def parse_number(value) -> float | None:
    """Best-effort numeric parse tolerant of currency symbols, thousands
    separators, and K/M/Cr/L magnitude suffixes. Returns None (not 0) for
    missing/unparseable values so callers can distinguish 'no data' from
    'zero'."""
    if is_missing(value):
        return None
    s = str(value).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = _CURRENCY_SYMBOLS.sub("", s)
    s = s.replace("%", "")
    s = _NUMERIC_STRIP.sub("", s)
    m = re.match(r"^-?\d*\.?\d+([a-zA-Z]{1,4})?$", s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    suffix = (m.group(1) or "").lower()
    mult = _SUFFIX_MULT.get(suffix, 1)
    numeric_part = s[: len(s) - len(suffix)] if suffix else s
    try:
        result = float(numeric_part) * mult
    except ValueError:
        return None
    return -result if negative else result


def parse_date(value) -> str | None:
    """Parse a wide variety of date strings/formats into ISO 8601
    (YYYY-MM-DD). Returns None if the value is missing or unparseable
    (never raises -- unparseable dates are a data-quality signal, not a
    crash)."""
    if is_missing(value):
        return None
    s = str(value).strip()
    # monday.com date columns sometimes append a time, e.g. "2024-03-15 13:00:00"
    s = s.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s
    try:
        dt = dateparser.parse(s, dayfirst=False, fuzzy=True, default=datetime(1900, 1, 1))
    except (ValueError, OverflowError):
        return None
    if dt.year == 1900:
        # default sentinel never got overwritten with a real year -> treat as unparsed
        return None
    return dt.date().isoformat()


def clean_record(raw: dict, column_types: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Clean one item's {column_title: raw_text} dict.

    Returns (cleaned_dict, quality_flags) where quality_flags notes any
    field that was missing or failed to parse, keyed by column title.
    """
    column_types = column_types or {}
    cleaned = {}
    flags = {}
    for key, val in raw.items():
        ctype = column_types.get(key, "")
        if is_missing(val):
            cleaned[key] = None
            flags[key] = "missing"
            continue
        if str(val).strip().lower() == str(key).strip().lower():
            # Real-world corruption seen in the source data: a stray copy of
            # the column header leaks into individual cells (e.g. a cell in
            # the "Deal Status" column literally containing the text "Deal
            # Status"), rather than an actual value. Treat as missing.
            cleaned[key] = None
            flags[key] = "header_echoed_as_value"
            continue
        if ctype == "date":
            parsed = parse_date(val)
            cleaned[key] = parsed
            if parsed is None:
                flags[key] = "unparseable_date"
        elif ctype in ("numeric",):
            parsed = parse_number(val)
            cleaned[key] = parsed
            if parsed is None:
                flags[key] = "unparseable_number"
        elif ctype in ("status", "dropdown", "color"):
            # alias_map only folds known sector-ish spelling variants; any
            # other categorical value (stage, status, ...) simply falls
            # through to normalized raw text since it won't match a key.
            cleaned[key] = normalize_category(val)
        else:
            cleaned[key] = normalize_text(val)
    return cleaned, flags


def summarize_data_quality(records_flags: list[dict]) -> dict:
    """Aggregate per-record quality flags into column-level counts, e.g.
    {'Close Date': {'missing': 4, 'unparseable_date': 1}}. Used to give the
    agent (and the user) an honest caveat about data completeness."""
    summary: dict[str, dict[str, int]] = {}
    for flags in records_flags:
        for col, kind in flags.items():
            summary.setdefault(col, {}).setdefault(kind, 0)
            summary[col][kind] += 1
    return summary
