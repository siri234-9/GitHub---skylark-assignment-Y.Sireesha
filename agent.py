"""Gemini tool-use loop that turns founder-level questions into monday.com
queries and grounded, caveated answers.

Conversation history is kept in a simple, provider-agnostic shape --
`[{"role": "user"|"assistant", "content": "..."}]` -- for storage in
Streamlit session state. Internally this module converts that into Gemini's
Content/Part format and drives the tool-calling round trips; only the final
text answer is persisted back into the simple history (intermediate
function_call/function_response turns are transient, scoped to one turn).
"""
from __future__ import annotations

import json
import re
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from . import config
from .tools import TOOL_SPECS, dispatch

SYSTEM_PROMPT_TEMPLATE = """You are the Business Intelligence agent for Skylark Drones' \
founders and executives. You answer questions about the company's sales \
pipeline (Deals board) and project execution (Work Orders board) by querying \
monday.com live -- never invent numbers, and never rely on memory of a prior \
answer when a fresh tool call would confirm it.

The Deals board is configured as "{deals_board}" and the Work Orders board \
as "{work_orders_board}" -- use these names directly with the tools below \
rather than guessing or calling list_boards first, unless a tool call tells \
you one of these names doesn't resolve.

Ground rules:
1. Column names and types are NOT hardcoded -- get_board_data's response \
   already includes the board's columns, so call get_board_data directly \
   rather than calling get_board_schema first as a separate step (only use \
   get_board_schema on its own if you specifically want structure without \
   fetching rows). Never assume a column exists without having seen it in a \
   tool result first.
2. For any sum, average, count, or group-by, use aggregate_board so the \
   arithmetic is exact -- do not hand-compute totals from a printed list of \
   rows. If you already know the columns from a prior call in this \
   conversation, you may call aggregate_board directly. Minimize tool \
   calls: when comparing several values of the same column (e.g. deal count \
   by status, revenue by sector), make ONE aggregate_board call with \
   group_by set, instead of one call per value -- API calls are quota- \
   limited, so batch via group_by/filters rather than looping calls.
3. The underlying data is real-world messy: missing values, inconsistent \
   date formats, inconsistent capitalization of sectors/stages/statuses, \
   and even stray cells where the column header text leaked in as a bogus \
   value (already normalized to "missing" upstream). Tool results include \
   data-quality info (missing/unparseable percent per column). When a \
   meaningful share of the relevant column is missing or unparseable (rule \
   of thumb: >10-15%), say so explicitly and frame the answer as \
   directional rather than exact, e.g. "based on the ~80% of deals with a \
   recorded close date...".
4. If a question is genuinely ambiguous in a way that would change the \
   answer (e.g. "this quarter" when today's date and the company's fiscal \
   calendar aren't given, or "pipeline" meaning open deals only vs. all \
   deals), ask ONE concise clarifying question before running numbers -- \
   don't ask about things you can just look up yourself via tools. Default \
   to sensible assumptions and state them rather than asking when the \
   ambiguity is minor.
5. Deals and Work Orders are separate boards. When a question spans both \
   (e.g. "which deals converted into delayed projects"), fetch both and \
   cross-reference using whatever shared identifier exists -- a masked \
   deal name/code, client code, or sector -- normalizing casing/whitespace \
   before matching, since names won't match exactly across boards.
6. Answer like a sharp analyst briefing a founder: lead with the headline \
   number/insight, then 2-4 sentences of context (trend, biggest driver, \
   notable risk), then caveats if any. Not a wall of text, not just a raw \
   number.
7. If a tool call errors (e.g. monday.com is unreachable, a board/column \
   doesn't exist), tell the user plainly what failed and what you'd need to \
   proceed -- don't silently guess.
"""


def _client() -> genai.Client:
    if not config.GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured.")
    return genai.Client(api_key=config.GOOGLE_API_KEY)


def _system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        deals_board=config.MONDAY_DEALS_BOARD,
        work_orders_board=config.MONDAY_WORK_ORDERS_BOARD,
    )


_RETRY_DELAY_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _generate_with_retry(client, **kwargs):
    """Gemini's free tier has a low requests-per-minute quota, and a single
    question can take several tool round trips -- easily enough to hit it.
    Retry 429s a few times, honoring the server's suggested retry delay when
    present, rather than failing the whole turn on a transient rate limit."""
    last_err = None
    for attempt in range(4):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) != 429:
                raise
            last_err = e
            delay = 5.0 * (attempt + 1)
            match = _RETRY_DELAY_RE.search(str(e))
            if match:
                delay = float(match.group(1)) + 1.0
            time.sleep(delay)
    raise last_err


def _to_gemini_schema(schema: dict) -> dict:
    """Convert a JSON-Schema-style tool spec (lowercase types, as used in
    tools.py) into the OpenAPI-subset shape Gemini's function declarations
    expect (uppercase type names)."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


def _build_tools() -> list[types.Tool]:
    declarations = [
        types.FunctionDeclaration(
            name=spec["name"],
            description=spec["description"],
            parameters=_to_gemini_schema(spec["input_schema"]) or None,
        )
        for spec in TOOL_SPECS
    ]
    return [types.Tool(function_declarations=declarations)]


def _history_to_contents(history: list[dict]) -> list[types.Content]:
    role_map = {"user": "user", "assistant": "model"}
    return [
        types.Content(role=role_map.get(m["role"], "user"), parts=[types.Part(text=m["content"])])
        for m in history
        if m.get("content")
    ]


def run_turn(conversation: list[dict], on_tool_call=None) -> tuple[str, list[dict]]:
    """Run one user turn to completion (including any tool-use round trips).

    `conversation` is the simple history described above, already including
    the latest user message. Returns (final_text, updated_conversation)
    where updated_conversation has the assistant's reply appended.

    `on_tool_call(name, input, result)` is an optional callback fired after
    each tool execution, useful for showing a "thinking" trace in the UI.
    """
    client = _client()
    gen_config = types.GenerateContentConfig(
        system_instruction=_system_prompt(),
        tools=_build_tools(),
    )

    contents = _history_to_contents(conversation)

    for _ in range(8):  # hard cap on tool round-trips per turn
        response = _generate_with_retry(
            client, model=config.GEMINI_MODEL, contents=contents, config=gen_config
        )

        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if (candidate and candidate.content) else []
        function_calls = [p for p in parts if getattr(p, "function_call", None)]

        if not function_calls:
            final_text = "".join(p.text for p in parts if getattr(p, "text", None)) or (
                "I couldn't produce an answer -- try rephrasing the question."
            )
            updated = conversation + [{"role": "assistant", "content": final_text}]
            return final_text, updated

        # Model's turn (including the function call parts) must be echoed
        # back before the function_response turn, per Gemini's protocol.
        contents.append(candidate.content)

        response_parts = []
        for p in function_calls:
            name = p.function_call.name
            args = dict(p.function_call.args or {})
            result_json = dispatch(name, args)
            if on_tool_call:
                on_tool_call(name, args, result_json)
            try:
                parsed = json.loads(result_json)
            except (TypeError, ValueError):
                parsed = {"raw": result_json}
            if not isinstance(parsed, dict):
                parsed = {"result": parsed}
            response_parts.append(
                types.Part.from_function_response(name=name, response=parsed)
            )
        contents.append(types.Content(role="user", parts=response_parts))

    final_text = (
        "I had to stop after several data lookups without reaching a final answer -- "
        "try narrowing the question."
    )
    updated = conversation + [{"role": "assistant", "content": final_text}]
    return final_text, updated
