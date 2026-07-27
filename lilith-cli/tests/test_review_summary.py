"""Tests for ``/review --summary`` — diff categorization helper.

The summary mode of ``/review`` exists so a user can triage a large PR
without scrolling through every hunk. It must report:

  * The files touched
  * Added/removed line counts
  * Heuristic risk tags (auth, db, subprocess, eval, TODO, FIXME, secrets)
  * The number of hunks per file

No subprocess or LLM call is involved — the function operates on the
text returned by ``git diff`` (already a string by the time it reaches
the parser). The tests below run purely on in-memory diffs so they
don't depend on git state.
"""

from __future__ import annotations

import pytest

from lilith_cli.extra_commands import _review_summary


# ── Empty / non-diff inputs ───────────────────────────────────────────


def test_summary_handles_empty_diff() -> None:
    """An empty diff produces an empty summary, not a crash."""
    summary = _review_summary("")
    assert summary["files"] == []
    assert summary["total_added"] == 0
    assert summary["total_removed"] == 0
    assert summary["risk_tags"] == []
    assert summary["total_hunks"] == 0


def test_summary_handles_non_diff_garbage() -> None:
    """A string with no unified-diff markers should still return a sane dict."""
    summary = _review_summary("hello world\nno diff markers here\n")
    assert summary["files"] == []
    assert summary["total_added"] == 0
    assert summary["total_removed"] == 0


# ── Single-file diff ──────────────────────────────────────────────────


SINGLE_FILE_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index 1111111..2222222 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,3 +1,5 @@
 def login(user, password):
+    # TODO: add rate limiting
+    token = create_token(user, password)
     return token
-def old_helper():
-    pass
"""

EXPECTED_RISK_SINGLE = {"auth", "todo"}


def test_summary_single_file_counts_lines() -> None:
    summary = _review_summary(SINGLE_FILE_DIFF)
    assert summary["files"] == ["src/auth.py"]
    assert summary["total_added"] == 2
    assert summary["total_removed"] == 2
    assert summary["total_hunks"] == 1
    per_file = summary["per_file"]
    assert len(per_file) == 1
    assert per_file[0]["path"] == "src/auth.py"
    assert per_file[0]["added"] == 2
    assert per_file[0]["removed"] == 2


def test_summary_detects_risk_tags() -> None:
    """Path and content keywords both contribute to the risk-tag set."""
    summary = _review_summary(SINGLE_FILE_DIFF)
    tags = set(summary["risk_tags"])
    # Path component "auth" and content marker "TODO" must both appear.
    assert tags == EXPECTED_RISK_SINGLE


# ── Multi-file diff ───────────────────────────────────────────────────


MULTI_FILE_DIFF = """\
diff --git a/db/migrate.sql b/db/migrate.sql
index aaaaaaa..bbbbbbb 100644
--- a/db/migrate.sql
+++ b/db/migrate.sql
@@ -10,0 +10,4 @@
+ALTER TABLE users ADD COLUMN api_key TEXT;
+-- FIXME: rotate old keys before deploy
+UPDATE users SET api_key = 'placeholder';
+INSERT INTO secrets (name) VALUES ('temp-key');
diff --git a/src/subproc.py b/src/subproc.py
new file mode 100644
index 0000000..ccccccc
--- /dev/null
+++ b/src/subproc.py
@@ -0,0 +1,4 @@
+import subprocess
+def run(cmd):
+    return subprocess.run(cmd, shell=True)
+    # hardcoded secret literal below
"""


def test_summary_multi_file_aggregates_counts() -> None:
    summary = _review_summary(MULTI_FILE_DIFF)
    assert sorted(summary["files"]) == ["db/migrate.sql", "src/subproc.py"]
    # 4 added in db/migrate.sql + 4 added in src/subproc.py = 8
    assert summary["total_added"] == 8
    assert summary["total_removed"] == 0
    assert summary["total_hunks"] == 2


def test_summary_multi_file_risk_tags() -> None:
    """db + secret + FIXME + subprocess + shell all surface in risk_tags."""
    summary = _review_summary(MULTI_FILE_DIFF)
    tags = set(summary["risk_tags"])
    # At least these markers must be present.
    assert {"db", "secret", "fixme", "subprocess", "shell"}.issubset(tags)


def test_summary_risk_tags_are_sorted_unique() -> None:
    """Risk tags must be a sorted list with no duplicates."""
    summary = _review_summary(MULTI_FILE_DIFF)
    tags = summary["risk_tags"]
    assert tags == sorted(tags)
    assert len(tags) == len(set(tags))


# ── File-size cap safety ──────────────────────────────────────────────


def test_summary_ignores_textconv_or_ext_diff_noise() -> None:
    """Lines starting with ``Only in`` or ``Binary files`` must not inflate counts."""
    diff = (
        "diff --git a/foo b/foo\n"
        "Only in foo: bar\n"
        "Binary files a/img.png and b/img.png differ\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    summary = _review_summary(diff)
    assert summary["files"] == ["foo"]
    assert summary["total_added"] == 1
    assert summary["total_removed"] == 1