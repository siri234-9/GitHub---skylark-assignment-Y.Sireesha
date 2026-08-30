import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import tools, data_service

FAKE_BOARD = {
    "board_name_or_id": "Deals",
    "resolved_board_id": "123",
    "columns": [
        {"title": "Name", "type": "text"},
        {"title": "Sector", "type": "status"},
        {"title": "Stage", "type": "status"},
        {"title": "Deal Value", "type": "numeric"},
    ],
    "records": [
        {"Name": "Acme Solar", "Sector": "Energy", "Stage": "Won", "Deal Value": 100000.0, "_item_id": "1"},
        {"Name": "Beta Mining", "Sector": "Mining", "Stage": "Open", "Deal Value": 50000.0, "_item_id": "2"},
        {"Name": "Gamma Energy", "Sector": "Energy", "Stage": "Open", "Deal Value": None, "_item_id": "3"},
    ],
    "row_count": 3,
    "truncated": False,
    "data_quality_issue_counts": {"Deal Value": {"missing": 1}},
    "data_quality_issue_pct": {"Deal Value": {"missing": 33.3}},
}


def test_aggregate_board_sum_with_group_by(monkeypatch):
    monkeypatch.setattr(data_service, "get_board_data", lambda board, force_refresh=False: FAKE_BOARD)
    result = json.loads(
        tools.dispatch(
            "aggregate_board",
            {"board": "Deals", "agg": "sum", "metric_column": "Deal Value", "group_by": "Sector"},
        )
    )
    values = {row["Sector"]: row["value"] for row in result["result"]}
    assert values["Energy"] == 100000.0  # missing value excluded, not treated as 0
    assert values["Mining"] == 50000.0


def test_aggregate_board_filters_eq(monkeypatch):
    monkeypatch.setattr(data_service, "get_board_data", lambda board, force_refresh=False: FAKE_BOARD)
    result = json.loads(
        tools.dispatch(
            "aggregate_board",
            {
                "board": "Deals",
                "agg": "count",
                "filters": [{"column": "Stage", "op": "eq", "value": "open"}],
            },
        )
    )
    assert result["result"] == 2


def test_aggregate_board_unknown_column_returns_error_not_exception(monkeypatch):
    monkeypatch.setattr(data_service, "get_board_data", lambda board, force_refresh=False: FAKE_BOARD)
    result = json.loads(
        tools.dispatch("aggregate_board", {"board": "Deals", "agg": "sum", "metric_column": "Nope"})
    )
    assert "error" in result


def test_get_board_data_column_subset(monkeypatch):
    monkeypatch.setattr(data_service, "get_board_data", lambda board, force_refresh=False: FAKE_BOARD)
    result = json.loads(tools.dispatch("get_board_data", {"board": "Deals", "columns": ["Sector"]}))
    assert all(set(r.keys()) == {"Sector"} for r in result["records"])
