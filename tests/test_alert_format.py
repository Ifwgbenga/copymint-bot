import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

COLLECTION = "0x" + "ee" * 20


class AlertFormat(unittest.TestCase):
    def test_card_shows_the_full_contract_address_and_no_links(self):
        text = bot.card(
            "🎯 Mint detected · Base",
            ("Wallet", bot.code("0x" + "ab" * 20)),
            ("Collection (CA)", "\n" + bot.collections_html({COLLECTION: 3})),
        )
        self.assertIn(f"<code>{COLLECTION}</code> ×3", text)
        self.assertNotIn("http", text)
        self.assertNotIn("href", text)

    def test_text_is_escaped_for_telegram_html(self):
        self.assertEqual(bot.esc("a<b>&"), "a&lt;b&gt;&amp;")

    def test_alert_logs_plain_text_and_sends_html(self):
        with mock.patch.object(bot, "log") as log, mock.patch.object(bot, "notify") as notify:
            bot.alert("plain", "<b>rich</b>")
        log.assert_called_once_with("plain")
        notify.assert_called_once_with("<b>rich</b>", html=True)

    def test_alert_without_rich_text_stays_plain(self):
        with mock.patch.object(bot, "log"), mock.patch.object(bot, "notify") as notify:
            bot.alert("plain")
        notify.assert_called_once_with("plain")

    def test_html_mode_sets_the_parse_mode(self):
        sent = {}

        class Done:
            def read(self):
                return b""

        def fake_urlopen(req, timeout):
            sent["body"] = req.data.decode()
            return Done()

        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
        with mock.patch.dict(os.environ, env), mock.patch.object(bot.urllib.request, "urlopen", fake_urlopen), mock.patch.object(
            bot.threading, "Thread", lambda target, daemon: mock.Mock(start=target)
        ):
            bot.notify("<b>x</b>", html=True)
        self.assertIn("parse_mode=HTML", sent["body"])


if __name__ == "__main__":
    unittest.main()
