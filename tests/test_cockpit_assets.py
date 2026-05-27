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


def test_cockpit_css_agent_tile():
    css = _read("static/cockpit.css")
    assert ".agent-tile" in css, "missing .agent-tile"
    # halo via radial-gradient on ::after — single-side accent is BANNED per spec §3.4
    assert "radial-gradient" in css, "agent-tile halo missing"
    # agent color slots
    for agent in ["codex", "claude", "gemini", "grok"]:
        sel = f'[data-agent="{agent}"]'
        assert sel in css, f"missing agent slot {sel}"


def test_cockpit_css_progress_bar():
    css = _read("static/cockpit.css")
    assert ".progress-track" in css, "missing .progress-track"
    assert ".progress-fill" in css, "missing .progress-fill"


def test_cockpit_css_shell_layout():
    css = _read("static/cockpit.css")
    assert "#cockpit-mode" in css, "missing shell container styles"
    # show/hide via body class
    assert "body.cockpit-active" in css, "missing body.cockpit-active rules"
    # ambient orbs for refraction
    assert "body::before" in css or "body.cockpit-active::before" in css \
        or "#cockpit-mode::before" in css, \
        "missing ambient orbs (refraction source) per spec §4.1"


def test_cockpit_css_empty_state():
    css = _read("static/cockpit.css")
    assert ".cockpit-standby" in css, \
        "missing .cockpit-standby (STANDING BY empty state) class"


def test_index_loads_geist_fonts():
    html = _read("static/index.html")
    assert "fonts.googleapis.com" in html, "Google Fonts preconnect/link missing"
    assert "Geist" in html, "Geist font not requested"
    assert "Geist+Mono" in html or "Geist%20Mono" in html, \
        "Geist Mono not requested"


def test_index_links_cockpit_css():
    html = _read("static/index.html")
    assert "/static/cockpit.css" in html, "cockpit.css not linked"


def test_index_loads_cockpit_js():
    html = _read("static/index.html")
    assert "/static/cockpit.js" in html, "cockpit.js not loaded"


def test_index_has_cockpit_toggle_button():
    html = _read("static/index.html")
    assert 'id="cockpit-toggle"' in html, "cockpit toggle button missing"
    assert 'onclick="toggleCockpitMode()"' in html, \
        "toggle button must wire to toggleCockpitMode()"


def test_index_has_cockpit_shell():
    html = _read("static/index.html")
    assert 'id="cockpit-mode"' in html, "<section id='cockpit-mode'> missing"
    assert 'cockpit-standby' in html, "STANDING BY empty state markup missing"
    assert 'cockpit-transcript' in html, "transcript container markup missing"


def test_cockpit_js_exists():
    js = _read("static/cockpit.js")
    assert js.strip(), "static/cockpit.js empty"


def test_cockpit_js_exports_toggle():
    js = _read("static/cockpit.js")
    assert "window.toggleCockpitMode" in js, \
        "cockpit.js must expose window.toggleCockpitMode"
    assert "localStorage" in js, "must persist mode via localStorage"
    assert "cockpit-active" in js, "must apply body.cockpit-active class"
    assert "window.__cockpit" in js, \
        "cockpit.js must expose window.__cockpit handle for Task 8 to wrap"


def test_cockpit_js_url_param():
    js = _read("static/cockpit.js")
    # Either ?cockpit=1 enabling or URLSearchParams handling
    assert "cockpit" in js and ("URLSearchParams" in js or "location.search" in js), \
        "must support ?cockpit=1 URL param to enable"


def test_cockpit_js_uses_safe_dom_apis():
    js = _read("static/cockpit.js")
    # Slice 1 explicitly avoids innerHTML — see plan §Architecture for rationale.
    assert "innerHTML" not in js, \
        "cockpit.js must not use innerHTML; build DOM with createElement + textContent"


def test_cockpit_js_hub_subscribe():
    js = _read("static/cockpit.js")
    assert "Hub.on" in js and "'message'" in js, \
        "cockpit.js must subscribe to Hub 'message' events"
    assert "cockpit-messages" in js, "must render into #cockpit-messages"


def test_cockpit_js_renders_sender_and_text():
    js = _read("static/cockpit.js")
    # Sanity: the renderer touches sender, time, text fields
    assert "msg.sender" in js or "data.sender" in js, "render must read sender"
    assert "msg.text" in js or "data.text" in js, "render must read text"


def test_cockpit_js_respects_active_channel():
    js = _read("static/cockpit.js")
    # We only render messages for the currently-viewed channel
    assert "activeChannel" in js or "agentchattr-channel" in js, \
        "transcript must scope to the active channel"


def test_cockpit_js_uses_text_content():
    js = _read("static/cockpit.js")
    assert "textContent" in js, \
        "renderer must use textContent for safe DOM construction"
    assert "createElement" in js, \
        "renderer must build elements with createElement"


def test_cockpit_css_briefing_mode():
    css = _read("static/cockpit.css")
    assert "body.cockpit-briefing" in css, "missing body.cockpit-briefing toggle"
    assert ".cockpit-briefing-form" in css, "missing briefing form container"


def test_cockpit_css_briefing_components():
    css = _read("static/cockpit.css")
    for sel in [
        ".cockpit-title-input",
        ".cockpit-objective",
        ".setup-card",
        ".agent-chip",
        ".agent-chip-on",
        ".deliv-item",
        ".budgets",
        ".budget",
        ".go-bar",
        ".go-launch",
    ]:
        assert sel in css, f"missing briefing component {sel}"
