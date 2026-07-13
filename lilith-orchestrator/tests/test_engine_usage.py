"""Tests for lilith_orchestrator.engine (EngineUsage, basic engine)."""
import pytest

from lilith_orchestrator.engine import EngineUsage


def test_engine_usage_defaults():
    u = EngineUsage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.total_tokens == 0
    assert u.latency_ms == 0.0
    assert u.agents_used == []


def test_engine_usage_with_values():
    u = EngineUsage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        latency_ms=123.456,
        agents_used=["odin", "mimir"],
    )
    assert u.total_tokens == 150
    assert u.latency_ms == 123.456
    assert u.agents_used == ["odin", "mimir"]


def test_engine_usage_to_dict():
    u = EngineUsage(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        latency_ms=12.345,
        agents_used=["lilith"],
    )
    d = u.to_dict()
    assert d["prompt_tokens"] == 10
    assert d["completion_tokens"] == 20
    assert d["total_tokens"] == 30
    assert d["latency_ms"] == 12.35
    assert d["agents_used"] == ["lilith"]


def test_engine_usage_to_dict_rounds_latency():
    u = EngineUsage(latency_ms=12.3456789)
    d = u.to_dict()
    # Latency rounded to 2 decimal places
    assert d["latency_ms"] == 12.35
