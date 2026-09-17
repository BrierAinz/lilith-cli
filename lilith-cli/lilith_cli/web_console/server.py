from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import chat, files, git, health, sessions, terminal
from .auth import TokenAuthMiddleware


def create_app(
    workspace: str | None = None,
    auth_token: str | None = None,
    allowed_origins: list[str] | None = None,
    config_path: str | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Lilith Web Console",
        description="Web surface for the canonical Lilith SessionRuntime.",
    )
    workspace_root = Path(workspace or Path.cwd()).expanduser().resolve()
    if not workspace_root.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace_root}")
    app.state.workspace = workspace_root
    app.state.config_path = config_path

    origins = allowed_origins or [
        "http://127.0.0.1:12356",
        "http://localhost:12356",
        "http://[::1]:12356",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        TokenAuthMiddleware,
        token=auth_token,
        allowed_origins=origins,
    )

    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(files.router, prefix="/api/files")
    app.include_router(terminal.router, prefix="/api/terminal")
    app.include_router(git.router, prefix="/api/git")
    app.include_router(sessions.router, prefix="/api/sessions")
    app.include_router(health.router, prefix="/api/health")
    return app
