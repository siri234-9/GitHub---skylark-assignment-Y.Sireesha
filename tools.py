"""Tool definitions exposed to the LLM agent, plus their implementations.

Design choice: rather than building a bespoke query DSL, the agent gets two
kinds of tools -- (a) fetch cleaned raw records for a board so it can reason
over them directly for qualitative/exploratory questions, and (b) a
deterministic `aggregate_board` tool backed by pandas for anything numeric
(sums, averages, counts, group-bys) so arithmetic is never left to the LLM.
All tools are read-only: none of them can write back to monday.com.
"""
from __future__ import annotations

import json

import pandas as pd

from . import data_service
from .monday_client import MondayAPIError

TOOL_SPECS = [
    {
        "name": "list_boards",
        "description": (
            "List all monday.com boards visible to this integration, with their "
            "IDs and names. Use this if you're unsure of the exact board name."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_board_schema",
        "description": (
            "Get the column names and types for a board (e.g. Work Orders or "
            "Deals). Always call this before assuming a column exists -- board "
            "structure is not hardcoded and may vary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name or ID, e.g. 'Deals' or 'Work Orders'"}
            },
            "required": ["board"],
        },
    },
    {
        "name": "get_board_data",
        "description": (
            "Fetch cleaned records from a board as a list of JSON objects (one per "
            "item/row), plus a data-quality summary (missing values, unparseable "
            "dates/numbers per column). Dates are normalized to YYYY-MM-DD and "
            "numbers to plain floats. Use this for exploratory questions, listing "
            "items, or when you need to reason over raw rows (e.g. cross-referencing "
            "clients between boards). For sums/averages/counts/group-bys, prefer "
            "aggregate_board instead -- it computes exact numbers rather than you "
            "estimating from a printed list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name or ID"},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of column titles to include, to save context space.",
                },
            },
            "required": ["board"],
        },
    },
    {
        "name": "aggregate_board",
        "description": (
            "Compute an exact aggregate over a board's cleaned data: sum/avg/count/"
            "min/max/median of a numeric column, optionally grouped by another "
            "column, optionally filtered first. Always prefer this over eyeballing "
            "get_board_data output for any numeric answer (revenue totals, deal "
            "counts by stage, average deal size by sector, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string"},
                "metric_column": {
                    "type": "string",
                    "description": "Numeric column to aggregate. Omit only if agg is 'count'.",
                },
                "agg": {
                    "type": "string",
                    "enum": ["sum", "avg", "count", "min", "max", "median"],
                },
                "group_by": {
                    "type": "string",
                    "description": "Optional column to group results by (e.g. 'Sector', 'Stage').",
                },
                "filters": {
                    "type": "array",
                    "description": "Optional list of filters, all combined with AND.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "op": {
                                "type": "string",
                                "enum": ["eq", "neq", "contains", "gte", "lte", "gt", "lt", "not_missing"],
                            },
                            "value": {
                                "description": "Comparison value. Omit for 'not_missing'. For date columns use YYYY-MM-DD."
                            },
                        },
                        "required": ["column", "op"],
                    },
                },
            },
            "required": ["board", "agg"],
        },
    },
]


def _records_df(board: str) -> tuple[pd.DataFrame, dict]:
    data = data_service.get_board_data(board)
    df = pd.DataFrame(data["records"])
    return df, data


def _apply_filters(df: pd.DataFrame, filters: list[dict] | None) -> pd.DataFrame:
    if not filters:
        return df
    for f in filters:
        col, op, val = f.get("column"), f.get("op"), f.get("value")
        if col not in df.columns:
            raise ValueError(f"Unknown column '{col}'. Call get_board_schema first.")
        series = df[col]
        if op == "not_missing":
            df = df[series.notna()]
        elif op == "eq":
            df = df[series.astype(str).str.lower() == str(val).lower()]
        elif op == "neq":
            df = df[series.astype(str).str.lower() != str(val).lower()]
        elif op == "contains":
            df = df[series.astype(str).str.lower().str.contains(str(val).lower(), na=False)]
        elif op in ("gte", "lte", "gt", "lt"):
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.isna().all():
                # try as dates (ISO strings compare lexicographically, safe here)
                numeric = series
            v = val
            if op == "gte":
                df = df[numeric >= v]
            elif op == "lte":
                df = df[numeric <= v]
            elif op == "gt":
                df = df[numeric > v]
            elif op == "lt":
                df = df[numeric < v]
        else:
            raise ValueError(f"Unsupported op '{op}'")
    return df


def dispatch(name: str, tool_input: dict) -> str:
    """Execute a tool call and return a JSON string result. Errors are
    returned as a JSON error object (not raised) so the agent can see and
    react to them instead of the whole turn failing."""
    try:
        if name == "list_boards":
            boards = data_service.get_client().list_boards()
            return json.dumps({"boards": boards})

        if name == "get_board_schema":
            data = data_service.get_board_data(tool_input["board"])
            return json.dumps({"board": tool_input["board"], "columns": data["columns"]})

        if name == "get_board_data":
            data = data_service.get_board_data(tool_input["board"])
            records = data["records"]
            cols = tool_input.get("columns")
            if cols:
                records = [{k: r.get(k) for k in cols} for r in records]
            return json.dumps(
                {
                    "board": tool_input["board"],
                    "row_count": data["row_count"],
                    "truncated": data["truncated"],
                    "data_quality_issue_pct": data["data_quality_issue_pct"],
                    "records": records,
                }
            )

        if name == "aggregate_board":
            df, data = _records_df(tool_input["board"])
            if df.empty:
                return json.dumps({"result": None, "note": "Board has no rows."})
            df = _apply_filters(df, tool_input.get("filters"))
            agg = tool_input["agg"]
            metric = tool_input.get("metric_column")
            group_by = tool_input.get("group_by")

            if agg != "count" and not metric:
                return json.dumps({"error": "metric_column is required unless agg='count'"})
            if metric and metric not in df.columns:
                return json.dumps({"error": f"Unknown metric_column '{metric}'"})
            if group_by and group_by not in df.columns:
                return json.dumps({"error": f"Unknown group_by column '{group_by}'"})

            agg_map = {"sum": "sum", "avg": "mean", "count": "count", "min": "min", "max": "max", "median": "median"}
            pandas_agg = agg_map[agg]

            if metric:
                numeric_col = pd.to_numeric(df[metric], errors="coerce")
                non_numeric = int(numeric_col.isna().sum() - df[metric].isna().sum())
                df = df.assign(**{metric: numeric_col})
            else:
                non_numeric = 0

            if group_by:
                if metric:
                    result = df.groupby(group_by, dropna=False)[metric].agg(pandas_agg)
                else:
                    result = df.groupby(group_by, dropna=False).size()
                result = result.reset_index()
                result.columns = [group_by, "value"]
                result[group_by] = result[group_by].fillna("(missing)")
                out = result.to_dict(orient="records")
            else:
                if metric:
                    out = float(getattr(df[metric], pandas_agg)()) if len(df) else None
                else:
                    out = int(len(df))

            return json.dumps(
                {
                    "result": out,
                    "rows_considered": int(len(df)),
                    "non_numeric_values_ignored_in_metric": non_numeric,
                    "board_row_count_total": data["row_count"],
                    "board_truncated": data["truncated"],
                }
            )

        return json.dumps({"error": f"Unknown tool '{name}'"})

    except MondayAPIError as e:
        return json.dumps({"error": f"monday.com API error: {e}"})
    except Exception as e:  # noqa: BLE001 - surface any tool failure to the agent, don't crash the app
        return json.dumps({"error": f"Tool '{name}' failed: {e}"})
