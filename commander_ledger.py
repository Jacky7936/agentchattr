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

    def set_backlog(
        self,
        channel: str,
        *,
        items: list[str],
        created_by: str,
        worker: str = "",
        reviewer: str = "",
        note: str = "",
        auto_advance: bool = True,
        now: float | None = None,
    ) -> dict | None:
        ts = time.time() if now is None else float(now)
        clean_items = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in clean_items:
                clean_items.append(text[:300])
        if not clean_items:
            return None
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            lane["backlog"] = {
                "items": [
                    {
                        "text": item,
                        "status": "pending",
                        "updated_by": "",
                        "updated_at": 0.0,
                        "note": "",
                    }
                    for item in clean_items
                ],
                "current_index": 0,
                "worker": _clean_agent(worker),
                "reviewer": _clean_agent(reviewer),
                "auto_advance": bool(auto_advance),
                "created_by": _clean_agent(created_by),
                "note": str(note or "").strip()[:500],
                "updated_at": ts,
            }
            merged_agents = _clean_agents(
                list(lane.get("active_agents") or []) + [worker, reviewer]
            )
            if merged_agents:
                lane["active_agents"] = merged_agents
                lane["progress"] = {
                    agent: progress
                    for agent, progress in (lane.get("progress") or {}).items()
                    if agent in merged_agents
                }
            lane["updated_at"] = ts
            self._append_event_locked(
                lane,
                "backlog",
                {
                    "created_by": _clean_agent(created_by),
                    "items": clean_items,
                    "worker": _clean_agent(worker),
                    "reviewer": _clean_agent(reviewer),
                    "auto_advance": bool(auto_advance),
                },
                ts,
            )
            self._save_locked()
            return dict(lane)

    def mark_backlog_item(
        self,
        channel: str,
        *,
        item: str = "",
        state: str,
        updated_by: str,
        note: str = "",
        now: float | None = None,
    ) -> dict | None:
        ts = time.time() if now is None else float(now)
        clean_state = _clean_backlog_state(state)
        clean_item = str(item or "").strip()
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            backlog = dict(lane.get("backlog") or {})
            items = [dict(entry) for entry in backlog.get("items") or [] if isinstance(entry, dict)]
            if not items:
                return None
            index = _find_backlog_index(items, clean_item, int(backlog.get("current_index", 0) or 0))
            if index < 0:
                return None
            items[index]["status"] = clean_state
            items[index]["updated_by"] = _clean_agent(updated_by)
            items[index]["updated_at"] = ts
            items[index]["note"] = str(note or "").strip()[:500]
            backlog["items"] = items
            backlog["updated_at"] = ts
            backlog["current_index"] = _next_pending_index(items) if clean_state == "approved" else index
            lane["backlog"] = backlog
            lane["last_activity_at"] = ts
            lane["updated_at"] = ts
            self._append_event_locked(
                lane,
                "backlog-item",
                {
                    "item": items[index]["text"],
                    "state": clean_state,
                    "updated_by": _clean_agent(updated_by),
                    "note": items[index]["note"],
                    "next_item": self._next_item_from_backlog_locked(backlog).get("text", ""),
                },
                ts,
            )
            self._save_locked()
            return dict(lane)

    def next_backlog_item(self, channel: str) -> dict | None:
        with self._lock:
            lane = self._lanes.get(channel)
            if not lane or lane.get("status") != "active":
                return None
            item = self._next_item_from_backlog_locked(lane.get("backlog") or {})
            return dict(item) if item else None

    def _next_item_from_backlog_locked(self, backlog: dict) -> dict:
        items = [entry for entry in backlog.get("items") or [] if isinstance(entry, dict)]
        if not items:
            return {}
        current_index = int(backlog.get("current_index", 0) or 0)
        for index in range(max(0, current_index), len(items)):
            if items[index].get("status") in ("pending", "needs_fix"):
                return items[index]
        for entry in items:
            if entry.get("status") in ("pending", "needs_fix"):
                return entry
        return {}

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


def _clean_backlog_state(state: str) -> str:
    clean = str(state or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "handoff_ready": "approved",
        "放行": "approved",
        "通過": "approved",
        "ready": "approved",
        "pass": "approved",
        "passed": "approved",
        "approved": "approved",
        "needs_fix": "needs_fix",
        "needs_fixes": "needs_fix",
        "需修": "needs_fix",
        "需要修正": "needs_fix",
        "待修": "needs_fix",
        "fix": "needs_fix",
        "卡住": "blocked",
        "blocked": "blocked",
        "完成待審": "ready_for_review",
        "待審": "ready_for_review",
        "done": "ready_for_review",
        "ready_for_review": "ready_for_review",
        "review": "ready_for_review",
        "running": "running",
        "working": "running",
        "started": "running",
    }
    return aliases.get(clean, clean[:40] if clean else "running")


def _clean_eta_seconds(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _find_backlog_index(items: list[dict], item: str, current_index: int) -> int:
    clean_item = item.strip()
    if clean_item:
        for index, entry in enumerate(items):
            if str(entry.get("text") or "").strip() == clean_item:
                return index
        return -1
    if 0 <= current_index < len(items):
        return current_index
    return _next_pending_index(items)


def _next_pending_index(items: list[dict]) -> int:
    for index, entry in enumerate(items):
        if entry.get("status") in ("pending", "needs_fix"):
            return index
    return len(items)
