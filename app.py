"""agentchattr — FastAPI web UI + agent auto-trigger."""

import asyncio
import json
import re as _re
import sys
import threading
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from store import MessageStore
from rules import RuleStore
from summaries import SummaryStore
from jobs import JobStore
from missions import MissionStore
from schedules import ScheduleStore, parse_schedule_spec
from router import Router
from agents import AgentTrigger
from registry import RuntimeRegistry
from agent_profiles import AgentProfileStore, normalize_profile_id
from commander_ledger import CommanderLedger
from dispatcher import plan_orchestrator_dispatch, requests_all_agents
from session_store import SessionStore, validate_session_template
from session_engine import SessionEngine

log = logging.getLogger(__name__)

app = FastAPI(title="agentchattr")

# --- globals (set by configure()) ---
store: MessageStore | None = None
rules: RuleStore | None = None
summaries: SummaryStore | None = None
jobs: JobStore | None = None
missions: MissionStore | None = None
schedules: ScheduleStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
registry: RuntimeRegistry | None = None
agent_profiles: AgentProfileStore | None = None
session_store: SessionStore | None = None
session_engine: SessionEngine | None = None
commander_ledger: CommanderLedger | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()
_restored_commander_lanes: set[str] = set()
_restore_reconcile_notified: set[str] = set()

# --- Security: session token (set by configure()) ---
session_token: str = ""

# Room settings (persisted to data/settings.json)
room_settings: dict = {
    "title": "agentchattr",
    "username": "user",
    "font": "sans",
    "channels": ["general"],
    "history_limit": "all",
    "contrast": "normal",
    "custom_roles": [],
}

# Channel validation
_CHANNEL_NAME_RE = _re.compile(r'^[a-z0-9][a-z0-9\-]{0,19}$')
MAX_CHANNELS = 8

# Agent hats (persisted to data/hats.json)
agent_hats: dict[str, str] = {}  # { agent_name: svg_string }


def _hats_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "hats.json"


def _load_hats():
    global agent_hats
    p = _hats_path()
    if p.exists():
        try:
            agent_hats = json.loads(p.read_text("utf-8"))
        except Exception:
            agent_hats = {}


def _save_hats():
    p = _hats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(agent_hats), "utf-8")


def _sanitize_svg(svg: str) -> str:
    """Strip dangerous content from SVG string."""
    svg = _re.sub(r'<script[^>]*>.*?</script>', '', svg, flags=_re.DOTALL | _re.IGNORECASE)
    svg = _re.sub(r'\bon\w+\s*=', '', svg, flags=_re.IGNORECASE)
    svg = _re.sub(r'javascript\s*:', '', svg, flags=_re.IGNORECASE)
    return svg


def set_agent_hat(agent: str, svg: str) -> str | None:
    """Validate, sanitize, and store a hat SVG. Returns error string or None."""
    svg = svg.strip()
    if not svg.lower().startswith("<svg"):
        return "Hat must be an SVG element (starts with <svg)."
    if len(svg) > 5120:
        return "Hat SVG too large (max 5KB)."
    svg = _sanitize_svg(svg)
    agent_hats[agent.lower()] = svg
    _save_hats()
    if _event_loop:
        asyncio.run_coroutine_threadsafe(broadcast_hats(), _event_loop)
    return None


def clear_agent_hat(agent: str):
    """Remove an agent's hat."""
    key = agent.lower()
    if key in agent_hats:
        del agent_hats[key]
        _save_hats()
        if _event_loop:
            asyncio.run_coroutine_threadsafe(broadcast_hats(), _event_loop)


def _settings_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "settings.json"


def _load_settings():
    global room_settings
    p = _settings_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text("utf-8"))
            room_settings.update(saved)
        except Exception:
            pass
    # Ensure "general" always exists and is first
    if "channels" not in room_settings or not room_settings["channels"]:
        room_settings["channels"] = ["general"]
    elif "general" not in room_settings["channels"]:
        room_settings["channels"].insert(0, "general")


def _save_settings():
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(room_settings, indent=2), "utf-8")


def _extract_agent_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-agent-token", "").strip()


def _resolve_authenticated_agent(request: Request) -> dict | None:
    if not registry:
        return None
    token = _extract_agent_token(request)
    if not token:
        return None
    return registry.resolve_token(token)


# --- Security middleware ---
# Paths that don't require the session token (public assets and same-origin
# HTML shells that inject the session token before loading their runtime).
_PUBLIC_PREFIXES = ("/", "/login", "/landing", "/search", "/files", "/notifications", "/thread", "/member", "/settings", "/sidebar", "/static/")


def install_security_middleware(target_app: FastAPI, token: str, cfg: dict):
    """Add token validation and origin checking middleware to a FastAPI app."""
    import app as _self
    _self.session_token = token
    port = cfg.get("server", {}).get("port", 8300)
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        *cfg.get("server", {}).get("allowed_origins", []),
    }

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path

            # Static assets, index page, and uploaded images are public.
            # The index page injects the token client-side via same-origin script.
            # Uploads use random filenames and have path-traversal protection.
            if path in _PUBLIC_PREFIXES or path.startswith(("/static/", "/uploads/", "/api/roles")):
                return await call_next(request)

            # Agent registration/heartbeat: loopback only (no remote agent minting).
            if path.startswith(("/api/register", "/api/deregister/", "/api/heartbeat/", "/api/agents/registered/")):
                client_ip = request.client.host if request.client else ""
                if client_ip not in ("127.0.0.1", "::1", "localhost"):
                    return JSONResponse(
                        {"error": f"forbidden: agent registration is restricted to local loopback. Source {client_ip} is not allowed."},
                        status_code=403,
                    )
                return await call_next(request)

            # --- Origin check (blocks cross-origin / DNS-rebinding attacks) ---
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": "forbidden: origin not allowed"},
                    status_code=403,
                )

            # --- Token check ---
            # Allow registered agents to authenticate via Bearer token
            # for /api/messages and /api/send (no browser session needed).
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer ") and (
                path in ("/api/messages", "/api/send")
                or path.startswith(("/api/rules/", "/api/agent-profiles/"))
            ):
                bearer = auth_header[7:].strip()
                if _self.registry and _self.registry.resolve_token(bearer):
                    return await call_next(request)

            req_token = (
                request.headers.get("x-session-token")
                or request.query_params.get("token")
            )
            if req_token != _self.session_token:
                return JSONResponse(
                    {"error": "forbidden: invalid or missing session token"},
                    status_code=403,
                )

            return await call_next(request)

    target_app.add_middleware(SecurityMiddleware)


def _install_security_middleware(token: str, cfg: dict):
    """Add token validation and origin checking middleware to the main app."""
    install_security_middleware(app, token, cfg)


def configure(cfg: dict, session_token: str = ""):
    global store, rules, summaries, jobs, missions, schedules, router, agents, registry, agent_profiles, session_store, session_engine, commander_ledger, config
    config = cfg
    # --- Security: store the session token and install middleware ---
    _install_security_middleware(session_token, cfg)

    data_dir = cfg.get("server", {}).get("data_dir", "./data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    log_path = Path(data_dir) / "agentchattr_log.jsonl"
    legacy_log_path = Path(data_dir) / "room_log.jsonl"
    if not log_path.exists() and legacy_log_path.exists():
        # Backward compatibility for existing installs.
        log_path = legacy_log_path

    store = MessageStore(str(log_path))
    # Initialize store upload dir from config
    raw_upload_dir = cfg.get("images", {}).get("upload_dir", "./uploads")
    store.upload_dir = Path(raw_upload_dir)
    
    # Rules store — migrates from legacy decisions.json automatically
    rules_path = Path(data_dir) / "rules.json"
    legacy_decisions = Path(data_dir) / "decisions.json"
    if not rules_path.exists() and legacy_decisions.exists():
        legacy_decisions.rename(rules_path)
    rules = RuleStore(str(rules_path))
    rules.on_change(_on_rule_change)

    summaries = SummaryStore(str(Path(data_dir) / "summaries.json"))

    # Migrate legacy activities.json → jobs.json
    jobs_path = Path(data_dir) / "jobs.json"
    legacy_activities = Path(data_dir) / "activities.json"
    if not jobs_path.exists() and legacy_activities.exists():
        legacy_activities.rename(jobs_path)

    jobs = JobStore(str(jobs_path))
    jobs.on_change(_on_job_change)

    missions_path = Path(data_dir) / "missions.json"
    missions = MissionStore(str(missions_path))
    missions.on_change(_on_mission_change)

    schedules = ScheduleStore(str(Path(data_dir) / "schedules.json"))
    schedules.on_change(_on_schedule_change)

    max_hops = cfg.get("routing", {}).get("max_agent_hops", 4)
    commander_max_hops = cfg.get("routing", {}).get("commander_lane_max_hops", max_hops)
    commander_ledger = CommanderLedger(Path(data_dir) / "commander_ledger.json")
    _restored_commander_lanes.clear()
    _restore_reconcile_notified.clear()

    # Registry: single source of truth for all live agent state
    registry = RuntimeRegistry(data_dir=data_dir)
    registry.seed(cfg.get("agents", {}))
    registry.on_change(_on_registry_change)

    agent_profiles = AgentProfileStore(Path(data_dir) / "agent_profiles.json", cfg.get("agents", {}))
    agent_profiles.bootstrap_from_roles(Path(data_dir) / "roles.json")

    # Router starts with base agent names (backward compat for direct MCP users),
    # registry.on_change updates it dynamically when instances register/deregister
    agent_names = list(cfg.get("agents", {}).keys())
    router = Router(
        agent_names=agent_names,
        default_mention=cfg.get("routing", {}).get("default", "none"),
        max_hops=max_hops,
        commander_max_hops=commander_max_hops,
        online_checker=lambda: set(registry.get_active_names()) if registry else set(),
    )
    agents = AgentTrigger(registry, data_dir=data_dir)

    # Sessions
    ROOT = Path(__file__).parent
    session_store = SessionStore(
        str(Path(data_dir) / "session_runs.json"),
        templates_dir=str(ROOT / "session_templates"),
    )
    session_engine = SessionEngine(session_store, store, agents, registry)
    session_store.on_change(_on_session_change)

    # Bridge: when ANY message is added to store (including via MCP),
    # broadcast to all WebSocket clients
    store.on_message(_on_store_message)

    _load_settings()
    _load_hats()

    # Apply saved loop guard setting
    if "max_agent_hops" in room_settings:
        router.max_hops = room_settings["max_agent_hops"]

    # Background thread: check for wrapper recovery flag files
    _data_dir = Path(data_dir)

    _known_online: set[str] = set()  # agents we've seen join — track for leave messages
    _posted_leave: set[str] = set()  # agents we've already posted a leave for — debounce

    _known_active = set()

    def _background_checks():
        import time as _time
        import mcp_bridge

        while True:
            _time.sleep(3)
            # Recovery flags
            try:
                for flag in _data_dir.glob("*_recovered"):
                    agent_name = flag.read_text("utf-8").strip()
                    flag.unlink()
                    store.add(
                        "system",
                        f"Agent routing for {agent_name} interrupted — auto-recovered. "
                        "If agents aren't responding, try sending your message again."
                    )
            except Exception:
                pass

            # Pending instances (slot 2+) wait for human naming or agent claim.
            # No auto-confirm — identity must be explicitly resolved.

            # Presence expiry — post leave messages (but do NOT deregister).
            # Deregistration only happens via /api/deregister (wrapper shutdown)
            # OR the 60s crash timeout below.
            # Short timeout (10s) prevents slot theft when MCP tool calls are intermittent.
            try:
                now = _time.time()
                with mcp_bridge._presence_lock:
                    currently_online = {
                        name for name, ts in mcp_bridge._presence.items()
                        if now - ts < mcp_bridge.PRESENCE_TIMEOUT
                    }
                    currently_active = set()
                    for name, active in mcp_bridge._activity.items():
                        if active:
                            if now - mcp_bridge._activity_ts.get(name, 0) < mcp_bridge.ACTIVITY_TIMEOUT:
                                currently_active.add(name)
                            else:
                                mcp_bridge._activity[name] = False  # auto-expire
                                mcp_bridge._activity_channel.pop(name, None)

                # Crash timeout: if a wrapper hasn't heartbeated for 60s,
                # it's dead — deregister it to free the slot.
                _CRASH_TIMEOUT = 15
                registered = set(registry.get_all_names())
                for name in registered:
                    with mcp_bridge._presence_lock:
                        last_seen = mcp_bridge._presence.get(name, 0)
                    if last_seen > 0 and now - last_seen > _CRASH_TIMEOUT:
                        log.info(f"Crash timeout: deregistering {name} (no heartbeat for {_CRASH_TIMEOUT}s)")
                        result = registry.deregister(name)
                        if result:
                            mcp_bridge.purge_identity(name)
                            registry.clean_renames_for(name)
                            renamed = result.get("_renamed_back")
                            if renamed:
                                mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
                                store.rename_sender(renamed["old"], renamed["new"])
                                if _event_loop:
                                    rename_event = json.dumps({
                                        "type": "agent_renamed",
                                        "old_name": renamed["old"],
                                        "new_name": renamed["new"],
                                    })
                                    asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
                            store.add(name, f"{name} disconnected (timeout)", msg_type="leave", channel=_last_active_channel)
                            _posted_leave.add(name)

                # Re-fetch registered names (may have changed from crash timeout above)
                registered = set(registry.get_all_names())

                # Detect registered instances going offline (leave message only)
                timed_out = registered - currently_online
                for name in timed_out:
                    inst = registry.get_instance(name)
                    if not inst:
                        continue
                    # Skip names that were just renamed (not actually offline)
                    with mcp_bridge._presence_lock:
                        was_renamed = name in mcp_bridge._renamed_from
                        if was_renamed:
                            mcp_bridge._renamed_from.discard(name)
                    if was_renamed:
                        continue
                    # Post leave message ONCE per offline transition (debounced)
                    if name not in _posted_leave:
                        _posted_leave.add(name)
                        store.add(name, f"{name} disconnected", msg_type="leave", channel=_last_active_channel)

                # Clear leave debounce for agents that came back online
                _posted_leave -= currently_online

                # Detect other agents (non-registered) going offline
                went_offline = (_known_online - currently_online) - timed_out
                for name in went_offline:
                    # Skip leave messages for names that were just renamed
                    with mcp_bridge._presence_lock:
                        was_renamed = name in mcp_bridge._renamed_from
                        if was_renamed:
                            mcp_bridge._renamed_from.discard(name)
                    if was_renamed:
                        continue
                    if not registry.is_registered(name) and name not in _posted_leave:
                        _posted_leave.add(name)
                        store.add(name, f"{name} disconnected", msg_type="leave", channel=_last_active_channel)

                if _known_online != currently_online and _event_loop:
                    asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)

                # Clear stale activity for agents that went offline
                with mcp_bridge._presence_lock:
                    stale_active = [n for n in mcp_bridge._activity
                                    if mcp_bridge._activity.get(n) and n not in currently_online]
                    for n in stale_active:
                        mcp_bridge._activity[n] = False
                    if stale_active:
                        currently_active -= set(stale_active)

                # Broadcast status on any change (online set or activity set)
                if currently_active != _known_active or _known_online != currently_online:
                    _known_active.clear()
                    _known_active.update(currently_active)
                    if _event_loop:
                        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)
                _known_online.clear()
                _known_online.update(currently_online)
            except Exception:
                pass

            try:
                if _event_loop:
                    asyncio.run_coroutine_threadsafe(_maybe_restore_commander_lanes(now), _event_loop)
                    asyncio.run_coroutine_threadsafe(_run_commander_watchdog(now), _event_loop)
            except Exception:
                pass

    threading.Thread(target=_background_checks, daemon=True).start()

    # --- Schedule runner: fires due scheduled prompts every 30s ---
    def _schedule_runner():
        import time as _time
        while True:
            _time.sleep(30)
            try:
                if not schedules:
                    continue
                due = schedules.run_due()
                for s in due:
                    prompt = s.get("prompt", "")
                    targets = s.get("targets", [])
                    channel = s.get("channel", "general")
                    if not prompt or not targets:
                        schedules.mark_run(s["id"])
                        continue
                    sender = s.get("created_by", "user")
                    mention_str = " ".join(f"@{t}" for t in targets)
                    full_text = f"{mention_str} {prompt}" if mention_str else prompt
                    # store.add triggers _handle_new_message via callback,
                    # which routes @mentions to agents — no manual trigger needed.
                    store.add(
                        sender,
                        full_text,
                        channel=channel,
                    )
                    if s.get("one_shot"):
                        schedules.delete(s["id"])
                    else:
                        schedules.mark_run(s["id"])
            except Exception:
                log.exception("schedule runner error")

    threading.Thread(target=_schedule_runner, daemon=True).start()


