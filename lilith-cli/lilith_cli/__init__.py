"""Terminal interface for Lilith ecosystem."""

__version__ = "4.4.0"

from lilith_cli.trace import AgentTrace


_FEATURE_DOCS: dict[str, str] = {
    "release": (
        "/release [patch|minor|major] [--dry-run] bumps the package version, "
        "prepends a CHANGELOG.md entry, and creates a git commit. Default level "
        "is patch. With --dry-run prints intended changes without writing."
    ),
    "multi-file": (
        "/multi-file performs atomic edits across multiple files in a single "
        "transaction. Syntax: [file] old -> new ; [file2] old2 -> new2. Uses "
        "BatchEditTool under the hood so partial failures roll back."
    ),
    "print-error": (
        "_print_error(context, err) is a helper that formats errors with "
        "actionable tips from the _ERROR_TIPS dict mapping exception types to "
        "user-facing suggestions. Use it to keep error UX consistent."
    ),
    "voice": (
        "/voice synthesizes text to speech using the configured TTS provider "
        "(edge, openai, elevenlabs, etc.) and plays the audio. Streams output "
        "if streaming is enabled via /stream."
    ),
    "pin": (
        "/pin [name] bookmarks the current state of the conversation so you can "
        "return to it later via /bookmark. Useful for branching exploration."
    ),
}

__all__ = ["AgentTrace", "_FEATURE_DOCS"]
