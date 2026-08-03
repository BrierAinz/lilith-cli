"""Vision analysis tool for Lilith.

Inspired by Talon's screenshot capture + vision analysis:
    - Analyze images from file paths, URLs, or base64 data
    - Supports any OpenAI-compatible vision endpoint (OpenAI, Ollama, etc.)
    - Can describe images, answer questions about them, and extract text (OCR)

Usage:
    The tool accepts an image source (file path, URL, or base64) and a question,
    then sends it to a vision-capable LLM endpoint for analysis.

Configuration:
    Set the vision endpoint in config or environment:
        VISION_ENDPOINT=https://localhost:11434/v1/chat/completions
        VISION_MODEL=minicpm-v
        VISION_API_KEY=optional
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


# ── Image loading helpers ────────────────────────────────────────────────────


def _load_image_from_file(path: str) -> str:
    """Load an image file and return base64-encoded data.

    Args:
        path: Path to the image file.

    Returns:
        Base64-encoded image data string.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is too large (>10MB).
    """
    import pathlib

    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    size = p.stat().st_size
    if size > 10 * 1024 * 1024:
        raise ValueError(f"Image too large: {size} bytes (max 10MB)")

    with open(p, "rb") as f:
        data = f.read()

    return base64.b64encode(data).decode("utf-8")


def _load_image_from_url(url: str, timeout: int = 30) -> str:
    """Download an image from a URL and return base64-encoded data.

    Args:
        url: HTTP/HTTPS URL of the image.
        timeout: Download timeout in seconds.

    Returns:
        Base64-encoded image data string.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Lilith/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()

    if len(data) > 10 * 1024 * 1024:
        raise ValueError(f"Image too large: {len(data)} bytes (max 10MB)")

    return base64.b64encode(data).decode("utf-8")


def _detect_mime_type(path: str) -> str:
    """Detect MIME type from file extension.

    Args:
        path: File path or filename.

    Returns:
        MIME type string (e.g., 'image/png').
    """
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "tiff": "image/tiff",
    }
    return mime_map.get(ext, "image/png")


def _is_url(source: str) -> bool:
    """Check if a string looks like a URL."""
    return source.startswith("http://") or source.startswith("https://")


def _is_base64(source: str) -> bool:
    """Check if a string is already base64-encoded data."""
    if source.startswith("data:image/"):
        return True
    # Heuristic: base64 strings are long, alphanumeric with +/=
    if len(source) > 100 and not source.startswith("/") and not source.startswith("C:\\"):
        try:
            base64.b64decode(source[:100])
            return True
        except Exception:
            pass
    return False


def _is_file_path(source: str) -> bool:
    """Check if a string looks like a file path."""
    import pathlib

    try:
        return pathlib.Path(source).exists()
    except Exception:
        return False


# ── Vision Analysis Tool ─────────────────────────────────────────────────────


@ToolRegistry.register
class VisionAnalyzeTool(BaseTool):
    """Analyze an image using a vision-capable LLM.

    Sends an image (from file path, URL, or base64) along with a question
    to a vision endpoint and returns the analysis result.

    Supports any OpenAI-compatible vision endpoint:
        - OpenAI GPT-4 Vision
        - Ollama with MiniCPM-V, LLaVA, etc.
        - Any provider that accepts image_url in messages

    Configuration via environment variables:
        VISION_ENDPOINT: API endpoint URL
        VISION_MODEL: Model name (e.g., minicpm-v, gpt-4o)
        VISION_API_KEY: API key (optional for local models)
    """

    name = "vision_analyze"
    description = (
        "Analyze an image using AI vision. "
        "Parameters: image (str, file path/URL/base64), question (str, what to ask about the image)"
    )
    parameters = {
        "image": {
            "type": "string",
            "description": "Image source: file path, URL, or base64 data",
            "required": True,
        },
        "question": {
            "type": "string",
            "description": "Question about the image (e.g., 'What is in this image?')",
            "default": "Describe this image.",
        },
    }

    def execute(
        self,
        image: str = "",
        question: str = "Describe this image.",
        **_kwargs: Any,
    ) -> ToolResult:
        """Analyze an image with a vision-capable LLM.

        Args:
            image: Image source — file path, URL, or base64 data.
            question: Question to ask about the image.

        Returns:
            ToolResult with the vision analysis in data['analysis'].
        """
        if not image:
            return ToolResult(success=False, data=None, error="No image source provided")
        if not question:
            question = "Describe this image."

        # Load the image
        try:
            image_data, mime_type = self._load_image(image)
        except FileNotFoundError as e:
            return ToolResult(success=False, data=None, error=str(e))
        except ValueError as e:
            return ToolResult(success=False, data=None, error=str(e))
        except Exception as e:
            return ToolResult(success=False, data=None, error=f"Failed to load image: {e}")

        # Call the vision endpoint
        try:
            analysis = self._call_vision_api(image_data, mime_type, question)
            return ToolResult(
                success=True,
                data={
                    "analysis": analysis,
                    "image_source": self._describe_source(image),
                    "question": question,
                    "mime_type": mime_type,
                },
            )
        except Exception as e:
            return ToolResult(success=False, data=None, error=f"Vision API call failed: {e}")

    def _load_image(self, source: str) -> tuple[str, str]:
        """Load an image from various sources and return (base64_data, mime_type).

        Args:
            source: File path, URL, or base64 data.

        Returns:
            Tuple of (base64_encoded_data, mime_type).

        Raises:
            FileNotFoundError: If file path doesn't exist.
            ValueError: If image is too large or source is invalid.
        """
        # Check if it's a data URL (data:image/png;base64,...)
        if source.startswith("data:image/"):
            # Extract mime type and data
            header, _, data = source.partition(",")
            mime = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
            return data, mime

        # Check if it's a URL
        if _is_url(source):
            data = _load_image_from_url(source)
            # Try to detect MIME from URL extension
            mime = _detect_mime_type(source.split("?")[0])
            return data, mime

        # Check if it's a file path
        if _is_file_path(source):
            data = _load_image_from_file(source)
            mime = _detect_mime_type(source)
            return data, mime

        # Assume it's raw base64 data
        # Try to validate it
        try:
            base64.b64decode(source[:100])
            return source, "image/png"  # default mime
        except Exception:
            raise ValueError(f"Could not determine image source type: {source[:50]}...")

    def _call_vision_api(
        self,
        image_data: str,
        mime_type: str,
        question: str,
    ) -> str:
        """Call a vision-capable LLM endpoint with the image and question.

        Uses OpenAI-compatible chat completions format with image_url.

        Args:
            image_data: Base64-encoded image data.
            mime_type: MIME type of the image.
            question: Question to ask about the image.

        Returns:
            The analysis text from the vision model.
        """
        endpoint = os.environ.get("VISION_ENDPOINT", "")
        model = os.environ.get("VISION_MODEL", "minicpm-v")
        api_key = os.environ.get("VISION_API_KEY", "")

        if not endpoint:
            raise RuntimeError(
                "VISION_ENDPOINT not set. Configure with: "
                "export VISION_ENDPOINT=http://localhost:11434/v1/chat/completions"
            )

        # Build the request payload (OpenAI vision format)
        data_url = f"data:{mime_type};base64,{image_data}"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.3,
        }

        # Build request
        req_data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Extract the response text
        choices = result.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            return content or "(no response from vision model)"

        return "(no response from vision model)"

    def _describe_source(self, source: str) -> str:
        """Return a human-readable description of the image source."""
        if _is_url(source):
            return f"url:{source[:60]}"
        if _is_file_path(source):
            return f"file:{source}"
        if source.startswith("data:image/"):
            return "data-url"
        return "base64"