# --- Store → WebSocket bridge ---

_event_loop = None  # set by run.py after starting the event loop
_last_active_channel: str = "general"  # last channel any message was sent in


def set_event_loop(loop):
    global _event_loop
    _event_loop = loop


def _on_store_message(msg: dict):
    """Called from any thread when a message is added to the store."""
    if _event_loop is None:
        return
    try:
        # If called from the event loop thread (e.g. WebSocket handler),
        # schedule directly as a task
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(_handle_new_message(msg))
            return
    except RuntimeError:
        pass  # No running loop — we're in a different thread (MCP)
    asyncio.run_coroutine_threadsafe(_handle_new_message(msg), _event_loop)


def _on_rule_change(action: str, rule: dict):
    """Called from any thread when a rule changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_rule(action, rule))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_rule(action, rule), _event_loop)


def _on_job_change(action: str, data: dict):
    """Called from any thread when a job changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_job(action, data))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_job(action, data), _event_loop)


def _on_mission_change(action: str, mission: dict) -> None:
    """Called from any thread when a mission changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_mission(action, mission))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_mission(action, mission), _event_loop)


def _on_schedule_change(action: str, schedule: dict):
    """Called from any thread when a schedule changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_schedule(action, schedule))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_schedule(action, schedule), _event_loop)


def _on_session_change(action: str, session: dict):
    """Called from any thread when a session changes."""
    if _event_loop is None:
        return
    # Enrich with computed fields so the frontend gets phase_name, current_agent, etc.
    if session_engine:
        session = session_engine._enrich(dict(session))

    # Add completion/interruption banners to chat timeline
    if action == "complete" and store:
        output_id = session.get("output_message_id")
        # Tag the output message so it renders highlighted on reload
        if output_id:
            msg = store.get_by_id(output_id)
            if msg:
                meta = msg.get("metadata") or {}
                meta["session_output"] = True
                store.update_message(output_id, {"metadata": meta})
        store.add(
            sender="system",
            text=f"Session complete: {session.get('template_name', '?')}",
            msg_type="session_end",
            channel=session.get("channel", "general"),
            metadata={"session_id": session.get("id"), "output_message_id": output_id},
        )
    elif action == "interrupt" and store:
        reason = session.get("interrupt_reason", "interrupted")
        store.add(
            sender="system",
            text=f"Session ended: {session.get('template_name', '?')} ({reason})",
            msg_type="session_end",
            channel=session.get("channel", "general"),
            metadata={"session_id": session.get("id"), "reason": reason},
        )

    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_session(action, session))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_session(action, session), _event_loop)


_draft_ref_re = _re.compile(r'\[([a-f0-9]{8})\]')

def _resolve_draft_lineage(text: str, channel: str) -> tuple[str, int]:
    """Check if a session draft block is a revision of an existing draft.

    Looks at the agent's own message text for a [draft_id] reference, and also
    scans recent channel messages for "revise session draft [XXXX]" requests.
    Returns (draft_id, revision). New drafts get a fresh id and revision=1.
    """
    # Check the message text itself for a draft_id reference
    ref_match = _draft_ref_re.search(text)
    ref_id = ref_match.group(1) if ref_match else None

    if not ref_id:
        # Also check recent messages for a "revise session draft [XXXX]" request
        recent = store.get_recent(count=20, channel=channel)
        for m in reversed(recent):
            m_text = m.get("text", "")
            if "revise session draft" in m_text.lower():
                ref_match = _draft_ref_re.search(m_text)
                if ref_match:
                    ref_id = ref_match.group(1)
                    break

    if ref_id:
        # Find the highest revision for this draft_id in existing messages
        max_rev = 0
        recent = store.get_recent(count=100, channel=channel)
        for m in recent:
            meta = m.get("metadata") or {}
            if meta.get("draft_id") == ref_id:
                max_rev = max(max_rev, meta.get("revision", 1))
        if max_rev > 0:
            return ref_id, max_rev + 1

    return str(uuid.uuid4())[:8], 1


def _routing_int(key: str, default: int) -> int:
    try:
        return int(config.get("routing", {}).get(key, default))
    except (TypeError, ValueError):
        return default


async def _maybe_restore_commander_lanes(now: float | None = None) -> list[str]:
    if not (router and commander_ledger and registry):
        return []

    import time as _time
    import mcp_bridge

    ts = _time.time() if now is None else float(now)
    max_age = _routing_int("commander_restore_max_age_seconds", 43200)
    restored: list[str] = []
    for lane in commander_ledger.active_lanes():
        channel = str(lane.get("channel") or "general")
        if channel in _restored_commander_lanes:
            continue
        updated_at = float(lane.get("updated_at") or 0.0)
        # Use last_activity_at (worker activity only) instead of updated_at,
        # so watchdog events don't refresh the stale-restore timer (P0-A fix).
        activity_at = float(lane.get("last_activity_at") or lane.get("created_at") or 0.0)
        if max_age > 0 and activity_at and ts - activity_at > max_age:
            commander_ledger.release_lane(
                channel,
                updated_by="system",
                reason=f"stale restore timeout after {int(ts - activity_at)}s",
                now=ts,
            )
            if store:
                store.add(
                    "system",
                    f"Commander restore skipped in #{channel}: previous lane was stale.",
                    msg_type="system",
                    channel=channel,
                )
            continue

        commander = normalize_profile_id(str(lane.get("commander") or ""))
        active_agents = [
            normalize_profile_id(str(agent))
            for agent in lane.get("active_agents", [])
            if normalize_profile_id(str(agent))
        ]
        if not commander or not active_agents:
            continue

        if not _commander_watchdog_has_actionable_work(
            channel,
            {"active_agents": active_agents, "reason": str(lane.get("reason") or lane.get("task") or "")},
        ):
            commander_ledger.release_lane(
                channel,
                updated_by="system",
                reason="restore standby: no active task",
                now=ts,
            )
            if store:
                store.add(
                    "system",
                    f"Commander restore skipped in #{channel}: no active task; waiting for user instruction.",
                    msg_type="system",
                    channel=channel,
                )
            continue

        registered = set(registry.get_active_names())
        online = {name for name in registered if mcp_bridge.is_online(name)}
        commander_online = commander in online
        online_workers = [agent for agent in active_agents if agent in online]

        if commander_online and len(online_workers) == len(active_agents):
            router.set_commander_lock(
                channel,
                active_agents=active_agents,
                updated_by=commander,
                reason=str(lane.get("reason") or lane.get("task") or "restored commander lane"),
                now=updated_at or ts,
            )
            _restored_commander_lanes.add(channel)
            _restore_reconcile_notified.discard(channel)
            if store:
                restored_text = " ".join(f"@{agent}" for agent in active_agents)
                store.add(
                    "system",
                    f"Commander lane restored in #{channel}: @{commander} coordinating {restored_text}.",
                    msg_type="system",
                    channel=channel,
                )
            restored.append(channel)
            continue

        if commander_online and online_workers and channel not in _restore_reconcile_notified:
            _restore_reconcile_notified.add(channel)
            missing = [agent for agent in active_agents if agent not in online]
            online_text = " ".join(f"@{agent}" for agent in online_workers)
            missing_text = " ".join(f"@{agent}" for agent in missing) or "none"
            notice = (
                f"Commander restore pending in #{channel}: @{commander} is online, "
                f"online workers={online_text or 'none'}, missing={missing_text}."
            )
            prompt = (
                f"use mcp to read #{channel}. A previous commander lane was restored from the ledger, "
                f"but only these workers are online: {online_text or 'none'}. Missing: {missing_text}. "
                "Do not assume the old lane is active. Decide whether to /handoff to online workers, "
                "/release the stale lane, or ask the human for the missing decision. Post one concise "
                "restore decision and stop."
            )
            if store:
                store.add("system", notice, msg_type="system", channel=channel)
            if agents and agents.is_available(commander):
                await agents.trigger(commander, message=notice, channel=channel, prompt=prompt)
    return restored


def _record_commander_lane(
    channel: str,
    *,
    commander: str,
    workers: list[str],
    task: str,
    reason: str,
    event_type: str = "start",
):
    if commander_ledger:
        commander_ledger.start_lane(
            channel,
            commander=commander,
            active_agents=workers,
            task=task,
            reason=reason,
            event_type=event_type,
        )


def _release_commander_lane(channel: str, *, updated_by: str = "", reason: str = ""):
    if commander_ledger:
        commander_ledger.release_lane(channel, updated_by=updated_by, reason=reason)


def _note_commander_activity(channel: str, sender: str, timestamp: float | None = None):
    if commander_ledger:
        commander_ledger.note_activity(channel, sender, now=timestamp)


def _auto_dispatch_enabled() -> bool:
    return bool(config.get("routing", {}).get("auto_dispatch", False))


def _should_auto_dispatch(sender: str, text: str, msg_type: str, stripped: str, known_agents: set[str]) -> bool:
    return (
        _auto_dispatch_enabled()
        and agent_profiles is not None
        and msg_type == "chat"
        and bool(text.strip())
        and sender not in known_agents
        and not stripped.startswith("/")
    )


def _select_auto_dispatch_plan(text: str) -> dict[str, object]:
    if not agent_profiles:
        return {"orchestrator": "", "commander": "", "workers": [], "targets": []}
    active_names = registry.get_active_names() if registry else None
    return plan_orchestrator_dispatch(
        text,
        agent_profiles.get_all(),
        active_names=active_names,
        max_workers=_routing_int("auto_dispatch_max_targets", 0),
    )


def _auto_dispatch_prompt(
    channel: str,
    *,
    target: str = "",
    commander: str = "",
    workers: list[str] | None = None,
    near_misses: list[dict] | None = None,
) -> str:
    workers = workers or []
    near_misses = near_misses or []
    if commander and target == commander:
        worker_text = ", ".join(f"@{worker}" for worker in workers) or "none"
        near_text = ""
        if near_misses:
            near_parts = []
            for item in near_misses:
                name = str(item.get("name") or "").strip()
                role = str(item.get("role") or "").strip()
                if name:
                    near_parts.append(f"@{name}" + (f" ({role})" if role else ""))
            if near_parts:
                near_text = " near-miss specialists for context only: " + ", ".join(near_parts) + ". Ask them only if the lane needs that specialty."
        return (
            f"use mcp to read #{channel}. ROLE: Orchestrator. You are coordinating "
            f"the active worker lane: {worker_text}. Track progress, split work, ask for concise "
            "status updates, and consolidate the result for the human. There is no fixed worker cap; "
            "when many agents are active, assign explicit slices and expected outputs. You may parallel-dispatch "
            "active workers and let active workers coordinate with each other inside the lane. "
            "For repeatable multi-item work, create a structured lane backlog with chat_set_lane_backlog; "
            "after each approved item, the server will auto-start the configured worker on the next pending item. "
            "Prevent loops: use /handoff @agent, /freeze @agent @agent, /standby @agent, "
            "/release, and mention only intended active workers. When the work is complete, "
            "summarize once for the human, release or narrow the lane, and stop."
            + near_text
        )
    if commander and target in workers:
        peer_text = ", ".join(f"@{worker}" for worker in workers if worker != target) or "none"
        return (
            f"use mcp to read #{channel} - you were dispatched as an active worker under "
            f"@{commander}. Work only on your role's slice, report progress and blockers back "
            f"to @{commander}. For long work or test/build runs, call chat_report_progress with "
            "state, eta_seconds, and a short note. Coordinate only with active lane peers when it is necessary: "
            f"{peer_text}. For structured backlog work, call chat_update_lane_item with state='running' "
            "when you start and state='ready_for_review' when your item is complete. Do not mention or wake "
            "agents outside the active lane. Before any final done/blockers chat reply, make sure the backlog "
            "state has already been updated. When your slice is done, report done/blockers once and stop."
        )
    return (
        f"use mcp to read #{channel} - you were auto-dispatched because your "
        "profile role/specialty matches the latest user task. Take appropriate "
        "action and respond in the channel."
    )


_COMMANDER_COMMANDS = {"/freeze", "/handoff", "/standby", "/release", "/unfreeze", "/commander"}


def _strip_mentions_for_command(text: str) -> str:
    return _re.sub(r"@[\w-]+\s*", "", text or "").strip().lower()


def _commander_command_line(text: str) -> str:
    for line in (text or "").splitlines():
        if _is_commander_command(_strip_mentions_for_command(line)):
            return line.strip()
    return ""


def _command_word(stripped: str) -> str:
    return stripped.split()[0] if stripped else ""


def _is_commander_command(stripped: str) -> bool:
    return _command_word(stripped) in _COMMANDER_COMMANDS


def _is_commander_sender(sender: str) -> bool:
    clean = normalize_profile_id(sender or "")
    if not clean:
        return False
    username = normalize_profile_id(room_settings.get("username", "user"))
    if clean == username:
        return True
    if clean in ("dispatcher", "orchestrator") or clean.endswith(("-dispatcher", "-orchestrator")):
        return True
    if agent_profiles:
        profile = agent_profiles.get_by_name(clean)
        role = str((profile or {}).get("role", "")).strip().lower()
        if role in ("dispatcher", "orchestrator"):
            return True
    return False


def _resolve_control_targets(text: str) -> list[str]:
    if not router:
        return []
    targets = []
    for name in router.parse_mentions(text or ""):
        if name in ("all", "both"):
            continue
        if registry:
            targets.extend(registry.resolve_to_instances(name))
        else:
            targets.append(name)
    return list(dict.fromkeys(targets))


