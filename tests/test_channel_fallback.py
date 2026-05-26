"""Tests for chat_send channel fallback behavior.

When an agent calls chat_read(channel="X") and then chat_send(...)
without passing a channel, the message should go to #X instead of
the default "general" channel. Closes #58.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_bridge


class ChannelFallbackStateTests(unittest.TestCase):
    """Tests the in-memory state tracking that powers the fallback.

    The full chat_send path requires a configured MessageStore, registry,
    etc. — testing the fallback logic via the state dicts directly
    exercises the contract without needing that scaffolding.
    """

    def setUp(self):
        # Snapshot and clear the channel/job maps before each test so
        # parallel tests don't leak into each other.
        self._saved_ch = dict(mcp_bridge._last_read_channel)
        self._saved_job = dict(mcp_bridge._last_read_job_id)
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_job_id.clear()

    def tearDown(self):
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_channel.update(self._saved_ch)
        mcp_bridge._last_read_job_id.clear()
        mcp_bridge._last_read_job_id.update(self._saved_job)

    def test_last_read_channel_state_exists(self):
        # Contract: module exposes the state maps chat_send reads from
        self.assertIsInstance(mcp_bridge._last_read_channel, dict)
        self.assertIsInstance(mcp_bridge._last_read_job_id, dict)
        self.assertTrue(hasattr(mcp_bridge, "_last_read_lock"))

    def test_recording_channel_clears_job_mapping(self):
        # Simulate an agent reading a job first, then a channel
        mcp_bridge._last_read_job_id["agent"] = 42
        # Manually apply the same state transition chat_read does
        sender = "agent"
        ch = "bugfixing"
        with mcp_bridge._last_read_lock:
            mcp_bridge._last_read_channel[sender] = ch
            mcp_bridge._last_read_job_id.pop(sender, None)
        self.assertEqual(mcp_bridge._last_read_channel["agent"], "bugfixing")
        self.assertNotIn("agent", mcp_bridge._last_read_job_id)

    def test_recording_job_clears_channel_mapping(self):
        mcp_bridge._last_read_channel["agent"] = "bugfixing"
        sender = "agent"
        job_id = 42
        with mcp_bridge._last_read_lock:
            mcp_bridge._last_read_job_id[sender] = job_id
            mcp_bridge._last_read_channel.pop(sender, None)
        self.assertEqual(mcp_bridge._last_read_job_id["agent"], 42)
        self.assertNotIn("agent", mcp_bridge._last_read_channel)

    def test_different_agents_tracked_independently(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge._last_read_channel["bob"] = "portfolio"
        self.assertEqual(mcp_bridge._last_read_channel["alice"], "bugfixing")
        self.assertEqual(mcp_bridge._last_read_channel["bob"], "portfolio")

    def test_chat_send_fallback_prefers_job_over_channel(self):
        """If both job_id and channel are set (shouldn't happen in practice,
        but guard the precedence), fallback logic prefers job_id."""
        sender = "agent"
        # Simulate both being recorded, even though read_channel and
        # read_job_id paths mutually clear each other — this verifies the
        # chat_send resolution rule.
        mcp_bridge._last_read_job_id[sender] = 99
        mcp_bridge._last_read_channel[sender] = "bugfixing"

        # Replicate the precedence logic from chat_send:
        channel = ""
        job_id = 0
        if sender and not channel and not job_id:
            with mcp_bridge._last_read_lock:
                fallback_job = mcp_bridge._last_read_job_id.get(sender, 0)
                fallback_channel = mcp_bridge._last_read_channel.get(sender, "")
            if fallback_job:
                job_id = fallback_job
            elif fallback_channel:
                channel = fallback_channel
        self.assertEqual(job_id, 99)
        self.assertEqual(channel, "")  # not set because job took precedence

    def test_chat_send_fallback_uses_channel_when_no_job(self):
        sender = "agent"
        mcp_bridge._last_read_channel[sender] = "bugfixing"
        # No job_id recorded

        channel = ""
        job_id = 0
        if sender and not channel and not job_id:
            with mcp_bridge._last_read_lock:
                fallback_job = mcp_bridge._last_read_job_id.get(sender, 0)
                fallback_channel = mcp_bridge._last_read_channel.get(sender, "")
            if fallback_job:
                job_id = fallback_job
            elif fallback_channel:
                channel = fallback_channel
        self.assertEqual(channel, "bugfixing")
        self.assertEqual(job_id, 0)

    def test_chat_send_fallback_falls_through_to_general(self):
        sender = "new-agent"  # never read anything
        channel = ""
        job_id = 0
        # Apply full fallback logic including the 'general' final fallback
        if sender and not channel and not job_id:
            with mcp_bridge._last_read_lock:
                fallback_job = mcp_bridge._last_read_job_id.get(sender, 0)
                fallback_channel = mcp_bridge._last_read_channel.get(sender, "")
            if fallback_job:
                job_id = fallback_job
            elif fallback_channel:
                channel = fallback_channel
        if not channel and not job_id:
            channel = "general"
        self.assertEqual(channel, "general")

    def test_explicit_channel_is_never_overridden(self):
        sender = "agent"
        mcp_bridge._last_read_channel[sender] = "bugfixing"

        # Caller explicitly passed channel="portfolio"
        channel = "portfolio"
        job_id = 0
        # Fallback condition requires BOTH channel and job_id empty
        applied_fallback = bool(sender and not channel and not job_id)
        self.assertFalse(applied_fallback)
        self.assertEqual(channel, "portfolio")


class FakeMessageStore:
    def __init__(self, messages):
        self.messages = list(messages)

    def get_recent(self, limit, channel=None):
        messages = [
            message
            for message in self.messages
            if channel is None or message.get("channel", "general") == channel
        ]
        return messages[-limit:]


class ChatResyncChannelFallbackTests(unittest.TestCase):
    def setUp(self):
        self._saved_store = mcp_bridge.store
        self._saved_registry = mcp_bridge.registry
        self._saved_ch = dict(mcp_bridge._last_read_channel)
        self._saved_job = dict(mcp_bridge._last_read_job_id)
        self._saved_cursors = {
            sender: dict(channels)
            for sender, channels in mcp_bridge._cursors.items()
        }
        mcp_bridge.registry = None
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_job_id.clear()
        mcp_bridge._cursors.clear()
        mcp_bridge.store = FakeMessageStore(
            [
                {
                    "id": 1,
                    "sender": "Jacky",
                    "text": "infra task",
                    "type": "chat",
                    "time": "10:00",
                    "channel": "infra",
                },
                {
                    "id": 2,
                    "sender": "Jacky",
                    "text": "general task",
                    "type": "chat",
                    "time": "10:01",
                    "channel": "general",
                },
            ]
        )

    def tearDown(self):
        mcp_bridge.store = self._saved_store
        mcp_bridge.registry = self._saved_registry
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_channel.update(self._saved_ch)
        mcp_bridge._last_read_job_id.clear()
        mcp_bridge._last_read_job_id.update(self._saved_job)
        mcp_bridge._cursors.clear()
        mcp_bridge._cursors.update(self._saved_cursors)

    def test_chat_resync_records_explicit_channel_for_send_fallback(self):
        mcp_bridge._last_read_job_id["codex-planner"] = 42

        result = mcp_bridge.chat_resync(
            sender="codex-planner",
            limit=10,
            channel="infra",
        )

        self.assertIn("infra task", result)
        self.assertEqual(mcp_bridge._last_read_channel["codex-planner"], "infra")
        self.assertNotIn("codex-planner", mcp_bridge._last_read_job_id)

    def test_chat_resync_without_channel_does_not_overwrite_fallback(self):
        mcp_bridge._last_read_channel["codex-planner"] = "infra"

        mcp_bridge.chat_resync(sender="codex-planner", limit=10)

        self.assertEqual(mcp_bridge._last_read_channel["codex-planner"], "infra")


if __name__ == "__main__":
    unittest.main()
