# Web console

`lilith web` serves a local browser view of a workspace: a read-only file
browser and viewer, plus the same agent chat as the REPL. File and git
changes go through the agent and its tool policy; the API refuses direct
mutations and has no terminal.

## Run it

```bash
uv sync --package lilith-cli --extra web
(cd lilith-cli/lilith_cli/web_console/frontend && npm ci && npm run build)
uv run lilith web --root /path/to/project
```

Then open the address it prints (default `http://127.0.0.1:12356/`). Without
the frontend build, the server still exposes the API under `/api`.

For frontend development, run `npm run dev` in the frontend directory: Vite
serves the UI on port 5173 and proxies `/api` to `lilith web`.
`lilith web --dev` reloads the server when Python files change.

## Authentication

- **No token (default):** the server binds to loopback and only answers
  requests whose client, `Host` header and (when present) `Origin` are
  local. This blocks other sites from reaching it through DNS rebinding.
- **With `LILITH_AUTH_TOKEN`:** every API call needs the token. Open
  `http://127.0.0.1:12356/#token=<token>`; the fragment never reaches the
  server and the UI keeps the token in session storage, or asks for it.
  Binding to a non-loopback address requires a token.

The static page itself and `/api/health` need no token.
