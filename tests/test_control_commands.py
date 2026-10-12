import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
NEW_WALLET = "0x" + "12" * 20
THIRD_WALLET = "0x" + "34" * 20
COLLECTION = "0x" + "ee" * 20
TEST_KEY = "0x" + "11" * 32


def topic(addr):
    return "0x" + addr[2:].rjust(64, "0")


def make_bot(**env):
    values = {
        "WATCH_ADDRESSES": WATCHED,
        "MINTER_PRIVATE_KEYS": TEST_KEY,
        "CHAINS": "1,56",
        "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
    }
    values.update(env)
    with mock.patch.dict(os.environ, values, clear=True):
        return bot.Bot()


class WalletCommands(unittest.TestCase):
    def test_watch_adds_and_persists_over_the_env_list(self):
        b = make_bot()
        self.assertIn("added", b.command_reply("/watch", [NEW_WALLET]))
        again = make_bot(STATE_PATH=str(b.state_path))
        self.assertEqual(again.watch, {WATCHED, NEW_WALLET})
        self.assertIn("saved list", again.command_reply("/wallets"))

    def test_watch_rejects_bad_own_and_duplicate_addresses(self):
        b = make_bot()
        reply = b.command_reply("/watch", ["0x123", b.minters[0].address, WATCHED])
        self.assertIn("invalid EVM address", reply)
        self.assertIn("one of your own wallets", reply)
        self.assertIn("already watched", reply)
        self.assertEqual(b.watch, {WATCHED})
        self.assertFalse(b.watch_saved)

    def test_watch_respects_the_wallet_limit(self):
        b = make_bot()
        b.watch = {f"0x{i:040x}" for i in range(bot.MAX_WATCH)}
        self.assertIn("limit", b.command_reply("/watch", [NEW_WALLET]))
        self.assertNotIn(NEW_WALLET, b.watch)

    def test_unwatch_removes_but_never_the_last_wallet(self):
        b = make_bot(WATCH_ADDRESSES=f"{WATCHED},{NEW_WALLET}")
        self.assertIn("removed", b.command_reply("/unwatch", [NEW_WALLET]))
        self.assertEqual(b.watch, {WATCHED})
        self.assertIn("last watched wallet", b.command_reply("/unwatch", [WATCHED]))
        self.assertEqual(b.watch, {WATCHED})
        self.assertIn("not currently watched", b.command_reply("/unwatch", [THIRD_WALLET]))

    def test_saved_list_is_cleaned_of_own_wallets_on_load(self):
        b = make_bot()
        b.command_reply("/watch", [NEW_WALLET])
        own = b.minters[0].address.lower()
        b.watch = {WATCHED, NEW_WALLET, own}
        b._save()
        again = make_bot(STATE_PATH=str(b.state_path))
        self.assertNotIn(own, again.watch)

    def test_new_wallet_is_used_by_mint_detection(self):
        b = make_bot()
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(NEW_WALLET), "0x" + "0" * 64]}]
        self.assertEqual(b.minted_map(logs), {})
        b.command_reply("/watch", [NEW_WALLET])
        self.assertEqual(b.minted_map(logs), {COLLECTION: 1})

    def test_minted_for_does_not_touch_the_watch_list(self):
        b = make_bot()
        before = b.watch
        b.minted_for([], "0x" + "99" * 20)
        self.assertIs(b.watch, before)

    def test_commands_with_arguments_work_from_the_configured_chat(self):
        b = make_bot()
        with mock.patch.object(bot, "notify") as notify:
            b.handle_command({"message": {"chat": {"id": 42}, "text": f"/watch {NEW_WALLET}"}}, "42")
        self.assertIn(NEW_WALLET, b.watch)
        self.assertIn("added", notify.call_args[0][0])


