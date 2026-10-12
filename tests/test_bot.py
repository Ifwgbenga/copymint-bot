import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eth_abi import encode  # noqa: E402

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
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


class Decoding(unittest.TestCase):
    def test_selector(self):
        self.assertEqual(bot.selector("mint(uint256)"), "0xa0712d68")

    def test_multicall_bytes_array(self):
        inner = [bytes.fromhex("a0712d68") + encode(["uint256"], [2]), b"\x12\x34\x56\x78"]
        data = bot.selector("multicall(bytes[])") + encode(["bytes[]"], [inner]).hex()
        calls = bot.expand_calls("0xtarget", data, 0)
        self.assertEqual([c[0] for c in calls], ["0xtarget", "0xtarget"])
        self.assertEqual(calls[0][1], "0x" + inner[0].hex())

    def test_aggregate3_keeps_inner_target(self):
        payload = bytes.fromhex("a0712d68") + encode(["uint256"], [1])
        data = bot.selector("aggregate3((address,bool,bytes)[])") + encode(
            ["(address,bool,bytes)[]"], [[(COLLECTION, True, payload)]]
        ).hex()
        calls = bot.expand_calls("0xrouter", data, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], COLLECTION)
        self.assertEqual(calls[0][1], "0x" + payload.hex())

    def test_plain_call_is_returned_as_is(self):
        data = "0xa0712d68" + "0" * 63 + "1"
        self.assertEqual(bot.expand_calls("0xt", data, 5), [("0xt", data, 5)])

    def test_signature_calls_are_detected(self):
        signed = "0x12345678" + encode(["uint256", "bytes"], [1, b"\x01" * 64 + b"\x1b"]).hex()
        self.assertTrue(bot.looks_signed(signed))
        self.assertFalse(bot.looks_signed("0xa0712d68" + "0" * 63 + "1"))

    def test_quantities(self):
        single = "0x" + encode(["uint256", "uint256"], [7, 3]).hex()
        self.assertEqual(bot.single_qty(single), 3)
        batch = "0x" + encode(["uint256[]", "uint256[]"], [[1, 2], [4, 5]]).hex()
        self.assertEqual(bot.batch_qty(batch), 9)

    def test_rewrite_swaps_the_address_word(self):
        data = "0xa0712d68" + "0" * 24 + WATCHED[2:] + "0" * 63 + "1"
        out = bot.rewrite(data, WATCHED, OTHER)
        self.assertIn(OTHER[2:], out)
        self.assertNotIn(WATCHED[2:], out)


class MintedMap(unittest.TestCase):
    def setUp(self):
        self.bot = make_bot()

    def test_erc721_mint_to_watched(self):
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(WATCHED), "0x" + "0" * 63 + "1"]}]
        self.assertEqual(self.bot.minted_map(logs), {COLLECTION: 1})

    def test_ignores_other_recipients_and_non_mints(self):
        logs = [
            {"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(OTHER), "0x" + "0" * 64]},
            {"address": COLLECTION, "topics": [bot.TRANSFER, topic(OTHER), topic(WATCHED), "0x" + "0" * 64]},
        ]
        self.assertEqual(self.bot.minted_map(logs), {})

    def test_erc1155_single_and_batch(self):
        single = {
            "address": COLLECTION,
            "topics": [bot.TRANSFER_SINGLE, topic(OTHER), bot.ZERO_TOPIC, topic(WATCHED)],
            "data": "0x" + encode(["uint256", "uint256"], [1, 3]).hex(),
        }
        batch = {
            "address": COLLECTION,
            "topics": [bot.TRANSFER_BATCH, topic(OTHER), bot.ZERO_TOPIC, topic(WATCHED)],
            "data": "0x" + encode(["uint256[]", "uint256[]"], [[1, 2], [4, 5]]).hex(),
        }
        self.assertEqual(self.bot.minted_map([single, batch]), {COLLECTION: 12})

    def test_minted_for_checks_the_copy_recipient(self):
        minter = "0x" + "99" * 20
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(minter), "0x" + "0" * 64]}]
        self.assertEqual(self.bot.minted_for(logs, minter), {COLLECTION: 1})
        self.assertEqual(self.bot.minted_for(logs, OTHER), {})
        self.assertIn(WATCHED.lower(), self.bot.watch)  # the watch list is restored afterwards


