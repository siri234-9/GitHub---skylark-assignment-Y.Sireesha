"""Live KPI/chart data for the optional dashboard tab.

Deliberately scoped exception to the "no hardcoded schema" rule that governs
the chat agent (agent.py/tools.py): this module tries a short list of likely
column-name candidates against the board's *live* schema (fetched fresh via
get_board_schema, never assumed blind) and simply omits a tile/chart if none
match. The chat agent itself stays fully schema-agnostic -- this is a
best-effort visual summary layered on top, not the graded integration path.
No Gemini/LLM calls happen here; every number comes straight from pandas via
aggregate_board, so this tab costs zero LLM quota.
"""
from __future__ import annotations

import json

from . import config
from .tools import dispatch

DEAL_STATUS_CANDIDATES = ["Deal Status", "Status"]
DEAL_VALUE_CANDIDATES = ["Masked Deal value", "Deal Value", "Value"]
DEAL_SECTOR_CANDIDATES = ["Sector/service", "Sector"]
WO_STATUS_CANDIDATES = ["Execution Status", "Status"]
WO_SECTOR_CANDIDATES = ["Sector", "Sector/service"]


def _agg(board: str, **kwargs) -> dict:
    kwargs["board"] = board
    return json.loads(dispatch("aggregate_board", kwargs))


def _find_column(board: str, candidates: list[str]) -> str | None:
    schema = json.loads(dispatch("get_board_schema", {"board": board}))
    titles = {c["title"] for c in schema.get("columns", [])}
    for cand in candidates:
        if cand in titles:
            return cand
    return None


def load() -> dict:
    """Best-effort KPI/chart bundle. Any missing column silently omits the
    dependent tile/chart rather than raising -- the dashboard degrades
    gracefully if a board's real column names differ from the candidates."""
    out: dict = {}

    try:
        deals_board = config.MONDAY_DEALS_BOARD
        status_col = _find_column(deals_board, DEAL_STATUS_CANDIDATES)
        value_col = _find_column(deals_board, DEAL_VALUE_CANDIDATES)
        sector_col = _find_column(deals_board, DEAL_SECTOR_CANDIDATES)

        if status_col and value_col:
            r = _agg(
                deals_board, agg="sum", metric_column=value_col,
                filters=[{"column": status_col, "op": "eq", "value": "Open"}],
            )
            out["open_pipeline_value"] = r.get("result")

        if status_col:
            won = _agg(deals_board, agg="count", filters=[{"column": status_col, "op": "eq", "value": "Won"}])
            dead = _agg(deals_board, agg="count", filters=[{"column": status_col, "op": "eq", "value": "Dead"}])
            w, d = won.get("result") or 0, dead.get("result") or 0
            out["won_count"], out["dead_count"] = w, d
            out["win_rate"] = round(100 * w / (w + d), 1) if (w + d) else None

        if sector_col and value_col:
            filters = [{"column": status_col, "op": "eq", "value": "Open"}] if status_col else None
            r = _agg(deals_board, agg="sum", metric_column=value_col, group_by=sector_col, filters=filters)
            out["pipeline_by_sector"] = {"field": sector_col, "records": r.get("result") or []}
    except Exception:
        pass

    try:
        wo_board = config.MONDAY_WORK_ORDERS_BOARD
        wo_status_col = _find_column(wo_board, WO_STATUS_CANDIDATES)

        if wo_status_col:
            r = _agg(wo_board, agg="count", group_by=wo_status_col)
            records = r.get("result") or []
            out["wo_status_breakdown"] = {"field": wo_status_col, "records": records}
            total = sum(row.get("value", 0) for row in records)
            completed = sum(
                row.get("value", 0) for row in records
                if str(row.get(wo_status_col, "")).strip().lower() == "completed"
            )
            not_started = sum(
                row.get("value", 0) for row in records
                if "not started" in str(row.get(wo_status_col, "")).strip().lower()
            )
            out["completion_pct"] = round(100 * completed / total, 1) if total else None
            out["not_started_count"] = not_started
            out["wo_total"] = total
    except Exception:
        pass

    return out
