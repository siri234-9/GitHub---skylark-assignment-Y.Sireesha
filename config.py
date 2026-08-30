"""Central configuration. Reads from environment variables, with a fallback
to Streamlit secrets when running inside the Streamlit app (st.secrets is
only populated in that context)."""
import os

try:
    import streamlit as st
    _HAS_STREAMLIT = True
except Exception:
    _HAS_STREAMLIT = False


def _get(key: str, default: str | None = None) -> str | None:
    if _HAS_STREAMLIT:
        try:
            if key in st.secrets:
                return st.secrets[key]
        except Exception:
            pass
    return os.environ.get(key, default)


GOOGLE_API_KEY = _get("GOOGLE_API_KEY")
MONDAY_API_TOKEN = _get("MONDAY_API_TOKEN")

# Either an exact numeric board ID, or a board name to resolve at runtime.
MONDAY_WORK_ORDERS_BOARD = _get("MONDAY_WORK_ORDERS_BOARD", "Work Orders")
MONDAY_DEALS_BOARD = _get("MONDAY_DEALS_BOARD", "Deals")

GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-flash-lite-latest")

# Safety cap on rows pulled per board per query so a single question can't
# blow up context size or API complexity budget on very large boards.
MAX_ROWS_PER_BOARD = int(_get("MAX_ROWS_PER_BOARD", "1000") or "1000")

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2024-10"
