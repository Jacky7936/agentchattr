"""Mission store — structured mission lifecycle (Slice 2).

A mission sits one level above commander lanes / jobs: it owns a briefing
contract (title, objective, deliverables), a crew assignment, and budget
limits. The lifecycle goes briefing → active → (paused) → complete | failed.

Storage is JSON at the configured path; concurrency is guarded by a
threading.Lock. Mirrors the JobStore pattern in jobs.py.
"""

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable


@dataclass
class Mission:
    id: str
    title: str
    objective: str
    crew: list                       # [{"agent": str, "role": str}]
    reviewer: str
    deliverables: list               # [{"text": str, "required": bool, "met": bool, "artifact_ref": str | None}]
    eta_minutes: int
    hop_budget: int
    auto_pause_blockers: int
    status: str                      # briefing | active | paused | complete | failed
    transcript_channel_id: str
    decisions: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None


class MissionStore:
    """JSON-backed mission store; thread-safe; emits change callbacks."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._missions: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[str, dict], None]] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self._missions = raw
                if self._missions:
                    nums = []
                    for m in self._missions:
                        mid = m.get("id", "")
                        if mid.startswith("mission-"):
                            try:
                                nums.append(int(mid.split("-", 1)[1]))
                            except ValueError:
                                continue
                    if nums:
                        self._next_id = max(nums) + 1
        except (json.JSONDecodeError, KeyError):
            self._missions = []

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._missions, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def on_change(self, callback: Callable[[str, dict], None]) -> None:
        """Register callback(action, mission) for create/update events."""
        self._callbacks.append(callback)

    def _fire(self, action: str, mission: dict) -> None:
        for cb in self._callbacks:
            try:
                cb(action, mission)
            except Exception:
                pass

    def create(self, *, title: str, objective: str, crew: list,
               reviewer: str, deliverables: list, eta_minutes: int,
               hop_budget: int, auto_pause_blockers: int) -> dict:
        with self._lock:
            mid = f"mission-{self._next_id}"
            self._next_id += 1
            mission = asdict(Mission(
                id=mid,
                title=title,
                objective=objective,
                crew=list(crew),
                reviewer=reviewer,
                deliverables=list(deliverables),
                eta_minutes=int(eta_minutes),
                hop_budget=int(hop_budget),
                auto_pause_blockers=int(auto_pause_blockers),
                status="briefing",
                transcript_channel_id=mid,
                created_at=time.time(),
            ))
            self._missions.append(mission)
            self._save()
        self._fire("create", mission)
        return mission

    def get(self, mission_id: str) -> dict | None:
        with self._lock:
            for m in self._missions:
                if m["id"] == mission_id:
                    return dict(m)
        return None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(m) for m in self._missions]

    def update_status(self, mission_id: str, status: str) -> dict | None:
        mission = None
        with self._lock:
            for m in self._missions:
                if m["id"] == mission_id:
                    m["status"] = status
                    if status == "active" and m.get("started_at") is None:
                        m["started_at"] = time.time()
                    if status in ("complete", "failed"):
                        m["completed_at"] = time.time()
                    self._save()
                    mission = dict(m)
                    break
        if mission is not None:
            self._fire("update", mission)
        return mission

    def append_decision(self, mission_id: str, *, type: str, agent: str = "",
                        body: str = "") -> dict | None:
        decision = {
            "ts": time.time(),
            "type": type,
            "agent": agent,
            "body": body,
        }
        mission = None
        with self._lock:
            for m in self._missions:
                if m["id"] == mission_id:
                    m.setdefault("decisions", []).append(decision)
                    self._save()
                    mission = dict(m)
                    break
        if mission is not None:
            self._fire("update", mission)
        return decision if mission is not None else None

    def set_deliverable_met(self, mission_id: str, index: int, met: bool) -> dict | None:
        mission = None
        with self._lock:
            for m in self._missions:
                if m["id"] == mission_id:
                    deliverables = m.get("deliverables") or []
                    if 0 <= index < len(deliverables):
                        deliverables[index]["met"] = bool(met)
                        self._save()
                        mission = dict(m)
                    break
        if mission is not None:
            self._fire("update", mission)
        return mission
