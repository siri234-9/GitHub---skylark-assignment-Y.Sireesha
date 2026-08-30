import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data_cleaning import (
    is_missing,
    normalize_text,
    parse_number,
    parse_date,
    clean_record,
    summarize_data_quality,
)


def test_is_missing_common_tokens():
    for v in [None, "", "N/A", "n/a", "-", "TBD", "null", "  "]:
        assert is_missing(v), f"expected {v!r} to be missing"
    assert not is_missing("Energy")
    assert not is_missing(0)


def test_normalize_text_collapses_whitespace_and_trims():
    assert normalize_text("  Energy   Sector ") == "Energy Sector"
    assert normalize_text("N/A") is None


def test_parse_number_currency_and_suffixes():
    assert parse_number("$12,500") == 12500.0
    assert parse_number("₹1.5L") == 150000.0
    assert parse_number("2.3M") == 2_300_000.0
    assert parse_number("(500)") == -500.0
    assert parse_number("") is None
    assert parse_number("garbage") is None


def test_parse_date_multiple_formats():
    assert parse_date("2024-03-15") == "2024-03-15"
    assert parse_date("March 15, 2024") == "2024-03-15"
    assert parse_date("15/03/2024") is not None  # dateutil resolves ambiguous dd/mm
    assert parse_date("") is None
    assert parse_date("not a date") is None


def test_clean_record_flags_missing_and_unparseable():
    raw = {"Sector": "Energy", "Close Date": "gibberish", "Value": "N/A"}
    types = {"Sector": "status", "Close Date": "date", "Value": "numeric"}
    cleaned, flags = clean_record(raw, types)
    assert cleaned["Sector"] == "Energy"
    assert cleaned["Close Date"] is None
    assert flags["Close Date"] == "unparseable_date"
    assert flags["Value"] == "missing"


def test_clean_record_treats_header_echoed_as_value_as_missing():
    # Observed in the real Deal Funnel data: a handful of rows have the
    # literal column header text leaked into the cell instead of real data
    # (e.g. the "Deal Status" column containing the text "Deal Status").
    raw = {"Deal Status": "Deal Status", "Sector/service": "Sector/service", "Owner code": "OWNER_003"}
    types = {"Deal Status": "status", "Sector/service": "status", "Owner code": "text"}
    cleaned, flags = clean_record(raw, types)
    assert cleaned["Deal Status"] is None
    assert flags["Deal Status"] == "header_echoed_as_value"
    assert cleaned["Owner code"] == "OWNER_003"
    assert "Owner code" not in flags


def test_summarize_data_quality_aggregates_across_records():
    flags_list = [
        {"Value": "missing"},
        {"Value": "missing", "Close Date": "unparseable_date"},
    ]
    summary = summarize_data_quality(flags_list)
    assert summary["Value"]["missing"] == 2
    assert summary["Close Date"]["unparseable_date"] == 1
