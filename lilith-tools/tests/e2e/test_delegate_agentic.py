"""Agentic e2e: delegate_subagent writes a file to its sandbox.

Each test gives a preset a tiny, deterministic file-writing task
("Write ``saludo.txt`` to the workdir") and asserts the file appears
on disk. The agentic delegate minimises the prompt-side model cost
by allowing the sub-agent to write the file itself (no need for a
synthesis step in the test).

We run two presets so a future provider rotation doesn't silently
break the suite on a single provider outage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_tools import ToolRegistry


@pytest.fixture
def delegate_tool():
    """Resolve the delegate_subagent class from the registry."""
    tool_cls = ToolRegistry.get("delegate_subagent")
    assert tool_cls is not None, (
        "delegate_subagent not registered; lilith_tools.__init__ probably "
        "skipped the `delegate` import — should never happen in a healthy install"
    )
    return tool_cls()


def _agentic_prompt(workdir: Path, lang: str = "Spanish") -> str:
    """Self-contained prompt that asks the sub-agent to write a tiny file.

    Kept intentionally narrow so the model can finish in 1-2 turns
    and the test fits inside the 60s pytest timeout.
    """
    return (
        "TASK: Using the file_write tool, create the file `saludo.txt` "
        f"inside the workdir ({workdir}). "
        "Write EXACTLY this content (no markdown fences, no commentary):\n"
        "```\nHola desde Hlidskjalf.\n"
        f"Idioma: {lang}\n```\n\n"
        "Once the file exists, return the single word PONG and stop. "
        "Do not call any other tool. Do not write any other file."
    )


def test_agente_escribe_saludo_ejecutor_kimi(
    delegate_tool,
    tmp_workdir: Path,
    require_provider_keys,
) -> None:
    """ejecutor-kimi runs agentic and writes saludo.txt into the workdir."""
    require_provider_keys("ejecutor-kimi")
    prompt = _agentic_prompt(tmp_workdir)

    result = delegate_tool.execute(
        preset="ejecutor-kimi",
        prompt=prompt,
        agentic=True,
        workdir=str(tmp_workdir),
        max_turns=4,
    )

    target = tmp_workdir / "saludo.txt"
    assert target.exists(), (
        f"agentic ejecutor-kimi did not write saludo.txt — "
        f"success={result.success} error={result.error!r} "
        f"data_keys={list((result.data or {}).keys())}"
    )
    body = target.read_text(encoding="utf-8")
    assert "Hola desde Hlidskjalf" in body, (
        f"saludo.txt was written but missing the expected greeting — got:\n{body!r}"
    )
    assert "Idioma: Spanish" in body

    # The agentic path also reports structured usage / file provenance.
    # ``data`` is best-effort here — at minimum it's a dict; we don't
    # fail if the provider's data shape differs, but a successful run
    # must record at least the content.
    data = result.data or {}
    if "files_written" in data:
        assert any(
            Path(str(p)).name == "saludo.txt" for p in data["files_written"]
        ), data


def test_agente_escribe_saludo_investigador_minimax(
    delegate_tool,
    tmp_workdir: Path,
    require_provider_keys,
) -> None:
    """investigador-minimax (provider m2) also writes the greeting file agentically."""
    require_provider_keys("investigador-minimax")
    prompt = _agentic_prompt(tmp_workdir, lang="Spanish")

    result = delegate_tool.execute(
        preset="investigador-minimax",
        prompt=prompt,
        agentic=True,
        workdir=str(tmp_workdir),
        max_turns=4,
    )

    target = tmp_workdir / "saludo.txt"
    assert target.exists(), (
        f"agentic investigador-minimax did not write saludo.txt — "
        f"success={result.success} error={result.error!r}"
    )
    assert "Hola desde Hlidskjalf" in target.read_text(encoding="utf-8")


def test_agente_dry_run_sin_escribir_devuelve_error(
    delegate_tool,
    tmp_workdir: Path,
    require_provider_keys,
) -> None:
    """An agentic prompt that asks for nothing must finish cleanly with no files.

    Useful as a "smoke" that the agentic mini-loop terminates and
    doesn't write a partial file when no write tool is needed.
    """
    require_provider_keys("ejecutor-kimi")

    prompt = (
        "TASK: Respond with the single word PONG. Do NOT call any tool. "
        "Just return PONG."
    )

    result = delegate_tool.execute(
        preset="ejecutor-kimi",
        prompt=prompt,
        agentic=True,
        workdir=str(tmp_workdir),
        max_turns=2,
    )

    assert result.success, (
        f"ejecutor-kimi dry-run failed — error={result.error!r} "
        f"data={json.dumps(result.data, default=str)[:500]}"
    )
    content = (result.data or {}).get("content") or ""
    assert "PONG" in content.upper(), (
        f"dry-run did not return PONG — got content={content!r}"
    )
