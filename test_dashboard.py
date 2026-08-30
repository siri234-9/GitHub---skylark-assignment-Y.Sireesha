import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import dashboard, data_service, config

DEALS_BOARD = {
    "board_name_or_id": "Deal funnel Data",
    "resolved_board_id": "1",
    "columns": [
        {"title": "Deal Status", "type": "status"},
        {"title": "Masked Deal value", "type": "numeric"},
        {"title": "Sector/service", "type": "status"},
    ],
    "records": [
        {"Deal Status": "Open", "Masked Deal value": 100.0, "Sector/service": "Mining", "_item_id": "1"},
        {"Deal Status": "Won", "Masked Deal value": 200.0, "Sector/service": "Mining", "_item_id": "2"},
        {"Deal Status": "Dead", "Masked Deal value": 50.0, "Sector/service": "Energy", "_item_id": "3"},
    ],
    "row_count": 3,
    "truncated": False,
    "data_quality_issue_counts": {},
    "data_quality_issue_pct": {},
}

WO_BOARD = {
    "board_name_or_id": "Work_Order_Tracker Data",
    "resolved_board_id": "2",
    "columns": [{"title": "Execution Status", "type": "status"}],
    "records": [
        {"Execution Status": "Completed", "_item_id": "1"},
        {"Execution Status": "Not Started", "_item_id": "2"},
        {"Execution Status": "Completed", "_item_id": "3"},
    ],
    "row_count": 3,
    "truncated": False,
    "data_quality_issue_counts": {},
    "data_quality_issue_pct": {},
}


def _fake_get_board_data(board, force_refresh=False):
    if board == config.MONDAY_DEALS_BOARD:
        return DEALS_BOARD
    if board == config.MONDAY_WORK_ORDERS_BOARD:
        return WO_BOARD
    raise AssertionError(f"unexpected board {board}")


def test_dashboard_load_computes_kpis_from_known_columns(monkeypatch):
    monkeypatch.setattr(data_service, "get_board_data", _fake_get_board_data)
    out = dashboard.load()

    assert out["open_pipeline_value"] == 100.0
    assert out["won_count"] == 1
    assert out["dead_count"] == 1
    assert out["win_rate"] == 50.0
    assert out["completion_pct"] == round(100 * 2 / 3, 1)
    assert out["not_started_count"] == 1
    assert out["wo_total"] == 3


def test_dashboard_load_degrades_gracefully_on_unknown_schema(monkeypatch):
    weird_board = {**DEALS_BOARD, "columns": [{"title": "Totally Different", "type": "text"}]}
    monkeypatch.setattr(data_service, "get_board_data", lambda board, force_refresh=False: weird_board)
    out = dashboard.load()
    assert out == {}
