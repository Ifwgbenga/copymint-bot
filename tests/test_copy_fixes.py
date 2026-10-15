import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
COLLECTION = "0x" + "ee" * 20
TEST_KEY = "0x" + "11" * 32


def make_bot():
    values = {
        "WATCH_ADDRESSES": WATCHED,
        "MINTER_PRIVATE_KEYS": TEST_KEY,
        "CHAINS": "1",
        "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
    }
    with mock.patch.dict(os.environ, values, clear=True):
        return bot.Bot()


def tx(to=COLLECTION, data="0x", value="0x2386f26fc10000"):
    return {"hash": "0xh1", "from": WATCHED, "to": to, "value": value, "input": data}


class EmptyCallData(unittest.TestCase):
    def test_a_plain_eth_send_to_the_collection_is_a_candidate(self):
        b = make_bot()
        self.assertEqual(b.candidates(tx(), {COLLECTION: 1}), [(COLLECTION, "0x", 10**16)])

    def test_a_plain_send_to_anything_else_is_not(self):
        b = make_bot()
        self.assertEqual(b.candidates(tx(to="0x" + "99" * 20), {COLLECTION: 1}), [])

    def test_a_free_empty_call_is_not(self):
        b = make_bot()
        self.assertEqual(b.candidates(tx(value="0x0"), {COLLECTION: 1}), [])

    def test_without_the_minted_collections_nothing_changes(self):
        self.assertEqual(make_bot().candidates(tx()), [])


class SimulationBalance(unittest.TestCase):
    def test_simulation_gives_the_minter_a_balance(self):
        b = make_bot()
        b.rpc = mock.Mock()
        b.rpc.call.return_value = [{"calls": [{"status": "0x1", "gasUsed": "0x5208", "logs": []}]}]
        b.simulate(1, b.minters[0], COLLECTION, "0x", 10**16)
        params = b.rpc.call.call_args[0][2]
        overrides = params[0]["blockStateCalls"][0]["stateOverrides"]
        self.assertEqual(overrides[b.minters[0].address]["balance"], bot.SIM_BALANCE)


class Explanations(unittest.TestCase):
    def test_plain_send(self):
        self.assertIn("no call data", make_bot().explain_no_mint(tx()))

    def test_non_mint_call(self):
        data = bot.selector("approve(address,uint256)") + "0" * 128
        self.assertIn("not a mint", make_bot().explain_no_mint(tx(data=data)))

    def test_seadrop_signed_mint_is_named(self):
        text = make_bot().explain_no_mint(tx(data="0x4b61cd6f" + "0" * 64))
        self.assertIn("SeaDrop signed mint", text)
        self.assertEqual(bot.SEADROP_MINT_SIGNED, "0x4b61cd6f")

    def test_unknown_call_shows_its_function_id(self):
        self.assertIn("0xdeadbeef", make_bot().explain_no_mint(tx(data="0xdeadbeef" + "0" * 64)))


if __name__ == "__main__":
    unittest.main()


class SignedAndAllowlistCalls(unittest.TestCase):
    def test_signed_looking_call_is_still_a_candidate(self):
        from eth_abi import encode
        data = "0x12345678" + encode(["uint256", "bytes"], [1, b"\x01" * 64 + b"\x1b"]).hex()
        self.assertTrue(bot.looks_signed(data))
        b = make_bot()
        source = {"hash": "0xs", "from": "0x" + "ab" * 20, "to": "0x" + "ee" * 20, "value": "0x0", "input": data}
        self.assertEqual(len(b.candidates(source, {"0x" + "ee" * 20: 1})), 1)

    def test_only_the_eligible_minter_sends(self):
        from eth_abi import encode
        data = "0x12345678" + encode(["uint256", "bytes"], [1, b"\x01" * 64 + b"\x1b"]).hex()
        values = {
            "WATCH_ADDRESSES": WATCHED,
            "MINTER_PRIVATE_KEYS": TEST_KEY + ",0x" + "22" * 32 + ",0x" + "33" * 32,
            "CHAINS": "1",
            "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
        }
        with mock.patch.dict(os.environ, values, clear=True):
            b = bot.Bot()
        collection = "0x" + "ee" * 20
        eligible = b.minters[0].address.lower()
        sims = []

        def simulate(chain_id, acct, target, call, value):
            sims.append(acct.address.lower())
            if acct.address.lower() != eligible:
                return None
            return [], 100_000

        b.simulate = simulate
        b.minted_for = lambda logs, who: {collection: 1} if who.lower() == eligible else {}
        chosen = [b.pick(1, acct, [(collection, data, 0)], {collection: 1}) for acct in b.minters]
        self.assertIsNotNone(chosen[0])
        for other in chosen[1:]:
            self.assertIsNone(other)
        self.assertIn("not eligible", b.last_pick_reason)