class PauseAndOtherCommands(unittest.TestCase):
    def test_pause_survives_a_restart(self):
        b = make_bot()
        b.command_reply("/off")
        self.assertTrue(make_bot(STATE_PATH=str(b.state_path)).paused)

    def test_off_drops_waiting_retries_and_status_reports_it(self):
        b = make_bot()
        b.pending["1:0xabc"] = {"chain": 1}
        b.command_reply("/off")
        self.assertEqual(b.pending, {})
        self.assertIn("1:0xabc", b.seen)
        self.assertIn("PAUSED", b.command_reply("/status"))
        b.command_reply("/on")
        self.assertIn("ON", b.command_reply("/status"))

    def test_paused_bot_skips_mints_and_never_replays_them(self):
        b = make_bot()
        entry = {
            "address": COLLECTION,
            "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(WATCHED), "0x" + "0" * 64],
            "data": "0x",
            "transactionHash": "0xh1",
            "blockNumber": "0x1",
            "transactionIndex": "0x0",
        }
        tx = {"hash": "0xh1", "from": WATCHED, "to": COLLECTION, "value": "0x0", "input": "0xa0712d68" + "0" * 63 + "1"}
        b.logs_for = lambda chain_id, start, end: [entry]
        b.rpc = mock.Mock()
        b.rpc.call.return_value = tx
        b.copy = mock.Mock()
        b.command_reply("/off")
        with mock.patch.object(bot, "notify"):
            b.take_range(1, 1, 1)
        b.copy.assert_not_called()
        b.command_reply("/on")
        b.take_range(1, 1, 1)
        b.copy.assert_not_called()  # already seen
        entry["transactionHash"] = tx["hash"] = "0xh2"
        b.take_range(1, 2, 2)
        b.copy.assert_called_once()

    def test_retry_pending_does_nothing_while_paused(self):
        b = make_bot()
        b.paused = True
        b.pending["1:0xabc"] = {"chain": 1}
        b.copy = mock.Mock()
        b.retry_pending()
        b.copy.assert_not_called()

    def test_minters_lists_addresses_and_never_keys(self):
        b = make_bot(FUNDER_PRIVATE_KEY="0x" + "22" * 32)
        reply = b.command_reply("/minters")
        self.assertIn(b.minters[0].address, reply)
        self.assertIn(b.funder.address, reply)
        self.assertNotIn("11" * 32, reply)
        self.assertNotIn("22" * 32, reply)

    def test_balance_reports_every_chain(self):
        b = make_bot()
        b.balance = lambda chain_id, token, who: 10**18 if chain_id == 1 else None
        text = b.command_reply("/balance")
        self.assertIn("Ethereum: 1.0000", text)
        self.assertIn("BNB Chain: n/a", text)

    def test_cap_view_set_and_persist(self):
        b = make_bot(MAX_MINT_ETH="0.05")
        self.assertIn("Ethereum (1): 0.05", b.command_reply("/cap"))
        self.assertIn("Cap set", b.command_reply("/cap", ["56", "0.1"]))
        self.assertEqual((b.cap(1), b.cap(56)), (5 * 10**16, 10**17))
        again = make_bot(MAX_MINT_ETH="0.05", STATE_PATH=str(b.state_path))
        self.assertEqual((again.cap(1), again.cap(56)), (5 * 10**16, 10**17))

    def test_cap_rejects_bad_input(self):
        b = make_bot()
        for args in (["1", "abc"], ["1", "nan"], ["1", "-1"], ["1", "5"], ["999", "0.1"], ["x", "0.1"], ["1"]):
            before = dict(b.max_values)
            b.command_reply("/cap", args)
            self.assertEqual(b.max_values, before, args)

    def test_help_lists_the_commands(self):
        text = make_bot().command_reply("/help")
        for command in ("/watch", "/unwatch", "/wallets", "/minters", "/balance", "/cap", "/off", "/on", "/status"):
            self.assertIn(command, text)

    def test_unknown_commands_get_no_reply(self):
        self.assertIsNone(make_bot().command_reply("/nonsense"))


if __name__ == "__main__":
    unittest.main()
