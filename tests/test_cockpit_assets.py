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


def test_cockpit_css_glass_primitive():
    css = _read("static/cockpit.css")
    assert ".glass {" in css or ".glass{" in css, "missing .glass class"
    assert "backdrop-filter" in css, "glass primitive must use backdrop-filter"
    assert "saturate(220%)" in css, "glass primitive missing saturate(220%) per spec §4.2"
    assert "blur(36px)" in css, "glass primitive missing blur(36px) per spec §4.2"


def test_cockpit_css_pill_variants():
    css = _read("static/cockpit.css")
    for variant in [".pill", ".pill.go", ".pill.hold", ".pill.stop",
                    ".pill.brief", ".pill.plain"]:
        assert variant in css, f"missing pill variant {variant}"
