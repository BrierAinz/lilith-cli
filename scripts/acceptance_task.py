"""Bounded real-provider acceptance task on the isolated website worktree."""

import argparse
from pathlib import Path

from lilith_cli import config as config_module, repl
from lilith_cli.work_session import task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--resume")
    args = parser.parse_args()
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    repl._CONVERSATIONS_DIR = evidence / "conversations"
    cfg = config_module.load_config()
    cfg.memory.enabled = False
    cfg.system_prompt = "You are Lilith. Implement the requested scoped change using file tools. Return concise evidence, never claim tests ran unless a tool ran them. No delegation."
    cfg.retry_max = 0
    cfg.max_tokens = 5000
    for profile in cfg.providers.values():
        profile.max_tokens = 5000
        profile.thinking_enabled = False
    config_module.load_config = lambda *a, **kw: cfg.model_copy(deep=True)
    task(
        ("Continue from the saved tool results. Do not repeat completed writes. Inspect only if needed. " if args.resume else "") +
        "Read search.js. Fix the legacy SiteSearch.search function so surrounding whitespace is trimmed before matching; whitespace-only queries return []; an explicit limit of 0 returns [] instead of the default 8. Preserve the default limit and existing case-insensitive ranking. "
        "Add tests/search-behavior.test.mjs using node:test and node:vm to evaluate search.js with a window object. Cover whitespace equivalence, whitespace-only, case insensitivity, explicit zero, positive limit, and default limit. Only edit search.js and tests/search-behavior.test.mjs. Do not touch any other file. You have file_read and file_write only; external verification will run the tests after you finish. Implement, do not just propose.",
        root=args.root, resume=args.resume,
        verify="node --test tests/search-behavior.test.mjs" if args.resume else None,
        pause_after_tools=0 if args.resume else 2,
        max_iterations=10, allow_tools="file_read,file_write",
        allowed_files="search.js,tests/search-behavior.test.mjs", yes=True,
    )


if __name__ == "__main__":
    main()
