import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
OTHER_WATCHED = "0x" + "cd" * 20
COLLECTION = "0x" + "ee" * 20
SECOND_COLLECTION = "0x" + "ff" * 20
TEST_KEY = "0x" + "11" * 32


def topic(addr):
    return "0x" + addr[2:].rjust(64, "0")


def make_bot(**env):
    values = {
        "WATCH_ADDRESSES": f"{WATCHED},{OTHER_WATCHED}",
        "MINTER_PRIVATE_KEYS": TEST_KEY,
        "CHAINS": "1,56",
        "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
        "MINT_COOLDOWN_SECONDS": "300",
    }
    values.update(env)
    with mock.patch.dict(os.environ, values, clear=True):
        return bot.Bot()


def mint_logs(wallet, *collections):
    return [
        {"address": c, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(wallet), "0x" + "0" * 64]}
        for c in collections
    ]


class Claiming(unittest.TestCase):
    def test_cooldown_is_off_unless_asked_for(self):
        with mock.patch.dict(os.environ, {"WATCH_ADDRESSES": WATCHED, "MINTER_PRIVATE_KEYS": TEST_KEY, "CHAINS": "1"}, clear=True):
            self.assertEqual(bot.Bot().mint_cooldown_seconds, 0)

    def test_a_wallet_minting_ten_times_is_copied_ten_times_by_default(self):
        b = make_bot(MINT_COOLDOWN_SECONDS="0")
        for n in range(10):
            self.assertTrue(b.claim_collection_cooldown(1, f"0xh{n}", {COLLECTION: 1})[0])

    def test_bad_cooldown_setting_stops_the_bot(self):
        for value in ("abc", "1.5"):
            with self.assertRaises(SystemExit):
                make_bot(MINT_COOLDOWN_SECONDS=value)

    def test_second_watched_wallet_cannot_retrigger_same_collection(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            self.assertEqual(b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1}), (True, []))
            allowed, blocked = b.claim_collection_cooldown(1, "0xbbb", {COLLECTION.lower(): 1})
        self.assertFalse(allowed)
        self.assertEqual(blocked[0][0], f"1:{COLLECTION.lower()}")
        self.assertGreater(blocked[0][1], 0)

    def test_same_transaction_may_claim_again_for_retries(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
            self.assertTrue(b.claim_collection_cooldown(1, "0xAAA", {COLLECTION: 1})[0])

    def test_cooldown_is_scoped_to_chain_and_collection(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
            self.assertTrue(b.claim_collection_cooldown(56, "0xbbb", {COLLECTION: 1})[0])
            self.assertTrue(b.claim_collection_cooldown(1, "0xccc", {SECOND_COLLECTION: 1})[0])

    def test_cooldown_expires(self):
        b = make_bot(MINT_COOLDOWN_SECONDS="30")
        with mock.patch.object(bot.time, "time", return_value=1000):
            self.assertTrue(b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})[0])
        with mock.patch.object(bot.time, "time", return_value=1031):
            self.assertTrue(b.claim_collection_cooldown(1, "0xbbb", {COLLECTION: 1})[0])

    def test_zero_disables_it(self):
        b = make_bot(MINT_COOLDOWN_SECONDS="0")
        self.assertTrue(b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})[0])
        self.assertTrue(b.claim_collection_cooldown(1, "0xbbb", {COLLECTION: 1})[0])
        self.assertEqual(b.collection_cooldowns, {})

    def test_multi_collection_event_is_all_or_nothing(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
            allowed, _ = b.claim_collection_cooldown(1, "0xbbb", {COLLECTION: 1, SECOND_COLLECTION: 1})
            self.assertFalse(allowed)
            # The second collection was not claimed by the suppressed event.
            self.assertTrue(b.claim_collection_cooldown(1, "0xccc", {SECOND_COLLECTION: 1})[0])

    def test_release_only_frees_the_claim_that_transaction_made(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
            b.release_collection_cooldown(1, "0xbbb", {COLLECTION: 1})
            self.assertFalse(b.claim_collection_cooldown(1, "0xccc", {COLLECTION: 1})[0])
            b.release_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
            self.assertTrue(b.claim_collection_cooldown(1, "0xccc", {COLLECTION: 1})[0])


class Persistence(unittest.TestCase):
    def test_cooldowns_survive_state_reload(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
        with mock.patch.object(bot.time, "time", return_value=1001):
            again = make_bot(STATE_PATH=str(b.state_path))
            allowed, _ = again.claim_collection_cooldown(1, "0xbbb", {COLLECTION: 1})
        self.assertFalse(allowed)

    def test_expired_cooldowns_are_not_restored_or_saved(self):
        b = make_bot()
        with mock.patch.object(bot.time, "time", return_value=1000):
            b.claim_collection_cooldown(1, "0xaaa", {COLLECTION: 1})
        with mock.patch.object(bot.time, "time", return_value=5000):
            again = make_bot(STATE_PATH=str(b.state_path))
            self.assertEqual(again.collection_cooldowns, {})
            again._save()
            self.assertNotIn("collection_cooldowns", json.loads(b.state_path.read_text()))

    def test_damaged_saved_records_are_ignored(self):
        b = make_bot()
        b.state_path.write_text(json.dumps({"collection_cooldowns": {"1:x": "junk", "1:y": {"until": "soon", "tx": "0x1"}}}))
        self.assertEqual(make_bot(STATE_PATH=str(b.state_path)).collection_cooldowns, {})


class SharedHandler(unittest.TestCase):
    """Polling and the websocket both go through handle_tx, so the cooldown covers both."""

    def build(self, copy_result=1):
        b = make_bot()
        txs = {
            "0xh1": {"hash": "0xh1", "from": WATCHED, "to": COLLECTION, "value": "0x0", "input": "0xa0712d68" + "0" * 63 + "1"},
            "0xh2": {"hash": "0xh2", "from": OTHER_WATCHED, "to": COLLECTION, "value": "0x0", "input": "0xa0712d68" + "0" * 63 + "1"},
        }
        b.rpc = mock.Mock()
        b.rpc.call.side_effect = lambda chain, method, params: txs[params[0]]
        b.copy = mock.Mock(return_value=copy_result)
        return b

    def test_two_watched_wallets_minting_one_collection_copy_once(self):
        b = self.build()
        with mock.patch.object(bot, "notify") as notify:
            b.handle_tx(1, "0xh1", mint_logs(WATCHED, COLLECTION))
            b.handle_tx(1, "0xh2", mint_logs(OTHER_WATCHED, COLLECTION))
        self.assertEqual(b.copy.call_count, 1)
        self.assertIn("1:0xh2", b.seen)
        notify.assert_not_called()  # suppressed mints are logged, not sent to Telegram

    def test_a_copy_that_minted_nothing_does_not_block_the_next_wallet(self):
        b = self.build(copy_result=0)
        with mock.patch.object(bot, "notify"):
            b.handle_tx(1, "0xh1", mint_logs(WATCHED, COLLECTION))
            b.handle_tx(1, "0xh2", mint_logs(OTHER_WATCHED, COLLECTION))
        self.assertEqual(b.copy.call_count, 2)

    def test_a_copy_that_is_still_retrying_keeps_the_claim(self):
        b = self.build(copy_result=0)

        def retrying(chain_id, tx, want):
            b.pending[f"{chain_id}:{tx['hash']}"] = {"chain": chain_id}
            return 0

        b.copy.side_effect = retrying
        with mock.patch.object(bot, "notify"):
            b.handle_tx(1, "0xh1", mint_logs(WATCHED, COLLECTION))
            b.handle_tx(1, "0xh2", mint_logs(OTHER_WATCHED, COLLECTION))
        self.assertEqual(b.copy.call_count, 1)

    def test_paused_bot_does_not_claim_a_collection(self):
        b = self.build()
        b.paused = True
        with mock.patch.object(bot, "notify"):
            b.handle_tx(1, "0xh1", mint_logs(WATCHED, COLLECTION))
        b.copy.assert_not_called()
        self.assertEqual(b.collection_cooldowns, {})

    def test_disabled_cooldown_copies_both(self):
        b = self.build()
        b.mint_cooldown_seconds = 0
        with mock.patch.object(bot, "notify"):
            b.handle_tx(1, "0xh1", mint_logs(WATCHED, COLLECTION))
            b.handle_tx(1, "0xh2", mint_logs(OTHER_WATCHED, COLLECTION))
        self.assertEqual(b.copy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
