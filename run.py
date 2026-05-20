"""Entry point — starts MCP server (port 8200) + web UI (port 8300)."""

import argparse
import asyncio
import html
import secrets
import sys
import threading
import time
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

# Ensure the project directory is on the import path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

UI_AUTH_COOKIE = "agentchattr_ui_auth"

SURFACE_FILES = {
    "landing": "landing.html",
    "search": "search.html",
    "files": "files.html",
    "notifications": "notifications.html",
    "thread": "thread.html",
    "member": "member-detail.html",
    "settings": "settings.html",
    "sidebar": "sidebar.html",
}


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Start agentchattr (web UI + MCP server).",
        epilog="Flags override config.toml for this invocation. The same flags "
               "are also accepted by wrapper.py and wrapper_api.py so a launcher "
               "can isolate per-project instances by passing matching values to "
               "each process.",
    )
    parser.add_argument("--data-dir",      default=None, help="Override server.data_dir (path)")
    parser.add_argument("--port",          default=None, help="Override server.port (int)")
    parser.add_argument("--mcp-http-port", default=None, help="Override mcp.http_port (int)")
    parser.add_argument("--mcp-sse-port",  default=None, help="Override mcp.sse_port (int)")
    parser.add_argument("--upload-dir",    default=None, help="Override images.upload_dir (path)")
    parser.add_argument("--allow-network", action="store_true",
                        help="Allow binding to non-localhost hosts (with confirmation).")
    return parser.parse_args()


def _ui_password_from_config(config: dict) -> str:
    value = config.get("security", {}).get("ui_password", "")
    if value is None:
        return ""
    return str(value)


def _normalize_ui_next_path(value: str | None) -> str:
    """Return a safe same-origin redirect target for the UI login flow."""
    if not value:
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


def _inject_session_token(html_text: str, session_token: str) -> str:
    return html_text.replace(
        "</head>",
        f'<script>window.__SESSION_TOKEN__="{session_token}";</script>\n</head>',
    )


def _inject_surface_runtime(html_text: str, surface: str, session_token: str) -> str:
    html_text = _inject_session_token(html_text, session_token)
    runtime = (
        f'<script>window.__AGENTCHATTR_SURFACE__="{surface}";</script>\n'
        '<script src="/static/surfaces.js?v=1"></script>\n'
    )
    return html_text.replace("</body>", runtime + "</body>")


