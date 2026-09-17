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
    assert "Nordic personal workspace" in theme.description


def test_lilith_theme_has_nordic_palette():
    """Lilith pairs ice-blue information with aged-gold accents."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.border_style == "#D5B96D"
    assert theme.theme.get("realm") == "#D5B96D"
    assert theme.theme.get("frost") == "#8FD8E8"


def test_lilith_theme_has_distinctive_prompt():
    """Lilith theme uses ᛭ (rune) as prompt prefix."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.prompt_prefix == "\u16ed"


def test_lilith_theme_thinking_label():
    """Thinking state is understandable in Spanish."""
    from lilith_cli.render import THEMES

    theme = THEMES["lilith"]
    assert theme.spinner_label == "Pensando"
    assert "Pensando" in theme.thinking_label


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


def test_all_themes_have_consistent_keys():
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
    assert len(THEMES) >= 5, f"Expected at least 5 themes, got {len(THEMES)}"
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
        "_OBSIDIANA_BANNER",
    )
    for name in banner_names:
        banner = getattr(render, name)
        assert f"v{__version__}" in banner
        assert "v6.6" not in banner


def test_norse_box_banner_keeps_aligned_interior_width():
    """Norse box banner preserves the 33-char interior on the version line.

    The top and bottom borders are ``╔═══╗`` / ``╚═══╝`` (35 chars
    total, 33-char interior). Every row between them must be exactly
    ``║<33 chars>║`` so the box stays rectangular when the version
    changes.
    """
    from lilith_cli import render

    banner = render._NORSE_BANNER
    version_line = next(
        line.lstrip() for line in banner.splitlines() if "C L I ·" in line
    )
    assert version_line.startswith("║")
    assert version_line.endswith("║")
    assert len(version_line) == 35


def test_cyberpunk_banner_layout_and_version():
    """Cyberpunk banner has three non-empty lines and carries live version."""
    from lilith_cli import __version__, render

    lines = [line for line in render._CYBERPUNK_BANNER.splitlines() if line.strip()]
    assert len(lines) == 3
    assert f"v{__version__}" in render._CYBERPUNK_BANNER


# ── Obsidiana theme tests ────────────────────────────────────────────


def test_obsidiana_theme_registered():
    """'obsidiana' is a registered theme in THEMES."""
    from lilith_cli.render import THEMES, get_theme, set_theme

    assert "obsidiana" in THEMES
    theme = THEMES["obsidiana"]
    assert theme.name == "obsidiana"
    assert theme.label == "Obsidiana"

    # get_theme / set_theme round-trip
    original = get_theme().name
    try:
        result = set_theme("obsidiana")
        assert result.name == "obsidiana"
        assert get_theme().name == "obsidiana"
    finally:
        set_theme(original)


def test_obsidiana_banner_carries_live_version():
    """Obsidiana banner uses __version__, not a hardcoded string."""
    from lilith_cli import __version__
    from lilith_cli.render import _OBSIDIANA_BANNER

    assert f"v{__version__}" in _OBSIDIANA_BANNER
    assert "v6.6" not in _OBSIDIANA_BANNER


def test_obsidiana_spinner_frames_are_distinct():
    """Obsidiana spinner frames must differ from every other theme's."""
    from lilith_cli.render import THEMES

    obs = THEMES["obsidiana"]
    assert obs.spinner_frames is not None
    assert len(obs.spinner_frames) >= 3

    for name, other in THEMES.items():
        if name == "obsidiana":
            continue
        if other.spinner_frames is None:
            continue  # minimal has no custom spinner
        assert obs.spinner_frames != other.spinner_frames, (
            f"obsidiana spinner_frames must be distinct from {name}"
        )


def test_obsidiana_prompt_prefix_is_rune_tiwaz():
    """Obsidiana uses ᛏ (Tiwaz) as its single turn/prompt rune."""
    from lilith_cli.render import THEMES

    assert THEMES["obsidiana"].prompt_prefix == "\u16cf"  # ᛏ