def _control_status_text(channel: str) -> str:
    status = router.get_commander_status(channel) if router else {}
    active_names = status.get("active_agents") or ([status.get("active_agent")] if status.get("active_agent") else [])
    active = ", ".join(f"@{name}" for name in active_names) or "none"
    standby = ", ".join(f"@{name}" for name in status.get("standby", [])) or "none"
    locked = "locked" if status.get("locked") else "released"
    progress = _watchdog_progress_text(status)
    progress_text = f"; progress={progress}" if progress else ""
    return f"Commander status for #{channel}: {locked}; active={active}; standby={standby}{progress_text}."


async def _trigger_commander_target(target: str, sender: str, text: str, channel: str):
    await _trigger_commander_targets([target], sender, text, channel)


async def _trigger_commander_targets(
    targets: list[str],
    sender: str,
    text: str,
    channel: str,
    *,
    task_context: str = "",
):
    if not agents:
        return
    active_text = ", ".join(f"@{target}" for target in targets)
    task_text = str(task_context or "").strip()
    task_sentence = f"Current assigned task/context: {task_text}. " if task_text else ""
    for target in targets:
        if not agents or not agents.is_available(target):
            continue
        prompt = (
            f"use mcp to read #{channel}. Commander lock is active and the active worker lane is "
            f"{active_text}. {task_sentence}@{target}, respond with your assigned handoff/status. Do not mention "
            "or wake agents outside the active lane unless the orchestrator or human explicitly "
            "hands off. If this is a structured backlog lane, update your item state with "
            "chat_update_lane_item before your final chat reply instead of waiting for a human checkpoint. "
            "Coordinate with active lane peers only when useful, then report done or blocked once and stop."
        )
        await agents.trigger(target, message=f"{sender}: {text}", channel=channel, prompt=prompt)


def _commander_task_context(sender: str, text: str, command_text: str, channel: str) -> str:
    direct_context = _task_context_from_commander_message(text, command_text)
    if direct_context:
        return direct_context
    clean_sender = normalize_profile_id(sender or "")
    if clean_sender not in _known_agent_names():
        return ""
    return _latest_human_task_context(clean_sender, channel)


def _task_context_from_commander_message(text: str, command_text: str) -> str:
    parts = []
    command_line = (command_text or "").strip()
    command_pattern = "|".join(_re.escape(command) for command in sorted(_COMMANDER_COMMANDS, key=len, reverse=True))
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        stripped = _strip_mentions_for_command(line)
        if _is_commander_command(stripped):
            remainder = _re.sub(rf"^({command_pattern})\b", "", line, flags=_re.IGNORECASE).strip()
            remainder = _re.sub(r"@[\w-]+\s*", "", remainder).strip(" :-\t")
            if remainder:
                parts.append(remainder)
            continue
        if line != command_line:
            parts.append(line)
    return "\n".join(parts).strip()


def _latest_human_task_context(commander: str, channel: str) -> str:
    if not store or not hasattr(store, "get_recent"):
        return ""
    known_agents = _known_agent_names()
    try:
        recent = store.get_recent(count=100, channel=channel)
    except Exception:
        return ""
    fallback = ""
    for msg in reversed(recent):
        if msg.get("type", "chat") != "chat":
            continue
        sender = normalize_profile_id(str(msg.get("sender") or ""))
        if not sender or sender in known_agents or sender == normalize_profile_id(commander):
            continue
        text = str(msg.get("text") or "").strip()
        if not _re.sub(r"@[\w-]+\s*", "", text).strip():
            continue
        context = f"Latest human instruction from {msg.get('sender', 'human')}: {text}"
        if _human_instruction_has_task_detail(text):
            return context
        if not fallback:
            fallback = context
    return fallback


def _human_instruction_has_task_detail(text: str) -> bool:
    cleaned = _re.sub(r"@[\w-]+\s*", "", str(text or "")).strip().lower()
    if not cleaned:
        return False
    if _re.search(r"\b[\w./-]+\.(?:md|txt|py|ts|tsx|js|jsx|json|toml|sql|html)\b", cleaned):
        return True
    detail_terms = (
        "review",
        "patch",
        "plan",
        "docs/",
        "文件",
        "修",
        "修改",
        "統整",
        "判定",
        "負責",
        "指派",
        "實作",
    )
    return len(cleaned) >= 16 and any(term in cleaned for term in detail_terms)


async def _handle_commander_command(sender: str, text: str, channel: str) -> bool:
    command_text = _commander_command_line(text)
    if not command_text:
        return False
    stripped = _strip_mentions_for_command(command_text)
    cmd = _command_word(stripped)
    if cmd not in _COMMANDER_COMMANDS:
        return False

    if not _is_commander_sender(sender):
        store.add(
            "system",
            f"Commander control denied: {sender} is not an orchestrator/dispatcher or human operator.",
            msg_type="system",
            channel=channel,
        )
        return True

    targets = _resolve_control_targets(command_text)
    reason = command_text.strip()

    if cmd in ("/freeze", "/handoff"):
        if not targets:
            store.add(
                "system",
                f"Commander control needs a target, e.g. `{cmd} @codex-builder`.",
                msg_type="system",
                channel=channel,
            )
            return True
        task_context = _commander_task_context(sender, text, command_text, channel)
        task = task_context or reason
        router.set_commander_lock(channel, active_agents=targets, updated_by=sender, reason=reason)
        _record_commander_lane(
            channel,
            commander=sender,
            workers=targets,
            task=task,
            reason=reason,
            event_type="handoff",
        )
        active = " ".join(f"@{target}" for target in targets)
        store.add(
            "system",
            f"Commander lock: only {active} may advance agent routing in #{channel}. "
            "Other agents are standby until `/handoff @agent` or `/release`.",
            msg_type="system",
            channel=channel,
        )
        await broadcast_status()
        await _trigger_commander_targets(targets, sender, text, channel, task_context=task_context)
        return True

    if cmd == "/standby":
        if not targets:
            store.add(
                "system",
                "Commander standby needs at least one @agent target.",
                msg_type="system",
                channel=channel,
            )
            return True
        status = router.set_standby(channel, agents=targets, updated_by=sender, reason=reason)
        remaining = status.get("active_agents", [])
        if remaining:
            _record_commander_lane(
                channel,
                commander=sender,
                workers=remaining,
                task=reason,
                reason=reason,
                event_type="standby",
            )
        else:
            _release_commander_lane(channel, updated_by=sender, reason=reason)
        standby = ", ".join(f"@{name}" for name in status["standby"])
        store.add("system", f"Commander standby: {standby} will not route other agents in #{channel}.", msg_type="system", channel=channel)
        await broadcast_status()
        return True

    if cmd in ("/release", "/unfreeze"):
        router.release_commander_lock(channel, updated_by=sender)
        _release_commander_lane(channel, updated_by=sender, reason=reason)
        store.add("system", f"Commander lock released in #{channel}.", msg_type="system", channel=channel)
        await broadcast_status()
        return True

    if cmd == "/commander":
        store.add("system", _control_status_text(channel), msg_type="system", channel=channel)
        return True

    return False


def _known_agent_names() -> set[str]:
    names: set[str] = set()
    if registry:
        names.update(registry.get_all_names())
    if router:
        names.update(router.agent_names)
    names.update(config.get("agents", {}).keys())
    return {normalize_profile_id(name) for name in names if normalize_profile_id(name)}


def _resolve_watchdog_commander(status: dict) -> str:
    known_agents = _known_agent_names()
    candidates = [status.get("updated_by", "")]
    candidates.extend(sorted(name for name in known_agents if name.endswith(("-orchestrator", "-dispatcher"))))
    candidates.extend(sorted(name for name in known_agents if name in ("orchestrator", "dispatcher")))
    for candidate in candidates:
        clean = normalize_profile_id(str(candidate or ""))
        if clean in known_agents and _is_commander_sender(clean):
            return clean
    return ""


def _progress_eta_hold_until(status: dict, threshold: int) -> float:
    hold_until = max(
        float(status.get("last_worker_activity_at") or 0.0) + threshold,
        float(status.get("last_worker_progress_at") or 0.0) + threshold,
        float(status.get("updated_at") or 0.0) + threshold,
    )
    for progress in (status.get("worker_progress") or {}).values():
        state = str(progress.get("state", "")).strip().lower()
        if state in ("blocked", "done", "complete", "completed", "stalled"):
            continue
        eta_seconds = _safe_int(progress.get("eta_seconds"), 0)
        updated_at = float(progress.get("updated_at") or 0.0)
        if updated_at and eta_seconds > 0:
            hold_until = max(hold_until, updated_at + eta_seconds)
    return hold_until


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _watchdog_progress_text(status: dict) -> str:
    parts = []
    for agent, progress in (status.get("worker_progress") or {}).items():
        state = str(progress.get("state") or "working")
        eta_seconds = _safe_int(progress.get("eta_seconds"), 0)
        note = str(progress.get("note") or "").strip()
        eta_text = f", eta={eta_seconds}s" if eta_seconds > 0 else ""
        note_text = f", note={note}" if note else ""
        parts.append(f"@{agent}: {state}{eta_text}{note_text}")
    return "; ".join(parts)


_WATCHDOG_INACTIVE_PROGRESS_STATES = {
    "approved",
    "complete",
    "completed",
    "done",
    "idle",
    "released",
    "standby",
    "waiting",
}

_WATCHDOG_DONE_BACKLOG_STATES = {
    "approved",
    "cancelled",
    "canceled",
    "complete",
    "completed",
    "done",
    "released",
}


def _is_commander_control_only_text(text: str) -> bool:
    stripped = _strip_mentions_for_command(str(text or "").strip())
    return bool(stripped) and _is_commander_command(stripped)


