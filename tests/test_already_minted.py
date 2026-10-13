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
CALL = "0xa0712d68" + "0" * 63 + "1"


def make_bot(**env):
    values = {
        "WATCH_ADDRESSES": WATCHED,
        "MINTER_PRIVATE_KEYS": TEST_KEY,
        "CHAINS": "1",
        "STATE_PATH": os.path.join(tempfile.mkdtemp(), "state.json"),
    }
    values.update(env)
    with mock.patch.dict(os.environ, values, clear=True):
        return bot.Bot()


def source_tx(txh="0xh1"):
    return {"hash": txh, "from": WATCHED, "to": COLLECTION, "value": "0x0", "input": CALL}


def refusing(b, kind="reverted"):
    def pick(chain_id, acct, options, want):
        b.last_pick_kind = kind
        b.last_pick_reason = "the simulated mint reverted for this minter"
        return None

    b.pick = pick


class QuietSkip(unittest.TestCase):
    def alerts(self, b):
        with mock.patch.object(bot, "notify") as notify, mock.patch.object(bot, "log"):
            b.copy(1, source_tx(), {COLLECTION: 1})
        return [call[0][0] for call in notify.call_args_list]

    def test_a_refusal_after_a_past_mint_is_silent(self):
        b = make_bot()
        refusing(b)
        b.minted_before[f"1:{COLLECTION}"] = [b.minters[0].address.lower()]
        messages = self.alerts(b)
        self.assertEqual([m for m in messages if "Not copied" in m], [])
        self.assertIn("1:0xh1", b.seen)

    def test_a_refusal_on_a_new_collection_still_alerts_with_the_reason(self):
        b = make_bot()
        refusing(b)
        self.assertTrue(any("Not copied" in m for m in self.alerts(b)))

    def test_a_different_nft_result_still_alerts_even_after_a_past_mint(self):
        b = make_bot()
        refusing(b, kind="different")
        b.minted_before[f"1:{COLLECTION}"] = [b.minters[0].address.lower()]
        self.assertTrue(any("Not copied" in m for m in self.alerts(b)))

    def test_a_collection_that_still_allows_mints_is_minted_again(self):
        b = make_bot()
        b.minted_before[f"1:{COLLECTION}"] = [b.minters[0].address.lower()]
        b.pick = lambda chain_id, acct, options, want: (0, COLLECTION, CALL, 100_000)
        b.fees = lambda chain_id: (1, 1)
        b.ensure = lambda acct, chain_id, need: True
        b.land = mock.Mock(return_value=("0xmine", True, ["0xmine"]))
        with mock.patch.object(bot, "notify"), mock.patch.object(bot, "log"):
            b.copy(1, source_tx(), {COLLECTION: 1})
        b.land.assert_called_once()


class Remembering(unittest.TestCase):
    def test_a_landed_mint_is_remembered_and_survives_a_restart(self):
        b = make_bot()
        b.pick = lambda chain_id, acct, options, want: (0, COLLECTION, CALL, 100_000)
        b.fees = lambda chain_id: (1, 1)
        b.ensure = lambda acct, chain_id, need: True
        b.land = mock.Mock(return_value=("0xmine", True, ["0xmine"]))
        with mock.patch.object(bot, "notify"), mock.patch.object(bot, "log"):
            b.copy(1, source_tx(), {COLLECTION: 1})
        self.assertTrue(b.minted_before_any(1, b.minters[0].address, {COLLECTION: 1}))
        again = make_bot(STATE_PATH=str(b.state_path))
        self.assertTrue(again.minted_before_any(1, again.minters[0].address, {COLLECTION.upper().replace("0X", "0x"): 1}))

    def test_a_dry_run_is_not_remembered(self):
        b = make_bot(DRY_RUN="true")
        b.pick = lambda chain_id, acct, options, want: (0, COLLECTION, CALL, 100_000)
        b.fees = lambda chain_id: (1, 1)
        b.ensure = lambda acct, chain_id, need: True
        b.land = mock.Mock(return_value=("0xmine", True, ["0xmine"]))
        with mock.patch.object(bot, "notify"), mock.patch.object(bot, "log"):
            b.copy(1, source_tx(), {COLLECTION: 1})
        self.assertEqual(b.minted_before, {})

    def test_damaged_saved_records_are_ignored(self):
        b = make_bot()
        b.state_path.write_text('{"minted_before": {"1:x": "junk", "1:y": ["nope", 5]}}')
        self.assertEqual(make_bot(STATE_PATH=str(b.state_path)).minted_before, {})


if __name__ == "__main__":
    unittest.main()
