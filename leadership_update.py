"""'Prepare a leadership update' -- the optional deliverable.

Interpretation (see DECISION_LOG.md for the full rationale): rather than a
separate bespoke pipeline, this reuses the same agent/tool loop with a
structured prompt that asks for an executive-ready brief: headline metrics,
pipeline health, operational status, risks, and explicit data-quality
caveats. Output is markdown so it can be pasted into an email/doc, and the
UI offers it as a downloadable .md file.
"""
from __future__ import annotations

from .agent import run_turn

LEADERSHIP_UPDATE_PROMPT = """Prepare a leadership update summarizing current \
business status, suitable for pasting into a weekly founder/exec update. \
Pull live data from both the Deals and Work Orders boards. Structure it as \
markdown with these sections:

## Headline
2-3 sentences: the single most important thing leadership should know right now.

## Pipeline Snapshot
Total open pipeline value, deal count by stage, and any notable sector \
concentration or shift. Call out win rate if computable.

## Operations Snapshot
Work order status breakdown (e.g. in progress / completed / delayed), any \
overdue or at-risk projects worth flagging, sector mix of active work.

## Watch Items
Risks, anomalies, or notable data gaps a founder should know about before \
trusting the numbers above at face value.

## Data Quality Caveats
Be explicit and specific: which fields had meaningful missing/unparseable \
data, and how that affects confidence in the numbers above.

Use aggregate_board for every number in this update -- do not estimate. \
Keep it tight: this is a briefing document, not a report, aim for something \
a founder can read in under a minute plus skim the caveats."""


def generate(on_tool_call=None) -> str:
    conversation = [{"role": "user", "content": LEADERSHIP_UPDATE_PROMPT}]
    text, _ = run_turn(conversation, on_tool_call=on_tool_call)
    return text
