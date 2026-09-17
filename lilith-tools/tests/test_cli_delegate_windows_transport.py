"""Bounded Windows transport check; no agents, providers or files are invoked."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import pytest
from lilith_tools.cli_delegate import _output_text, _powershell, _recovery_context


@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="Windows PowerShell transport check",
)
def test_real_powershell_timeout_retains_job_header():
    started = time.monotonic()
    # No child process is created by this script. It intentionally does not test
    # cancellation of a real launcher/worker tree or access any existing job.
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _powershell(
            [
                "-NonInteractive",
                "-Command",
                (
                    "Write-Output '=== Vor job 20260912-000000-1234  (synthetic)  ==='; "
                    "Start-Sleep -Seconds 10"
                ),
            ],
            timeout=3,
        )
    output = _output_text(caught.value.output)
    assert _recovery_context("Vor", output)["job_id"] == "20260912-000000-1234"
    assert time.monotonic() - started < 9