class Config(unittest.TestCase):
    def test_to_wei(self):
        self.assertEqual(bot.to_wei("0.05"), 5 * 10**16)

    def test_per_chain_caps(self):
        b = make_bot(MAX_MINT_ETH="0.05", MAX_MINT_56="0.2")
        self.assertEqual(b.cap(1), 5 * 10**16)
        self.assertEqual(b.cap(56), 2 * 10**17)

    def test_estimate_fallback_is_off_by_default(self):
        self.assertFalse(make_bot().allow_estimate)
        self.assertTrue(make_bot(ALLOW_ESTIMATE_FALLBACK="true").allow_estimate)

    def test_watching_your_own_minter_is_not_blocked_here_but_is_in_own(self):
        b = make_bot()
        self.assertIn(b.minters[0].address.lower(), b.own)


class SeenSetTests(unittest.TestCase):
    def test_tail_keeps_the_newest_keys(self):
        seen = bot.SeenSet()
        for i in range(10):
            seen.add(f"k{i}")
        self.assertEqual(seen.tail(3), ["k7", "k8", "k9"])
        self.assertIn("k0", seen)

    def test_state_round_trip(self):
        b = make_bot()
        for i in range(6000):
            b.seen.add(f"1:{i}")
        b._save()
        again = bot.Bot.__new__(bot.Bot)
        again.state_path = b.state_path
        again.heads, again.seen, again.pending = {}, bot.SeenSet(), {}
        again._load()
        self.assertEqual(len(again.seen), 5000)
        self.assertIn("1:5999", again.seen)
        self.assertNotIn("1:0", again.seen)


class FakeRPC:
    """Reports a receipt for `lands` only once a replacement has been sent."""

    def __init__(self, bot_under_test, lands):
        self.block = 100
        self.lands = lands
        self.bot = bot_under_test

    def call(self, chain_id, method, params, timeout=25):
        if method == "eth_blockNumber":
            self.block += 1
            return hex(self.block)
        if method == "eth_getTransactionCount":
            return "0x5"
        if method == "eth_getTransactionReceipt":
            if params[0] == self.lands and len(self.bot.sent_hashes) >= 2:
                return {"status": "0x1"}
            return None
        raise AssertionError(method)


class Landing(unittest.TestCase):
    def build(self, lands):
        b = make_bot()
        b.sent_hashes = []

        def fake_send(chain_id, acct, to, data="0x", value=0, gas=None, fee=None, tip=None, nonce=None, wait=True):
            h = f"0x{len(b.sent_hashes) + 10:x}"
            b.sent_hashes.append(h)
            return h, False

        b.fees = lambda chain_id: (10, 1)
        b.send = fake_send
        b.rpc = FakeRPC(b, lands)
        return b

    def test_original_landing_after_a_replacement_is_still_seen(self):
        b = self.build(lands="0xa")  # the first hash sent
        with mock.patch.object(bot.time, "sleep", lambda s: None):
            txh, ok, sent = b.land(1, b.minters[0], COLLECTION, "0x", 0, 100_000)
        self.assertTrue(ok)
        self.assertEqual(txh, "0xa")
        self.assertEqual(sent, ["0xa", "0xb"])

    def test_replacement_landing_is_seen_too(self):
        b = self.build(lands="0xb")
        with mock.patch.object(bot.time, "sleep", lambda s: None):
            txh, ok, sent = b.land(1, b.minters[0], COLLECTION, "0x", 0, 100_000)
        self.assertTrue(ok)
        self.assertEqual(txh, "0xb")

    def test_receipt_for_returns_none_when_nothing_landed(self):
        b = self.build(lands="0xzzz")
        self.assertIsNone(b.receipt_for(1, ["0xa", "0xb"]))