def _safe_float_timestamp(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ledger_lane_activity_at(lane: dict | None) -> float:
    if not isinstance(lane, dict):
        return 0.0
    latest = _safe_float_timestamp(lane.get("last_activity_at"))
    for progress in (lane.get("progress") or {}).values():
        if isinstance(progress, dict):
            latest = max(latest, _safe_float_timestamp(progress.get("updated_at")))
    backlog = lane.get("backlog") or {}
    if isinstance(backlog, dict):
        latest = max(latest, _safe_float_timestamp(backlog.get("updated_at")))
        for item in backlog.get("items") or []:
            if isinstance(item, dict):
                latest = max(latest, _safe_float_timestamp(item.get("updated_at")))
    return latest


def _progress_has_active_watchdog_work(progress_by_agent: dict, active_agents: list[str]) -> bool:
    active = {normalize_profile_id(agent) for agent in active_agents if normalize_profile_id(agent)}
    for agent, progress in (progress_by_agent or {}).items():
        clean_agent = normalize_profile_id(agent)
        if clean_agent not in active or not isinstance(progress, dict):
            continue
        state = str(progress.get("state") or "working").strip().lower()
        if state not in _WATCHDOG_INACTIVE_PROGRESS_STATES:
            return True
    return False


def _backlog_has_active_watchdog_work(backlog: dict, active_agents: list[str]) -> bool:
    if not isinstance(backlog, dict):
        return False
    active = {normalize_profile_id(agent) for agent in active_agents if normalize_profile_id(agent)}
    if not active:
        return False

    items = [item for item in backlog.get("items") or [] if isinstance(item, dict)]
    if not items:
        return False
    current_index = _safe_int(backlog.get("current_index"), 0)
    ordered_items = []
    if 0 <= current_index < len(items):
        ordered_items.append(items[current_index])
    ordered_items.extend(item for item in items if item not in ordered_items)

    worker = normalize_profile_id(backlog.get("worker") or "")
    reviewer = normalize_profile_id(backlog.get("reviewer") or "")
    for item in ordered_items:
        state = str(item.get("status") or "").strip().lower()
        if not state or state in _WATCHDOG_DONE_BACKLOG_STATES:
            continue
        updated_by = normalize_profile_id(item.get("updated_by") or "")
        if state == "ready_for_review":
            if reviewer in active or (not reviewer and updated_by in active):
                return True
            continue
        if state in ("running", "needs_fix", "pending"):
            if worker in active or updated_by in active or (not worker and active):
                return True
            continue
        if state == "blocked":
            return True
        if worker in active or reviewer in active or updated_by in active:
            return True
    return False


def _commander_watchdog_has_actionable_work(channel: str, status: dict) -> bool:
    active_agents = status.get("active_agents") or (
        [status.get("active_agent")] if status.get("active_agent") else []
    )
    active_agents = [name for name in active_agents if name]
    if not active_agents:
        return False
    if not commander_ledger:
        return True

    lane = commander_ledger.get(channel)
    if not lane or lane.get("status") != "active":
        return False

    if _backlog_has_active_watchdog_work(lane.get("backlog") or {}, active_agents):
        return True
    if _progress_has_active_watchdog_work(lane.get("progress") or {}, active_agents):
        return True
    if _progress_has_active_watchdog_work(status.get("worker_progress") or {}, active_agents):
        return True

    task_text = str(lane.get("task") or lane.get("reason") or status.get("reason") or "").strip()
    return bool(task_text and not _is_commander_control_only_text(task_text))


def _commander_watchdog_prompt(
    channel: str,
    active_text: str,
    quiet_for: int,
    level: int,
    *,
    progress_text: str = "",
) -> str:
    base = (
        f"use mcp to read #{channel}. Commander watchdog handoff/ETA check: {active_text} has not posted "
        f"a worker update for {quiet_for} seconds. "
    )
    if progress_text:
        base += f"last progress: {progress_text}. "
    if level <= 1:
        return (
            base
            + "Check the latest channel state. If no handoff or ETA landed, ask the active worker for exactly one of: "
            "handoff if ready; ETA + completed stage + blocker; or a smaller split inside the current pass. "
            "Do not open a new module. After sending the request, stop."
        )
    if level == 2:
        return (
            base
            + "This is watchdog escalation 2. Do not simply wait again. Re-read the lane, ask each quiet active worker "
            "for one concise status, and decide whether to narrow the lane, reassign to another active specialist, "
            "or split the current pass smaller. Post the decision and stop."
        )
    return (
        base
        + f"This is watchdog escalation {level}. Summarize the lane state for the human, name the likely blocker, "
        "and make one commander decision: /handoff to a better active worker, /standby stuck workers, /release if done, "
        "or ask the human for the one missing decision. Do not keep the lane waiting silently; post the escalation "
        "summary and stop."
    )


async def _run_commander_watchdog(now: float | None = None) -> list[str]:
    if not (router and store and agents):
        return []
    threshold = _routing_int("commander_watchdog_seconds", 180)
    if threshold <= 0:
        return []

    import time as _time
    ts = _time.time() if now is None else float(now)
    nudged: list[str] = []
    for channel in room_settings.get("channels", ["general"]):
        status = router.get_commander_status(channel)
        active_agents = status.get("active_agents") or (
            [status.get("active_agent")] if status.get("active_agent") else []
        )
        active_agents = [name for name in active_agents if name]
        if not status.get("locked") or not active_agents:
            continue

        if not _commander_watchdog_has_actionable_work(channel, status):
            reason = "watchdog standby: no active task"
            router.release_commander_lock(channel, updated_by="watchdog")
            _release_commander_lane(channel, updated_by="watchdog", reason=reason)
            store.add(
                "system",
                f"Commander watchdog: no active task in #{channel}; released lane and waiting for user instruction.",
                msg_type="system",
                channel=channel,
            )
            await broadcast_status()
            continue

        last_worker_at = float(status.get("last_worker_activity_at") or 0.0)
        last_progress_at = float(status.get("last_worker_progress_at") or 0.0)
        lane = commander_ledger.get(channel) if commander_ledger else None
        ledger_activity_at = _ledger_lane_activity_at(lane)
        last_lane_at = max(
            last_worker_at,
            last_progress_at,
            ledger_activity_at,
            float(status.get("updated_at") or 0.0),
        )
        hold_until = max(
            _progress_eta_hold_until(status, threshold),
            ledger_activity_at + threshold if ledger_activity_at else 0.0,
        )
        if not last_lane_at or ts < hold_until:
            continue

        reminded_at = float(status.get("watchdog_reminded_at") or 0.0)
        if reminded_at and ts - reminded_at < threshold:
            continue

        commander = _resolve_watchdog_commander(status)
        if not commander or not agents.is_available(commander):
            # P1-A: if no commander is reachable and the lane has been quiet
            # for more than 2x the watchdog threshold, auto-release the lane
            # so the channel doesn't stay locked forever. The human can
            # re-dispatch when ready.
            quiet_for = int(ts - last_lane_at) if last_lane_at else 0
            if quiet_for >= threshold * 2:
                commander_label = commander or "(no commander resolved)"
                reason = (
                    f"commander {commander_label} unavailable; lane quiet for {quiet_for}s; auto-released"
                )
                router.release_commander_lock(channel, updated_by="watchdog")
                _release_commander_lane(channel, updated_by="watchdog", reason=reason)
                store.add(
                    "system",
                    f"Commander watchdog: {reason} in #{channel}. Re-dispatch when ready.",
                    msg_type="system",
                    channel=channel,
                )
                await broadcast_status()
            continue

        level = int(status.get("watchdog_count", 0) or 0) + 1
        router.mark_commander_watchdog(channel, timestamp=ts)
        quiet_for = max(0, int(ts - last_lane_at))
        active_text = " ".join(f"@{name}" for name in active_agents)
        label = "Commander watchdog" if level <= 1 else f"Commander watchdog escalation {level}"
        notice = (
            f"{label}: {active_text} has been quiet for {quiet_for}s in #{channel}; "
            f"nudging @{commander} for handoff/ETA."
        )
        prompt = _commander_watchdog_prompt(
            channel,
            active_text,
            quiet_for,
            level,
            progress_text=_watchdog_progress_text(status),
        )
        if commander_ledger:
            commander_ledger.note_watchdog(channel, quiet_for=quiet_for, level=level, now=ts)
        store.add("system", notice, msg_type="system", channel=channel)
        await agents.trigger(commander, message=notice, channel=channel, prompt=prompt)
        await broadcast_status()
        nudged.append(channel)
    return nudged


async def _handle_new_message(msg: dict):
    """Broadcast message to web clients + check for @mention triggers."""
    # For broadcast slash commands, suppress the raw message — only the expanded
    # version should appear. Delete from store if it was persisted (MCP path),
    # and skip broadcasting the raw text.
    text = msg.get("text", "")
    msg_type = msg.get("type", "chat")
    sender = msg.get("sender", "")
    channel = msg.get("channel", "general")

    # Track last active channel for leave/join messages (skip system messages)
    global _last_active_channel
    if msg_type not in ("system", "leave", "join"):
        _last_active_channel = channel
    # Strip @mentions to find the slash command (e.g. "@claude @codex /hatmaking")
    stripped = _re.sub(r"@[\w-]+\s*", "", text).strip().lower()
    _broadcast_cmds = ("/hatmaking", "/artchallenge", "/roastreview", "/poetry")
    cmd_word = stripped.split()[0] if stripped else ""
    is_broadcast_cmd = cmd_word in _broadcast_cmds
    is_commander_cmd = _is_commander_command(stripped)
    known_agents = set(registry.get_all_names()) if registry else set()
    known_agents.update(config.get("agents", {}).keys())
    _session_draft_re = _re.compile(r'```session\s*\n(.*?)\n```', _re.DOTALL)
    draft_match = _session_draft_re.search(text)
    is_agent_session_draft = bool(draft_match and sender in known_agents)
    is_hidden_session_request = msg_type == "session_request"

    is_agent_continue = (stripped == "/continue" and sender in known_agents)
    suppress_broadcast = (
        is_broadcast_cmd
        or is_commander_cmd
        or is_hidden_session_request
        or is_agent_session_draft
        or is_agent_continue
    )

    if not suppress_broadcast:
        await broadcast(msg)

    # If the raw slash command was persisted (MCP path), silently remove it.
    # It was never broadcast to WebSocket clients, so no delete event needed.
    if suppress_broadcast and msg.get("id"):
        store.delete([msg["id"]])

    # System messages never trigger routing - prevents infinite callback loops
    if sender == "system":
        return

    if router:
        router.note_message(channel, sender, timestamp=msg.get("timestamp"))
        _note_commander_activity(channel, sender, msg.get("timestamp"))

    if await _handle_commander_command(sender, text, channel):
        return

    # Check for slash commands — use stripped text (sans @mentions)
    if stripped == "/continue":
        if sender in known_agents:
            store.add("system", f"Loop guard: only humans can /continue. {sender} tried to self-resume.", channel=channel)
            return
        router.continue_routing(channel)
        store.add("system", f"Routing resumed by {sender}.", channel=channel)
        await broadcast_status()
        return

    if stripped == "/roastreview":
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        store.add(sender, f"{mentions} Time for a roast review! Inspect each other's work and constructively roast it.", channel=channel)
        return

    if stripped.startswith("/artchallenge"):
        parts = stripped.split(None, 1)
        theme = parts[1] if len(parts) > 1 else "anything you like"
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        store.add(
            sender,
            f"{mentions} Art challenge! Create an SVG artwork with the theme: **{theme}**. "
            "Write your SVG code to a .svg file, then attach it using chat_send(image_path=...). "
            "Make it creative, keep it under 5KB. Let's see what you've got!",
            channel=channel,
        )
        return

    if stripped == "/hatmaking":
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        all_instances = registry.get_all() if registry else {}
        agents_cfg = config.get("agents", {})
        color_parts = ", ".join(
            f"{a}={all_instances[a]['color']}" if a in all_instances
            else f"{a}={agents_cfg.get(a, {}).get('color', '#888')}"
            for a in agent_names
        )
        store.add(
            sender,
            f"{mentions} Hat making time! Design a new hat for your avatar using SVG. "
            "Use viewBox=\"0 0 32 16\" so it fits on top of a 32px avatar circle. "
            f"Background is dark (#0f0f17). Avatar colors: {color_parts}. Design for good contrast! "
            "Call chat_set_hat(sender=your_name, svg='<svg ...>...</svg>') to wear it. "
            "Be creative — top hats, party hats, crowns, propeller beanies, whatever you want!",
            channel=channel,
        )
        return

    if stripped.startswith("/poetry"):
        parts = stripped.split(None, 1)
        form = parts[1] if len(parts) > 1 else "haiku"
        if form not in ("haiku", "limerick", "sonnet"):
            form = "haiku"
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        prompts = {
            "haiku": "Write a haiku about the current state of this codebase.",
            "limerick": "Write a limerick about the current state of this codebase.",
            "sonnet": "Write a sonnet about the current state of this codebase.",
        }
        store.add(sender, f"{mentions} {prompts[form]}", channel=channel)
        return

    # Detect session draft blocks from agents only.
    # The session request prompt contains an example ```session block,
    # so treating every non-system sender as a draft source creates a false
    # invalid-draft card the moment the user asks for a custom session.
    _session_draft_re = _re.compile(r'```session\s*\n(.*?)\n```', _re.DOTALL)
    draft_match = _session_draft_re.search(text)
    known_agents = set(registry.get_all_names()) if registry else set()
    known_agents.update(config.get("agents", {}).keys())
    if draft_match and sender in known_agents:
        # Check if this is a revision of an existing draft
        draft_id, revision = _resolve_draft_lineage(text, channel)

        try:
            draft_json = json.loads(draft_match.group(1))
            errors = validate_session_template(draft_json)
            if errors:
                store.add(
                    "system",
                    f"Session draft from {sender} has errors:\n" + "\n".join(f"- {e}" for e in errors),
                    msg_type="session_draft",
                    channel=channel,
                    metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                              "template": draft_json, "errors": errors, "valid": False},
                )
            else:
                draft_json.setdefault("id", f"draft-{draft_id}")
                store.add(
                    "system",
                    f"Session draft from {sender}: **{draft_json.get('name', '?')}**",
                    msg_type="session_draft",
                    channel=channel,
                    metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                              "template": draft_json, "errors": [], "valid": True},
                )
        except json.JSONDecodeError:
            store.add(
                "system",
                f"Session draft from {sender} contains invalid JSON.",
                msg_type="session_draft",
                channel=channel,
                metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                           "errors": ["Invalid JSON in session block"], "valid": False},
            )

    raw_targets = router.get_targets(sender, text, channel)
    auto_dispatched = False
    auto_dispatch_plan: dict[str, object] = {"orchestrator": "", "commander": "", "workers": [], "targets": []}
    explicit_all_dispatch = (
        _auto_dispatch_enabled()
        and msg_type == "chat"
        and bool(text.strip())
        and sender not in known_agents
        and not stripped.startswith("/")
        and requests_all_agents(text)
    )
    if explicit_all_dispatch or (not raw_targets and _should_auto_dispatch(sender, text, msg_type, stripped, known_agents)):
        auto_dispatch_plan = _select_auto_dispatch_plan(text)
        raw_targets = list(auto_dispatch_plan.get("targets", []))
        commander_status = router.get_commander_status(channel) if router else {}
        if not explicit_all_dispatch and commander_status.get("locked") and (
            commander_status.get("active_agents") or commander_status.get("active_agent")
        ):
            active_targets = commander_status.get("active_agents") or [commander_status["active_agent"]]
            commander = str(auto_dispatch_plan.get("commander", "") or "")
            raw_targets = ([commander] if commander else []) + list(active_targets)
            auto_dispatch_plan = {
                "orchestrator": commander,
                "commander": commander,
                "workers": list(active_targets),
                "targets": raw_targets,
                "near_misses": [],
            }
        elif commander_status.get("standby"):
            standby = set(commander_status.get("standby", []))
            raw_targets = [target for target in raw_targets if target not in standby]
            auto_dispatch_plan["workers"] = [
                target for target in auto_dispatch_plan.get("workers", []) if target not in standby
            ]
            auto_dispatch_plan["targets"] = raw_targets
            auto_dispatch_plan["near_misses"] = [
                item
                for item in auto_dispatch_plan.get("near_misses", [])
                if item.get("name") not in standby
            ]
        commander = str(auto_dispatch_plan.get("commander", "") or "")
        workers = [str(worker) for worker in auto_dispatch_plan.get("workers", [])]
        if router and commander and workers and (explicit_all_dispatch or not commander_status.get("locked")):
            router.set_commander_lock(channel, active_agents=workers, updated_by=commander, reason=text.strip())
            _record_commander_lane(
                channel,
                commander=commander,
                workers=workers,
                task=text.strip(),
                reason=text.strip(),
                event_type="all-dispatch" if explicit_all_dispatch else "auto-dispatch",
            )
        auto_dispatched = bool(raw_targets)
        if auto_dispatched and config.get("routing", {}).get("auto_dispatch_announce", True):
            if commander and workers:
                worker_mentions = " ".join(f"@{worker}" for worker in workers)
                announcement = f"Orchestrator auto-dispatched: @{commander} coordinating {worker_mentions}"
            else:
                mentions = " ".join(f"@{target}" for target in raw_targets)
                announcement = f"Auto-dispatched to {mentions}"
            store.add(
                "system",
                announcement,
                msg_type="system",
                channel=channel,
                metadata={
                    "auto_dispatch": True,
                    "orchestrator": commander,
                    "commander": commander,
                    "workers": workers,
                    "targets": raw_targets,
                    "near_misses": auto_dispatch_plan.get("near_misses", []),
                },
            )
    # Resolve base family names to actual registered instances
    # e.g. 'claude' → 'claude-prime' when slot-1 was renamed
    targets = []
    for t in raw_targets:
        if registry:
            targets.extend(registry.resolve_to_instances(t))
        else:
            targets.append(t)
    targets = list(dict.fromkeys(targets))  # dedupe, preserve order

    if router.is_paused(channel):
        # Only emit the loop guard notice once per pause
        if not router.is_guard_emitted(channel):
            router.set_guard_emitted(channel)
            hop_limit = router.effective_max_hops(channel)
            hop_text = "unlimited" if hop_limit <= 0 else str(hop_limit)
            store.add(
                "system",
                f"Loop guard: {hop_text} agent-to-agent hops reached. "
                "Type /continue to resume.",
                channel=channel
            )
        return

    # Build a readable message string for the wake prompt
    chat_msg = f"{sender}: {text}" if text else ""
    custom_prompt = text if is_hidden_session_request else ""
    auto_dispatch_commander = str(auto_dispatch_plan.get("commander", "") or "")
    auto_dispatch_workers = [str(worker) for worker in auto_dispatch_plan.get("workers", [])]
    auto_dispatch_near_misses = [
        item for item in auto_dispatch_plan.get("near_misses", []) if isinstance(item, dict)
    ]

    # Session turn guard: if a session is active on this channel and the sender
    # is an agent, only allow triggering the agent whose turn it is.
    # Human @mentions are always allowed (the session engine handles pausing).
    sender_is_agent = sender in known_agents
    allowed_agent = session_engine.get_allowed_agent(channel) if session_engine and sender_is_agent else None

    import mcp_bridge
    for target in targets:
        # Skip pending instances — they haven't been named/claimed yet
        if registry:
            inst = registry.get_instance(target)
            if inst and inst.get("state") == "pending":
                continue
        # Session guard: suppress out-of-turn agent triggers
        if allowed_agent and target != allowed_agent:
            continue
        if not mcp_bridge.is_online(target):
            store.add("system", f"{target} appears offline — message queued.", msg_type="system", channel=channel)
        if agents.is_available(target):
            target_prompt = custom_prompt
            if auto_dispatched:
                target_prompt = _auto_dispatch_prompt(
                    channel,
                    target=target,
                    commander=auto_dispatch_commander,
                    workers=auto_dispatch_workers,
                    near_misses=auto_dispatch_near_misses,
                )
            await agents.trigger(target, message=chat_msg, channel=channel, prompt=target_prompt)


# --- broadcasting ---

