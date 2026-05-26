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
