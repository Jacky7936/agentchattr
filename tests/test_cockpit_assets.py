# tests/test_cockpit_assets.py
"""Static-asset shape tests for Mission Cockpit (Slice 1).

These do not exercise runtime behavior — they verify the right files
exist and contain the right selectors / wires. Runtime is verified
manually in the browser (see plan's verification steps).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "static"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_cockpit_css_exists():
    assert (STATIC / "cockpit.css").is_file(), \
        "static/cockpit.css missing — Slice 1 not started"


TOKENS_REQUIRED = [
    "--canvas",
    "--glass-bg",
    "--glass-edge",
    "--ink",
    "--ink-3",
    "--c-codex",
    "--c-claude",
    "--c-gemini",
    "--c-grok",
    "--sem-go",
    "--sem-hold",
    "--sem-stop",
    "--sem-brief",
    "--sans",
    "--mono",
]


def test_cockpit_css_root_tokens():
    css = _read("static/cockpit.css")
    assert ":root" in css, "cockpit.css missing :root block"
    for token in TOKENS_REQUIRED:
        assert token in css, f"cockpit.css missing token {token!r}"
