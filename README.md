# Skylark Drones — monday.com BI Agent

A conversational agent that answers founder-level business questions
("How's our pipeline looking for the energy sector this quarter?") by
querying two live monday.com boards — **Deals** (sales pipeline) and
**Work Orders** (project execution) — cleaning the messy real-world data on
the fly, and reasoning across both boards.

Live demo: `<PASTE HOSTED URL HERE>`

See `DECISION_LOG.md` for assumptions, trade-offs, and how "leadership
updates" was interpreted.

## Architecture

```
app.py                  Streamlit chat UI (also renders the leadership-update panel)
src/
  config.py             Env var / Streamlit-secrets configuration
  monday_client.py       Read-only GraphQL client for monday.com API v2
                          (list boards, get schema, paginate items)
  data_cleaning.py       Schema-agnostic cleaners: dates, numbers, categorical
                          text, missing-value detection, per-column quality flags
  data_service.py        Bridges the client + cleaners into cached, cleaned
                          board data (short TTL cache per board)
  tools.py                Tool definitions + dispatch (provider-agnostic JSON
                          Schema): list_boards, get_board_schema,
                          get_board_data, aggregate_board (pandas-backed,
                          exact arithmetic)
  agent.py                Gemini tool-use loop + system prompt
  leadership_update.py    Structured prompt that reuses the same agent/tools
                          to draft an exec-ready markdown brief
tests/                   Unit tests for cleaning logic and tool dispatch (mocked)
```

**Data flow for a question:** user message → Gemini (with tool definitions)
→ Gemini calls `get_board_data`/`aggregate_board`/`get_board_schema` as
needed → `monday_client` hits the live GraphQL API → `data_cleaning`
normalizes dates/numbers/text and flags quality issues → results go back to
Gemini as function responses → Gemini drafts a grounded, caveated answer.

**No CSV data is hardcoded anywhere.** The agent discovers board columns at
runtime via `get_board_schema`/`get_board_data`; `tools.py` and `agent.py`
contain no references to specific column names from the source spreadsheets.

**Numbers are computed by pandas, not by the LLM.** Any sum/average/count/
group-by goes through `aggregate_board`, which does the arithmetic in Python.
The LLM's job is choosing what to query and narrating the result — this
avoids LLM arithmetic errors on larger boards.

**LLM choice:** the agent uses Google Gemini (`gemini-flash-lite-latest` by
default, configurable) rather than a paid-only API, since Google AI Studio
issues a genuinely free API key with no billing setup required — the
priority for a freely testable hosted prototype. The "-latest" lite alias
was chosen specifically because pinned model names (`gemini-2.5-flash`,
then `gemini-3.6-flash`) each hit either a hard deprecation or an
exhausted per-model daily free quota during testing; the lite tier and
"-latest" aliasing both help the free tier stretch further. `tools.py`'s
tool specs are plain JSON Schema and `agent.py` isolates all Gemini-specific
request/response handling, so swapping providers later is a contained
change. See `DECISION_LOG.md`.

## monday.com setup (one-time)

1. Create a free monday.com account.
2. Create two boards by importing the provided files directly (**+ Add** →
   **Import data** → Excel/CSV on the board creation screen):
   - Import `Work_Order_Tracker Data` as a board named **Work Orders**
   - Import `Deal funnel Data` as a board named **Deals**
   Let monday.com auto-detect column types on import, then adjust so that:
   - Any date field is a **Date** column type
   - Any monetary/quantity field is a **Numbers** column type
   - Categorical fields (sector, stage, status) are **Status** or **Dropdown**
   columns
   (Exact column names don't matter — the agent introspects them at
   runtime — but correct *types* materially improve cleaning quality, since
   `data_cleaning.py` uses the column type to decide how to parse a value.)
3. Generate a personal API token: profile avatar (bottom-left) →
   **Developers** → **My Access Tokens** → **Generate**. This app only ever
   sends read (`query`) GraphQL requests — no mutations exist in the
   codebase — but note that a personal API token is scoped to whatever the
   token owner can see, so use a token from a user with read access to both
   boards.
4. Note the two board names (or numeric IDs from the board URL).

## Configuration

Set these as environment variables (local) or Streamlit secrets (hosted —
see below):

| Key | Description |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key — free from https://aistudio.google.com/apikey |
| `MONDAY_API_TOKEN` | monday.com personal API token |
| `MONDAY_WORK_ORDERS_BOARD` | Board name or numeric ID, default `"Work Orders"` |
| `MONDAY_DEALS_BOARD` | Board name or numeric ID, default `"Deals"` |
| `GEMINI_MODEL` | Default `gemini-flash-lite-latest` |
| `MAX_ROWS_PER_BOARD` | Safety cap per board per fetch, default `1000` |

Copy `.env.example` → `.env` for local reference, or
`.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` for local
Streamlit runs (both are gitignored).

## Run locally

```bash
pip install -r requirements.txt
# put real values in .streamlit/secrets.toml (see above)
streamlit run app.py
```

## Run tests

```bash
python -m pytest tests/ -q
```

## Deploy (Streamlit Community Cloud, free)

1. Push this repo to GitHub.
2. On share.streamlit.io, **New app** → point at this repo, main branch,
   `app.py`.
3. In the app's **Settings → Secrets**, paste the contents of
   `.streamlit/secrets.toml.example` with real values filled in.
4. Deploy. The link is testable without any local setup.

## Known limitations

- `MAX_ROWS_PER_BOARD` caps how much of a board is pulled per query
  (default 1000 rows) to keep tool-call payloads and API complexity bounded;
  `aggregate_board` will report `board_truncated: true` if a board exceeds
  this so the agent can flag it rather than silently under-counting.
- Cross-board joins (e.g. matching a Deal to its resulting Work Order) rely
  on fuzzy matching client/company name text, since there's no guaranteed
  shared ID between the two boards in the source data.
- The in-process cache (`data_service.py`, 120s TTL) means very rapid
  back-to-back questions may see slightly stale data if something changed
  in monday.com moments earlier.
- **Gemini free-tier quotas** apply both per-minute and per-day, per model.
  `agent.py` retries 429s with backoff honoring the server's suggested
  delay, which absorbs the per-minute limit within a question. The
  per-day limit (as low as 20 requests/day on some pinned models) cannot
  be waited out the same way -- if you see a `RESOURCE_EXHAUSTED` error
  mentioning `PerDay`, that model's daily quota is spent; either wait for
  the daily reset or point `GEMINI_MODEL` at a different model name
  (quota is tracked per-model, so a fresh model has its own allowance).
  The default `gemini-flash-lite-latest` and the "batch via group_by"
  instruction in the system prompt (`agent.py`) both exist specifically to
  economize on this. A paid Gemini tier removes the constraint entirely.
