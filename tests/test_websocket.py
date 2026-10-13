import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402

WATCHED = "0x" + "ab" * 20
COLLECTION = "0x" + "ee" * 20
TEST_KEY = "0x" + "11" * 32
SECRET_URL = "wss://base-mainnet.example.invalid/v2/secretkey"


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


def notification(txh, removed=False):
    entry = {
        "address": COLLECTION,
        "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(WATCHED), "0x" + "0" * 64],
        "transactionHash": txh,
    }
    if removed:
        entry["removed"] = True
    return json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {"subscription": "0xs1", "result": entry}})


class Stop(BaseException):
    """Ends a websocket loop in a test. It is not an Exception, so the loop's error handling does not swallow it."""


class FakeSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    def settimeout(self, seconds):
        pass

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        frame = self.frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        if callable(frame):
            return frame()
        return frame

    def ping(self):
        pass

    def close(self):
        self.closed = True


def fake_websocket_module(create_connection):
    module = types.ModuleType("websocket")
    module.WebSocketTimeoutException = type("WebSocketTimeoutException", (Exception,), {})
    module.create_connection = create_connection
    return module


CONFIRMATIONS = [json.dumps({"jsonrpc": "2.0", "id": n, "result": f"0xsub{n}"}) for n in (1, 2, 3)]


class Messages(unittest.TestCase):
    def setUp(self):
        self.b = make_bot()
        self.b._pool = mock.Mock()

    def test_only_the_first_log_of_a_transaction_starts_work(self):
        for _ in range(10):
            self.b.ws_message(1, notification("0xh1"))
        self.b._pool.submit.assert_called_once_with(self.b.process_ws_tx, 1, "0xh1")

    def test_other_chains_and_transactions_are_separate(self):
        self.b.ws_message(1, notification("0xh1"))
        self.b.ws_message(56, notification("0xh1"))
        self.b.ws_message(1, notification("0xh2"))
        self.assertEqual(self.b._pool.submit.call_count, 3)

    def test_removed_logs_and_noise_are_ignored(self):
        self.b.ws_message(1, notification("0xh1", removed=True))
        self.b.ws_message(1, "not json")
        self.b.ws_message(1, json.dumps({"id": 1, "result": "0xsub"}))
        self.b._pool.submit.assert_not_called()

    def test_already_copied_transactions_are_ignored(self):
        self.b.seen.add("1:0xh1")
        self.b.ws_message(1, notification("0xh1"))
        self.b._pool.submit.assert_not_called()