# ── Screenshot Capture Tool (Windows/Linux) ──────────────────────────────────


@ToolRegistry.register
class ScreenshotCaptureTool(BaseTool):
    """Capture a screenshot and return it as base64 data.

    Uses platform-native screenshot methods:
        - Windows: Pillow ImageGrab
        - Linux: scrot or Pillow ImageGrab (if X11)
        - macOS: screencapture

    The captured screenshot can be passed directly to vision_analyze
    for AI analysis.
    """

    name = "screenshot_capture"
    description = (
        "Capture a screenshot of the current screen. "
        "Returns base64-encoded PNG data that can be passed to vision_analyze."
    )
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Capture a screenshot of the current screen.

        Returns:
            ToolResult with data['image'] (base64 PNG) and data['mime_type'].
        """
        import platform

        system = platform.system()

        try:
            if system == "Windows":
                return self._capture_windows()
            elif system == "Linux":
                return self._capture_linux()
            elif system == "Darwin":
                return self._capture_macos()
            else:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"Unsupported platform: {system}",
                )
        except ImportError as e:
            return ToolResult(
                success=False,
                data=None,
                error=f"Screenshot dependency missing: {e}. Install Pillow: pip install Pillow",
            )
        except Exception as e:
            return ToolResult(success=False, data=None, error=f"Screenshot failed: {e}")

    def _capture_windows(self) -> ToolResult:
        """Capture screenshot on Windows using Pillow ImageGrab."""
        from PIL import ImageGrab
        import io

        screenshot = ImageGrab.grab()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        data = base64.b64encode(buf.getvalue()).decode("utf-8")

        return ToolResult(
            success=True,
            data={"image": data, "mime_type": "image/png", "platform": "windows"},
        )

    def _capture_linux(self) -> ToolResult:
        """Capture screenshot on Linux using Pillow ImageGrab or scrot."""
        import io

        try:
            from PIL import ImageGrab

            screenshot = ImageGrab.grab()
        except Exception:
            # Fallback to scrot
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            subprocess.run(["scrot", tmp_path], check=True, timeout=10)

            with open(tmp_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")

            os.unlink(tmp_path)
            return ToolResult(
                success=True,
                data={"image": data, "mime_type": "image/png", "platform": "linux-scrot"},
            )

        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        data = base64.b64encode(buf.getvalue()).decode("utf-8")

        return ToolResult(
            success=True,
            data={"image": data, "mime_type": "image/png", "platform": "linux-pillow"},
        )

    def _capture_macos(self) -> ToolResult:
        """Capture screenshot on macOS using screencapture."""
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        subprocess.run(["screencapture", "-x", tmp_path], check=True, timeout=10)

        with open(tmp_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        os.unlink(tmp_path)

        return ToolResult(
            success=True,
            data={"image": data, "mime_type": "image/png", "platform": "macos"},
        )
