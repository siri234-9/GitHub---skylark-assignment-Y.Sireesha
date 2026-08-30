"""Thin, read-only client for the monday.com GraphQL API (v2).

Deliberately minimal: only the queries this agent needs (list boards, read
board schema, page through items). No mutations are implemented anywhere in
this module, matching the "Read only" integration requirement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from . import config


class MondayAPIError(RuntimeError):
    """Raised for auth failures, GraphQL errors, or exhausted retries."""


@dataclass
class Column:
    id: str
    title: str
    type: str


@dataclass
class Item:
    id: str
    name: str
    # column title -> human-readable text value (may be "" or None)
    values: dict = field(default_factory=dict)


class MondayClient:
    def __init__(self, token: str | None = None):
        self.token = token or config.MONDAY_API_TOKEN
        if not self.token:
            raise MondayAPIError(
                "No monday.com API token configured. Set MONDAY_API_TOKEN."
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": self.token,
                "Content-Type": "application/json",
                "API-Version": config.MONDAY_API_VERSION,
            }
        )
        self._board_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    def _execute(self, query: str, variables: dict | None = None, retries: int = 3) -> dict:
        payload = {"query": query, "variables": variables or {}}
        last_err = None
        for attempt in range(retries):
            try:
                resp = self._session.post(config.MONDAY_API_URL, json=payload, timeout=30)
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 429:
                # rate limited - backoff and retry
                time.sleep(2.0 * (attempt + 1))
                continue

            if resp.status_code == 401:
                raise MondayAPIError(
                    "monday.com rejected the API token (401 Unauthorized). "
                    "Check MONDAY_API_TOKEN."
                )

            try:
                data = resp.json()
            except ValueError:
                last_err = RuntimeError(f"Non-JSON response: {resp.text[:200]}")
                time.sleep(1.5 * (attempt + 1))
                continue

            if "errors" in data:
                msg = "; ".join(e.get("message", str(e)) for e in data["errors"])
                # complexity / rate-limit style errors are worth a retry
                if "complexity" in msg.lower() or "rate limit" in msg.lower():
                    last_err = MondayAPIError(msg)
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise MondayAPIError(f"monday.com API error: {msg}")

            return data["data"]

        raise MondayAPIError(f"monday.com API request failed after {retries} attempts: {last_err}")

    # ------------------------------------------------------------------
    def list_boards(self) -> list[dict]:
        query = """
        query {
          boards (limit: 100, state: active) {
            id
            name
          }
        }
        """
        data = self._execute(query)
        return data["boards"]

    def resolve_board_id(self, name_or_id: str) -> str:
        """Accept either a numeric board id or a board name and return the id."""
        if name_or_id in self._board_id_cache:
            return self._board_id_cache[name_or_id]
        if str(name_or_id).isdigit():
            self._board_id_cache[name_or_id] = str(name_or_id)
            return str(name_or_id)

        boards = self.list_boards()
        target = name_or_id.strip().lower()
        exact = [b for b in boards if b["name"].strip().lower() == target]
        partial = [b for b in boards if target in b["name"].strip().lower()]
        match = (exact or partial)
        if not match:
            available = ", ".join(b["name"] for b in boards) or "(none found)"
            raise MondayAPIError(
                f"Could not find a monday.com board named '{name_or_id}'. "
                f"Boards visible to this token: {available}"
            )
        if len(match) > 1 and not exact:
            names = ", ".join(b["name"] for b in match)
            raise MondayAPIError(
                f"Board name '{name_or_id}' is ambiguous, matches: {names}. "
                "Please use a more specific name or the numeric board ID."
            )
        board_id = str(match[0]["id"])
        self._board_id_cache[name_or_id] = board_id
        return board_id

    def get_schema(self, board_name_or_id: str) -> list[Column]:
        board_id = self.resolve_board_id(board_name_or_id)
        query = """
        query ($boardId: [ID!]) {
          boards (ids: $boardId) {
            columns {
              id
              title
              type
            }
          }
        }
        """
        data = self._execute(query, {"boardId": [board_id]})
        boards = data["boards"]
        if not boards:
            raise MondayAPIError(f"Board id {board_id} not found or not accessible.")
        return [Column(**c) for c in boards[0]["columns"]]

    def get_items(self, board_name_or_id: str, max_rows: int | None = None) -> list[Item]:
        """Page through all items on a board, returning cleaned-shape Items.

        Column values are keyed by column *title* (not internal id) since
        that's what a human question and the LLM will reason about.
        """
        board_id = self.resolve_board_id(board_name_or_id)
        max_rows = max_rows or config.MAX_ROWS_PER_BOARD
        query = """
        query ($boardId: [ID!], $cursor: String, $limit: Int) {
          boards (ids: $boardId) {
            items_page (limit: $limit, cursor: $cursor) {
              cursor
              items {
                id
                name
                column_values {
                  id
                  text
                  value
                  column {
                    title
                    type
                  }
                }
              }
            }
          }
        }
        """
        items: list[Item] = []
        cursor = None
        page_size = min(100, max_rows)
        while True:
            data = self._execute(
                query, {"boardId": [board_id], "cursor": cursor, "limit": page_size}
            )
            boards = data["boards"]
            if not boards:
                raise MondayAPIError(f"Board id {board_id} not found or not accessible.")
            page = boards[0]["items_page"]
            for raw in page["items"]:
                values = {"Name": raw["name"]}
                types = {}
                for cv in raw["column_values"]:
                    title = cv["column"]["title"]
                    values[title] = cv["text"]
                    types[title] = cv["column"]["type"]
                items.append(Item(id=raw["id"], name=raw["name"], values=values))
                if len(items) >= max_rows:
                    return items
            cursor = page.get("cursor")
            if not cursor:
                break
        return items
