"""Two-process recovery smoke using a real file tool, without model calls."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from lilith_cli import repl
from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.providers import ToolCall
from lilith_cli.work_session import checkpoint, restore_session


async def run(root: Path, resume: bool):
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    os.environ["YGGDRASIL_PROVIDER_HEALTH_DB"] = str(root / "health.sqlite3")
    repl._CONVERSATIONS_DIR = root / "conversations"
    cfg = YggdrasilConfig(provider="local", memory={"enabled": False}, confirm_write=False)
    session = AgentSession(cfg)
    session._project_root = str(root)
    session._progress_enabled = True
    target = root / "proof.txt"
    call = ToolCall("write-proof", "file_write", {"path": str(target), "content": "durable checkpoint"})
    if not resume:
        if target.exists():
            raise RuntimeError("Use a fresh destination for the smoke")
        session.history = [{"role": "user", "content": "Write proof.txt once"}]
        result = await session.execute_tool(call)
        assert not result.content.startswith("Error"), result.content
        session.history.append(result.to_openai_message())
        session._run_progress = {"status": "paused", "pending": []}
        checkpoint(session)
        print("WRITE_CHECKPOINT_OK")
    else:
        saved_path = next((root / "conversations").glob("conv_*.json"))
        data = json.loads(saved_path.read_text(encoding="utf-8"))
        restore_session(session, data, root, replay_guard=True)
        before = target.stat().st_mtime_ns
        result = await session.execute_tool(call)
        assert "no se repitió" in result.content, result.content
        assert target.stat().st_mtime_ns == before
        assert target.read_text(encoding="utf-8") == "durable checkpoint"
        print("RESTART_NO_REPLAY_OK")
    await session.provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.root.resolve(), args.resume))
