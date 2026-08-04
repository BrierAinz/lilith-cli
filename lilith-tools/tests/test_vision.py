"""Tests for vision and screenshot tools."""

import base64
import pytest
from unittest.mock import MagicMock, patch

from lilith_tools.base import ToolResult
from lilith_tools.vision import (
    ScreenshotCaptureTool,
    VisionAnalyzeTool,
    _detect_mime_type,
    _is_url,
    _is_base64,
    _is_file_path,
    _load_image_from_file,
    _load_image_from_url,
)


# ── Helper function tests ────────────────────────────────────────────────────


class TestHelperFunctions:
    """Tests for image loading and detection helpers."""

    def test_detect_mime_type_png(self):
        assert _detect_mime_type("image.png") == "image/png"

    def test_detect_mime_type_jpg(self):
        assert _detect_mime_type("photo.jpg") == "image/jpeg"

    def test_detect_mime_type_jpeg(self):
        assert _detect_mime_type("photo.jpeg") == "image/jpeg"

    def test_detect_mime_type_webp(self):
        assert _detect_mime_type("anim.webp") == "image/webp"

    def test_detect_mime_type_unknown(self):
        assert _detect_mime_type("file.xyz") == "image/png"  # default

    def test_detect_mime_type_no_extension(self):
        assert _detect_mime_type("filename") == "image/png"  # default

    def test_is_url_http(self):
        assert _is_url("http://example.com/image.png") is True

    def test_is_url_https(self):
        assert _is_url("https://example.com/image.png") is True

    def test_is_url_not_url(self):
        assert _is_url("/path/to/file.png") is False
        assert _is_url("C:\\Users\\image.png") is False

    def test_is_base64_data_url(self):
        assert _is_base64("data:image/png;base64,iVBORw0KGgo=") is True

    def test_is_base64_plain_string(self):
        assert _is_base64("hello world") is False

    def test_is_file_path_existing(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("test")
        assert _is_file_path(str(f)) is True

    def test_is_file_path_nonexistent(self):
        assert _is_file_path("/nonexistent/path/to/file.png") is False

    def test_load_image_from_file(self, tmp_path):
        """Test loading an image file as base64."""
        # Create a small fake image file
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        data = _load_image_from_file(str(img_path))
        assert isinstance(data, str)
        # Verify it's valid base64
        decoded = base64.b64decode(data)
        assert decoded.startswith(b"\x89PNG")

    def test_load_image_from_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            _load_image_from_file("/nonexistent/file.png")

    def test_load_image_from_file_too_large(self, tmp_path):
        """Files > 10MB should be rejected."""
        # Create a file just over 10MB
        big_path = tmp_path / "big.png"
        big_path.write_bytes(b"\x00" * (10 * 1024 * 1024 + 1))
        with pytest.raises(ValueError, match="too large"):
            _load_image_from_file(str(big_path))


class TestLoadImageFromUrl:
    """Tests for URL image loading (mocked)."""

    def test_load_image_from_url(self):
        """Test loading an image from a URL (mocked)."""
        fake_image_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_image_data
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            data = _load_image_from_url("https://example.com/image.png")
            assert isinstance(data, str)
            decoded = base64.b64decode(data)
            assert decoded.startswith(b"\x89PNG")

    def test_load_image_from_url_too_large(self):
        """URLs returning > 10MB should be rejected."""
        big_data = b"\x00" * (10 * 1024 * 1024 + 1)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = big_data
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            with pytest.raises(ValueError, match="too large"):
                _load_image_from_url("https://example.com/big.png")


# ── VisionAnalyzeTool tests ──────────────────────────────────────────────────


class TestVisionAnalyzeTool:
    """Tests for the VisionAnalyzeTool."""

    @pytest.fixture
    def tool(self):
        return VisionAnalyzeTool()

    def test_name(self, tool):
        assert tool.name == "vision_analyze"

    def test_no_image_returns_error(self, tool):
        result = tool.execute(image="", question="What is this?")
        assert not result.success
        assert "No image" in result.error

    def test_file_not_found(self, tool, monkeypatch):
        """Nonexistent file path should return an error."""
        monkeypatch.delenv("VISION_ENDPOINT", raising=False)
        result = tool.execute(image="/nonexistent/file.png", question="What?")
        assert not result.success

    def test_no_endpoint_returns_error(self, tool, tmp_path, monkeypatch):
        """If VISION_ENDPOINT is not set, should return error."""
        # Create a small image file
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        # Ensure VISION_ENDPOINT is not set
        monkeypatch.delenv("VISION_ENDPOINT", raising=False)

        result = tool.execute(image=str(img_path), question="What?")
        assert not result.success
        assert "VISION_ENDPOINT" in result.error

    def test_successful_analysis(self, tool, tmp_path, monkeypatch):
        """Test a successful vision analysis with mocked API."""
        # Create a small image file
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        # Set env vars
        monkeypatch.setenv("VISION_ENDPOINT", "https://api.example.com/v1/chat/completions")
        monkeypatch.setenv("VISION_MODEL", "test-vision-model")
        monkeypatch.setenv("VISION_API_KEY", "test-key")

        # Mock the API response
        fake_response = b'{"choices": [{"message": {"content": "A cat sitting on a chair"}}]}'

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = tool.execute(image=str(img_path), question="What animal?")

        assert result.success
        assert result.data["analysis"] == "A cat sitting on a chair"
        assert result.data["question"] == "What animal?"
        assert result.data["mime_type"] == "image/png"

    def test_analysis_with_data_url(self, tool, monkeypatch):
        """Test analysis with a data URL image source."""
        monkeypatch.setenv("VISION_ENDPOINT", "https://api.example.com/v1/chat/completions")
        monkeypatch.setenv("VISION_MODEL", "test-vision")

        # Create a data URL
        img_data = base64.b64encode(b"\x89PNG" + b"\x00" * 50).decode()
        data_url = f"data:image/png;base64,{img_data}"

        fake_response = b'{"choices": [{"message": {"content": "Test image"}}]}'

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = tool.execute(image=data_url, question="Describe")

        assert result.success
        assert result.data["analysis"] == "Test image"

    def test_describe_source(self, tool, tmp_path):
        """Test the _describe_source helper method."""
        assert "url:" in tool._describe_source("https://example.com/image.png")
        assert "data-url" in tool._describe_source("data:image/png;base64,abc")


# ── ScreenshotCaptureTool tests ──────────────────────────────────────────────


class TestScreenshotCaptureTool:
    """Tests for the ScreenshotCaptureTool."""

    @pytest.fixture
    def tool(self):
        return ScreenshotCaptureTool()

    def test_name(self, tool):
        assert tool.name == "screenshot_capture"

    def test_capture_windows_mocked(self, tool, monkeypatch):
        """Test Windows screenshot capture with mocked PIL."""
        # Only run on Windows or when PIL is available
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        # Create a mock image
        mock_img = MagicMock()
        mock_img.save = MagicMock(side_effect=lambda buf, format: buf.write(b"\x89PNG" + b"\x00" * 100))

        with patch("PIL.ImageGrab.grab", return_value=mock_img):
            with patch("platform.system", return_value="Windows"):
                result = tool.execute()

        assert result.success
        assert "image" in result.data
        assert result.data["mime_type"] == "image/png"

    def test_capture_unsupported_platform(self, tool, monkeypatch):
        """Test that unsupported platforms return an error."""
        with patch("platform.system", return_value="FreeBSD"):
            result = tool.execute()

        assert not result.success
        assert "Unsupported" in result.error
