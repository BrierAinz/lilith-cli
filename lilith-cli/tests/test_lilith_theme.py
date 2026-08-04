"""Tests for the Lilith theme/banner."""

from __future__ import annotations


def test_lilith_banner_defined():
    """_LILITH_BANNER constant is defined and carries the live version."""
    from lilith_cli import __version__
    from lilith_cli.render import _LILITH_BANNER

    assert _LILITH_BANNER is not None
    assert len(_LILITH_BANNER) > 50
    assert "L I L I T H" in _LILITH_BANNER
    assert f"v{__version__}" in _LILITH_BANNER
    assert "v6.6" not in _LILITH_BANNER


def test_lilith_theme_registered():
    """'lilith' is a registered theme in THEMES."""
    from lilith_cli.render import THEMES

    assert "lilith" in THEMES
    theme = THEMES["lilith"]
    assert theme.name == "lilith"
    assert theme.label == "Lilith"
    assert "Demon of Information" in theme.description


def test_lilith_theme_has_magenta_palette():
    """Lilith theme uses bright_magenta as primary color."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.border_style == "bright_magenta"
    assert theme.theme.get("realm") == "bright_magenta"


def test_lilith_theme_has_distinctive_prompt():
    """Lilith theme uses ᛭ (rune) as prompt prefix."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.prompt_prefix == "\u16ed"


def test_lilith_theme_thinking_label():
    """Lilith theme uses 'Manifesting' for thinking state."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.spinner_label == "Manifesting"
    assert "Manifesting" in theme.thinking_label


def test_lilith_theme_banner_references_banner_constant():
    """Lilith theme banner is _LILITH_BANNER."""
    from lilith_cli.render import THEMES, _LILITH_BANNER

    theme = THEMES["lilith"]
    assert theme.banner == _LILITH_BANNER


def test_lilith_theme_pt_style_present():
    """Lilith theme has prompt_toolkit style dict."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.pt_style
    assert theme.pt_style.get("")  # default style
    assert theme.pt_style.get("prompt")  # prompt style


def test_all_four_themes_have_consistent_keys():
    """All themes have the same set of theme keys."""
    from lilith_cli.render import THEMES

    expected_keys = {
        "realm", "frost", "grove", "bark", "rune",
        "error", "success", "warning", "info",
        "tool.name", "tool.arg", "tool.result",
        "thinking", "usage", "model",
        "status.ok", "status.fail", "status.warn",
        "turn", "duration",
    }
    for name, theme in THEMES.items():
        missing = expected_keys - set(theme.theme.keys())
        assert not missing, f"{name} missing keys: {missing}"


def test_all_banners_carry_live_version():
    """Every banner module constant references the live ``__version__``."""
    from lilith_cli import __version__, render

    banner_names = (
        "_NORSE_BANNER",
        "_CYBERPUNK_BANNER",
        "_MINIMAL_BANNER",
        "_LILITH_BANNER",
    )
    for name in banner_names:
        banner = getattr(render, name)
        assert f"v{__version__}" in banner
        assert "v6.6" not in banner


def test_box_banners_keep_aligned_interior_width():
    """Box banners preserve the 33-char interior on every version line.

    The top and bottom borders are ``╔═══╗`` / ``╚═══╝`` (35 chars
    total, 33-char interior). Every row between them must be exactly
    ``║<33 chars>║`` so the box stays rectangular when the version
    changes.
    """
    from lilith_cli import render

    for name in ("_NORSE_BANNER", "_CYBERPUNK_BANNER"):
        banner = getattr(render, name)
        version_line = next(
            line.lstrip() for line in banner.splitlines() if "C L I ·" in line
        )
        assert version_line.startswith("║")
        assert version_line.endswith("║")
        assert len(version_line) == 35
