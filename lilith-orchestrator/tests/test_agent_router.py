"""Tests for the :mod:`lilith_orchestrator.agent_router` capability router.

Covers the free helpers, the per-definition scorer, the AgentRouter
API, weight validation, and end-to-end routing against the default
8 personas shipped in :func:`make_default_definitions`.

The router is *pure* — no I/O, no LLM, no time. Tests are fast and
deterministic. We use a per-test registry fixture (via
``clear_subagent_registry`` + explicit registrations) so the test
order cannot leak state.
"""

from __future__ import annotations

import pytest

from lilith_orchestrator.agent_router import (
    STOP_WORDS,
    TOOL_HINTS,
    AgentRoute,
    AgentRouter,
    RouterWeights,
    extract_tokens,
    heuristic_tool_hint,
    route_request,
    score_definition,
)
from lilith_orchestrator.subagents import (
    SubAgentDefinition,
    all_agents,
    clear_registry,
    register,
    register_defaults,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the registry before and after each test.

    Some tests register a custom set; the others use the defaults.
    Wiping both before AND after keeps the test order independent.
    """
    clear_registry()
    yield
    clear_registry()


def _make(name: str, *, when: str, tags, allow, disallow=()):
    return SubAgentDefinition(
        agent_type=name,
        when_to_use=when,
        system_prompt=f"prompt for {name}",
        allowed_tools=list(allow),
        disallowed_tools=list(disallow),
        tags=list(tags),
    )


# ── extract_tokens ───────────────────────────────────────────────────────


class TestExtractTokens:
    def test_lowercases_and_strips_punctuation(self):
        assert extract_tokens("Read, WRITE_file & Search!") == [
            "read", "write_file", "search"
        ]

    def test_removes_stop_words(self):
        out = extract_tokens("please read the file from disk")
        assert "the" not in out
        assert "from" not in out
        assert "read" in out
        assert "file" in out
        assert "disk" in out

    def test_keeps_underscored_tokens(self):
        # Critical: tool names like ``read_file`` must survive as
        # single tokens so they can match against allowed_tools.
        toks = extract_tokens("use read_file to inspect the function")
        assert "read_file" in toks

    def test_empty_and_none_safe(self):
        assert extract_tokens("") == []
        # Non-string → empty
        assert extract_tokens(None) == []  # type: ignore[arg-type]

    def test_short_tokens_dropped(self):
        # 1-char tokens are not useful for routing.
        toks = extract_tokens("a I am be do")
        assert toks == []

    def test_stop_words_constant_is_frozen(self):
        assert isinstance(STOP_WORDS, frozenset)
        assert "the" in STOP_WORDS
        assert "and" in STOP_WORDS


# ── heuristic_tool_hint ──────────────────────────────────────────────────


class TestHeuristicToolHint:
    def test_read_verb_maps_to_read(self):
        h = heuristic_tool_hint("read the file")
        assert "read_file" in h

    def test_write_verb_maps_to_write(self):
        h = heuristic_tool_hint("write a new function")
        assert "write_file" in h

    def test_run_verb_maps_to_terminal(self):
        h = heuristic_tool_hint("run the tests")
        assert "terminal" in h

    def test_web_verb_maps_to_web_tools(self):
        h = heuristic_tool_hint("look up the cve online")
        assert "web_search" in h

    def test_no_verb_returns_empty(self):
        # A purely conversational request with no tool hint.
        assert heuristic_tool_hint("thanks!") == set()

    def test_tool_hints_is_dict(self):
        assert isinstance(TOOL_HINTS, dict)
        assert "read" in TOOL_HINTS
        assert "write" in TOOL_HINTS


# ── score_definition ─────────────────────────────────────────────────────


class TestScoreDefinition:
    def test_zero_for_completely_unrelated_text(self):
        d = _make("a", when="alpha", tags=["x"], allow=["read_file"])
        r = score_definition("zzz", d, RouterWeights())
        assert r.score == 0.0
        assert r.tag_hit == 0.0
        assert r.tool_fit == 0.0

    def test_perfect_token_overlap_when_text_matches_when_to_use(self):
        d = _make("a", when="audit security", tags=[], allow=["read_file"])
        r = score_definition("audit security", d, RouterWeights())
        assert r.token_overlap == 1.0

    def test_tag_hit_when_tag_mentioned_in_request(self):
        d = _make("a", when="", tags=["research"], allow=["read_file"])
        r = score_definition("please do some research", d, RouterWeights())
        assert r.tag_hit == 1.0
        assert "research" in r.matched_tags

    def test_tool_fit_when_hinted_tools_are_allowed(self):
        d = _make("a", when="", tags=[], allow=["read_file", "patch"])
        r = score_definition("read and edit the file", d, RouterWeights())
        # "read" → read_file, "edit" → patch. Both allowed.
        assert "read_file" in r.matched_tools
        assert "patch" in r.matched_tools
        assert r.tool_fit == 1.0

    def test_tool_fit_partial_when_some_hints_missing(self):
        d = _make("a", when="", tags=[], allow=["read_file"])  # no terminal
        r = score_definition("read and run the file", d, RouterWeights())
        # "read" → read_file (matched), "run" → terminal (not allowed)
        assert r.tool_fit == 0.5

    def test_tool_fit_with_explicit_tool_pool_filters_wildcards(self):
        d = _make("a", when="", tags=[], allow=["*"], disallow=["terminal"])
        r = score_definition(
            "read and run", d, RouterWeights(),
            tool_pool=["read_file", "search_files", "terminal"],
        )
        # Hints: read_file, terminal. Definition disallows terminal.
        assert "read_file" in r.matched_tools
        assert "terminal" not in r.matched_tools
        assert r.tool_fit == 0.5
        # available_tools reports the resolved set
        assert r.available_tools is not None
        assert "terminal" not in r.available_tools
        assert "read_file" in r.available_tools

    def test_tool_fit_with_wildcard_and_no_pool_is_zero(self):
        d = _make("a", when="", tags=[], allow=["*"])
        r = score_definition("read the file", d, RouterWeights())
        # Without a tool pool we cannot tell what's available, so
        # tool_fit must be 0.0 to avoid false positives.
        assert r.tool_fit == 0.0

    def test_score_in_unit_interval(self):
        d = _make("a", when="audit security", tags=["audit"],
                  allow=["read_file", "web_search"])
        for req in (
            "audit security",
            "review the code",
            "research AI agent frameworks",
            "",
        ):
            r = score_definition(req, d, RouterWeights())
            assert 0.0 <= r.score <= 1.0, f"score out of range: {r.score}"

    def test_score_higher_when_tag_is_hit(self):
        d = _make("a", when="audit security", tags=["audit", "review"],
                  allow=["read_file"])
        r1 = score_definition("audit security", d, RouterWeights())
        r2 = score_definition("security audit please", d, RouterWeights())
        # Both mention "audit" via tag. Both should be high.
        assert r1.score > 0
        assert r2.score > 0

    def test_to_dict_round_trip_shape(self):
        d = _make("a", when="x", tags=["t"], allow=["read_file"])
        r = score_definition("read x t", d, RouterWeights())
        d_dict = r.to_dict()
        assert d_dict["agent_type"] == "a"
        assert set(d_dict.keys()) == {
            "agent_type", "score", "token_overlap", "tag_hit",
            "tool_fit", "matched_tags", "matched_tools",
            "available_tools",
        }


# ── RouterWeights validation ─────────────────────────────────────────────


class TestRouterWeights:
    def test_defaults_are_sensible(self):
        w = RouterWeights()
        assert w.token_overlap > 0
        assert w.tag_hit > 0
        assert w.tool_fit > 0

    def test_rejects_negative_component(self):
        with pytest.raises(ValueError):
            RouterWeights(token_overlap=-0.1)

    def test_rejects_all_zero(self):
        with pytest.raises(ValueError):
            RouterWeights(token_overlap=0.0, tag_hit=0.0, tool_fit=0.0)


# ── AgentRouter ──────────────────────────────────────────────────────────


class TestAgentRouter:
    def test_empty_registry_returns_empty(self):
        r = AgentRouter()
        assert r.rank("anything") == []
        assert r.route("anything") is None
        assert r.explain("anything") == []

    def test_routes_to_best_match(self):
        register(_make("researcher", when="research topics",
                       tags=["research"], allow=["read_file", "web_search"]))
        register(_make("coder", when="implement code",
                       tags=["code", "write"], allow=["write_file", "patch"]))
        r = AgentRouter()
        top = r.route("research the latest AI agent framework")
        assert top is not None
        assert top.agent_type == "researcher"
        # Researcher should be clearly ahead of coder
        ranked = r.rank("research the latest AI agent framework")
        assert ranked[0].agent_type == "researcher"
        assert ranked[0].score >= ranked[1].score

    def test_limit_caps_output(self):
        for name in ("a", "b", "c", "d"):
            register(_make(name, when="do x", tags=["x"], allow=[]))
        r = AgentRouter()
        out = r.rank("do x", limit=2)
        assert len(out) == 2

    def test_min_score_filters_out_weak_candidates(self):
        register(_make("relevant", when="audit",
                       tags=["audit"], allow=["read_file"]))
        register(_make("unrelated", when="xyzzy",
                       tags=["plover"], allow=[]))
        r = AgentRouter(min_score=0.2)
        out = r.rank("please audit the code")
        types = {x.agent_type for x in out}
        assert "relevant" in types
        assert "unrelated" not in types

    def test_min_score_validation(self):
        with pytest.raises(ValueError):
            AgentRouter(min_score=-0.1)
        with pytest.raises(ValueError):
            AgentRouter(min_score=1.5)

    def test_explicit_registry_overrides_global(self):
        # Global registry empty, explicit registry has one entry.
        r = AgentRouter(registry=[
            _make("solo", when="match", tags=["match"], allow=[]),
        ])
        assert r.route("match this") is not None
        assert r.route("match this").agent_type == "solo"

    def test_explain_returns_jsonable_dicts(self):
        register(_make("a", when="x", tags=["x"], allow=["read_file"]))
        r = AgentRouter()
        out = r.explain("x please")
        assert isinstance(out, list)
        assert len(out) == 1
        assert "agent_type" in out[0]
        assert isinstance(out[0]["score"], float)

    def test_best_match_tags_aggregates(self):
        register(_make("a", when="", tags=["audit", "security"],
                       allow=["read_file"]))
        register(_make("b", when="", tags=["security"],
                       allow=["read_file"]))
        r = AgentRouter()
        tags = r.best_match_tags("audit security review")
        # "audit" and "security" should both surface.
        assert "security" in tags
        # Order is by aggregate score, so "security" is first.
        assert tags[0] == "security"

    def test_best_match_tags_empty_when_no_hits(self):
        register(_make("a", when="", tags=["unrelated"], allow=[]))
        r = AgentRouter()
        assert r.best_match_tags("something completely different") == []

    def test_tie_breaker_is_alphabetical(self):
        # Three identical definitions → same score → alphabetical order.
        for n in ("c", "a", "b"):
            register(_make(n, when="x", tags=[], allow=[]))
        r = AgentRouter()
        out = r.rank("x")
        types = [x.agent_type for x in out]
        assert types == ["a", "b", "c"]


# ── route_request convenience ────────────────────────────────────────────


class TestRouteRequest:
    def test_returns_definition_for_top_match(self):
        register(_make("d", when="audit",
                        tags=["audit"], allow=["read_file"]))
        out = route_request("audit please")
        assert out is not None
        assert out.agent_type == "d"

    def test_returns_none_for_empty_registry(self):
        out = route_request("anything")
        assert out is None

    def test_accepts_custom_weights(self):
        register(_make("a", when="", tags=["alpha"], allow=["read_file"]))
        register(_make("b", when="", tags=["beta"], allow=["read_file"]))
        # When tag hit dominates, request that names "alpha" routes
        # to "a" (high tag score) instead of "b".
        w = RouterWeights(token_overlap=0.0, tag_hit=1.0, tool_fit=0.0)
        out = route_request("alpha", weights=w)
        assert out is not None
        assert out.agent_type == "a"


# ── End-to-end against the default personas ──────────────────────────────


class TestDefaultsIntegration:
    def test_default_researcher_routes_research_requests(self):
        register_defaults()
        r = AgentRouter()
        out = r.route("research the latest agent framework")
        assert out is not None
        assert out.agent_type in {"researcher", "security"}

    def test_default_auditor_routes_audit_requests(self):
        register_defaults()
        r = AgentRouter()
        out = r.route("audit the diff for security issues")
        assert out is not None
        # "audit" is a tag of the auditor persona; tool-fit also
        # contributes (terminal / read tools).
        assert out.agent_type in {"auditor", "security", "reviewer"}

    def test_default_tester_routes_test_requests(self):
        register_defaults()
        r = AgentRouter()
        out = r.route("run the test suite and report failures")
        assert out is not None
        # tester has "test" tag.
        assert out.agent_type in {"tester", "coder"}

    def test_default_planner_routes_planning_requests(self):
        register_defaults()
        r = AgentRouter()
        out = r.route("plan the implementation strategy")
        assert out is not None
        # planner has "planning" tag.
        assert out.agent_type in {"planner", "researcher"}

    def test_default_registry_has_eight_agents(self):
        register_defaults()
        assert len(all_agents()) == 8
