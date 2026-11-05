import threading
import unittest
import urllib.parse
from collections import deque
from unittest.mock import Mock, patch

import server
from server_modules.web.api_routes import handle_post_api


class SyncQueueTests(unittest.TestCase):
    def setUp(self):
        for name, value in (
            ("sync_lock", threading.Lock()),
            ("sync_queue_lock", threading.RLock()),
            ("sync_jobs", deque()),
            ("sync_state", {**server.sync_state, "running": False, "queued_jobs": 0, "completed_jobs": 0}),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def wait_until_finished(self, count):
        # Acquire the submission lock to observe a complete handoff.
        for _ in range(500):
            with server.sync_queue_lock:
                if server.sync_state["completed_jobs"] == count and not server.sync_state["running"]:
                    return
            threading.Event().wait(0.01)
        self.fail("Queue did not finish")

    def test_fifo_serial_execution_and_options_survive_queue(self):
        entered, release = threading.Event(), threading.Event()
        seen = []
        active = 0
        maximum = 0

        def worker(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            seen.append((args[:7], kwargs["force_full_refresh"]))
            if len(seen) == 1:
                entered.set()
                release.wait(5)
            active -= 1

        with patch.object(server, "run_sync_worker", side_effect=worker):
            first = server.start_sync_thread(1, 2, 0.5, "manual")
            self.assertTrue(entered.wait(2))
            try:
                details = ["https://jnovels.com/book/"]
                second = server.start_sync_thread(3, 4, 1, "repair", details)
                third = server.start_sync_thread(5, 6, 2, "series_refresh", force_full_refresh=True)
                details.append("https://jnovels.com/not-submitted/")
                self.assertFalse(first.queued)
                self.assertEqual((second.queue_position, third.queue_position), (1, 2))
                self.assertEqual(server.sync_state["queued_jobs"], 2)
                self.assertEqual(server.sync_state["mode"], "manual")
                self.assertEqual(len(seen), 1)
            finally:
                release.set()
            self.wait_until_finished(3)
        self.assertEqual([item[0][3] for item in seen], ["manual", "repair", "series_refresh"])
        self.assertEqual(seen[1][0][4], ["https://jnovels.com/book/"])
        self.assertTrue(seen[2][1])
        self.assertEqual(maximum, 1)
        self.assertEqual(server.sync_state["queued_jobs"], 0)
        self.assertFalse(server.sync_lock.locked())

    def test_failed_job_does_not_block_next_job(self):
        entered, release = threading.Event(), threading.Event()
        seen = []

        def worker(*args, **kwargs):
            seen.append(args[3])
            if len(seen) == 1:
                entered.set()
                release.wait(5)
                server.sync_state["error"] = "Request failed"

        with patch.object(server, "run_sync_worker", side_effect=worker):
            server.start_sync_thread(1, 1, 1, "manual")
            self.assertTrue(entered.wait(2))
            server.start_sync_thread(1, 1, 1, "repair")
            release.set()
            self.wait_until_finished(2)
        self.assertEqual(seen, ["manual", "repair"])

    def test_busy_automatic_sync_is_skipped_without_queuing(self):
        server.sync_lock.acquire()
        try:
            with patch.object(server, "last_auto_sync_started", None):
                self.assertEqual(server.start_auto_catchup(), (False, "sync_running", 0))
            self.assertFalse(server.sync_jobs)
        finally:
            server.sync_lock.release()

    def test_initial_thread_launch_failure_releases_lock(self):
        with patch.object(server.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no thread")
            with self.assertRaises(RuntimeError):
                server.start_sync_thread(1, 1, 1, "manual")
        self.assertFalse(server.sync_lock.locked())
        self.assertFalse(server.sync_state["running"])

    def test_queued_thread_launch_failure_preserves_waiting_job(self):
        server.sync_lock.acquire()
        server.start_sync_thread(1, 1, 1, "repair", ["https://jnovels.com/book/"])
        server.sync_lock.release()
        with server.sync_queue_lock, patch.object(server.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no thread")
            server._start_next_sync_job()
        self.assertEqual(len(server.sync_jobs), 1)
        self.assertEqual(server.sync_state["queued_jobs"], 1)
        self.assertFalse(server.sync_lock.locked())
        self.assertIn("Could not start queued job", server.sync_state["error"])

    def test_concurrent_submissions_are_not_lost(self):
        server.sync_lock.acquire()
        threads = [threading.Thread(target=server.start_sync_thread, args=(i, 1, 1, "manual")) for i in range(12)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(server.sync_jobs), 12)
            self.assertEqual({job["start_page"] for job in server.sync_jobs}, set(range(12)))
        finally:
            server.sync_lock.release()


class SyncQueueRouteTests(unittest.TestCase):
    def test_all_user_job_routes_accept_queued_requests(self):
        for path, payload in (
            ("/api/sync", {"mode": "series_refresh"}),
            ("/api/refresh-series", {"series_url": "https://jnovels.com/example/"}),
            ("/api/repair-entry", {"url": "https://jnovels.com/example/"}),
        ):
            with self.subTest(path=path):
                handler, backend = Mock(), Mock()
                handler.read_json.return_value = payload
                backend.start_sync_thread.return_value = server.SyncSubmission(True, 3)
                handle_post_api(handler, backend, urllib.parse.urlparse(path))
                handler.send_json.assert_called_once_with({"ok": True, "queued": True, "queue_position": 3}, 202)
