"""Tests for lilith_core.exceptions — custom exception hierarchy."""

import pytest

from lilith_core.exceptions import LilithError, LLMError, ToolError


class TestLilithError:
    """Tests for the base LilithError exception."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(LilithError, Exception)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(LilithError, match="core failure"):
            raise LilithError("core failure")

    def test_str_message(self) -> None:
        err = LilithError("something went wrong")
        assert str(err) == "something went wrong"

    def test_empty_message(self) -> None:
        err = LilithError()
        assert str(err) == ""


class TestToolError:
    """Tests for the ToolError exception."""

    def test_inherits_from_lilith_error(self) -> None:
        assert issubclass(ToolError, LilithError)

    def test_raise_and_catch_as_lilith_error(self) -> None:
        with pytest.raises(LilithError):
            raise ToolError("tool crashed")

    def test_raise_and_catch_as_tool_error(self) -> None:
        with pytest.raises(ToolError, match="tool crashed"):
            raise ToolError("tool crashed")

    def test_distinct_from_llm_error(self) -> None:
        """ToolError should NOT be caught as LLMError."""
        with pytest.raises(ToolError):
            try:
                raise ToolError("tool issue")
            except LLMError:
                pytest.fail("ToolError incorrectly caught as LLMError")


class TestLLMError:
    """Tests for the LLMError exception."""

    def test_inherits_from_lilith_error(self) -> None:
        assert issubclass(LLMError, LilithError)

    def test_raise_and_catch_as_lilith_error(self) -> None:
        with pytest.raises(LilithError):
            raise LLMError("model unavailable")

    def test_raise_and_catch_as_llm_error(self) -> None:
        with pytest.raises(LLMError, match="model unavailable"):
            raise LLMError("model unavailable")

    def test_distinct_from_tool_error(self) -> None:
        """LLMError should NOT be caught as ToolError."""
        with pytest.raises(LLMError):
            try:
                raise LLMError("connection timeout")
            except ToolError:
                pytest.fail("LLMError incorrectly caught as ToolError")


class TestHierarchy:
    """Tests for the exception hierarchy integrity."""

    def test_catch_all_with_lilith_error(self) -> None:
        """All custom exceptions should be caught by LilithError."""
        errors = [
            ToolError("tool fail"),
            LLMError("llm fail"),
        ]
        for err in errors:
            with pytest.raises(LilithError):
                raise err

    def test_tool_and_llm_errors_are_not_siblings(self) -> None:
        """ToolError should not be a subclass of LLMError or vice versa."""
        assert not issubclass(ToolError, LLMError)
        assert not issubclass(LLMError, ToolError)
