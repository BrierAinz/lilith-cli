"""Bounded Windows transport check; no agents, providers or files are invoked."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import pytest
from lilith_tools.cli_delegate import _output_text, _powershell, _recovery_context


def test_recovery_context_extracts_vor_job_header():
    context = _recovery_context(
        "Vor",
        "=== Vor job 20260912-000000-1234  (synthetic)  ===\n",
    )
    assert context["job_id"] == "20260912-000000-1234"
    assert context["job_id_source"] == "launcher_stdout_unverified"
    assert context["inspection_tool"] == "cli_job_inspect"
    assert context["retry_safe"] is False


@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="Windows PowerShell transport check",
)
def test_real_powershell_timeout_is_bounded_and_fail_closed():
    started = time.monotonic()
    # No child process is created by this script. It intentionally does not test
    # cancellation of a real launcher/worker tree or access any existing job.
    #
    # subprocess.run(..., timeout=...) does not guarantee that partial redirected
    # stdout is preserved in TimeoutExpired on Windows, even after an explicit
    # child-side flush. Recovery identity is therefore an optional observation
    # hint on timeout, not a safety requirement.
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _powershell(
            [
                "-NonInteractive",
                "-Command",
                (
                    "[Console]::Out.WriteLine('=== Vor job 20260912-000000-1234  (synthetic)  ==='); "
                    "[Console]::Out.Flush(); "
                    "Start-Sleep -Seconds 10"
                ),
            ],
            timeout=3,
        )

    output = _output_text(caught.value.output)
    context = _recovery_context("Vor", output)
    assert context["retry_safe"] is False
    if context["job_id"] is not None:
        assert context["job_id"] == "20260912-000000-1234"
        assert context["job_id_source"] == "launcher_stdout_unverified"
        assert context["inspection_tool"] == "cli_job_inspect"
    assert time.monotonic() - started < 9
