import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wrapper


class StopQueueWatcher(Exception):
    pass


class QueueWatcherTests(unittest.TestCase):
    def test_multiple_queued_triggers_are_injected_as_ordered_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_file = Path(tmp) / "codex-planner_queue.jsonl"
            queue_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "sender": "Jacky",
                                "text": "@codex-planner review plan",
                                "time": "10:00",
                                "channel": "general",
                            }
                        ),
                        json.dumps(
                            {
                                "sender": "Jacky",
                                "text": "@codex-planner infra follow-up",
                                "time": "10:01",
                                "channel": "infra",
                            }
                        ),
                    ]
                )
                + "\n",
                "utf-8",
            )
            injected = []
            trigger_channel = [""]

            def get_identity():
                return "codex-planner", queue_file

            def fake_sleep(seconds):
                if seconds == 1:
                    raise StopQueueWatcher()

            with (
                patch.object(wrapper, "_fetch_profile_context", return_value={}),
                patch.object(wrapper, "_fetch_role", return_value=""),
                patch.object(wrapper, "_fetch_active_rules", return_value=None),
                patch.object(wrapper.time, "sleep", side_effect=fake_sleep),
                self.assertRaises(StopQueueWatcher),
            ):
                wrapper._queue_watcher(
                    get_identity,
                    injected.append,
                    agent_name="codex-planner",
                    trigger_channel=trigger_channel,
                )

        self.assertEqual(len(injected), 1)
        self.assertIn("#general", injected[0])
        self.assertIn("#infra", injected[0])
        self.assertLess(injected[0].index("#general"), injected[0].index("#infra"))
        self.assertEqual(trigger_channel[0], "general")


if __name__ == "__main__":
    unittest.main()
