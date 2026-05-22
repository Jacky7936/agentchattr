import tempfile
import unittest
from pathlib import Path

from commander_ledger import CommanderLedger


class CommanderLedgerTests(unittest.TestCase):
    def test_lane_persists_and_restores_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commander_ledger.json"
            ledger = CommanderLedger(path)
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder", "codex-qa"],
                task="Implement and verify checkout",
                reason="auto-dispatch",
                now=1000,
            )
            ledger.note_activity("general", "codex-builder", now=1010)
            ledger.note_watchdog("general", quiet_for=180, level=1, now=1190)

            restored = CommanderLedger(path)
            lanes = restored.active_lanes()

        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0]["channel"], "general")
        self.assertEqual(lanes[0]["commander"], "codex-orchestrator")
        self.assertEqual(lanes[0]["active_agents"], ["codex-builder", "codex-qa"])
        self.assertEqual(lanes[0]["watchdog_count"], 1)
        self.assertEqual(lanes[0]["events"][-1]["type"], "watchdog")

    def test_progress_persists_with_eta_and_resets_watchdog_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commander_ledger.json"
            ledger = CommanderLedger(path)
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder"],
                task="Run E2E",
                reason="auto-dispatch",
                now=1000,
            )
            ledger.note_watchdog("general", quiet_for=180, level=2, now=1180)
            ledger.note_progress(
                "general",
                "codex-builder",
                state="running",
                eta_seconds=600,
                note="running browser E2E",
                now=1190,
            )

            restored = CommanderLedger(path)
            lane = restored.get("general")

        progress = lane["progress"]["codex-builder"]
        self.assertEqual(progress["state"], "running")
        self.assertEqual(progress["eta_seconds"], 600)
        self.assertEqual(progress["note"], "running browser E2E")
        self.assertEqual(progress["updated_at"], 1190)
        self.assertEqual(lane["watchdog_count"], 0)
        self.assertEqual(lane["events"][-1]["type"], "progress")

    def test_release_removes_lane_from_active_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commander_ledger.json"
            ledger = CommanderLedger(path)
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder"],
                task="Fix login",
                reason="auto-dispatch",
                now=1000,
            )
            ledger.release_lane("general", updated_by="codex-orchestrator", reason="/release", now=1100)

            restored = CommanderLedger(path)

        self.assertEqual(restored.active_lanes(), [])
        self.assertEqual(restored.get("general")["status"], "released")


if __name__ == "__main__":
    unittest.main()
