import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
TEST_KEY = "0x" + "11" * 32


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


def node(b, head):
    b.rpc = mock.Mock()
    b.rpc.call.side_effect = lambda chain_id, method, params: hex(head) if method == "eth_blockNumber" else []
    return b


class Defaults(unittest.TestCase):
    def test_safe_defaults_for_a_free_plan(self):
        self.assertEqual(bot.LOG_CHUNK, 10)
        self.assertEqual(make_bot().wss_backup, 30)


class Backup(unittest.TestCase):
    def test_a_healthy_socket_makes_only_a_block_number_request(self):
        b = node(make_bot(), 500)
        b.heads[1] = 100
        b.ws_live[1] = True
        b.poll_chain(1)
        methods = [c[0][1] for c in b.rpc.call.call_args_list]
        self.assertEqual(methods, ["eth_blockNumber"])
        self.assertEqual(b.heads[1], 500)

    def test_the_position_never_moves_backwards(self):
        b = node(make_bot(), 50)
        b.heads[1] = 100
        b.ws_live[1] = True
        b.poll_chain(1)
        self.assertEqual(b.heads[1], 100)

    def test_a_dropped_socket_is_followed_by_a_full_catch_up_scan(self):
        b = node(make_bot(), 130)
        b.heads[1] = 100
        b.ws_live[1] = False
        b.ws_gap.add(1)
        b.poll_chain(1)
        methods = [c[0][1] for c in b.rpc.call.call_args_list]
        self.assertIn("eth_getLogs", methods)
        self.assertEqual(b.heads[1], 130)
        self.assertNotIn(1, b.ws_gap)

    def test_a_reconnected_socket_gets_one_scan_then_goes_quiet(self):
        b = node(make_bot(), 130)
        b.heads[1] = 100
        b.ws_live[1] = True
        b.ws_gap.add(1)
        b.poll_chain(1)
        self.assertIn("eth_getLogs", [c[0][1] for c in b.rpc.call.call_args_list])
        b.rpc.call.reset_mock()
        b.poll_chain(1)
        self.assertEqual([c[0][1] for c in b.rpc.call.call_args_list], ["eth_blockNumber"])

    def test_a_failed_catch_up_is_tried_again(self):
        b = make_bot()
        b.heads[1] = 100
        b.ws_gap.add(1)
        b.scan = mock.Mock(side_effect=RuntimeError("rpc down"))
        with self.assertRaises(RuntimeError):
            b.poll_chain(1)
        self.assertIn(1, b.ws_gap)

    def test_a_chain_without_a_socket_is_scanned_normally(self):
        b = node(make_bot(), 130)
        b.heads[1] = 100
        b.poll_chain(1)
        self.assertIn("eth_getLogs", [c[0][1] for c in b.rpc.call.call_args_list])

    def test_the_old_backup_scan_can_be_switched_back_on(self):
        b = node(make_bot(WSS_BACKUP_SCAN="true"), 130)
        b.heads[1] = 100
        b.ws_live[1] = True
        b.poll_chain(1)
        self.assertIn("eth_getLogs", [c[0][1] for c in b.rpc.call.call_args_list])

    def test_scans_use_ranges_of_at_most_ten_blocks(self):
        b = node(make_bot(), 125)
        b.heads[1] = 100
        b.scan(1)
        for call in b.rpc.call.call_args_list:
            if call[0][1] == "eth_getLogs":
                f = call[0][2][0]
                self.assertLessEqual(int(f["toBlock"], 16) - int(f["fromBlock"], 16) + 1, 10)


if __name__ == "__main__":
    unittest.main()