class Receipts(unittest.TestCase):
    def build(self, receipt):
        b = make_bot()
        tx = {"hash": "0xh1", "from": WATCHED, "to": COLLECTION, "value": "0x0", "input": "0xa0712d68" + "0" * 63 + "1"}

        def call(chain_id, method, params):
            return receipt if method == "eth_getTransactionReceipt" else tx

        b.rpc = mock.Mock()
        b.rpc.call.side_effect = call
        b.copy = mock.Mock(return_value=1)
        return b

    def test_a_mint_of_ten_is_copied_as_ten(self):
        logs = [
            {"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(WATCHED), "0x" + f"{n:064x}"]}
            for n in range(10)
        ]
        b = self.build({"status": "0x1", "logs": logs})
        b.process_ws_tx(1, "0xh1")
        b.copy.assert_called_once()
        self.assertEqual(b.copy.call_args[0][2], {COLLECTION: 10})

    def test_a_reverted_transaction_is_ignored(self):
        b = self.build({"status": "0x0", "logs": []})
        b.process_ws_tx(1, "0xh1")
        b.copy.assert_not_called()

    def test_a_missing_receipt_is_left_for_the_poll(self):
        b = self.build(None)
        with mock.patch.object(bot.time, "sleep", lambda s: None):
            b.process_ws_tx(1, "0xh1")
        b.copy.assert_not_called()
        self.assertIn(1, b.ws_gap)  # so the next poll scans for it

    def test_the_poll_and_the_websocket_copy_a_mint_once(self):
        logs = [{"address": COLLECTION, "topics": [bot.TRANSFER, bot.ZERO_TOPIC, topic(WATCHED), "0x" + "0" * 64]}]
        b = self.build({"status": "0x1", "logs": logs})
        b.copy.side_effect = lambda chain_id, tx, want: b.seen.add(f"{chain_id}:{tx['hash']}") or 1
        b.process_ws_tx(1, "0xh1")
        b.handle_tx(1, "0xh1", logs)  # what the polling path does for the same transaction
        b.copy.assert_called_once()


class Scheduling(unittest.TestCase):
    def test_a_live_websocket_is_only_polled_as_a_backup(self):
        b = make_bot(WSS_BACKUP_SECONDS="5")
        b.ws_live = {1: True}
        self.assertEqual(b.chains_due(100), [1, 56])  # the first round polls everything
        self.assertEqual(b.chains_due(101), [56])
        self.assertEqual(b.chains_due(106), [1, 56])

    def test_a_dropped_websocket_goes_back_to_full_speed_polling(self):
        b = make_bot()
        b.ws_live = {1: True}
        b.chains_due(100)
        b.ws_live[1] = False
        self.assertEqual(b.chains_due(100.4), [1, 56])

    def test_status_names_the_live_sockets(self):
        b = make_bot()
        self.assertIn("websocket live: none", b.command_reply("/status"))
        b.ws_live = {56: True}
        self.assertIn("websocket live: BNB Chain", b.command_reply("/status"))


class Settings(unittest.TestCase):
    def test_wss_urls_are_read_per_chain(self):
        b = make_bot(WSS_1=SECRET_URL)
        self.assertEqual(b.chains[1].wss, SECRET_URL)
        self.assertEqual(b.chains[56].wss, "")

    def test_a_bad_scheme_is_refused_without_echoing_the_url(self):
        with self.assertRaises(SystemExit) as caught:
            make_bot(WSS_1="https://base-mainnet.example.invalid/v2/secretkey")
        self.assertNotIn("secretkey", str(caught.exception))
        self.assertIn("WSS_1", str(caught.exception))


class Loop(unittest.TestCase):
    def build(self):
        b = make_bot(WSS_1=SECRET_URL)
        b._pool = mock.Mock()
        return b

    def test_it_subscribes_with_the_watch_filters_and_dispatches_events(self):
        b = self.build()
        socket = FakeSocket(CONFIRMATIONS + [notification("0xh1"), Stop()])
        module = fake_websocket_module(lambda url, timeout: socket)
        with mock.patch.dict(sys.modules, {"websocket": module}), self.assertRaises(Stop):
            b.ws_loop(1)
        subscriptions = [m for m in socket.sent if m["method"] == "eth_subscribe"]
        self.assertEqual(len(subscriptions), 3)
        for message in subscriptions:
            self.assertEqual(message["params"][0], "logs")
            self.assertIn(topic(WATCHED), message["params"][1]["topics"][-1])
        b._pool.submit.assert_called_once_with(b.process_ws_tx, 1, "0xh1")
        self.assertTrue(socket.closed)
        self.assertFalse(b.ws_live[1])

    def test_it_is_marked_live_while_connected(self):
        b = self.build()
        seen_live = []
        socket = FakeSocket(CONFIRMATIONS + [lambda: seen_live.append(b.ws_live.get(1)) or notification("0xh1"), Stop()])
        module = fake_websocket_module(lambda url, timeout: socket)
        with mock.patch.dict(sys.modules, {"websocket": module}), self.assertRaises(Stop):
            b.ws_loop(1)
        self.assertEqual(seen_live, [True])

    def test_a_changed_watch_list_forces_a_resubscribe(self):
        b = self.build()
        module = fake_websocket_module(None)
        first = FakeSocket(CONFIRMATIONS + [lambda: (b._watch_changed(), "")[1] or (_ for _ in ()).throw(module.WebSocketTimeoutException())])
        calls = []

        def connect(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                return first
            raise Stop()

        module.create_connection = connect
        with mock.patch.dict(sys.modules, {"websocket": module}), self.assertRaises(Stop):
            b.ws_loop(1)
        self.assertEqual(len(calls), 2)

    def test_connection_errors_never_log_the_url(self):
        b = self.build()
        lines = []

        def connect(url, timeout):
            raise ConnectionError(f"could not reach {url}")

        module = fake_websocket_module(connect)
        with mock.patch.dict(sys.modules, {"websocket": module}), mock.patch.object(bot, "log", lines.append), mock.patch.object(
            bot.time, "sleep", side_effect=Stop()
        ), self.assertRaises(Stop):
            b.ws_loop(1)
        self.assertTrue(lines)
        self.assertFalse(any("secretkey" in line for line in lines))

    def test_five_failures_in_a_row_send_one_alert(self):
        b = self.build()
        module = fake_websocket_module(lambda url, timeout: (_ for _ in ()).throw(ConnectionError("down")))
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 6:
                raise Stop()

        with mock.patch.dict(sys.modules, {"websocket": module}), mock.patch.object(bot, "log"), mock.patch.object(
            bot, "notify"
        ) as notify, mock.patch.object(bot.time, "sleep", sleep), self.assertRaises(Stop):
            b.ws_loop(1)
        notify.assert_called_once()
        self.assertEqual(sleeps[:4], [1, 2, 4, 8])  # backs off, up to 30 seconds

    def test_a_missing_library_falls_back_to_polling(self):
        b = self.build()
        lines = []
        with mock.patch.dict(sys.modules, {"websocket": None}), mock.patch.object(bot, "log", lines.append):
            b.ws_loop(1)
        self.assertTrue(any("polling only" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
