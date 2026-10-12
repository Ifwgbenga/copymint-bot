import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import bot


WATCHED = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
OWN = "0x" + "ef" * 20


def bare_bot():
    instance = object.__new__(bot.Bot)
    instance.watch = {WATCHED}
    instance.own = {OWN}
    instance.watch_saved = False
    instance.paused = False
    instance.cap_overrides = {}
    instance.max_values = {1: 50_000_000_000_000_000, 56: 50_000_000_000_000_000}
    instance.chains = {1: bot.Chain(1, "Ethereum", "https://example.invalid"), 56: bot.Chain(56, "BNB Chain", "https://example.invalid")}
    instance.minters = []
    instance.funder = None
    instance.dry = True
    instance.pending = {}
    instance.heads = {}
    instance.seen = bot.SeenSet()
    instance._state_lock = threading.Lock()
    instance._watch_lock = threading.RLock()
    instance.state_path = Path(tempfile.mkdtemp()) / "state.json"
    return instance


class TelegramControls(unittest.TestCase):
    def test_address_validation(self):
        self.assertEqual(bot.valid_address(WATCHED.upper().replace("0X", "0x")), WATCHED)
        self.assertIsNone(bot.valid_address("0x1234"))

    def test_watch_rejects_own_and_bad_addresses(self):
        instance = bare_bot()
        result = instance.watch_command([OTHER, OWN, "bad"])
        self.assertIn(OTHER, instance.watch)
        self.assertNotIn(OWN, instance.watch)
        self.assertIn("invalid EVM address", result)
        self.assertTrue(instance.state_path.exists())

    def test_last_wallet_cannot_be_removed(self):
        instance = bare_bot()
        result = instance.unwatch_command([WATCHED])
        self.assertIn(WATCHED, instance.watch)
        self.assertIn("last watched wallet", result)

    def test_pause_state_is_saved(self):
        instance = bare_bot()
        reply = instance.command_reply("/off")
        self.assertTrue(instance.paused)
        self.assertIn("Paused", reply)
        saved = json.loads(instance.state_path.read_text())
        self.assertTrue(saved["paused"])

    def test_cap_override_is_saved(self):
        instance = bare_bot()
        reply = instance.command_reply("/cap", ["56", "0.1"])
        self.assertIn("BNB Chain", reply)
        self.assertEqual(instance.cap(56), 100_000_000_000_000_000)
        saved = json.loads(instance.state_path.read_text())
        self.assertEqual(saved["caps"]["56"], "100000000000000000")

    def test_commands_only_accept_configured_chat(self):
        instance = bare_bot()
        with mock.patch.object(instance, "command_reply") as reply:
            instance.handle_command({"message": {"chat": {"id": 99}, "text": "/off"}}, "123")
            reply.assert_not_called()

    def test_slash_menu_is_registered_with_telegram(self):
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(bot.urllib.request, "urlopen") as urlopen:
            response = mock.Mock()
            response.read.return_value = b'{"ok": true, "result": true}'
            urlopen.return_value.__enter__.return_value = response
            bot.publish_commands()
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/setMyCommands"))
        for name, _text in bot.MENU:
            self.assertIn(name.encode(), request.data)


if __name__ == "__main__":
    unittest.main()
