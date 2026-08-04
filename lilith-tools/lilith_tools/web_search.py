"""Tool de busqueda web usando DuckDuckGo HTML (sin API key)."""

import re
import urllib.parse
import urllib.request

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@ToolRegistry.register
class WebSearchTool(BaseTool):
    """Web search tool using DuckDuckGo HTML (no API key required).

    Scrapes the DuckDuckGo HTML endpoint, extracts result links and
    titles, and returns up to ``max_results`` matches.
    """

    name = "web_search"
    description = (
        "Busca en la web usando DuckDuckGo. Parametros: query (str), max_results (int, default 5)"
    )
    parameters = {
        "query": {"type": "string", "description": "Termino de busqueda"},
        "max_results": {
            "type": "integer",
            "description": "Maximo resultados",
            "default": 5,
        },
    }

    def execute(self, query: str = "", max_results: int = 5) -> ToolResult:
        """Busca en la web usando DuckDuckGo HTML."""
        if not query:
            return ToolResult(success=False, data=None, error="Query vacia")
        try:
            url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            results = []
            for m in re.finditer(
                r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>',
                html,
            ):
                if len(results) >= max_results:
                    break
                href = m.group(1)
                title = re.sub(r"<[^>]+>", "", m.group(2))
                results.append({"title": title, "url": href})

            return ToolResult(
                success=True,
                data={"query": query, "results": results, "count": len(results)},
            )
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))
