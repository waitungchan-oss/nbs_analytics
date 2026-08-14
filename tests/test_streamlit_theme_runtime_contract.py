from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_styles_do_not_override_dynamic_theme_tokens() -> None:
    source = (ROOT / "app_styles.py").read_text(encoding="utf-8")
    for token in (
        "--nbs-page-bg",
        "--nbs-surface",
        "--nbs-surface-soft",
        "--nbs-surface-panel",
        "--nbs-border",
        "--nbs-text",
        "--nbs-muted",
        "--nbs-sidebar-bg",
        "--nbs-sidebar-text",
        "--nbs-dataframe-filter",
    ):
        assert f"{token}:" not in source


def test_theme_tokens_have_contrasting_light_and_dark_shell_values() -> None:
    from streamlit_rendering import _theme_tokens

    light = _theme_tokens("light")
    dark = _theme_tokens("dark")
    assert light["mode"] == "light" and dark["mode"] == "dark"
    assert light["page_bg"] != dark["page_bg"]
    assert light["surface"] != dark["surface"]
    assert light["text"] != dark["text"]
    assert light["sidebar_bg"] != dark["sidebar_bg"]

