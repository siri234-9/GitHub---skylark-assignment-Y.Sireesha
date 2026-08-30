# Decision Log

## Tech stack & why

- **Python** — pandas for exact aggregation, mature GraphQL/HTTP tooling,
  fastest to iterate in a 6-hour window.
- **Direct GraphQL API, not MCP, for monday.com.** monday.com's MCP server
  wraps the same API but adds an extra process/hosting dependency to a
  deployment that needs to stay one simple link. A thin `monday_client.py`
  (read-only: boards, columns, paginated items — no mutations anywhere in
  the codebase) gives direct control over pagination, retries, and error
  surfacing with fewer moving parts. MCP would pay off for open-ended,
  evolving actions; scope here is fixed and read-only against two boards.
- **Google Gemini** for the LLM, via native function-calling — see
  Trade-offs for why this changed mid-build. Tool specs are plain JSON
  Schema with a provider-agnostic `dispatch()`, so only `agent.py` knows
  which LLM API is in use.
- **Streamlit + Streamlit Community Cloud** — fastest path to a hosted,
  testable-without-local-setup UI, free indefinitely for a public app.
- **pandas**, via a dedicated `aggregate_board` tool — sums/averages/
  counts/group-bys are computed in Python, never estimated by the LLM.

## Key assumptions

- **No prior monday.com account existed**; account/board-import/token setup
  is a one-time human prerequisite (README), not agent runtime behavior,
  which stays strictly read-only.
- **Board schema is not fixed.** CSV shape can't be hardcoded, and
  monday.com's type auto-detection varies run to run — so the agent
  discovers columns at query time (`get_board_schema`) instead. This shaped
  the design more than anything else.
- **A shared identifier is the join key** between boards, not a formal
  foreign key. In the real data it's a masked deal codename (e.g. "Nezuko")
  appearing as `Deal Name` on Deals and `Deal name masked` on Work Orders —
  different titles, ~90% overlap. Since column names aren't hardcoded, the
  agent is told to find and match whatever shared identifier exists rather
  than assume a fixed pair.
- **Relative time ("this quarter") is ambiguous** without a fiscal
  calendar. The agent asks only when the ambiguity would change the
  answer, otherwise states its assumption.
- **Missing data is signal, not zero** — a blank value contributes `None`
  to aggregates, not `0`; the agent reports what fraction of a column was
  usable rather than silently dropping rows into a misleading total.

## Trade-offs chosen, and why

- **Fetch-and-reason over a query DSL.** Two tool shapes — cleaned rows
  (`get_board_data`) for exploration, pandas-backed `aggregate_board` for
  anything numeric — cover realistic founder questions without hand-rolling
  a query planner in the time available.
- **Row cap (`MAX_ROWS_PER_BOARD`, default 1000)** protects context size and
  API complexity; surfaced as `truncated: true` rather than silently
  dropping rows.
- **Type-aware cleaning driven by monday.com's own column `type`**
  (date/numeric/status) is more reliable than sniffing values from scratch,
  at the cost of depending on types being set reasonably on import.
- **LLM swapped Claude → Gemini mid-build, then re-pinned twice.** The
  provided Claude key had no billing credit, and OpenAI has no persistent
  free API tier either — Google AI Studio's free key was the only option
  keeping the hosted link testable without an evaluator funding an
  account. That free tier proved to be a moving target: `gemini-2.5-flash`
  was deprecated for new users mid-build; its replacement
  `gemini-3.6-flash` capped at 20 requests/*day* — easily blown by one
  multi-step question costing 5-8 calls. Settled on
  `gemini-flash-lite-latest` (lighter tier, larger daily allowance,
  "-latest" so a future deprecation doesn't 404 the app again), plus a
  system-prompt rule to batch same-column comparisons via `group_by`
  instead of looping calls. The single most time-consuming issue in the
  build — free-tier LLM APIs are a moving target, not a decision made once.
- **In-process 120s cache per board, not a database** — fine for a
  single-evaluator prototype, would need to be per-session for concurrent
  users.
- **One clarifying question, sparingly** — founders want a fast, caveated
  answer more than a Q&A round trip.

## How I interpreted "leadership updates"

The agent turns live board data into a short, exec-ready brief a founder
could paste into a weekly update — not a scheduling/email-sending system
(out of scope for a read-only, 6-hour prototype). `leadership_update.py`
reuses the same agent/tool loop with a structured prompt (headline,
pipeline snapshot, operations snapshot, watch items, data-quality
caveats), surfaced as a one-click sidebar action producing a downloadable
`.md`. Reusing the existing loop means the update inherits the same exact-
arithmetic, caveat-honest behavior as chat, instead of a second code path
that could drift from it.

## What I'd do differently with more time

- **A first-class cross-board join tool** (server-side fuzzy-match with a
  similarity threshold and confidence score) instead of leaving it to the
  LLM — more reliable at scale.
- **A shared time-range/fiscal-calendar utility** instead of leaning on the
  LLM's own date reasoning plus a clarifying question.
- **Data-quality trends over time** (e.g. "missing close dates went from 8%
  to 15%") — currently computed per-query only, no history.
- **An automated eval set** of canned questions with known-correct answers
  against a fixed seed board, run in CI — current tests cover cleaning/
  tool-dispatch but not end-to-end agent correctness (needs a live
  monday.com board + API key, unavailable in CI here).