async def _broadcast(raw_json: str):
    """Send a pre-serialized JSON string to all WebSocket clients."""
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(raw_json)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast(msg: dict):
    data = json.dumps({"type": "message", "data": msg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    status["commander"] = {
        ch: _commander_status_snapshot(ch)
        for ch in room_settings.get("channels", ["general"])
    }
    data = json.dumps({"type": "status", "data": status})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


def _public_lane_backlog(backlog: dict | None) -> dict:
    if not isinstance(backlog, dict):
        return {}
    items = []
    for raw_item in backlog.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        items.append(
            {
                "text": str(raw_item.get("text") or ""),
                "status": str(raw_item.get("status") or ""),
                "updated_by": str(raw_item.get("updated_by") or ""),
                "updated_at": float(raw_item.get("updated_at") or 0.0),
                "note": str(raw_item.get("note") or ""),
            }
        )
    return {
        "items": items,
        "current_index": int(backlog.get("current_index", 0) or 0),
        "worker": str(backlog.get("worker") or ""),
        "reviewer": str(backlog.get("reviewer") or ""),
        "note": str(backlog.get("note") or ""),
        "auto_advance": bool(backlog.get("auto_advance", True)),
        "updated_at": float(backlog.get("updated_at") or 0.0),
    }


def _public_commander_lane(lane: dict | None) -> dict:
    if not isinstance(lane, dict):
        return {}
    return {
        "status": str(lane.get("status") or ""),
        "channel": str(lane.get("channel") or ""),
        "commander": str(lane.get("commander") or ""),
        "active_agents": list(lane.get("active_agents") or []),
        "task": str(lane.get("task") or ""),
        "reason": str(lane.get("reason") or ""),
        "updated_at": float(lane.get("updated_at") or 0.0),
        "progress": dict(lane.get("progress") or {}),
        "backlog": _public_lane_backlog(lane.get("backlog")),
    }


def _commander_status_snapshot(channel: str) -> dict:
    status = router.get_commander_status(channel) if router else {}
    lane = commander_ledger.get(channel) if commander_ledger else None
    if not lane or lane.get("status") != "active":
        return status

    public_lane = _public_commander_lane(lane)
    merged_progress = dict(status.get("worker_progress") or {})
    for agent, progress in public_lane.get("progress", {}).items():
        if isinstance(progress, dict):
            merged_progress.setdefault(agent, dict(progress))

    status["lane"] = public_lane
    status["lane_status"] = public_lane.get("status", "")
    status["commander"] = public_lane.get("commander", "")
    status["task"] = public_lane.get("task", "")
    status["goal"] = (
        public_lane.get("backlog", {}).get("note")
        or public_lane.get("reason")
        or public_lane.get("task")
        or ""
    )
    status["backlog"] = public_lane.get("backlog", {})
    status["worker_progress"] = merged_progress
    return status


async def broadcast_typing(agent_name: str, is_typing: bool):
    data = json.dumps({"type": "typing", "agent": agent_name, "active": is_typing})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_clear(channel: str | None = None):
    payload = {"type": "clear"}
    if channel:
        payload["channel"] = channel
    data = json.dumps(payload)
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_todo_update(msg_id: int, status: str | None):
    data = json.dumps({"type": "todo_update", "data": {"id": msg_id, "status": status}})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_settings():
    data = json.dumps({"type": "settings", "data": room_settings})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_rule(action: str, rule: dict):
    data = json.dumps({"type": "rule", "action": action, "data": rule})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_job(action: str, data: dict):
    payload = json.dumps({"type": "job", "action": action, "data": data})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_schedule(action: str, schedule: dict):
    payload = json.dumps({"type": "schedule", "action": action, "data": schedule})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_session(action: str, session: dict):
    payload = json.dumps({"type": "session", "action": action, "data": session})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_mission(action: str, mission: dict):
    payload = json.dumps({"type": "mission", "action": action, "data": mission})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_hats():
    data = json.dumps({"type": "hats", "data": agent_hats})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_agents():
    """Send updated agent config (from registry) to all WebSocket clients."""
    agent_cfg = registry.get_agent_config() if registry else {}
    data = json.dumps({"type": "agents", "data": agent_cfg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


def _on_registry_change():
    """Called from registry (any thread) when instances register/deregister/claim/rename."""
    # Update router with current agent names (base names + registered instances)
    if router and registry:
        base_names = list(registry.get_bases().keys())
        # Only include active instances in routing (pending ones are inert)
        instance_names = registry.get_active_names()
        all_names = list(set(base_names + instance_names))
        router.update_agents(all_names)
    # Broadcast to WebSocket clients
    if _event_loop:
        asyncio.run_coroutine_threadsafe(_maybe_restore_commander_lanes(), _event_loop)
        asyncio.run_coroutine_threadsafe(broadcast_agents(), _event_loop)
        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)


def _sync_profile_identity(old_name: str, new_name: str, label: str, inst: dict | None = None):
    if not agent_profiles:
        return
    inst = inst or (registry.get_instance(new_name) if registry else None) or (registry.get_instance(old_name) if registry else None)
    base = inst.get("base", "") if inst else agent_profiles.infer_base(new_name)
    profile_id = inst.get("profile_id", "") if inst else ""
    try:
        import mcp_bridge
        role = mcp_bridge.get_role(new_name) or mcp_bridge.get_role(old_name)
    except Exception:
        role = ""
    agent_profiles.update_identity(
        old_name=old_name,
        new_name=new_name,
        label=label,
        base=base,
        profile_id=profile_id,
        role=role,
    )


def _sync_profile_role(agent_name: str, role: str):
    if not agent_profiles:
        return
    inst = registry.get_instance(agent_name) if registry else None
    agent_profiles.update_role_for_name(
        agent_name,
        role,
        base=inst.get("base", "") if inst else "",
        profile_id=inst.get("profile_id", "") if inst else "",
        label=inst.get("label", "") if inst else "",
    )


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --- Security: validate session token on WebSocket connect ---
    token = websocket.query_params.get("token", "")
    if token != session_token:
        # Must accept before closing so the browser receives the close frame.
        # Code 4003 triggers an auto-reload in the client to pick up the new token.
        await websocket.accept()
        await websocket.close(code=4003, reason="forbidden: invalid session token")
        return

    await websocket.accept()
    ws_clients.add(websocket)

    # Send settings
    await websocket.send_text(json.dumps({"type": "settings", "data": room_settings}))

    # Send registered instances (used for pills/mentions)
    agent_cfg = registry.get_agent_config() if registry else {}
    await websocket.send_text(json.dumps({"type": "agents", "data": agent_cfg}))

    # Send base agent colors (used for message coloring, no pills)
    base_colors = {}
    for name, cfg in config.get("agents", {}).items():
        base_colors[name] = {"color": cfg.get("color", "#888"), "label": cfg.get("label", name)}
    await websocket.send_text(json.dumps({"type": "base_colors", "data": base_colors}))

    # Send todos {msg_id: status}
    await websocket.send_text(json.dumps({"type": "todos", "data": store.get_todos()}))

    # Send rules
    await websocket.send_text(json.dumps({"type": "rules", "data": rules.list_all()}))

    # Send hats
    await websocket.send_text(json.dumps({"type": "hats", "data": agent_hats}))

    # Send jobs
    await websocket.send_text(json.dumps({"type": "jobs", "data": jobs.list_all()}))

    # Send schedules
    await websocket.send_text(json.dumps({"type": "schedules", "data": schedules.list_all()}))

    # Send pending instances (so late-connecting browsers still see the naming lightbox)
    if registry:
        for inst in registry.get_all().values():
            if inst.get("state") == "pending":
                await websocket.send_text(json.dumps({
                    "type": "pending_instance",
                    "name": inst["name"],
                    "base": inst.get("base", ""),
                    "label": inst.get("label", inst["name"]),
                    "color": inst.get("color", "#888"),
                }))

    # Send history (per channel based on history_limit)
    limit_val = room_settings.get("history_limit", "all")
    count = 10000 if limit_val == "all" else int(limit_val)
    
    history = []
    for ch in room_settings["channels"]:
        history.extend(store.get_recent(count, channel=ch))
    
    # Sort history by timestamp to interleave messages from different channels correctly
    history.sort(key=lambda m: m.get("timestamp", 0))
    
    for msg in history:
        await websocket.send_text(json.dumps({"type": "message", "data": msg}))

    # Send status
    await broadcast_status()

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)

            if event.get("type") == "message":
                text = event.get("text", "").strip()
                attachments = event.get("attachments", [])
                sender = event.get("sender") or room_settings.get("username", "user")
                channel = event.get("channel", "general")

                if not text and not attachments:
                    continue

                # Command handling
                if text.startswith("/"):
                    cmd_parts = text.split()
                    cmd = cmd_parts[0].lower()
                    if _is_commander_command(_strip_mentions_for_command(text)):
                        await _handle_commander_command(sender, text, channel)
                        continue
                    if cmd == "/clear":
                        store.clear(channel=channel)
                        await broadcast_clear(channel=channel)
                        continue
                    if cmd == "/continue":
                        router.continue_routing(channel)
                        store.add("system", "Resuming agent conversation...", msg_type="system", channel=channel)
                        await broadcast_status()
                        continue
                    # Broadcast slash commands — expand without storing the raw command.
                    # _handle_new_message will store the expanded version.
                    if cmd in ("/hatmaking", "/artchallenge", "/roastreview", "/poetry"):
                        await _handle_new_message({"sender": sender, "text": text, "channel": channel})
                        continue

                # Store message — the on_message callback handles broadcast + triggers
                reply_to = event.get("reply_to")
                if reply_to is not None:
                    reply_to = int(reply_to)

                store.add(sender, text, attachments=attachments, reply_to=reply_to,
                          channel=channel)

            elif event.get("type") == "delete":
                ids = event.get("ids", [])
                if ids:
                    deleted = store.delete([int(i) for i in ids])
                    if deleted:
                        data = json.dumps({"type": "delete", "ids": deleted})
                        dead = set()
                        for client in list(ws_clients):
                            try:
                                await client.send_text(data)
                            except Exception:
                                dead.add(client)
                        ws_clients.difference_update(dead)
                continue

            elif event.get("type") == "todo_add":
                msg_id = event.get("id")
                if msg_id is not None:
                    store.add_todo(int(msg_id))
                    await broadcast_todo_update(int(msg_id), "todo")
                continue

            elif event.get("type") == "todo_toggle":
                msg_id = event.get("id")
                if msg_id is not None:
                    mid = int(msg_id)
                    status = store.get_todo_status(mid)
                    if status == "todo":
                        store.complete_todo(mid)
                        await broadcast_todo_update(mid, "done")
                    elif status == "done":
                        store.reopen_todo(mid)
                        await broadcast_todo_update(mid, "todo")
                continue

            elif event.get("type") == "todo_remove":
                msg_id = event.get("id")
                if msg_id is not None:
                    store.remove_todo(int(msg_id))
                    await broadcast_todo_update(int(msg_id), None)
                continue

            elif event.get("type") in ("decision_propose", "rule_propose"):
                text = event.get("text") or event.get("decision", "")
                text = text.strip()
                author = event.get("author") or event.get("owner") or room_settings.get("username", "user")
                reason = event.get("reason", "")
                is_human = author.lower() == room_settings.get("username", "user").lower()
                if text:
                    rule = rules.propose(text, author, reason)
                    if rule:
                        if is_human:
                            # Human-created rules go straight to draft, no card
                            rules.make_draft(rule["id"])
                        else:
                            # Agent proposals get a card in the timeline
                            channel = event.get("channel", "general")
                            msg = store.add(
                                author, f"Rule proposal: {text}",
                                msg_type="rule_proposal",
                                channel=channel,
                                metadata={"rule_id": rule["id"], "text": text, "status": "pending"},
                            )
                            # store.add() fires _on_store_message → broadcast already.
                            # Do NOT call broadcast(msg) again here.
                continue

            elif event.get("type") in ("decision_approve", "rule_activate"):
                rid = event.get("id")
                if rid is not None:
                    rules.activate(int(rid))
                continue

            elif event.get("type") in ("decision_unapprove", "rule_deactivate"):
                rid = event.get("id")
                if rid is not None:
                    rules.deactivate(int(rid))
                continue

            elif event.get("type") == "rule_make_draft":
                rid = event.get("id")
                if rid is not None:
                    rules.make_draft(int(rid))
                continue

            elif event.get("type") in ("decision_edit", "rule_edit"):
                rid = event.get("id")
                if rid is not None:
                    rules.edit(
                        int(rid),
                        text=event.get("text") or event.get("decision"),
                        reason=event.get("reason"),
                    )
                continue

            elif event.get("type") in ("decision_delete", "rule_delete"):
                rid = event.get("id")
                if rid is not None:
                    rules.delete(int(rid))
                continue

            elif event.get("type") == "rule_remind":
                rules.set_remind()
                remind_data = json.dumps({"type": "rules_remind", "data": {}})
                for client in list(ws_clients):
                    try:
                        await client.send_text(remind_data)
                    except Exception:
                        pass
                continue

            elif event.get("type") == "update_settings":
                new = event.get("data", {})
                if "title" in new and isinstance(new["title"], str):
                    room_settings["title"] = new["title"].strip() or "agentchattr"
                if "username" in new and isinstance(new["username"], str):
                    room_settings["username"] = new["username"].strip() or "user"
                if "font" in new and new["font"] in ("mono", "serif", "sans"):
                    room_settings["font"] = new["font"]
                if "max_agent_hops" in new:
                    try:
                        hops = int(new["max_agent_hops"])
                        hops = max(1, min(hops, 1000))
                        room_settings["max_agent_hops"] = hops
                        router.max_hops = hops
                    except (ValueError, TypeError):
                        pass
                if "contrast" in new and new["contrast"] in ("normal", "high"):
                    room_settings["contrast"] = new["contrast"]
                if "rules_refresh_interval" in new:
                    try:
                        ri = int(new["rules_refresh_interval"])
                        room_settings["rules_refresh_interval"] = max(0, min(ri, 100))
                    except (ValueError, TypeError):
                        pass
                if "history_limit" in new:
                    val = str(new["history_limit"]).strip().lower()
                    if val == "all":
                        room_settings["history_limit"] = "all"
                    else:
                        try:
                            val_int = int(val)
                            room_settings["history_limit"] = max(1, min(val_int, 10000))
                        except (ValueError, TypeError):
                            pass
                if "custom_roles" in new and isinstance(new["custom_roles"], list):
                    room_settings["custom_roles"] = [
                        str(r).strip()[:20] for r in new["custom_roles"]
                        if isinstance(r, str) and r.strip()
                    ][:20]
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "rename_agent":
                agent_name = (event.get("name") or "").strip()
                new_label = (event.get("label") or "").strip()
                if agent_name and new_label and registry:
                    inst_before = registry.get_instance(agent_name)
                    # Derive a sanitized sender ID from the label
                    import re as _re
                    new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                    if not new_id:
                        new_id = agent_name  # fallback: keep old name, just change label
                    if new_id == agent_name:
                        # Same ID — label-only change
                        registry.set_label(agent_name, new_label)
                        _sync_profile_identity(agent_name, agent_name, new_label, inst_before)
                    else:
                        result = registry.rename(agent_name, new_id, new_label)
                        if isinstance(result, str):
                            # Rename failed (collision etc.) — fall back to label-only
                            registry.set_label(agent_name, new_label)
                            _sync_profile_identity(agent_name, agent_name, new_label, inst_before)
                        else:
                            # Migrate presence + cursors to new name
                            import mcp_bridge
                            mcp_bridge.migrate_identity(agent_name, new_id)
                            _sync_profile_identity(agent_name, new_id, new_label, result)
                            # Update sender on all historical messages
                            store.rename_sender(agent_name, new_id)
                            # Notify clients so they can update sender in DOM
                            rename_event = json.dumps({
                                "type": "agent_renamed",
                                "old_name": agent_name,
                                "new_name": new_id,
                            })
                            await _broadcast(rename_event)
                continue

            elif event.get("type") == "name_pending":
                # Human names a pending instance (from lightbox)
                agent_name = (event.get("name") or "").strip()
                new_label = (event.get("label") or "").strip()
                if agent_name and registry:
                    inst_before = registry.get_instance(agent_name)
                    if not new_label:
                        # Accept default name
                        registry.confirm_pending(agent_name)
                        _sync_profile_identity(agent_name, agent_name, agent_name, inst_before)
                    else:
                        import re as _re
                        new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                        if not new_id:
                            new_id = agent_name
                        if new_id == agent_name:
                            # Same ID — just update label and confirm
                            registry.set_label(agent_name, new_label)
                            registry.confirm_pending(agent_name)
                            _sync_profile_identity(agent_name, agent_name, new_label, inst_before)
                        else:
                            result = registry.rename(agent_name, new_id, new_label)
                            if isinstance(result, str):
                                # Rename failed — just confirm with label
                                registry.set_label(agent_name, new_label)
                                registry.confirm_pending(agent_name)
                                _sync_profile_identity(agent_name, agent_name, new_label, inst_before)
                            else:
                                # Rename succeeded — confirm new name
                                registry.confirm_pending(new_id)
                                import mcp_bridge
                                mcp_bridge.migrate_identity(agent_name, new_id)
                                _sync_profile_identity(agent_name, new_id, new_label, result)
                                # Update sender on all historical messages
                                store.rename_sender(agent_name, new_id)
                                rename_event = json.dumps({
                                    "type": "agent_renamed",
                                    "old_name": agent_name,
                                    "new_name": new_id,
                                })
                                await _broadcast(rename_event)
                continue

            elif event.get("type") == "channel_create":
                name = (event.get("name") or "").strip().lower()
                if not name or not _CHANNEL_NAME_RE.match(name):
                    continue
                if name in room_settings["channels"]:
                    continue
                if len(room_settings["channels"]) >= MAX_CHANNELS:
                    continue
                room_settings["channels"].append(name)
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "channel_rename":
                old_name = (event.get("old_name") or "").strip().lower()
                new_name = (event.get("new_name") or "").strip().lower()
                if old_name == "general":
                    continue
                if not new_name or not _CHANNEL_NAME_RE.match(new_name):
                    continue
                if old_name not in room_settings["channels"]:
                    continue
                if new_name in room_settings["channels"]:
                    continue
                idx = room_settings["channels"].index(old_name)
                room_settings["channels"][idx] = new_name
                store.rename_channel(old_name, new_name)
                import mcp_bridge
                mcp_bridge.migrate_cursors_rename(old_name, new_name)
                _save_settings()
                await broadcast_settings()
                # Tell clients to migrate DOM elements
                rename_event = json.dumps({
                    "type": "channel_renamed",
                    "old_name": old_name,
                    "new_name": new_name,
                })
                for c in list(ws_clients):
                    try:
                        await c.send_text(rename_event)
                    except Exception:
                        pass

            elif event.get("type") == "channel_delete":
                name = (event.get("name") or "").strip().lower()
                if name == "general":
                    continue
                if name not in room_settings["channels"]:
                    continue
                room_settings["channels"].remove(name)
                store.delete_channel(name)
                import mcp_bridge
                mcp_bridge.migrate_cursors_delete(name)
                _save_settings()
                await broadcast_settings()

    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception:
        ws_clients.discard(websocket)
        log.exception("WebSocket error")


# --- REST endpoints ---

ALLOWED_UPLOAD_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB default


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".png"
    if ext.lower() not in ALLOWED_UPLOAD_EXTS:
        return JSONResponse({"error": f"unsupported file type: {ext}"}, status_code=400)

    content = await file.read()
    max_bytes = config.get("images", {}).get("max_size_mb", 10) * 1024 * 1024
    if len(content) > max_bytes:
        return JSONResponse({"error": f"file too large (max {max_bytes // 1024 // 1024} MB)"}, status_code=400)

    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = upload_dir / filename
    filepath.write_bytes(content)

    return JSONResponse({
        "name": file.filename,
        "url": f"/uploads/{filename}",
    })


# --- Export / Import ---

@app.get("/api/export")
async def export_history():
    """Download a zip archive of project history."""
    import archive as _archive
    import time as _time
    try:
        zip_bytes = _archive.build_export(
            store, jobs, rules, summaries,
            app_version=config.get("server", {}).get("version", ""),
        )
    except Exception as exc:
        return JSONResponse({"error": f"export failed: {exc}"}, status_code=500)
    filename = f"agentchattr-export-{_time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/import")
async def import_history(file: UploadFile = File(...)):
    """Upload a zip archive and merge it into current stores."""
    import archive as _archive
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return JSONResponse({"error": "unsupported file type: expected .zip"}, status_code=400)
    content = await file.read()
    if len(content) > _archive.MAX_IMPORT_SIZE:
        return JSONResponse(
            {"error": f"file too large (max {_archive.MAX_IMPORT_SIZE // 1024 // 1024}MB)"},
            status_code=400,
        )
    channel_list = list(room_settings.get("channels", ["general"]))
    max_ch = room_settings.get("max_channels", 8)
    report = _archive.import_archive(
        content, store, jobs, rules, summaries,
        channel_list, max_channels=max_ch,
    )
    if not report.get("ok"):
        error = report.get("error", "import failed")
        status = 409 if "already running" in error else 400
        return JSONResponse({"error": error}, status_code=status)
    # Update channel list if new channels were created
    if report["channels"]["created"]:
        room_settings["channels"] = channel_list
        _save_settings()
        await broadcast_settings()
    # Tell all connected clients to reload (picks up imported messages)
    data = json.dumps({"type": "reload"})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)
    return JSONResponse(report)


