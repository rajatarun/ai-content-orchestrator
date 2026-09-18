"""The BAU draft prompt is where "stale" is decided.

`gemini_generate_json` is called with ``use_search=True``, so the search tool
is available on every call. Availability is not use: a prompt that never says
what day it is and never asks for recent evidence gives the model no reason to
search, and it answers from training data instead. That failure is invisible --
the drafts arrive, they read fluently, and they describe a world some months
old.

So these are the properties that make the search tool actually get used. None
of them needs AWS, a Gemini key, or a deployed stack.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# automation.py builds its boto3 clients at import time, which botocore refuses
# to do without a region. Nothing here calls AWS -- this only lets the module
# import outside Lambda, where the region comes from the execution environment.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import automation  # noqa: E402


def _prompt() -> dict:
    return automation._build_bau_prompt("Agentic CI pipelines", "Show measured impact")


def _all_instructions() -> str:
    return " ".join(_prompt()["instructions"]).lower()


def test_prompt_states_todays_date():
    today = time.strftime("%Y-%m-%d", time.gmtime())
    prompt = _prompt()
    assert prompt["today"] == today
    assert today in prompt["instructions"][0]


def test_prompt_tells_the_model_to_search_before_writing():
    assert "search" in _all_instructions()


def test_prompt_states_a_freshness_window_in_days():
    window = automation.FRESHNESS_WINDOW_DAYS
    assert isinstance(window, int) and window > 0
    assert _prompt()["context"]["freshness_window_days"] == window


def test_prompt_both_prefers_recent_sources_and_drops_stale_claims():
    # Two separate rules, and asserting the window "appears somewhere" would
    # pass with either one deleted. Preferring recent sources still lets an
    # old claim through when nothing newer is found; the second rule is what
    # makes the model drop it instead of presenting it as current.
    window = str(automation.FRESHNESS_WINDOW_DAYS)
    instructions = [i.lower() for i in _prompt()["instructions"]]

    prefer = [i for i in instructions if "prefer" in i and window in i]
    drop = [i for i in instructions if "older than" in i and window in i]

    assert prefer, "no instruction preferring sources inside the freshness window"
    assert drop, "no instruction dropping claims whose newest source is outside it"


def test_prompt_forbids_relative_dates():
    # "Recently" is true on the day it is written and false by the time the
    # post is approved and published a week later.
    instructions = _all_instructions()
    assert "relative date" in instructions or "'recently'" in instructions


def test_prompt_asks_for_sources():
    assert "sources" in _prompt()["schema"]["drafts"][0]


def test_topic_and_objective_reach_the_model():
    context = _prompt()["context"]
    assert context["topic"] == "Agentic CI pipelines"
    assert context["objective"] == "Show measured impact"


def test_normalize_drafts_keeps_the_sources():
    # The normaliser drops every key it does not name, so asking the model for
    # sources is only half of it -- without this they never reach the article
    # and the approver has nothing to check the claims against.
    drafts = automation._normalize_drafts(
        {
            "drafts": [
                {
                    "linkedin_post": "A post about agentic CI.",
                    "sources": ["2026-09-01 - Example - Headline - https://example.com"],
                }
            ]
        }
    )
    assert len(drafts) == 1
    assert drafts[0]["sources"] == ["2026-09-01 - Example - Headline - https://example.com"]


def test_normalize_drafts_defaults_sources_to_a_list():
    # A model that omits the field, or returns a bare string, must not leave a
    # non-list in the article item that a consumer then iterates over.
    for payload in ({"linkedin_post": "x"}, {"linkedin_post": "x", "sources": "not a list"}):
        drafts = automation._normalize_drafts({"drafts": [payload]})
        assert drafts[0]["sources"] == []
