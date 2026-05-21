"""Message routing based on @mentions with per-channel loop guard."""

import re


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4, online_checker=None):
        self.agent_names = set(n.lower() for n in agent_names)
        self.default_mention = default_mention
        self.max_hops = max_hops
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
                "commander_control": {
                    "locked": False,
                    "active_agent": "",
                    "standby": set(),
                    "updated_by": "",
                    "reason": "",
                },
            }
        return self._channels[channel]

    def _get_control(self, channel: str) -> dict:
        ch = self._get_ch(channel)
        if "commander_control" not in ch:
            ch["commander_control"] = {
                "locked": False,
                "active_agent": "",
                "standby": set(),
                "updated_by": "",
                "reason": "",
            }
        return ch["commander_control"]

    def _build_pattern(self):
        # Sort longest-first so "gemini-2" is tried before "gemini"
        names = [re.escape(n) for n in sorted(self.agent_names, key=len, reverse=True)]
        alternatives = "|".join(names + ["both", "all"])
        self._mention_re = re.compile(
            rf"@({alternatives})(?![\w-])", re.IGNORECASE
        )

    def parse_mentions(self, text: str) -> list[str]:
        mentions = set()
        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                # Only tag online agents when using @all
                if self._online_checker:
                    online = self._online_checker()
                    mentions.update(n for n in self.agent_names if n in online)
                else:
                    mentions.update(self.agent_names)
            else:
                mentions.add(name)
        return list(mentions)

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def _is_commander(self, sender: str) -> bool:
        name = sender.lower()
        return name == "dispatcher" or name.endswith("-dispatcher")

    def set_commander_lock(
        self,
        channel: str = "general",
        *,
        active_agent: str = "",
        updated_by: str = "",
        reason: str = "",
    ) -> dict:
        """Restrict agent-to-agent routing on a channel to one active agent."""
        ctl = self._get_control(channel)
        ctl["locked"] = True
        ctl["active_agent"] = active_agent.lower().lstrip("@")
        ctl["standby"] = set()
        ctl["updated_by"] = updated_by
        ctl["reason"] = reason
        return self.get_commander_status(channel)

    def release_commander_lock(self, channel: str = "general", *, updated_by: str = "") -> dict:
        """Clear commander routing restrictions for a channel."""
        ctl = self._get_control(channel)
        ctl["locked"] = False
        ctl["active_agent"] = ""
        ctl["standby"] = set()
        ctl["updated_by"] = updated_by
        ctl["reason"] = ""
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
        ctl["standby"].update(a.lower().lstrip("@") for a in agents if a)
        ctl["updated_by"] = updated_by
        ctl["reason"] = reason or ctl.get("reason", "")
        return self.get_commander_status(channel)

    def get_commander_status(self, channel: str = "general") -> dict:
        ctl = self._get_control(channel)
        return {
            "locked": bool(ctl.get("locked")),
            "active_agent": ctl.get("active_agent", ""),
            "standby": sorted(ctl.get("standby", set())),
            "updated_by": ctl.get("updated_by", ""),
            "reason": ctl.get("reason", ""),
        }

    def _filter_human_targets(self, targets: list[str], channel: str, explicit_mentions: bool) -> list[str]:
        ctl = self._get_control(channel)
        if not ctl.get("locked") and not ctl.get("standby"):
            return targets
        # Human @mentions are an explicit override. Unmentioned/default routing
        # follows the active commander lane to prevent accidental fan-out.
        if explicit_mentions:
            return targets
        active = ctl.get("active_agent", "")
        if active:
            return [active]
        return [t for t in targets if t not in ctl.get("standby", set())]

    def _filter_agent_targets(self, sender: str, targets: list[str], channel: str) -> list[str]:
        ctl = self._get_control(channel)
        sender = sender.lower()
        if not ctl.get("locked") and sender not in ctl.get("standby", set()):
            return targets

        active = ctl.get("active_agent", "")
        standby = ctl.get("standby", set())
        sender_is_commander = self._is_commander(sender)

        if sender in standby and not sender_is_commander:
            return []

        if sender_is_commander:
            if active:
                return [t for t in targets if t == active]
            return [t for t in targets if self._is_commander(t)]

        if active and sender == active:
            # The active worker may wake the dispatcher to request handoff, but
            # cannot fan the room out to reviewers or prototypers by mentioning them.
            return [t for t in targets if self._is_commander(t)]

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
            if ch["hop_count"] > self.max_hops:
                ch["paused"] = True
                return []
            # Don't route back to self
            return [m for m in mentions if m != sender]

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
