"""Bridges the raw monday.com client to cleaned, analysis-ready data.

Caches per-board data in-process for a short TTL so a single conversation
turn (which may call several tools) doesn't re-fetch the same board
repeatedly, while staying reasonably fresh across turns.
"""
from __future__ import annotations

import time

from . import config
from .data_cleaning import clean_record, summarize_data_quality
from .monday_client import MondayClient, MondayAPIError

_CACHE_TTL_SECONDS = 120
_cache: dict[str, tuple[float, dict]] = {}

_client: MondayClient | None = None


def get_client() -> MondayClient:
    global _client
    if _client is None:
        _client = MondayClient()
    return _client


def _fetch_board(board_name_or_id: str) -> dict:
    client = get_client()
    schema = client.get_schema(board_name_or_id)
    column_types = {c.title: c.type for c in schema}
    items = client.get_items(board_name_or_id, max_rows=config.MAX_ROWS_PER_BOARD)

    cleaned_records = []
    all_flags = []
    for item in items:
        cleaned, flags = clean_record(item.values, column_types)
        cleaned["_item_id"] = item.id
        cleaned_records.append(cleaned)
        all_flags.append(flags)

    quality = summarize_data_quality(all_flags)
    row_count = len(cleaned_records)
    quality_pct = {
        col: {kind: round(100 * n / row_count, 1) for kind, n in kinds.items()}
        for col, kinds in quality.items()
    } if row_count else {}

    return {
        "board_name_or_id": board_name_or_id,
        "resolved_board_id": client.resolve_board_id(board_name_or_id),
        "columns": [{"title": c.title, "type": c.type} for c in schema],
        "records": cleaned_records,
        "row_count": row_count,
        "truncated": row_count >= config.MAX_ROWS_PER_BOARD,
        "data_quality_issue_counts": quality,
        "data_quality_issue_pct": quality_pct,
    }


def get_board_data(board_name_or_id: str, force_refresh: bool = False) -> dict:
    key = board_name_or_id.strip().lower()
    now = time.time()
    if not force_refresh and key in _cache:
        ts, data = _cache[key]
        if now - ts < _CACHE_TTL_SECONDS:
            return data
    data = _fetch_board(board_name_or_id)
    _cache[key] = (now, data)
    return data


def clear_cache():
    _cache.clear()
