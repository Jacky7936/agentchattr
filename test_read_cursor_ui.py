import unittest
from pathlib import Path


class ReadCursorUiTests(unittest.TestCase):
    def test_chat_runtime_persists_and_restores_read_cursor(self):
        chat_js = Path("static/chat.js").read_text("utf-8")

        self.assertIn("READ_CURSOR_PREFIX", chat_js)
        self.assertIn("function getReadCursorKey(channel)", chat_js)
        self.assertIn("function markChannelReadThrough(channel, msgId)", chat_js)
        self.assertIn("function restoreUnreadStartPosition()", chat_js)
        self.assertIn("window.restoreChannelStartPosition = restoreChannelStartPosition", chat_js)
        self.assertIn("window.markActiveChannelReadThroughLatestVisible = markActiveChannelReadThroughLatestVisible", chat_js)
        self.assertIn("restoreUnreadStartPosition();", chat_js)
        self.assertIn("markChannelReadThrough(msgChannel, msg.id)", chat_js)
        self.assertIn("markActiveChannelReadThroughLatestVisible();", chat_js)

    def test_channel_switch_uses_read_cursor_when_no_saved_scroll_exists(self):
        channels_js = Path("static/channels.js").read_text("utf-8")

        self.assertIn("window.markActiveChannelReadThroughLatestVisible()", channels_js)
        self.assertIn("window.restoreChannelStartPosition(name)", channels_js)


if __name__ == "__main__":
    unittest.main()
