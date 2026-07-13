"""Tests for LilithClient — HTTP client for the Lilith Agent API."""

from unittest.mock import MagicMock, patch

from lilith_cli.client import DEFAULT_URL, LilithClient


class TestLilithClient:
    """Test LilithClient instantiation and method signatures."""

    def test_default_base_url(self):
        """Client should default to localhost:8000."""
        client = LilithClient()
        assert client.base_url == DEFAULT_URL

    def test_custom_base_url(self):
        """Client should accept a custom base URL."""
        client = LilithClient(base_url="http://custom:9000")
        assert client.base_url == "http://custom:9000"

    def test_trailing_slash_stripped(self):
        """Client should strip trailing slash from base URL."""
        client = LilithClient(base_url="http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"

    @patch("lilith_cli.client.httpx.Client")
    def test_health_calls_get(self, mock_client_cls):
        """health() should GET /health."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        result = client.health()
        mock_client.get.assert_called_once_with("http://localhost:8000/health")
        assert result == {"status": "ok"}

    @patch("lilith_cli.client.httpx.Client")
    def test_chat_sends_message(self, mock_client_cls):
        """chat() should POST /chat with the message."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Hello"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        result = client.chat("Hola")
        mock_client.post.assert_called_once_with(
            "http://localhost:8000/chat",
            json={"message": "Hola"},
        )
        assert result == {"response": "Hello"}

    @patch("lilith_cli.client.httpx.Client")
    def test_chat_with_model(self, mock_client_cls):
        """chat() should include model when provided."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "test"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        client.chat("test", model="gpt-4")
        mock_client.post.assert_called_once_with(
            "http://localhost:8000/chat",
            json={"message": "test", "model": "gpt-4"},
        )

    @patch("lilith_cli.client.httpx.Client")
    def test_list_tools(self, mock_client_cls):
        """list_tools() should GET /tools."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"system_info": "System information"}
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        result = client.list_tools()
        mock_client.get.assert_called_once_with("http://localhost:8000/tools")
        assert "system_info" in result

    @patch("lilith_cli.client.httpx.Client")
    def test_memory_recall(self, mock_client_cls):
        """memory_recall() should GET /memory with query params."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [{"text": "memory"}]
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        result = client.memory_recall("test", k=3)
        mock_client.get.assert_called_once_with(
            "http://localhost:8000/memory",
            params={"query": "test", "k": 3},
        )
        assert isinstance(result, list)

    @patch("lilith_cli.client.httpx.Client")
    def test_memory_store(self, mock_client_cls):
        """memory_store() should POST /memory with text and metadata."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "stored"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        result = client.memory_store("Recuerdo", metadata={"source": "test"})
        mock_client.post.assert_called_once_with(
            "http://localhost:8000/memory",
            json={"text": "Recuerdo", "metadata": {"source": "test"}},
        )
        assert result["status"] == "stored"

    @patch("lilith_cli.client.httpx.Client")
    def test_memory_store_no_metadata(self, mock_client_cls):
        """memory_store() should use empty dict when metadata is None."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "stored"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        client.memory_store("Test")
        mock_client.post.assert_called_once_with(
            "http://localhost:8000/memory",
            json={"text": "Test", "metadata": {}},
        )

    @patch("lilith_cli.client.httpx.Client")
    def test_execute_tool(self, mock_client_cls):
        """execute_tool() should POST /tools/execute."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "success"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = LilithClient()
        client.execute_tool("system_info", {})
        mock_client.post.assert_called_once_with(
            "http://localhost:8000/tools/execute",
            json={"tool": "system_info", "params": {}},
        )
