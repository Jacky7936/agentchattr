"""Persistent commander lane ledger for long-running multi-agent work."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class CommanderLedger:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._lanes: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            lanes = raw.get("lanes", raw)
            if isinstance(lanes, dict):
                self._lanes = {str(k): v for k, v in lanes.items() if isinstance(v, dict)}

    def _save_locked(self) -> None:
        self._path.write_text(
            json.dumps({"lanes": self._lanes}, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def active_lanes(self) -> list[dict]:
        with self._lock:
            return [
                dict(lane)
                for lane in self._lanes.values()
                if lane.get("status") == "active" and lane.get("active_agents")
            ]

    def get(self, channel: str) -> dict | None:
        with self._lock:
            lane = self._lanes.get(channel)
            return dict(lane) if lane else None

    def start_lane(
        self,
        channel: str,
        *,
        commander: str,
        active_agents: list[str],
        task: str,
        reason: str = "",
        event_type: str = "start",
        now: float | None = None,
    ) -> dict:
        ts = time.time() if now is None else float(now)
        clean_agents = _clean_agents(active_agents)
        with self._lock:
            lane = dict(self._lanes.get(channel, {}))
            lane.setdefault("channel", channel)
            lane.setdefault("created_at", ts)
            lane["status"] = "active"
            lane["commander"] = commander
            lane["active_agents"] = clean_agents
            if task.strip():
                lane["task"] = task.strip()
            lane["reason"] = reason.strip()
            lane["updated_at"] = ts
            lane["watchdog_count"] = 0
            lane["last_activity_at"] = lane.get("last_activity_at", 0.0)
            lane["progress"] = {
                agent: progress
                for agent, progress in (lane.get("progress") or {}).items()
                if agent in clean_agents
            }
            self._append_event_locked(
                lane,
                event_type,
                {"commander": commander, "active_agents": clean_agents, "reason": reason.strip()},
                ts,
            )
            self._lanes[channel] = lane
            self._save_locked()
            return dict(lane)

    def release_lane(self, channel: str, *, updated_by: str = "", reason: str = "", now: float | None = None) -> dict | None:
        ts = time.time() if now is None else float(now)
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane:
                return None
            lane["status"] = "released"
            lane["active_agents"] = []
            lane["progress"] = {}
            lane["updated_at"] = ts
            lane["released_by"] = updated_by
            lane["release_reason"] = reason.strip()
            self._append_event_locked(lane, "release", {"updated_by": updated_by, "reason": reason.strip()}, ts)
            self._save_locked()
            return dict(lane)

    def note_activity(self, channel: str, sender: str, *, now: float | None = None) -> dict | None:
        ts = time.time() if now is None else float(now)
        clean_sender = _clean_agent(sender)
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            if clean_sender not in lane.get("active_agents", []):
                return None
            lane["last_activity_at"] = ts
            lane["updated_at"] = ts
            lane["watchdog_count"] = 0
            self._append_event_locked(lane, "activity", {"sender": clean_sender}, ts)
            self._save_locked()
            return dict(lane)

    def note_progress(
        self,
        channel: str,
        sender: str,
        *,
        state: str = "working",
        eta_seconds: int = 0,
        note: str = "",
        now: float | None = None,
    ) -> dict | None:
        ts = time.time() if now is None else float(now)
        clean_sender = _clean_agent(sender)
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            if clean_sender not in lane.get("active_agents", []):
                return None
            progress = dict(lane.get("progress") or {})
            progress[clean_sender] = {
                "state": _clean_progress_state(state),
                "eta_seconds": _clean_eta_seconds(eta_seconds),
                "note": str(note or "").strip()[:500],
                "updated_at": ts,
            }
            lane["progress"] = progress
            lane["last_activity_at"] = ts
            lane["updated_at"] = ts
            lane["watchdog_count"] = 0
            self._append_event_locked(
                lane,
                "progress",
                {
                    "sender": clean_sender,
                    "state": progress[clean_sender]["state"],
                    "eta_seconds": progress[clean_sender]["eta_seconds"],
                    "note": progress[clean_sender]["note"],
                },
                ts,
            )
            self._save_locked()
            return dict(lane)

    def note_watchdog(self, channel: str, *, quiet_for: int, level: int, now: float | None = None) -> dict | None:
        ts = time.time() if now is None else float(now)
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            lane["watchdog_count"] = int(level)
            lane["updated_at"] = ts
            self._append_event_locked(lane, "watchdog", {"quiet_for": int(quiet_for), "level": int(level)}, ts)
            self._save_locked()
            return dict(lane)

    def _append_event_locked(self, lane: dict, event_type: str, data: dict, ts: float) -> None:
        events = list(lane.get("events") or [])
        events.append({"type": event_type, "time": ts, **data})
        lane["events"] = events[-100:]


def _clean_agents(agents: list[str]) -> list[str]:
    clean = []
    for agent in agents:
        name = _clean_agent(agent)
        if name and name not in clean:
            clean.append(name)
    return clean


def _clean_agent(agent: str) -> str:
    return str(agent or "").strip().lower().lstrip("@")


def _clean_progress_state(state: str) -> str:
    clean = str(state or "").strip().lower()
    return clean[:40] if clean else "working"


def _clean_eta_seconds(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
