"""Message routing based on @mentions with per-channel loop guard."""

import re
import time


def _new_commander_control(now: float | None = None) -> dict:
    ts = time.time() if now is None else float(now)
    return {
        "locked": False,
        "active_agent": "",
        "active_agents": [],
        "standby": set(),
        "updated_by": "",
        "reason": "",
        "updated_at": ts,
        "worker_activity": {},
        "worker_progress": {},
        "watchdog_reminded_at": 0.0,
        "watchdog_count": 0,
    }


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4, online_checker=None,
                 commander_max_hops: int | None = None):
        self.agent_names = set(n.lower() for n in agent_names)
        self.default_mention = default_mention
        self.max_hops = max_hops
        self.commander_max_hops = commander_max_hops
        self._online_checker = online_checker  # callable() -> set of online agent names
        # Per-channel state:
        # { channel: { hop_count, paused, guard_emitted, commander_control } }
        self._channels: dict[str, dict] = {}
        self._build_pattern()

    def _get_ch(self, channel: str) -> dict:
        if channel not in self._channels:
            self._channels[channel] = {
                "hop_count": 0,
                "paused": False,
                "guard_emitted": False,
                "commander_control": _new_commander_control(),
            }
        return self._channels[channel]

    def _get_control(self, channel: str) -> dict:
        ch = self._get_ch(channel)
        if "commander_control" not in ch:
            ch["commander_control"] = _new_commander_control()
        else:
            defaults = _new_commander_control()
            for key, value in defaults.items():
                ch["commander_control"].setdefault(key, value)
        return ch["commander_control"]

    def _build_pattern(self):
        # Sort longest-first so "gemini-2" is tried before "gemini"
        names = [re.escape(n) for n in sorted(self.agent_names, key=len, reverse=True)]
        alternatives = "|".join(names + ["both", "all"])
        self._mention_re = re.compile(
            rf"@({alternatives})(?![\w-])", re.IGNORECASE
        )

    def parse_mentions(self, text: str) -> list[str]:
        mentions = []

        def add_mention(name: str):
            if name not in mentions:
                mentions.append(name)

        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                # Only tag online agents when using @all
                if self._online_checker:
                    online = self._online_checker()
                    for agent_name in sorted(self.agent_names):
                        if agent_name in online:
                            add_mention(agent_name)
                else:
                    for agent_name in sorted(self.agent_names):
                        add_mention(agent_name)
            else:
                add_mention(name)
        return mentions

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def _is_commander(self, sender: str) -> bool:
        name = sender.lower()
        return name in ("dispatcher", "orchestrator") or name.endswith(("-dispatcher", "-orchestrator"))

    def set_commander_lock(
        self,
        channel: str = "general",
        *,
        active_agent: str = "",
        active_agents: list[str] | None = None,
        updated_by: str = "",
        reason: str = "",
        now: float | None = None,
    ) -> dict:
        """Restrict agent-to-agent routing on a channel to active worker lanes."""
        ch = self._get_ch(channel)
        ctl = self._get_control(channel)
        ts = time.time() if now is None else float(now)
        agents = active_agents if active_agents is not None else [active_agent]
        clean_agents = []
        for agent in agents:
            clean = str(agent or "").lower().lstrip("@")
            if clean and clean not in clean_agents:
                clean_agents.append(clean)
        ctl["locked"] = True
        ctl["active_agent"] = clean_agents[0] if clean_agents else ""
        ctl["active_agents"] = clean_agents
        ctl["standby"] = set()
        ctl["updated_by"] = updated_by
        ctl["reason"] = reason
        ctl["updated_at"] = ts
        ctl["worker_activity"] = {}
        ctl["worker_progress"] = {}
        ctl["watchdog_reminded_at"] = 0.0
        ctl["watchdog_count"] = 0
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False
        return self.get_commander_status(channel)

    def release_commander_lock(self, channel: str = "general", *, updated_by: str = "") -> dict:
        """Clear commander routing restrictions for a channel."""
        ch = self._get_ch(channel)
        ctl = self._get_control(channel)
        ctl["locked"] = False
        ctl["active_agent"] = ""
        ctl["active_agents"] = []
        ctl["standby"] = set()
        ctl["updated_by"] = updated_by
        ctl["reason"] = ""
        ctl["updated_at"] = time.time()
        ctl["worker_activity"] = {}
        ctl["worker_progress"] = {}
        ctl["watchdog_reminded_at"] = 0.0
        ctl["watchdog_count"] = 0
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False
        return self.get_commander_status(channel)

    def set_standby(
        self,
        channel: str = "general",
        *,
        agents: list[str],
        updated_by: str = "",
        reason: str = "",
    ) -> dict:
        """Mark selected agents as standby so their messages cannot route others."""
        ctl = self._get_control(channel)
        standby = {a.lower().lstrip("@") for a in agents if a}
        ctl["standby"].update(standby)
        active_agents = [a for a in self._active_agents(ctl) if a not in standby]
        ctl["active_agents"] = active_agents
        ctl["active_agent"] = active_agents[0] if active_agents else ""
        ctl["updated_by"] = updated_by
        ctl["reason"] = reason or ctl.get("reason", "")
        ctl["updated_at"] = time.time()
        return self.get_commander_status(channel)

    def get_commander_status(self, channel: str = "general") -> dict:
        ctl = self._get_control(channel)
        active_agents = self._active_agents(ctl)
        worker_activity = {
            agent: float(ctl.get("worker_activity", {}).get(agent, 0.0) or 0.0)
            for agent in active_agents
        }
        raw_progress = ctl.get("worker_progress", {}) or {}
        worker_progress = {
            agent: dict(raw_progress.get(agent, {}))
            for agent in active_agents
            if raw_progress.get(agent)
        }
        last_worker_activity_at = max(worker_activity.values(), default=0.0)
        last_worker_progress_at = max(
            (
                float(progress.get("updated_at", 0.0) or 0.0)
                for progress in worker_progress.values()
            ),
            default=0.0,
        )
        return {
            "locked": bool(ctl.get("locked")),
            "active_agent": active_agents[0] if active_agents else "",
            "active_agents": active_agents,
            "standby": sorted(ctl.get("standby", set())),
            "updated_by": ctl.get("updated_by", ""),
            "reason": ctl.get("reason", ""),
            "updated_at": float(ctl.get("updated_at", 0.0) or 0.0),
            "worker_activity": worker_activity,
            "worker_progress": worker_progress,
            "last_worker_activity_at": last_worker_activity_at,
            "last_worker_progress_at": last_worker_progress_at,
            "watchdog_reminded_at": float(ctl.get("watchdog_reminded_at", 0.0) or 0.0),
            "watchdog_count": int(ctl.get("watchdog_count", 0) or 0),
        }

    def note_message(self, channel: str, sender: str, *, timestamp: float | None = None) -> dict:
        """Record active-worker chat activity for commander watchdogs."""
        ctl = self._get_control(channel)
        clean = str(sender or "").lower().lstrip("@")
        if clean and clean in self._active_agents(ctl):
            ts = time.time() if timestamp is None else float(timestamp)
            activity = dict(ctl.get("worker_activity", {}))
            activity[clean] = ts
            ctl["worker_activity"] = activity
            ctl["watchdog_reminded_at"] = 0.0
            ctl["watchdog_count"] = 0
        return self.get_commander_status(channel)

    def note_progress(
        self,
        channel: str,
        sender: str,
        *,
        state: str = "working",
        eta_seconds: int = 0,
        note: str = "",
        timestamp: float | None = None,
    ) -> dict:
        """Record structured active-worker progress for commander watchdogs."""
        ctl = self._get_control(channel)
        clean = str(sender or "").lower().lstrip("@")
        if clean and clean in self._active_agents(ctl):
            ts = time.time() if timestamp is None else float(timestamp)
            activity = dict(ctl.get("worker_activity", {}))
            activity[clean] = ts
            progress = dict(ctl.get("worker_progress", {}))
            progress[clean] = {
                "state": _clean_progress_state(state),
                "eta_seconds": _clean_eta_seconds(eta_seconds),
                "note": str(note or "").strip()[:500],
                "updated_at": ts,
            }
            ctl["worker_activity"] = activity
            ctl["worker_progress"] = progress
            ctl["watchdog_reminded_at"] = 0.0
            ctl["watchdog_count"] = 0
        return self.get_commander_status(channel)

    def mark_commander_watchdog(self, channel: str, *, timestamp: float | None = None) -> dict:
        """Record that a watchdog reminder was sent for a commander lane."""
        ctl = self._get_control(channel)
        ctl["watchdog_reminded_at"] = time.time() if timestamp is None else float(timestamp)
        ctl["watchdog_count"] = int(ctl.get("watchdog_count", 0) or 0) + 1
        return self.get_commander_status(channel)

    def _active_agents(self, ctl: dict) -> list[str]:
        raw = ctl.get("active_agents") or ([ctl.get("active_agent", "")] if ctl.get("active_agent") else [])
        standby = ctl.get("standby", set())
        active = []
        for agent in raw:
            clean = str(agent or "").lower().lstrip("@")
            if clean and clean not in standby and clean not in active:
                active.append(clean)
        return active

    def _filter_human_targets(self, targets: list[str], channel: str, explicit_mentions: bool) -> list[str]:
        ctl = self._get_control(channel)
        if not ctl.get("locked") and not ctl.get("standby"):
            return targets
        # Human @mentions are an explicit override. Unmentioned/default routing
        # follows the active commander lane to prevent accidental fan-out.
        if explicit_mentions:
            return targets
        active = self._active_agents(ctl)
        if active:
            return active
        return [t for t in targets if t not in ctl.get("standby", set())]

    def _filter_agent_targets(self, sender: str, targets: list[str], channel: str) -> list[str]:
        ctl = self._get_control(channel)
        sender = sender.lower()
        if not ctl.get("locked") and sender not in ctl.get("standby", set()):
            return targets

        active = self._active_agents(ctl)
        standby = ctl.get("standby", set())
        sender_is_commander = self._is_commander(sender)

        if sender in standby and not sender_is_commander:
            return []

        if sender_is_commander:
            if active:
                return [t for t in targets if t in active]
            return [t for t in targets if self._is_commander(t)]

        if active and sender in active:
            # Active workers may coordinate with peers in the same commander lane
            # or ask the orchestrator for handoff/status. They still cannot fan
            # out to standby or unrelated agents.
            return [t for t in targets if self._is_commander(t) or t in active]

        return []

    def get_targets(self, sender: str, text: str, channel: str = "general") -> list[str]:
        """Determine which agents should receive this message."""
        ch = self._get_ch(channel)
        mentions = self.parse_mentions(text)

        if not self._is_agent(sender):
            # Human message resets hop counter and unpauses
            ch["hop_count"] = 0
            ch["paused"] = False
            ch["guard_emitted"] = False
            if not mentions:
                if self.default_mention in ("both", "all"):
                    return self._filter_human_targets(list(self.agent_names), channel, False)
                elif self.default_mention == "none":
                    return []
                return self._filter_human_targets([self.default_mention], channel, False)
            return self._filter_human_targets(mentions, channel, True)
        else:
            # Agent message: blocked while loop guard is active
            if ch["paused"]:
                return []
            # Only route if explicit @mention
            if not mentions:
                return []
            mentions = self._filter_agent_targets(sender, mentions, channel)
            if not mentions:
                return []
            ch["hop_count"] += 1
            max_hops = self._effective_max_hops(channel)
            if max_hops > 0 and ch["hop_count"] > max_hops:
                ch["paused"] = True
                return []
            # Don't route back to self
            return [m for m in mentions if m != sender]

    def _effective_max_hops(self, channel: str) -> int:
        ctl = self._get_control(channel)
        if ctl.get("locked") and self.commander_max_hops is not None:
            try:
                return int(self.commander_max_hops)
            except (TypeError, ValueError):
                return self.max_hops
        return self.max_hops

    def effective_max_hops(self, channel: str = "general") -> int:
        return self._effective_max_hops(channel)

    def continue_routing(self, channel: str = "general"):
        """Resume after loop guard pause."""
        ch = self._get_ch(channel)
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False

    def is_paused(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["paused"]

    def is_guard_emitted(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["guard_emitted"]

    def set_guard_emitted(self, channel: str = "general"):
        self._get_ch(channel)["guard_emitted"] = True

    def update_agents(self, names: list[str]):
        """Replace the agent name set and rebuild the mention regex."""
        self.agent_names = set(n.lower() for n in names)
        self._build_pattern()


def _clean_progress_state(state: str) -> str:
    clean = str(state or "").strip().lower()
    return clean[:40] if clean else "working"


def _clean_eta_seconds(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
