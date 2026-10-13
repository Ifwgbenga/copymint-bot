import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
COLLECTION = "0x" + "ee" * 20
KEYS = "0x" + "11" * 32 + ",0x" + "22" * 32
CALL = "0xa0712d68" + "0" * 63 + "1"


def make_bot(**env):
    values = {
        "WATCH_ADDRESSES": WATCHED,
        "MINTER_PRIVATE_KEYS": KEYS,
        "CHAINS": "1",
        "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
    }
    values.update(env)
    with mock.patch.dict(os.environ, values, clear=True):
        return bot.Bot()


def abi_string(text):
    raw = text.encode()
    return "0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + raw.ljust(32, b"\0").hex()


def tx():
    return {"hash": "0xsrc", "from": WATCHED, "to": COLLECTION, "value": "0x0", "input": CALL}


def rpc(b, name="Snoteleks"):
    def call(chain_id, method, params):
        if method == "eth_call":
            return abi_string(name)
        if method == "eth_getTransactionReceipt":
            return {"status": "0x1", "gasUsed": hex(100_000), "effectiveGasPrice": hex(4 * 10**9)}
        return None

    b.rpc = mock.Mock()
    b.rpc.call.side_effect = call


def run_copy(b, picks, dry=False):
    """picks: one entry per minter, True to mint or a reason string to refuse."""
    order = iter(picks)

    def pick(chain_id, acct, options, want):
        result = next(order)
        if result is True:
            return (0, COLLECTION, CALL, 100_000)
        b.last_pick_kind = "reverted"
        b.last_pick_reason = result
        return None

    b.pick = pick
    b.fees = lambda chain_id: (1, 1)
    b.ensure = lambda acct, chain_id, need: True
    counter = iter(range(1, 10))
    b.land = lambda *a, **k: (f"0xmine{next(counter)}", True, ["x"])
    b.dry = dry
    with mock.patch.object(bot, "notify") as notify, mock.patch.object(bot, "log"):
        b.copy(1, tx(), {COLLECTION: 20})
    return [c[0][0] for c in notify.call_args_list]


class Summary(unittest.TestCase):
    def test_every_minter_copied_gives_one_summary_with_name_ca_and_cost(self):
        b = make_bot()
        rpc(b)
        messages = run_copy(b, [True, True])
        summaries = [m for m in messages if "Copied" in m]
        self.assertEqual(len(summaries), 1)
        text = summaries[0]
        self.assertIn("✅ Copied · Ethereum", text)
        self.assertIn("Collection: Snoteleks", text)
        self.assertIn(f"CA: <code>{COLLECTION}</code> ×20", text)
        self.assertIn("40 NFTs across 2 minter(s)", text)
        self.assertIn("Minters copied: 2 of 2", text)
        self.assertIn("Total cost: 0.0008 ETH", text)  # two mints of 100000 gas at 4 gwei
        self.assertNotIn("http", text)

    def test_a_partial_copy_names_the_reason_and_who_was_skipped(self):
        b = make_bot()
        rpc(b)
        messages = run_copy(b, [True, "the simulated mint reverted for this minter"])
        text = [m for m in messages if "Partially" in m][0]
        self.assertIn("Minters copied: 1 of 2", text)
        self.assertIn("1 minter(s): the simulated mint reverted", text)

    def test_nothing_copied_says_so(self):
        b = make_bot()
        rpc(b)
        messages = run_copy(b, ["reason one", "reason one"])
        text = [m for m in messages if "Not copied" in m][0]
        self.assertIn("2 minter(s): reason one", text)
        self.assertNotIn("Total cost", text)

    def test_there_is_no_detected_alert_only_the_summary(self):
        b = make_bot()
        rpc(b)
        messages = run_copy(b, [True, True])
        self.assertEqual(len(messages), 1)
        self.assertNotIn("Mint detected", messages[0])

    def test_when_every_minter_already_minted_nothing_is_sent_after_detection(self):
        b = make_bot()
        rpc(b)
        for m in b.minters:
            b.minted_before[f"1:{COLLECTION}"] = b.minted_before.get(f"1:{COLLECTION}", []) + [m.address.lower()]
        messages = run_copy(b, ["reverted", "reverted"])
        self.assertEqual(messages, [])

    def test_dry_run_summary(self):
        b = make_bot()
        rpc(b)
        messages = run_copy(b, [True, True], dry=True)
        text = [m for m in messages if "Dry run" in m][0]
        self.assertIn("Would mint: 40 NFTs across 2 minter(s)", text)

    def test_a_collection_without_a_name_still_works(self):
        b = make_bot()
        rpc(b, name="")
        text = [m for m in run_copy(b, [True, True]) if "Copied" in m][0]
        self.assertNotIn("Collection:", text)
        self.assertIn(f"<code>{COLLECTION}</code> ×20", text)

    def test_a_name_with_markup_is_escaped(self):
        b = make_bot()
        rpc(b, name="<b>x</b>")
        text = [m for m in run_copy(b, [True, True]) if "Copied" in m][0]
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)


class Helpers(unittest.TestCase):
    def test_native_symbols_and_amounts(self):
        self.assertEqual(bot.fmt_native(4 * 10**14, 1), "0.0004 ETH")
        self.assertEqual(bot.fmt_native(10**13, 56), "0.000010 BNB")
        self.assertEqual(bot.fmt_native(0, 137), "0.0000 POL")

    def test_decode_abi_string_handles_junk(self):
        self.assertEqual(bot.decode_abi_string("0x"), "")
        self.assertEqual(bot.decode_abi_string(None), "")
        self.assertEqual(bot.decode_abi_string(abi_string("Snoteleks")), "Snoteleks")

    def test_the_name_is_looked_up_once_per_contract(self):
        b = make_bot()
        rpc(b)
        b.collection_name(1, COLLECTION)
        b.collection_name(1, COLLECTION)
        self.assertEqual(b.rpc.call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