@app.get("/api/messages")
async def get_messages(since_id: int = 0, limit: int = 50, channel: str = ""):
    ch = channel if channel else None
    if since_id:
        return store.get_since(since_id, channel=ch)
    return store.get_recent(limit, channel=ch)


@app.post("/api/send")
async def api_send(request: Request):
    """REST endpoint for API agents to send messages without WebSocket.

    Authenticated via Bearer registration token. Sender is resolved from
    the token — the agent cannot impersonate another identity.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing Authorization: Bearer <token>"}, status_code=401)
    token = auth[7:].strip()
    inst = registry.resolve_token(token) if registry else None
    if not inst:
        return JSONResponse({"error": "invalid or expired token"}, status_code=403)

    sender = inst["name"]
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    channel = body.get("channel", "general")

    msg = store.add(sender, text, channel=channel)
    return JSONResponse(msg)


STOP_ALL_AGENTS_PROMPT = (
    "Stop your current work now and stand by. Do not continue the current task, "
    "do not wake other agents, do not deregister yourself, and stay registered in agentchattr."
)


async def _request_registered_agents_to_stop(channel: str) -> dict:
    report = {
        "registered": [],
        "requested": [],
        "unavailable": [],
        "errors": [],
    }

    if registry:
        names = list(registry.get_all_names())
        report["registered"] = names
        for name in names:
            try:
                if agents and agents.is_available(name):
                    await agents.trigger(
                        name,
                        message="system: Stop current work and stand by.",
                        channel=channel,
                        prompt=STOP_ALL_AGENTS_PROMPT,
                    )
                    report["requested"].append(name)
                else:
                    report["unavailable"].append(name)
            except Exception as exc:
                report["errors"].append(f"request stop {name}: {exc}")

    return report


@app.post("/api/agents/stop-all")
async def stop_all_agents():
    channel = _last_active_channel or "general"
    report = await _request_registered_agents_to_stop(channel)
    count = len(report.get("requested", []))
    store.add(
        "system",
        f"Stop command sent to {count} agent(s). Agents remain registered.",
        msg_type="system",
        channel=channel,
        metadata={"stop_all_agents": report},
    )
    if _event_loop:
        await broadcast_status()
    return JSONResponse(report)


@app.get("/api/status")
async def get_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    status["commander"] = {
        ch: _commander_status_snapshot(ch)
        for ch in room_settings.get("channels", ["general"])
    }
    return status


@app.get("/api/agents/registered/{agent_name}")
async def agent_registered(agent_name: str):
    """Local readiness probe used by launcher scripts."""
    if registry and registry.is_registered(normalize_profile_id(agent_name)):
        return JSONResponse({"registered": True})
    return JSONResponse({"registered": False}, status_code=404)


@app.get("/api/settings")
async def get_settings():
    return room_settings


@app.delete("/api/hat/{agent_name}")
async def delete_hat(agent_name: str):
    """Remove an agent's hat (called by the trash-can UI)."""
    clear_agent_hat(agent_name)
    return JSONResponse({"ok": True})


# --- Jobs API ---

@app.get("/api/schedules")
async def get_schedules():
    return schedules.list_all()


