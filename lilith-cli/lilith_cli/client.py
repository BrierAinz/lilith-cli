"""Cliente HTTP para Lilith API."""

from typing import Any

import httpx


DEFAULT_URL = "http://localhost:8000"


class LilithClient:
    """Synchronous HTTP client for the Lilith Agent API."""

    def __init__(self, base_url: str = DEFAULT_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def health(self) -> dict[str, Any]:
        """Check API health status."""
        r = self.client.get(f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()

    def chat(self, message: str, model: str | None = None) -> dict[str, Any]:
        """Send a chat message and return the agent response."""
        payload = {"message": message}
        if model:
            payload["model"] = model
        r = self.client.post(f"{self.base_url}/chat", json=payload)
        r.raise_for_status()
        return r.json()

    def list_tools(self) -> dict[str, str]:
        """List available tools and their descriptions."""
        r = self.client.get(f"{self.base_url}/tools")
        r.raise_for_status()
        return r.json()

    def execute_tool(self, tool: str, params: dict[str, Any]) -> Any:
        """Execute a tool with the given parameters."""
        r = self.client.post(
            f"{self.base_url}/tools/execute",
            json={"tool": tool, "params": params},
        )
        r.raise_for_status()
        return r.json()

    def memory_recall(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Search memory for relevant entries matching the query."""
        r = self.client.get(f"{self.base_url}/memory", params={"query": query, "k": k})
        r.raise_for_status()
        return r.json()

    def memory_store(self, text: str, metadata: dict | None = None) -> dict[str, Any]:
        """Store a new entry in memory with optional metadata."""
        payload = {"text": text, "metadata": metadata or {}}
        r = self.client.post(f"{self.base_url}/memory", json=payload)
        r.raise_for_status()
        return r.json()
