import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import server
from server_modules.storage.db_schema import initialize_schema
from server_modules.storage.series_refresh_state import get_series_refresh_urls, count_stale_series
from server_modules.sync import source_changes as source
from server_modules.web.api_routes import handle_post_api


NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")
URL = "https://jnovels.com/example/"


def api(rows, pages=1):
    return json.dumps(rows).encode(), {"X-WP-TotalPages": str(pages)}


def post(url=URL, modified="2026-11-04T12:00:00"):
    return {"link": url, "modified_gmt": modified, "id": 1}


def xml(kind, entries):
    return (f'<{kind} xmlns="{source.NS["s"]}">{entries}</{kind}>').encode(), {}


INDEX = xml("sitemapindex", "<sitemap><loc>https://jnovels.com/post-sitemap.xml</loc></sitemap>")
SITEMAP = xml("urlset", f"<url><loc>{URL}</loc><lastmod>2026-11-04T12:00:00Z</lastmod></url>")


class SourceChangeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        initialize_schema(self.db)
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO series_status(series_url, checked_at, metadata_checked_at) VALUES (?, ?, ?)",
                        (URL, "2026-10-01T12:00:00+00:00", "2026-10-01T12:00:00+00:00"))
        self.db.commit()

    @contextmanager
    def connect(self):
        with self.db:
            yield self.db

    def row(self):
        return dict(self.db.execute("SELECT * FROM series_status WHERE series_url = ?", (URL,)).fetchone())

    def markers(self, **values):
        self.db.executemany("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", values.items())
        self.db.commit()

    def check(self, fetch, force=False):
        state = {}
        sleep = Mock()
        source.check_source_changes(self.connect, state, 0.5, sleep, fetch=fetch, force=force, now=NOW)
        self.assertTrue(all(c.args == (0.5,) for c in sleep.call_args_list))
        return state

    def initialized(self):
        self.markers(**{source.API_CURSOR: "2026-11-03T12:00:00+00:00", source.SITEMAP_CHECKED: STAMP})

    def test_bootstrap_uses_sitemaps_and_preserves_full_fetch_and_metadata_dates(self):
        before = self.row()
        fetch = Mock(side_effect=[INDEX, SITEMAP])
        state = self.check(fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("wp-json", str(fetch.call_args_list))
        self.assertEqual(self.row()["checked_at"], before["checked_at"])
        self.assertEqual(self.row()["metadata_checked_at"], before["metadata_checked_at"])
        self.assertEqual(self.row()["source_change_pending"], 1)
        self.assertEqual(state["source_series_checked"], 1)
        self.assertEqual(source.get_metadata(self.connect)[source.API_CURSOR], STAMP)

    def test_bootstrap_failure_has_no_cursor_or_partial_updates(self):
        broken = xml("urlset", f"<url><loc>{URL}</loc><lastmod>bad</lastmod></url>")
        state = self.check(Mock(side_effect=[INDEX, broken]))
        self.assertTrue(state["source_warnings"])
        self.assertNotIn(source.API_CURSOR, source.get_metadata(self.connect))
        self.assertIsNone(self.row()["source_modified_at"])

    def test_api_pagination_equal_timestamps_overlap_dedup_and_stop(self):
        fetch = Mock(side_effect=[
            api([post()] * 100, 3),
            api([post(URL.rstrip('/')), post("https://jnovels.com/unrelated/", "2026-11-02T12:00:00"),
                 post("https://jnovels.com/old/", "2026-11-02T11:59:59")], 3),
        ])
        changes = source.api_changes("2026-11-03T12:00:00Z", fetch, Mock())
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("page=2", fetch.call_args.args[0])
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[URL.rstrip('/')], "2026-11-04T12:00:00+00:00")

    def test_api_failure_retains_cursor_and_queue(self):
        self.initialized()
        before = source.get_metadata(self.connect)
        fetch = Mock(side_effect=[api([post()] * 100, 2), urllib.error.URLError("offline")])
        state = self.check(fetch)
        self.assertTrue(state["source_warnings"])
        self.assertEqual(source.get_metadata(self.connect), before)
        self.assertEqual(self.row()["source_change_pending"], 0)

    def test_interruption_does_not_advance_cursor(self):
        self.initialized()
        before = source.get_metadata(self.connect)
        with self.assertRaises(KeyboardInterrupt):
            self.check(Mock(side_effect=[api([post()] * 100, 2), KeyboardInterrupt()]))
        self.assertEqual(source.get_metadata(self.connect), before)

    def test_malformed_api_does_not_advance_cursor(self):
        for response in [api([post(modified="bad")]), api([{}]), api([None]), (b'{}', {}), (b'[]', {}), api([post()], 0)]:
            with self.subTest(response=response):
                self.initialized()
                before = source.get_metadata(self.connect)
                state = self.check(Mock(return_value=response))
                self.assertTrue(state["source_warnings"])
                self.assertEqual(source.get_metadata(self.connect), before)

    def test_api_daily_throttle_and_explicit_refresh(self):
        self.initialized()
        self.markers(**{source.API_CHECKED: STAMP})
        fetch = Mock(return_value=api([post()]))
        self.check(fetch)
        fetch.assert_not_called()
        self.check(fetch, force=True)
        fetch.assert_called_once()
        self.assertEqual(source.get_metadata(self.connect)[source.API_CURSOR], STAMP)

    def test_weekly_sitemap_runs_independently_of_api_failure(self):
        self.initialized()
        self.markers(**{source.SITEMAP_CHECKED: (NOW - timedelta(days=7)).isoformat()})
        state = self.check(Mock(side_effect=[urllib.error.URLError("offline"), INDEX, SITEMAP]))
        self.assertEqual(len(state["source_warnings"]), 1)
        metadata = source.get_metadata(self.connect)
        self.assertEqual(metadata[source.SITEMAP_CHECKED], STAMP)
        self.assertEqual(metadata[source.API_CURSOR], "2026-11-03T12:00:00+00:00")
        self.assertEqual(self.row()["source_change_pending"], 1)

    def test_partial_sitemap_failure_rolls_back_entire_scan(self):
        index = xml("sitemapindex", "".join(f"<sitemap><loc>https://jnovels.com/post-sitemap{i}.xml</loc></sitemap>" for i in (1, 2)))
        self.check(Mock(side_effect=[index, SITEMAP, urllib.error.URLError("offline")]))
        self.assertIsNone(self.row()["source_modified_at"])
        self.assertEqual(source.get_metadata(self.connect), {})

    def test_missing_sitemap_dates_leave_fallback_and_queue_intact(self):
        self.db.execute("UPDATE series_status SET source_change_pending = 1")
        missing = xml("urlset", f"<url><loc>{URL}</loc></url>")
        self.check(Mock(side_effect=[INDEX, missing]))
        self.assertEqual(self.row()["source_change_pending"], 1)
        self.assertIsNone(self.row()["source_modified_at"])

    def test_unrelated_and_unavailable_entries_do_not_queue(self):
        self.db.execute("UPDATE series_status SET unavailable_at = ?", (STAMP,))
        source.store_changes(self.connect, {URL.rstrip('/'): STAMP, "https://jnovels.com/other": STAMP}, {})
        self.assertEqual(self.row()["source_change_pending"], 0)

    def test_utc_normalization(self):
        self.assertEqual(source.parse_timestamp("2026-11-05T14:00:00+02:00"), NOW)
        self.assertEqual(source.parse_timestamp("2026-11-05T12:00:00"), NOW)

    def test_fetcher_reads_response_and_uses_existing_user_agent(self):
        response = Mock()
        response.read.return_value = b"[]"
        response.headers = {"X-WP-TotalPages": "0"}
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(source.urllib.request, "urlopen", return_value=context) as open_url:
            self.assertEqual(source.fetch_source("https://jnovels.com/wp-json/wp/v2/posts?page=1"), (b"[]", response.headers))
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), source.USER_AGENT)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 30)

    def test_api_rejects_out_of_order_results(self):
        with self.assertRaises(ValueError):
            source.api_changes(STAMP, Mock(return_value=api([post(modified="2026-11-04T12:00:00"), post(modified="2026-11-05T12:00:00")])), Mock())

    def test_absent_from_sitemap_still_receives_age_fallback(self):
        self.db.execute("UPDATE series_status SET checked_at = '2020-01-01T00:00:00+00:00'")
        other = xml("urlset", "<url><loc>https://jnovels.com/unrelated/</loc></url>")
        self.check(Mock(side_effect=[INDEX, other]))
        self.assertEqual(get_series_refresh_urls(self.connect, 180, 10), [URL])

    def test_180_day_boundary_and_forced_oldest_first_limit(self):
        cutoff = (NOW - timedelta(days=180)).isoformat(timespec="seconds")
        self.db.execute("UPDATE series_status SET checked_at = ?", (cutoff,))
        self.db.execute("INSERT INTO series_status(series_url, checked_at, metadata_checked_at) VALUES ('https://jnovels.com/new/', ?, ?)", (STAMP, STAMP))
        with patch("server_modules.storage.series_refresh_state.series_stale_cutoff", return_value=cutoff):
            self.assertEqual(count_stale_series(self.connect, 180), 1)
            self.assertEqual(get_series_refresh_urls(self.connect, 180, 1, force=True), [URL])
            self.assertEqual(get_series_refresh_urls(self.connect, 180, 10), [URL])

    def test_schema_migration_is_idempotent(self):
        initialize_schema(self.db)
        self.db.commit()
        initialize_schema(self.db)
        self.assertEqual(self.row()["source_change_pending"], 0)

    def test_existing_schema_is_migrated_without_losing_dates(self):
        with sqlite3.connect(":memory:") as db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE series_status(series_url TEXT PRIMARY KEY, checked_at TEXT NOT NULL, metadata_checked_at TEXT, unavailable_at TEXT)")
            db.execute("INSERT INTO series_status VALUES (?, ?, ?, NULL)", (URL, STAMP, STAMP))
            db.commit()
            initialize_schema(db)
            row = dict(db.execute("SELECT * FROM series_status").fetchone())
            self.assertEqual(row["checked_at"], STAMP)
            self.assertEqual(row["metadata_checked_at"], STAMP)
            self.assertEqual(row["source_change_pending"], 0)


class SourceSyncIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db = patch.object(server, "DB_PATH", Path(directory.name) / "test.sqlite")
        db.start()
        self.addCleanup(db.stop)
        server.initialize_db()
        self.details = server.PostDetails("series", None, "https://files.example/v1", [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)])
        for url in (URL, "https://jnovels.com/second/"):
            server.upsert_books([server.Book("Example " + url, url, None, None)])
            server.update_book_details(url, self.details)
        with server.connect_db() as c:
            c.execute("UPDATE series_status SET source_change_pending = 1, source_modified_at = '2026-01-01T00:00:00+00:00'")

    def test_limit_success_clears_only_fetched_series_and_failure_remains_queued(self):
        with patch.object(server, "check_source_changes"), patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", return_value=self.details) as fetch:
            server.run_sync(1, 1, 0.5, "series_refresh")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(server.count_stale_series(), 1)
        with patch.object(server, "check_source_changes"), patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", side_effect=ValueError("bad page")):
            server.run_sync(1, 1, 0.5, "series_refresh")
        self.assertEqual(server.count_stale_series(), 1)
        self.assertEqual(server.sync_state["series_errors"], 1)

    def test_force_refresh_fetches_fresh_series_oldest_first_and_honors_limit(self):
        with server.connect_db() as c:
            c.execute("UPDATE series_status SET source_change_pending = 0")
            c.execute("UPDATE series_status SET checked_at = '2026-10-01T00:00:00+00:00' WHERE series_url = ?", (URL,))
        with patch.object(server, "check_source_changes") as check, patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", return_value=self.details) as fetch:
            server.run_sync(1, 1, 0.5, "series_refresh", force_full_refresh=True)
        check.assert_called_once_with(0.5, True)
        fetch.assert_called_once_with(URL)

    def test_source_failure_warns_and_refresh_continues(self):
        def fail(delay, force):
            server.sync_state["source_warnings"].append("API check failed")
        with patch.object(server, "check_source_changes", side_effect=fail), patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", return_value=self.details):
            server.run_sync(1, 1, 0.5, "series_refresh")
        self.assertIsNone(server.sync_state["error"])
        self.assertEqual(server.sync_state["series_completed"], 1)
        self.assertEqual(server.sync_state["source_warnings"], ["API check failed"])

    def test_recovery_skips_metadata_and_keeps_saved_series_order(self):
        urls = ["https://jnovels.com/second/", URL]
        with patch.object(server, "check_source_changes") as check, patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", return_value=self.details) as fetch:
            server.run_sync(1, 2, 0.5, "recovery", [], urls, True)
        check.assert_not_called()
        self.assertEqual([call.args[0] for call in fetch.call_args_list], urls)

    def test_single_series_override_does_not_refresh_other_queued_series(self):
        with patch.object(server, "check_source_changes") as check, patch.object(server.time, "sleep"), patch.object(server, "fetch_post_details", return_value=self.details) as fetch:
            server.run_sync(1, 10, 0.5, "series_refresh", series_urls_override=[URL])
        check.assert_not_called()
        fetch.assert_called_once_with(URL)
        self.assertEqual(server.count_stale_series(), 1)

    def test_normal_sync_checks_source_without_forcing(self):
        with patch.object(server, "check_source_changes") as check, patch.object(server.time, "sleep"), patch.object(server, "fetch_page", return_value=[]), patch.object(server, "fetch_post_details", return_value=self.details):
            server.run_sync(1, 1, 0.5, "catchup")
        check.assert_called_once_with(0.5, False)


class ForceRefreshRouteTests(unittest.TestCase):
    def test_force_refresh_option_is_forwarded(self):
        handler, backend = Mock(), Mock()
        handler.read_json.return_value = {"mode": "series_refresh", "max_pages": 7, "force_full_refresh": True}
        handle_post_api(handler, backend, urllib.parse.urlparse("/api/sync"))
        backend.start_sync_thread.assert_called_once_with(1, 7, 1.0, "series_refresh", force_full_refresh=True)
        self.assertEqual(handler.send_json.call_args.args[1], 202)

    def test_force_refresh_rejects_invalid_type_or_mode(self):
        for mode, force in [("catchup", True), ("series_refresh", "true")]:
            handler, backend = Mock(), Mock()
            handler.read_json.return_value = {"mode": mode, "force_full_refresh": force}
            handle_post_api(handler, backend, urllib.parse.urlparse("/api/sync"))
            backend.start_sync_thread.assert_not_called()
            self.assertEqual(handler.send_json.call_args.args[1], 400)