def _login_page(next_path: str, error: bool = False) -> str:
    safe_next = html.escape(_normalize_ui_next_path(next_path), quote=True)
    error_markup = (
        '<p class="error" role="alert">Incorrect password. Try again.</p>'
        if error
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>agentchattr UI password</title>
    <style>
        :root {{
            color-scheme: light dark;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f6f3ee;
            color: #1f2933;
        }}
        body {{
            min-height: 100vh;
            margin: 0;
            display: grid;
            place-items: center;
            padding: 24px;
            box-sizing: border-box;
        }}
        main {{
            width: min(100%, 360px);
            border: 1px solid rgba(31, 41, 51, 0.14);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.86);
            box-shadow: 0 18px 45px rgba(31, 41, 51, 0.10);
            padding: 24px;
        }}
        h1 {{
            margin: 0 0 8px;
            font-size: 20px;
            line-height: 1.25;
        }}
        p {{
            margin: 0 0 18px;
            color: #52616b;
            font-size: 14px;
            line-height: 1.5;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #344854;
            font-size: 13px;
            font-weight: 700;
        }}
        input {{
            width: 100%;
            min-height: 44px;
            box-sizing: border-box;
            border: 1px solid rgba(31, 41, 51, 0.22);
            border-radius: 6px;
            padding: 0 12px;
            font: inherit;
            background: #fff;
            color: #1f2933;
        }}
        button {{
            width: 100%;
            min-height: 44px;
            margin-top: 14px;
            border: 0;
            border-radius: 6px;
            background: #2563eb;
            color: white;
            font: inherit;
            font-weight: 700;
            cursor: pointer;
        }}
        .error {{
            margin: 12px 0 0;
            color: #b42318;
            font-weight: 700;
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{ background: #111827; color: #f9fafb; }}
            main {{
                background: rgba(17, 24, 39, 0.92);
                border-color: rgba(255, 255, 255, 0.16);
            }}
            p {{ color: #a7b0ba; }}
            label {{ color: #d5dde5; }}
            input {{
                background: #0b1220;
                color: #f9fafb;
                border-color: rgba(255, 255, 255, 0.22);
            }}
        }}
    </style>
</head>
<body>
    <main>
        <h1>UI password</h1>
        <p>Enter the password configured for this agentchattr UI.</p>
        <form method="post" action="/login">
            <input type="hidden" name="next" value="{safe_next}">
            <label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
            <button type="submit">Unlock</button>
            {error_markup}
        </form>
    </main>
</body>
</html>"""


def install_web_routes(target_app, config: dict, session_token: str, root: Path = ROOT, static_dir: Path | None = None):
    """Install browser UI routes and the optional password gate."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(root)
    static_dir = Path(static_dir or root / "static")
    design_dir = root / "agentchattr_UI_design"
    ui_password = _ui_password_from_config(config)
    ui_auth_token = secrets.token_urlsafe(32) if ui_password else ""

    def _current_path(request: Request) -> str:
        value = request.url.path
        if request.url.query:
            value += "?" + request.url.query
        return _normalize_ui_next_path(value)

    def _auth_redirect(request: Request):
        if not ui_password:
            return None
        cookie = request.cookies.get(UI_AUTH_COOKIE, "")
        if cookie and secrets.compare_digest(cookie, ui_auth_token):
            return None
        return RedirectResponse(
            "/login?" + urlencode({"next": _current_path(request)}),
            status_code=303,
        )

    @target_app.get("/login")
    async def login(request: Request):
        next_path = _normalize_ui_next_path(request.query_params.get("next", "/"))
        if not ui_password:
            return RedirectResponse(next_path, status_code=303)
        if not _auth_redirect(request):
            return RedirectResponse(next_path, status_code=303)
        return HTMLResponse(_login_page(next_path), headers={"Cache-Control": "no-store"})

    @target_app.post("/login")
    async def login_submit(request: Request):
        body = (await request.body()).decode("utf-8", "replace")
        form = parse_qs(body, keep_blank_values=True)
        next_path = _normalize_ui_next_path(
            form.get("next", [request.query_params.get("next", "/")])[0]
        )
        if not ui_password:
            return RedirectResponse(next_path, status_code=303)

        submitted = form.get("password", [""])[0]
        if secrets.compare_digest(submitted, ui_password):
            response = RedirectResponse(next_path, status_code=303)
            response.set_cookie(
                UI_AUTH_COOKIE,
                ui_auth_token,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )
            return response

        return HTMLResponse(
            _login_page(next_path, error=True),
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )

    @target_app.get("/")
    async def index(request: Request):
        auth_redirect = _auth_redirect(request)
        if auth_redirect:
            return auth_redirect
        # Read index.html fresh each request so changes take effect without restart.
        # Inject the session token only after the optional UI password gate passes.
        html_text = (static_dir / "index.html").read_text("utf-8")
        injected = _inject_session_token(html_text, session_token)
        return HTMLResponse(injected, headers={"Cache-Control": "no-store"})

    async def _serve_design_surface(surface_name: str, request: Request):
        auth_redirect = _auth_redirect(request)
        if auth_redirect:
            return auth_redirect
        filename = SURFACE_FILES.get(surface_name)
        if not filename:
            return HTMLResponse("Not found", status_code=404)
        html_text = (design_dir / filename).read_text("utf-8")
        injected = _inject_surface_runtime(html_text, surface_name, session_token)
        return HTMLResponse(injected, headers={"Cache-Control": "no-store"})

    def _surface_endpoint(surface_name: str):
        async def endpoint(request: Request):
            return await _serve_design_surface(surface_name, request)
        return endpoint

    for surface_name in SURFACE_FILES:
        target_app.add_api_route(f"/{surface_name}", _surface_endpoint(surface_name), methods=["GET"])

    target_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse flags for --help support; the actual env propagation happens via
    # the shared config_loader.apply_cli_overrides helper so run.py and the
    # wrappers use identical extraction logic.
    _parse_args()

    from config_loader import apply_cli_overrides, load_config
    apply_cli_overrides()

    config_path = ROOT / "config.toml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    config = load_config(ROOT)

    # --- Security: generate a random session token (in-memory only) ---
    session_token = secrets.token_hex(32)

    # Configure the FastAPI app (creates shared store)
    from app import app, configure, set_event_loop, store as _store_ref
    configure(config, session_token=session_token)

    # Share stores with the MCP bridge
    from app import store, rules, summaries, jobs, room_settings, registry, router as app_router, agents as app_agents, session_engine, session_store
    import mcp_bridge
    mcp_bridge.store = store
    mcp_bridge.rules = rules
    mcp_bridge.summaries = summaries
    mcp_bridge.jobs = jobs
    mcp_bridge.room_settings = room_settings
    mcp_bridge.registry = registry
    mcp_bridge.config = config
    mcp_bridge.router = app_router
    mcp_bridge.agents = app_agents

    # Enable cursor and role persistence across restarts
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    mcp_bridge._CURSORS_FILE = data_dir / "mcp_cursors.json"
    mcp_bridge._load_cursors()
    mcp_bridge._ROLES_FILE = data_dir / "roles.json"
    mcp_bridge._load_roles()

    # Start MCP servers in background threads
    http_port = config.get("mcp", {}).get("http_port", 8200)
    sse_port = config.get("mcp", {}).get("sse_port", 8201)
    mcp_bridge.mcp_http.settings.port = http_port
    mcp_bridge.mcp_sse.settings.port = sse_port

    threading.Thread(target=mcp_bridge.run_http_server, daemon=True).start()
    threading.Thread(target=mcp_bridge.run_sse_server, daemon=True).start()
    time.sleep(0.5)
    logging.getLogger(__name__).info("MCP streamable-http on port %d, SSE on port %d", http_port, sse_port)

    install_web_routes(app, config, session_token, root=ROOT)

    # Capture the event loop for the store→WebSocket bridge
    @app.on_event("startup")
    async def on_startup():
        set_event_loop(asyncio.get_running_loop())
        # Resume any sessions that were active before restart
        if session_engine:
            session_engine.resume_active_sessions()

    # Run web server
    import uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8300)

    # --- Security: warn if binding to a non-localhost address ---
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"\n  !! SECURITY WARNING — binding to {host} !!")
        print("  This exposes agentchattr to your local network.")
        print()
        print("  Risks:")
        print("  - No TLS: traffic (including session token) is plaintext")
        print("  - Anyone on your network can sniff the token and gain full access")
        print("  - With the token, anyone can @mention agents and trigger tool execution")
        print("  - If agents run with auto-approve, this means remote code execution")
        print()
        print("  Only use this on a trusted home network. Never on public/shared WiFi.")
        if "--allow-network" not in sys.argv:
            print("  Pass --allow-network to start anyway, or set host to 127.0.0.1.\n")
            sys.exit(1)
        else:
            print()
            try:
                confirm = input("  Type YES to accept these risks and start: ").strip()
            except (EOFError, KeyboardInterrupt):
                confirm = ""
            if confirm != "YES":
                print("  Aborted.\n")
                sys.exit(1)

    print(f"\n  agentchattr")
    print(f"  Web UI:  http://{host}:{port}")
    print(f"  MCP HTTP: http://{host}:{http_port}/mcp  (Claude, Codex)")
    print(f"  MCP SSE:  http://{host}:{sse_port}/sse   (Gemini)")
    print(f"  Data:    {data_dir}")
    print(f"  Agents auto-trigger on @mention")
    print(f"\n  Session token: {session_token}\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