class Picking(unittest.TestCase):
    def test_no_simulation_means_skip_unless_fallback_is_allowed(self):
        b = make_bot()
        b.simulate = lambda *a, **k: "unsupported"
        b.fallback = mock.Mock(return_value="sent")
        data = "0xa0712d68" + "0" * 63 + "1"
        self.assertIsNone(b.pick(1, b.minters[0], [(COLLECTION, data, 0)], {COLLECTION.lower(): 1}))
        b.fallback.assert_not_called()

        b.allow_estimate = True
        self.assertEqual(b.pick(1, b.minters[0], [(COLLECTION, data, 0)], {COLLECTION.lower(): 1}), "sent")

    def test_matching_simulation_is_chosen(self):
        b = make_bot()
        minter = b.minters[0].address
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(minter), "0x" + "0" * 64]}]
        b.simulate = lambda *a, **k: (logs, 150_000)
        data = "0xa0712d68" + "0" * 63 + "1"
        value, target, _data, gas = b.pick(1, b.minters[0], [(COLLECTION, data, 0)], {COLLECTION.lower(): 1})
        self.assertEqual((value, target, gas), (0, COLLECTION, 150_000))

    def test_wrong_quantity_is_rejected(self):
        b = make_bot()
        minter = b.minters[0].address
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(minter), "0x" + "0" * 64]}]
        b.simulate = lambda *a, **k: (logs, 150_000)
        data = "0xa0712d68" + "0" * 63 + "1"
        self.assertIsNone(b.pick(1, b.minters[0], [(COLLECTION, data, 0)], {COLLECTION.lower(): 5}))

    def test_value_above_the_chain_cap_is_not_simulated(self):
        b = make_bot(MAX_MINT_ETH="0.01")
        calls = []
        b.simulate = lambda chain, acct, target, data, value: calls.append(value) or None
        data = "0xa0712d68" + "0" * 63 + "1"
        b.pick(1, b.minters[0], [(COLLECTION, data, 10**18)], {COLLECTION.lower(): 1})
        self.assertEqual(calls, [0])


class DryRun(unittest.TestCase):
    def test_true_values(self):
        for value in ("true", "TRUE", " yes ", '"true"'):
            self.assertTrue(make_bot(DRY_RUN=value).dry, value)
        self.assertFalse(make_bot().dry)
        self.assertFalse(make_bot(DRY_RUN="false").dry)

    def test_send_and_land_do_not_touch_the_rpc(self):
        b = make_bot(DRY_RUN="true")
        b.rpc = mock.Mock()
        b.rpc.call.side_effect = AssertionError("broadcast")
        txh, ok = b.send(1, b.minters[0], COLLECTION, "0x", 0, 100_000)
        self.assertEqual((txh, ok), ("dry", True))
        txh, ok, sent = b.land(1, b.minters[0], COLLECTION, "0x", 0, 100_000)
        self.assertEqual((txh, ok, sent), ("dry", True, ["dry"]))
        b.rpc.call.assert_not_called()

    def test_short_wallet_does_not_bridge(self):
        b = make_bot(DRY_RUN="true")
        b.balance = lambda chain_id, token, who: 0
        b.holdings = mock.Mock(side_effect=AssertionError("bridge"))
        self.assertTrue(b.ensure(b.minters[0], 1, 10**16))
        b.holdings.assert_not_called()
    class InlineThread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    def test_silent_without_telegram_settings(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(bot.threading, "Thread") as thread:
            bot.notify("hello")
        thread.assert_not_called()

    def test_posts_to_telegram(self):
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            bot.threading, "Thread", self.InlineThread
        ), mock.patch.object(bot.urllib.request, "urlopen") as urlopen:
            bot.notify("minted")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:abc/sendMessage")
        self.assertIn(b"chat_id=42", request.data)
        self.assertIn(b"minted", request.data)

    def test_failure_never_raises_or_leaks_the_token(self):
        env = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "42"}
        lines = []
        error = urllib.error.URLError("https://api.telegram.org/bot123:secret/sendMessage failed")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            bot.threading, "Thread", self.InlineThread
        ), mock.patch.object(bot.urllib.request, "urlopen", side_effect=error), mock.patch.object(
            bot, "log", lines.append
        ):
            bot.notify("minted")
        self.assertEqual(len(lines), 1)
        self.assertNotIn("secret", lines[0])


if __name__ == "__main__":
    unittest.main()