@app.post("/api/schedules")
async def create_schedule(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    targets = body.get("targets", [])
    channel = body.get("channel", "general")
    spec = body.get("spec", "")
    one_shot = body.get("one_shot", False)
    send_at_date = body.get("send_at_date", "")  # "YYYY-MM-DD" for one-shot
    created_by = body.get("created_by", "user")
    if not prompt or not targets or not spec:
        return JSONResponse({"error": "prompt, targets, and spec are required"}, status_code=400)
    interval_sec, daily_at = parse_schedule_spec(spec)
    if interval_sec is None:
        return JSONResponse({"error": f"Invalid schedule spec: {spec}"}, status_code=400)
    # For one-shot, compute exact send_at timestamp from date + daily_at time
    send_at = None
    if one_shot and daily_at and send_at_date:
        import datetime as _dt
        try:
            dt = _dt.datetime.strptime(f"{send_at_date} {daily_at}", "%Y-%m-%d %H:%M")
            send_at = dt.timestamp()
        except ValueError:
            pass
    s = schedules.create(
        prompt=prompt, targets=targets, channel=channel,
        interval_seconds=interval_sec, daily_at=daily_at,
        one_shot=one_shot, send_at=send_at,
        created_by=created_by,
    )
    return JSONResponse(s)


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    removed = schedules.delete(schedule_id)
    if not removed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.patch("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str):
    result = schedules.toggle(schedule_id)
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/api/jobs")
async def get_jobs(channel: str = "", status: str = ""):
    """List jobs, optionally filtered."""
    ch = channel if channel else None
    st = status if status else None
    return jobs.list_all(channel=ch, status=st)


@app.post("/api/messages/{msg_id}/demote")
async def demote_proposal(msg_id: int):
    """Demote a proposal-style message back to a regular chat message."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    msg_type = msg.get("type")
    if msg_type not in {"job_proposal", "session_draft"}:
        return JSONResponse({"error": "not a proposal"}, status_code=400)
    meta = msg.get("metadata", {})
    updated_fields = {"type": "chat", "metadata": {}}

    if msg_type == "job_proposal":
        body_text = meta.get("body", "")
        title = meta.get("title", "")
        plain_text = f"**{title}**\n\n{body_text}" if title else body_text or msg.get("text", "")
        updated_fields["text"] = plain_text
    else:
        tmpl = meta.get("template")
        errors = meta.get("errors", []) or []
        proposed_by = meta.get("proposed_by") or msg.get("sender", "system")
        parts = []

        if isinstance(tmpl, dict):
            name = str(tmpl.get("name", "")).strip()
            desc = str(tmpl.get("description", "")).strip()
            if name:
                parts.append(f"**{name}**")
            if desc:
                parts.append(desc)
            phases = tmpl.get("phases") or []
            if phases:
                lines = []
                for i, ph in enumerate(phases, 1):
                    ph_name = ph.get("name", f"Round {i}")
                    participants = ", ".join(ph.get("participants", []))
                    line = f"{i}. {ph_name}"
                    if participants:
                        line += f" -- {participants}"
                    prompt = (ph.get("prompt") or "").strip()
                    if prompt:
                        line += f"\n   {prompt}"
                    lines.append(line)
                parts.append("\n".join(lines))
        else:
            label = str(msg.get("text", "")).strip() or "Session draft"
            parts.append(label)
            if errors:
                parts.append("\n".join(f"- {e}" for e in errors))

        updated_fields["sender"] = proposed_by
        updated_fields["text"] = "\n\n".join(p for p in parts if p).strip()

    updated = store.update_message(msg_id, updated_fields)
    if updated:
        # Broadcast the updated message to all clients
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/messages/{msg_id}/resolve_decision")
async def resolve_decision(msg_id: int, request: Request):
    """Resolve an inline decision card by recording the chosen option."""
    body = await request.json()
    chosen = body.get("choice", "")
    if not chosen:
        return JSONResponse({"error": "choice is required"}, status_code=400)
    # Atomic check + resolve under lock to prevent double-click race
    error = None
    channel = "general"
    sender = ""
    with store._lock:
        msg = None
        for m in store._messages:
            if m["id"] == msg_id:
                msg = m
                break
        if not msg:
            error = ("message not found", 404)
        elif msg.get("type") != "decision":
            error = ("not a decision message", 400)
        else:
            meta = msg.get("metadata") or {}
            if meta.get("resolved"):
                error = ("already resolved", 400)
            else:
                valid_choices = meta.get("choices", [])
                if valid_choices and chosen not in valid_choices:
                    error = (f"invalid choice. Valid: {valid_choices}", 400)
                else:
                    meta["resolved"] = True
                    meta["chosen"] = chosen
                    msg["metadata"] = meta
                    channel = msg.get("channel", "general")
                    sender = msg.get("sender", "")
                    store._rewrite()
    if error:
        return JSONResponse({"error": error[0]}, status_code=error[1])
    # Post the chosen answer as a regular chat message tagged @sender
    username = room_settings.get("username", "user")
    reply_text = f"@{sender} {chosen}" if sender else chosen
    try:
        store.add(username, reply_text, reply_to=msg_id, channel=channel)
    except Exception:
        import traceback; traceback.print_exc()
    # Broadcast updated decision card so the UI swaps buttons to resolved state
    updated = store.get_by_id(msg_id)
    if updated:
        await _broadcast(json.dumps({"type": "message_update", "message": updated}))
    return {"ok": True, "chosen": chosen}


@app.post("/api/messages/{msg_id}/resolve_rule_proposal")
async def resolve_rule_proposal(msg_id: int, request: Request):
    """Activate or dismiss a rule proposal."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if msg.get("type") != "rule_proposal":
        return JSONResponse({"error": "not a rule proposal"}, status_code=400)
    body = await request.json()
    action = body.get("action", "")
    meta = msg.get("metadata", {})
    rule_id = meta.get("rule_id")

    if action == "activate" and rule_id is not None:
        rules.activate(int(rule_id))
        meta["status"] = "activated"
    elif action == "draft" and rule_id is not None:
        rules.make_draft(int(rule_id))
        meta["status"] = "drafted"
    elif action == "dismiss" and rule_id is not None:
        rules.delete(int(rule_id))
        meta["status"] = "dismissed"
    else:
        return JSONResponse({"error": "invalid action"}, status_code=400)

    updated = store.update_message(msg_id, {"metadata": meta})
    if updated:
        # Broadcast the updated message so all clients re-render the card
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/messages/{msg_id}/demote_rule_proposal")
async def demote_rule_proposal(msg_id: int):
    """Demote a rule_proposal message back to a regular chat message and delete the rule."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if msg.get("type") != "rule_proposal":
        return JSONResponse({"error": "not a rule proposal"}, status_code=400)
    meta = msg.get("metadata", {})
    rule_id = meta.get("rule_id")
    if rule_id is not None:
        rules.delete(int(rule_id))
    text = meta.get("text", msg.get("text", ""))
    updated = store.update_message(msg_id, {
        "type": "chat",
        "text": text,
        "metadata": {},
    })
    if updated:
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/trigger-agent")
async def trigger_agent_silent(request: Request):
    """Silently trigger an agent with a message (no chat message posted)."""
    body = await request.json()
    agent_name = body.get("agent", "").strip()
    message = body.get("message", "").strip()
    channel = body.get("channel", "general")
    source_msg_id = body.get("source_msg_id")
    if not agent_name or not message:
        return JSONResponse({"error": "agent and message required"}, status_code=400)

    custom_prompt = body.get("prompt", "").strip()
    if not custom_prompt:
        if source_msg_id is not None:
            custom_prompt = (
                f"use mcp to read #{channel} - you're mentioned, take appropriate action and respond "
                f"- conversion request: use chat history to find message #{source_msg_id} "
                f"and use chat_propose_job to propose it as a job with title<=80 chars and body<=500 chars."
            )
        else:
            custom_prompt = (
                f"use mcp to read #{channel} - you're mentioned, take appropriate action and respond "
                f"- conversion request: use chat_propose_job to propose a job from the referenced message."
            )
    # Resolve to instances if multi-instance
    targets = [agent_name]
    if registry:
        resolved = registry.resolve_to_instances(agent_name)
        if resolved:
            targets = resolved
    for target in targets:
        if agents.is_available(target):
            await agents.trigger(target, message=message, channel=channel, prompt=custom_prompt)
    return {"ok": True, "triggered": targets}


def _build_mission_kickoff_text(mission: dict) -> str:
    crew_mentions = " ".join(f"@{m.get('agent', '').strip()}"
                             for m in mission.get("crew", [])
                             if m.get("agent"))
    lines = [
        f"{crew_mentions} Mission {mission['id']} briefing",
        "",
        f"**Title:** {mission['title']}",
    ]
    obj = (mission.get("objective") or "").strip()
    if obj:
        lines += ["", f"**Objective:** {obj}"]
    deliverables = mission.get("deliverables") or []
    required = [d for d in deliverables if d.get("required")]
    if required:
        lines += ["", "**Deliverables (required):**"]
        for d in required:
            lines.append(f"- {d.get('text', '').strip()}")
    reviewer = (mission.get("reviewer") or "").strip()
    if reviewer:
        lines += ["", f"**Reviewer:** @{reviewer}"]
    eta = int(mission.get("eta_minutes") or 0)
    hops = int(mission.get("hop_budget") or 0)
    if eta or hops:
        budgets = []
        if eta:
            budgets.append(f"ETA {eta}m")
        if hops:
            budgets.append(f"hop budget {hops}")
        lines += ["", f"**Budgets:** {' · '.join(budgets)}"]
    return "\n".join(lines)


@app.post("/api/missions", status_code=201)
async def create_mission(request: Request):
    if missions is None:
        raise HTTPException(status_code=503, detail="server not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    crew = body.get("crew") or []
    if not isinstance(crew, list) or not crew:
        raise HTTPException(status_code=400, detail="crew required (at least one agent)")

    mission = missions.create(
        title=title,
        objective=(body.get("objective") or "").strip(),
        crew=crew,
        reviewer=(body.get("reviewer") or "").strip(),
        deliverables=body.get("deliverables") or [],
        eta_minutes=int(body.get("eta_minutes") or 0),
        hop_budget=int(body.get("hop_budget") or 0),
        auto_pause_blockers=int(body.get("auto_pause_blockers") or 0),
    )

    # Transition briefing → active and post kickoff message.
    missions.update_status(mission["id"], "active")
    active_mission = missions.get(mission["id"])
    if store is not None:
        kickoff_text = _build_mission_kickoff_text(active_mission)
        store.add(
            sender="user",
            text=kickoff_text,
            channel=active_mission["transcript_channel_id"],
        )
    return active_mission


@app.get("/api/missions")
async def list_missions():
    if missions is None:
        raise HTTPException(status_code=503, detail="server not configured")
    return missions.list_all()


@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: str):
    if missions is None:
        raise HTTPException(status_code=503, detail="server not configured")
    m = missions.get(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


def _post_intervention_to_channel(channel: str, *, action: str, agent: str, text: str) -> None:
    """Translate an intervention into chat messages on the mission channel."""
    if store is None:
        return
    agent = agent.strip().lstrip("@")
    if action == "freeze":
        store.add(sender="user", text=f"/freeze @{agent}",
                  msg_type="message", channel=channel)
    elif action == "redirect":
        instruction = (text or "").strip()
        if instruction:
            store.add(sender="user", text=f"@{agent} {instruction}",
                      msg_type="message", channel=channel)
        else:
            store.add(sender="user", text=f"@{agent} please pause and clarify your current task.",
                      msg_type="message", channel=channel)
    elif action == "stop":
        store.add(sender="user", text=f"/freeze @{agent}",
                  msg_type="message", channel=channel)
        store.add(sender="system",
                  text=f"human · stopped {agent}", msg_type="system", channel=channel)


@app.post("/api/missions/{mission_id}/intervene")
async def intervene_mission(mission_id: str, request: Request):
    if missions is None:
        raise HTTPException(status_code=503, detail="server not configured")
    mission = missions.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="mission not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    action = (body.get("action") or "").strip()
    if action not in ("freeze", "redirect", "stop"):
        raise HTTPException(status_code=400, detail="action must be freeze | redirect | stop")
    agent = (body.get("agent") or "").strip()
    if not agent:
        raise HTTPException(status_code=400, detail="agent required")
    text = body.get("text") or ""
    _post_intervention_to_channel(
        mission["transcript_channel_id"],
        action=action,
        agent=agent,
        text=text,
    )
    if missions is not None:
        body_text = ""
        if action == "freeze":
            body_text = f"human · froze {agent}"
        elif action == "redirect":
            body_text = f"human · redirected {agent}: {(text or '').strip()[:80]}"
        elif action == "stop":
            body_text = f"human · stopped {agent}"
        missions.append_decision(mission_id, type="intervention",
                                 agent=agent, body=body_text)
    return {"ok": True, "mission_id": mission_id, "action": action, "agent": agent}


@app.post("/api/missions/{mission_id}/complete")
async def complete_mission(mission_id: str):
    if missions is None:
        raise HTTPException(status_code=503, detail="server not configured")
    if missions.get(mission_id) is None:
        raise HTTPException(status_code=404, detail="mission not found")
    updated = missions.update_status(mission_id, "complete")
    if updated is None:
        raise HTTPException(status_code=500, detail="failed to complete mission")
    missions.append_decision(mission_id, type="complete", agent="",
                             body="mission marked complete by human")
    return updated


@app.post("/api/jobs")
async def create_job(request: Request):
    """Create a new job."""
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    job_type = body.get("type", "job")
    channel = body.get("channel", "general")
    created_by = body.get("created_by", "user")
    anchor_msg_id = body.get("anchor_msg_id")
    assignee = body.get("assignee", "")
    job_body = body.get("body", "")
    result = jobs.create(
        title=title, job_type=job_type, channel=channel,
        created_by=created_by, anchor_msg_id=anchor_msg_id,
        assignee=assignee, body=job_body,
    )
    # Mark the proposal message as accepted so it persists across refresh
    if anchor_msg_id:
        anchor_msg = store.get_by_id(anchor_msg_id)
        if anchor_msg and anchor_msg.get("type") == "job_proposal":
            meta = dict(anchor_msg.get("metadata", {}))
            meta["status"] = "accepted"
            updated_msg = store.update_message(anchor_msg_id, {"metadata": meta})
            if updated_msg:
                payload = json.dumps({"type": "edit", "message": updated_msg})
                dead = set()
                for client in list(ws_clients):
                    try:
                        await client.send_text(payload)
                    except Exception:
                        dead.add(client)
                ws_clients.difference_update(dead)
    # Post breadcrumb in main timeline with job_id for clickable link
    store.add(created_by, f"Job created: {title}", msg_type="job_created",
              channel=channel, metadata={"job_id": result["id"]})
    return result


@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: int, request: Request):
    """Update a job's status, title, or assignee."""
    body = await request.json()
    result = None
    if "status" in body:
        result = jobs.update_status(job_id, body["status"])
    if "title" in body:
        result = jobs.update_title(job_id, body["title"])
    if "assignee" in body:
        result = jobs.update_assignee(job_id, body["assignee"])
    if result is None:
        return JSONResponse({"error": "not found or invalid"}, status_code=404)
    return result


@app.post("/api/jobs/reorder")
async def reorder_jobs(request: Request):
    """Reorder jobs within a status group (globally, not per-channel)."""
    body = await request.json()
    status = body.get("status", "open")
    ordered_ids = body.get("ordered_ids", [])
    if not isinstance(ordered_ids, list) or len(ordered_ids) == 0:
        return JSONResponse({"error": "ordered_ids required"}, status_code=400)
    updated = jobs.reorder(status=status, ordered_ids=ordered_ids)
    return {"ok": True, "updated": len(updated)}


@app.get("/api/jobs/{job_id}/messages")
async def get_job_messages(job_id: int):
    """Get all messages in a job."""
    msgs = jobs.get_messages(job_id)
    if msgs is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return msgs


@app.post("/api/jobs/{job_id}/messages")
async def post_job_message(job_id: int, request: Request):
    """Post a message to a job."""
    body = await request.json()
    text = body.get("text", "").strip()
    sender = body.get("sender", "user")
    attachments = body.get("attachments", [])
    if not text and not attachments:
        return JSONResponse({"error": "text or attachments required"}, status_code=400)
    msg_type = body.get("type", "chat")
    msg = jobs.add_message(job_id, sender, text,
                           attachments=attachments, msg_type=msg_type)
    if msg is None:
        return JSONResponse({"error": "job not found"}, status_code=404)

    # Route @mentions in job messages to agents (with job_id context)
    job = jobs.get(job_id)
    if job:
        channel = job.get("channel", "general")
        raw_targets = router.get_targets(sender, text, channel)
        targets = []
        for t in raw_targets:
            if registry:
                targets.extend(registry.resolve_to_instances(t))
            else:
                targets.append(t)
        targets = list(dict.fromkeys(targets))

        import mcp_bridge
        chat_msg = f"{sender}: {text}" if text else ""
        for target in targets:
            if registry:
                inst = registry.get_instance(target)
                if inst and inst.get("state") == "pending":
                    continue
            if agents.is_available(target):
                await agents.trigger(target, message=chat_msg, channel=channel,
                                     job_id=job_id)

    return msg


@app.delete("/api/jobs/{job_id}/messages/{msg_id}")
async def delete_job_message(job_id: int, msg_id: int):
    """Soft-delete a message in a job thread."""
    result = jobs.delete_message(job_id, msg_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True, **result}


@app.post("/api/jobs/{job_id}/messages/{msg_index}/resolve")
async def resolve_job_message(job_id: int, msg_index: int, request: Request):
    """Resolve a suggestion message (accept/dismiss)."""
    body = await request.json()
    resolution = body.get("resolution", "dismissed")
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    msgs = job.get("messages", [])
    if msg_index < 0 or msg_index >= len(msgs):
        return JSONResponse({"error": "invalid message index"}, status_code=400)
    msg = msgs[msg_index]
    msg["resolved"] = resolution
    jobs._save()

    # If accepted, trigger the suggesting agent with context
    if resolution == "accepted" and msg.get("sender"):
        agent_name = msg["sender"]
        channel = job.get("channel", "general")
        if agents.is_available(agent_name):
            await agents.trigger(agent_name,
                                 message=f"Your suggestion was accepted: {msg.get('text', '')}",
                                 channel=channel, job_id=job_id)

    return {"ok": True, "resolution": resolution}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, request: Request):
    """Delete or archive a job. ?permanent=true for real delete."""
    permanent = request.query_params.get("permanent", "").lower() == "true"
    if permanent:
        result = jobs.delete(job_id)
    else:
        result = jobs.update_status(job_id, "archived")
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return result


@app.get("/api/roles")
async def get_roles():
    """Get all agent roles."""
    import mcp_bridge
    return mcp_bridge.get_all_roles()


@app.post("/api/roles/{agent_name}")
async def set_agent_role(agent_name: str, request: Request):
    """Set or clear an agent's role."""
    import mcp_bridge
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    role = body.get("role", "").strip()
    mcp_bridge.set_role(agent_name, role)
    _sync_profile_role(agent_name, role)
    await broadcast_status()
    return JSONResponse({"ok": True, "role": role})


@app.get("/api/agent-profiles/{agent_name}")
async def get_agent_profile(agent_name: str):
    """Get one agent profile, including role specialty metadata."""
    if not agent_profiles:
        return JSONResponse({})
    profile = agent_profiles.get_by_name(agent_name)
    return JSONResponse(profile or {})


# --- Rules API ---

@app.get("/api/rules")
async def get_rules():
    """Get all rules (all states)."""
    return JSONResponse(rules.list_all())


@app.get("/api/rules/active")
async def get_active_rules():
    """Get compact active rules for agent injection."""
    data = rules.active_list()
    data["refresh_interval"] = room_settings.get("rules_refresh_interval", 10)
    return JSONResponse(data)


@app.post("/api/rules/remind")
async def remind_agents():
    """Set remind flag — agents get rules on next trigger."""
    rules.set_remind()
    remind_data = json.dumps({"type": "rules_remind", "data": {}})
    for client in list(ws_clients):
        try:
            await client.send_text(remind_data)
        except Exception:
            pass
    return JSONResponse({"ok": True})


@app.post("/api/rules/agent_sync/{agent_name}")
async def report_rule_sync(agent_name: str, request: Request):
    """Wrapper reports that an agent has seen rules at a given epoch."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    epoch = body.get("epoch", 0)
    rules.report_agent_sync(agent_name, epoch)
    # Clear remind flag once any agent has seen the updated rules
    rules.clear_remind()
    return JSONResponse({"ok": True})


@app.get("/api/rules/freshness")
async def get_rules_freshness():
    """Get per-agent sync status."""
    return JSONResponse(rules.agent_freshness())


@app.post("/api/register")
async def register_agent(request: Request):
    """Wrapper calls this to register a new agent instance."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    base = body.get("base", "")
    label = body.get("label")
    profile_id = normalize_profile_id(body.get("profile") or body.get("profile_id") or "")
    if not base:
        return JSONResponse({"error": "base is required"}, status_code=400)
    profile = None
    requested_name = None
    if profile_id:
        if not agent_profiles:
            return JSONResponse({"error": "profiles not configured"}, status_code=500)
        profile, profile_err = agent_profiles.ensure_profile(profile_id, base, label)
        if profile_err:
            return JSONResponse({"error": profile_err}, status_code=400)
        requested_name = profile.get("name")
        label = profile.get("label") or label
    result = registry.register(base, label, requested_name=requested_name, profile_id=profile_id)
    if result is None:
        return JSONResponse({"error": f"unknown base: {base}"}, status_code=400)
    if isinstance(result, str):
        return JSONResponse({"error": result}, status_code=409)
    # Touch presence so the instance doesn't immediately time out
    import mcp_bridge
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[result["name"]] = __import__("time").time()
    if profile is not None:
        mcp_bridge.set_role(result["name"], profile.get("role", ""))
    # If slot 1 was renamed (e.g. "claude" → "claude-1"), migrate state
    renamed = result.pop("_renamed_slot1", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    # Broadcast pending_instance event so UI can show naming lightbox
    if result.get("state") == "pending" and _event_loop:
        pending_event = json.dumps({
            "type": "pending_instance",
            "name": result["name"],
            "base": base,
            "label": result.get("label", result["name"]),
            "color": result.get("color", "#888"),
        })
        asyncio.run_coroutine_threadsafe(_broadcast(pending_event), _event_loop)
    return JSONResponse(result)


@app.post("/api/deregister/{name}")
async def deregister_agent(name: str, request: Request):
    """Wrapper calls this on shutdown to remove its instance."""
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if auth_inst:
        name = auth_inst["name"]
    elif registry and registry.is_agent_family(name):
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    result = registry.deregister(name)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Clean up runtime state (presence, activity, cursors, rename chains)
    import mcp_bridge
    mcp_bridge.purge_identity(name)
    registry.clean_renames_for(name)
    # If the remaining instance was renamed back (e.g. "claude-1" → "claude"), migrate state
    renamed = result.pop("_renamed_back", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    return JSONResponse({"ok": True})


@app.post("/api/label/{name}")
async def rename_agent_label(name: str, request: Request):
    """Rename an agent (human-initiated from UI). Changes identity + label."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    label = body.get("label", "").strip()
    if not label:
        return JSONResponse({"error": "label is required"}, status_code=400)

    inst_before = registry.get_instance(name) if registry else None

    import re as _re
    new_id = _re.sub(r'[^a-z0-9-]', '', label.lower().replace(' ', '-')).strip('-')
    if not new_id:
        new_id = name

    if new_id == name:
        # Same ID — label-only change
        if registry.set_label(name, label):
            _sync_profile_identity(name, name, label, inst_before)
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "not found"}, status_code=404)

    result = registry.rename(name, new_id, label)
    if isinstance(result, str):
        # Rename failed — try label-only as fallback
        if registry.set_label(name, label):
            _sync_profile_identity(name, name, label, inst_before)
            return JSONResponse({"ok": True, "warning": result})
        return JSONResponse({"error": result}, status_code=400)

    import mcp_bridge
    mcp_bridge.migrate_identity(name, new_id)
    _sync_profile_identity(name, new_id, label, result)
    # Update sender on all historical messages
    store.rename_sender(name, new_id)
    return JSONResponse({"ok": True, "new_name": new_id})


@app.post("/api/heartbeat/{agent_name}")
async def heartbeat(agent_name: str, request: Request):
    """Wrapper calls this to keep presence alive and report activity.

    Returns the canonical name from the registry so the wrapper can
    detect renames (e.g. claim renamed 'claude-2' to 'claude-music').
    """
    import mcp_bridge
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if registry and registry.is_agent_family(agent_name) and not auth_inst:
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    current_name = auth_inst["name"] if auth_inst else agent_name
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[current_name] = __import__("time").time()
    # Optional activity report from wrapper's terminal monitor
    _activity_changed = False
    try:
        body = await request.json()
        if isinstance(body, dict) and "active" in body:
            active_val = bool(body["active"])
            channel = str(body.get("channel") or "").strip()
            was_active = mcp_bridge.is_active(current_name)
            was_channel = mcp_bridge.get_activity_channel(current_name) if was_active else ""
            mcp_bridge.set_active(current_name, active_val, channel=channel)
            is_now_active = mcp_bridge.is_active(current_name)
            now_channel = mcp_bridge.get_activity_channel(current_name) if is_now_active else ""
            _activity_changed = was_active != is_now_active or was_channel != now_channel
    except Exception:
        pass  # No body = plain heartbeat
    # Immediately broadcast on activity state change (don't wait for background checker)
    if _activity_changed:
        await broadcast_status()
    # Return canonical name so wrapper can track renames
    resp = {"ok": True, "name": current_name}
    if registry:
        # Follow rename chain (e.g. claude-2 was renamed to claude-music)
        canonical = registry.resolve_name(current_name)
        inst = registry.get_instance(canonical)
        # If rename chain didn't help, try family-based lookup
        # (handles case where _renames was cleared by server restart but
        # the instance was claimed/renamed via MCP)
        if not inst:
            base = current_name.split("-")[0] if "-" in current_name else current_name
            family_inst = registry.get_family_instance(base)
            if family_inst:
                inst = family_inst
                canonical = inst["name"]
        if inst:
            resp["name"] = inst["name"]
            resp["pending"] = inst.get("state") == "pending"
            # Also update presence under the canonical name
            if canonical != current_name:
                now = __import__("time").time()
                with mcp_bridge._presence_lock:
                    mcp_bridge._presence[canonical] = now
    return resp


# --- Open agent session in terminal ---

@app.get("/api/platform")
async def get_platform():
    """Return the server's platform so the web UI can match path formats."""
    import sys
    return JSONResponse({"platform": sys.platform})


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file or directory in the native file manager.

    Cross-platform: Explorer on Windows, Finder on macOS, xdg-open on Linux.

    Security note: This endpoint is intended for local-only use (127.0.0.1).
    Do not expose this server on a public network without additional access controls.
    """
    import subprocess
    import sys

    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "no path"}, status_code=400)

    p = Path(path)
    try:
        if sys.platform == "win32":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        elif sys.platform == "darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            elif p.is_dir():
                subprocess.Popen(["open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        else:
            # Linux — xdg-open opens the containing folder for files
            if p.is_file():
                subprocess.Popen(["xdg-open", str(p.parent)])
            elif p.is_dir():
                subprocess.Popen(["xdg-open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True})


# Serve uploaded images
# --- Sessions API ---

@app.get("/api/sessions/templates")
async def get_session_templates():
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    return JSONResponse(session_store.get_templates())


@app.get("/api/sessions/active")
async def get_active_session(channel: str = "general"):
    if not session_engine:
        return JSONResponse(None)
    session = session_engine.get_active(channel)
    return JSONResponse(session)


@app.get("/api/sessions/active-all")
async def get_all_active_sessions():
    if not session_engine:
        return JSONResponse([])
    return JSONResponse(session_engine.list_active())


@app.post("/api/sessions/start")
async def start_session(request: Request):
    if not session_engine or not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    body = await request.json()
    template_id = body.get("template_id", "")
    draft_message_id = body.get("draft_message_id")
    channel = body.get("channel", "general")
    cast = body.get("cast", {})
    goal = body.get("goal", "")
    started_by = body.get("started_by", "user")

    # If running from a draft, load the inline template from message metadata
    tmpl = None
    if draft_message_id:
        draft_msg = store.get_by_id(int(draft_message_id))
        if not draft_msg:
            return JSONResponse({"error": "draft message not found"}, status_code=404)
        meta = draft_msg.get("metadata", {})
        if not meta.get("valid"):
            return JSONResponse({"error": "draft is not valid"}, status_code=400)
        tmpl = meta.get("template")
        if not tmpl:
            return JSONResponse({"error": "draft has no template"}, status_code=400)
        # Register as a temporary template
        template_id = tmpl.get("id", f"draft-{draft_message_id}")
        tmpl["id"] = template_id
        tmpl["is_custom"] = True
        session_store._templates[template_id] = tmpl

    # Validate template exists
    if not tmpl:
        tmpl = session_store.get_template(template_id)
    if not tmpl:
        return JSONResponse({"error": f"unknown template: {template_id}"}, status_code=400)

    # Auto-fill cast from available agents if not fully provided
    if not cast:
        online = registry.get_active_names() if registry else []
        roles = tmpl.get("roles", [])
        cast = _auto_cast(roles, online, started_by)
        if not cast:
            return JSONResponse(
                {"error": "not enough agents online to fill all roles"},
                status_code=400,
            )

    session = session_engine.start_session(template_id, channel, cast, started_by, goal)
    if not session:
        return JSONResponse({"error": "could not start session (one may already be active)"}, status_code=409)

    # Add start banner to chat (only after confirmed success)
    store.add(
        sender="system",
        text=f"Session started: {tmpl.get('name', template_id)}",
        msg_type="session_start",
        channel=channel,
        metadata={"template_id": template_id, "goal": goal, "session_id": session["id"]},
    )
    session_engine.emit_current_phase_banner(session)

    return JSONResponse(session)


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: int):
    if not session_engine:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    session = session_engine.end_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found or already ended"}, status_code=404)

    # Banner is added by _on_session_change("interrupt", ...) callback
    return JSONResponse(session)


@app.post("/api/sessions/request-draft")
async def request_session_draft(request: Request):
    """Ask an agent to design a session template. Called by the 'Design a session' UI."""
    body = await request.json()
    agent_name = body.get("agent", "").strip()
    description = body.get("description", "").strip()
    channel = body.get("channel", "general")
    sender = body.get("sender", "user")
    if not agent_name or not description:
        return JSONResponse({"error": "agent and description required"}, status_code=400)

    mention_str = f"@{agent_name}"
    store.add(
        "system",
        f"Requested session draft from {mention_str}. Wait for a proposal.",
        channel=channel,
    )
    store.add(
        sender,
        f"{mention_str} Design a session workflow for: **{description}**\n\n"
        "Respond with a single chat message containing a fenced JSON code block with this exact structure:\n"
        "```session\n"
        '{"name": "...", "description": "...", "roles": ["role1", "role2", ...], '
        '"phases": [{"name": "...", "participants": ["role1"], "prompt": "...", "is_output": false}, ...]}\n'
        "```\n"
        "Rules: max 6 roles, max 6 phases, max 4 participants per phase, max 200 chars per prompt. "
        "Mark exactly one phase as `is_output: true` (the final deliverable). "
        f"Keep it focused and sequential. Use the chat_send tool to post your response in the #{channel} channel. "
        "Do NOT respond only in your terminal.",
        channel=channel,
        msg_type="session_request",
        metadata={"session_request": True, "mentions": [f"@{agent_name}"], "request": description},
    )
    return JSONResponse({"ok": True})


@app.post("/api/sessions/save-draft")
async def save_draft(request: Request):
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    body = await request.json()
    msg_id = body.get("message_id")
    if not msg_id:
        return JSONResponse({"error": "message_id required"}, status_code=400)
    msg = store.get_by_id(int(msg_id))
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    meta = msg.get("metadata", {})
    if not meta.get("valid"):
        return JSONResponse({"error": "draft is not valid"}, status_code=400)
    tmpl = meta.get("template")
    if not tmpl:
        return JSONResponse({"error": "no template in draft"}, status_code=400)

    tmpl.setdefault("id", f"custom-{msg_id}")
    session_store.save_custom_template(tmpl)
    return JSONResponse({"ok": True, "template_id": tmpl["id"]})


@app.delete("/api/sessions/templates/{template_id}")
async def delete_session_template(template_id: str):
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    deleted = session_store.delete_custom_template(template_id)
    if not deleted:
        return JSONResponse({"error": "template not found or not custom"}, status_code=404)
    return JSONResponse({"ok": True, "template_id": template_id})


def _auto_cast(roles: list[str], online_agents: list[str], started_by: str) -> dict:
    """Auto-assign roles to available agents. Returns empty dict if not enough agents."""
    cast = {}
    available = list(online_agents)

    for role in roles:
        if not available:
            # Reuse agents if we run out (one agent, multiple roles)
            available = list(online_agents)
        if not available:
            return {}
        agent = available.pop(0)
        cast[role] = agent

    return cast


# --- Version check (GitHub release notifier) ---

_version_cache: dict = {"data": None, "fetched_at": 0.0}
_VERSION_CACHE_TTL = 1800  # 30 minutes


def _read_local_version() -> str:
    """Read version from VERSION file in project root."""
    vfile = Path(__file__).parent / "VERSION"
    try:
        return vfile.read_text().strip()
    except Exception:
        return ""


def _detect_install_kind() -> str:
    """Detect how this copy was installed: official_git, fork, or unknown."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).parent,
        )
        url = result.stdout.strip().lower()
        if "bcurts/agentchattr" in url:
            return "official_git"
        elif url:
            return "fork"
    except Exception:
        pass
    return "unknown"


def _fetch_latest_release() -> dict | None:
    """Fetch latest release from GitHub API, with 30-min cache."""
    import time
    import urllib.request

    now = time.time()
    if _version_cache["data"] and (now - _version_cache["fetched_at"]) < _VERSION_CACHE_TTL:
        return _version_cache["data"]

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/bcurts/agentchattr/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "agentchattr"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            result = {
                "tag": data.get("tag_name", ""),
                "url": data.get("html_url", ""),
            }
            _version_cache["data"] = result
            _version_cache["fetched_at"] = now
            return result
    except Exception:
        return _version_cache.get("data")


def _compare_versions(current: str, latest_tag: str) -> str:
    """Compare version strings. Returns 'behind', 'current', or 'unknown'."""
    # Strip leading 'v' from tag
    latest = latest_tag.lstrip("v")
    if not current or not latest:
        return "unknown"
    try:
        from packaging.version import Version
        if Version(current) < Version(latest):
            return "behind"
        return "current"
    except Exception:
        return "unknown"


@app.get("/api/version_check")
async def version_check():
    """Check for newer releases on GitHub."""
    current = _read_local_version()
    loop = asyncio.get_event_loop()
    release = await loop.run_in_executor(None, _fetch_latest_release)

    if not release or not release.get("tag"):
        return JSONResponse({"current": current, "latest": "", "state": "unknown", "url": ""})

    latest_tag = release["tag"]
    install_kind = _detect_install_kind()
    comparison = _compare_versions(current, latest_tag)

    if comparison == "behind":
        if install_kind == "official_git":
            state = "update_available"
        elif install_kind == "fork":
            state = "upstream_update"
        else:
            state = "unknown"
    elif comparison == "current":
        state = "current"
    else:
        state = "unknown"

    return JSONResponse({
        "current": current,
        "latest": latest_tag,
        "state": state,
        "url": release.get("url", ""),
    })


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    filepath = (upload_dir / filename).resolve()
    if not filepath.is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if filepath.exists():
        return FileResponse(filepath)
    return JSONResponse({"error": "not found"}, status_code=404)
