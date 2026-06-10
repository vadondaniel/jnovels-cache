import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import server


SAMPLE_HTML = """
<article id="post-1" class="post">
  <header><h1 class="post-title entry-title">
    <a href="https://jnovels.com/example-volume-1-epub/">Example &amp; Story Volume 1 Epub</a>
  </h1></header>
  <div class="featured-media">
    <a href="#"><img src="https://img.example/cover.jpg?x=1&amp;y=2"></a>
  </div>
  <time><a class="post-date" href="#"><i></i>June 9, 2026</a></time>
</article>
"""

MAIN_POST_HTML = """
<article>
  <div class="post-content clear">
    <p>You can now Download Example</p>
    <div><a href="https://jnovels.com/faq/"><img src="download.jpg"></a></div>
    <ol><li>VOLUME 01 Epub <a href="https://files.example/main-one">DOWNLOAD</a></li></ol>
  </div>
</article>
"""

VOLUME_POST_HTML = """
<article>
  <div class="post-content clear">
    <p><a href="https://jnovels.com/example-light-novel-epub/">Refer to original post</a></p>
    <div><a href="https://ads.example/banner"><img src="ad.gif"></a></div>
    <p><a href="https://files.example/volume-16">VOLUME 16</a></p>
  </div>
</article>
"""

GROUPED_SERIES_HTML = """
<article>
  <div class="post-content clear">
    <div><strong>OFFICIAL VERSION</strong></div>
    <ol>
      <li>Example volume 1 epub <a href="https://files.example/official-1">DOWNLOAD</a></li>
    </ol>
    <div><strong>FAN TRANSLATED VERSION</strong></div>
    <ol>
      <li>Example volume 1 epub <a href="https://files.example/fan-1">DOWNLOAD</a></li>
    </ol>
  </div>
</article>
"""

NESTED_SERIES_HTML = """
<article>
  <div class="post-content clear">
    <ol>
      <li style="list-style-type: none;">
        <ol>
          <li>Volume 01 <a href="https://files.example/one">Download</a></li>
          <li>Volume 05 <a href="https://files.example/five">Download</a></li>
        </ol>
      </li>
    </ol>
  </div>
</article>
"""

SPECIAL_VOLUME_LABELS_HTML = """
<article>
  <div class="post-content clear">
    <ol>
      <li>VOLUME 14 <a href="https://files.example/14">DOWNLOAD</a></li>
      <li>VOLUME 14+ <a href="https://files.example/14-plus">DOWNLOAD</a></li>
      <li>VOLUME 14++ <a href="https://files.example/14-plus-plus">DOWNLOAD</a></li>
    </ol>
  </div>
</article>
"""

NAMED_RELEASES_HTML = """
<article>
  <div class="post-content clear">
    <ol>
      <li>D-Genesis Side Stories <a href="https://files.example/side-stories">DOWNLOAD</a></li>
      <li>Disc EX Epub <a href="https://files.example/disc-ex">DOWNLOAD</a></li>
      <li>Outbreak Company Gaiden <a href="https://files.example/gaiden">DOWNLOAD</a></li>
      <li>After Story <a href="https://files.example/after-story">DOWNLOAD</a></li>
    </ol>
  </div>
</article>
"""

STANDALONE_VOLUME_LINKS_HTML = """
<article>
  <div class="post-content clear">
    <p><a href="https://files.example/one">VOLUME 01 Epub</a></p>
    <p><a href="https://files.example/year-two-ten">YEAR 2 VOLUME 10</a></p>
    <p><a href="https://files.example/part-five-point-five">PART 5 VOL. 4.5</a></p>
  </div>
</article>
"""


class ParserTests(unittest.TestCase):
    def test_parses_long_volume_title_into_series_and_volume_label(self):
        title = (
            "New Game Plus After Defeating the Last Boss: All These Beautiful, "
            "Problematic Girls in the First Town Have Gotten Really Attached "
            "to Me… Volume 2 Epub"
        )
        series_title, volume_label = server.parse_book_identity(
            title,
            "https://jnovels.com/example-volume-2-epub/",
        )

        self.assertEqual(
            series_title,
            "New Game Plus After Defeating the Last Boss: All These Beautiful, "
            "Problematic Girls in the First Town Have Gotten Really Attached to Me",
        )
        self.assertEqual(volume_label, "Vol. 2")

    def test_extracts_search_result_metadata(self):
        parser = server.SearchResultsParser()
        parser.feed(SAMPLE_HTML)

        self.assertEqual(len(parser.books), 1)
        book = parser.books[0]
        self.assertEqual(book.title, "Example & Story Volume 1 Epub")
        self.assertEqual(book.url, "https://jnovels.com/example-volume-1-epub/")
        self.assertEqual(book.image_url, "https://img.example/cover.jpg?x=1&y=2")
        self.assertEqual(book.published_at, "2026-06-09")

    def test_ignores_non_article_content(self):
        parser = server.SearchResultsParser()
        parser.feed('<h1 class="post-title"><a href="/wrong">Wrong</a></h1>')
        self.assertEqual(parser.books, [])

    def test_extracts_main_series_download(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-light-novel-epub/")
        parser.feed(MAIN_POST_HTML)
        details = parser.details()

        self.assertEqual(details.entry_type, "series")
        self.assertIsNone(details.original_post_url)
        self.assertEqual(details.download_url, "https://files.example/main-one")
        self.assertEqual(
            details.volumes,
            [server.SeriesVolume("Volume 1", "https://files.example/main-one", 0)],
        )

    def test_extracts_volume_series_and_download_links(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-volume-16-epub/")
        parser.feed(VOLUME_POST_HTML)
        details = parser.details()

        self.assertEqual(details.entry_type, "volume")
        self.assertEqual(
            details.original_post_url,
            "https://jnovels.com/example-light-novel-epub/",
        )
        self.assertEqual(details.download_url, "https://files.example/volume-16")

    def test_accepts_original_post_link_with_article(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-volume-5-epub/")
        parser.feed(
            """
            <div class="post-content">
              <a href="/example-light-novel-epub/">Refer to the original post</a>
              <a href="https://files.example/five">VOLUME 05</a>
            </div>
            """
        )

        details = parser.details()
        self.assertEqual(details.entry_type, "volume")
        self.assertEqual(
            details.original_post_url,
            "https://jnovels.com/example-light-novel-epub/",
        )

    def test_preserves_official_and_fan_translation_groups(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(GROUPED_SERIES_HTML)
        details = parser.details()

        self.assertEqual(
            [(volume.label, volume.release_group) for volume in details.volumes],
            [
                ("Volume 1", "Official translation"),
                ("Volume 1", "Fan translation"),
            ],
        )

    def test_extracts_each_volume_from_nested_lists(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(NESTED_SERIES_HTML)

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in parser.details().volumes],
            [
                ("Volume 1", "https://files.example/one"),
                ("Volume 5", "https://files.example/five"),
            ],
        )

    def test_preserves_meaningful_volume_suffixes(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(SPECIAL_VOLUME_LABELS_HTML)

        self.assertEqual(
            [volume.label for volume in parser.details().volumes],
            ["Volume 14", "Volume 14+", "Volume 14++"],
        )

    def test_extracts_standalone_and_prefixed_volume_links(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(STANDALONE_VOLUME_LINKS_HTML)

        details = parser.details()
        self.assertEqual(
            [volume.label for volume in details.volumes],
            ["Volume 1", "Year 2 Volume 10", "Part 5 Volume 4.5"],
        )
        self.assertEqual(details.download_url, "https://files.example/one")

    def test_recognizes_decimal_and_prefixed_volume_labels(self):
        self.assertTrue(server.is_volume_label("VOLUME 04.5"))
        self.assertTrue(server.is_volume_label("YEAR 2 VOLUME 10"))
        self.assertTrue(server.is_volume_label("Vol. 12.5"))

    def test_extracts_named_catalog_releases(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(NAMED_RELEASES_HTML)

        self.assertEqual(
            [volume.label for volume in parser.details().volumes],
            ["Side Stories", "Disc Ex", "Gaiden", "After Story"],
        )

    def test_extracts_standalone_named_catalog_release(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(
            """
            <div class="post-content">
              <p><a href="https://files.example/side-stories">Side Stories</a></p>
            </div>
            """
        )

        details = parser.details()
        self.assertEqual(details.download_url, "https://files.example/side-stories")
        self.assertEqual(
            details.volumes,
            [
                server.SeriesVolume(
                    "Side Stories",
                    "https://files.example/side-stories",
                    0,
                )
            ],
        )

    def test_normalizes_zero_padded_volume_numbers(self):
        self.assertEqual(server.normalize_volume_number("01"), "1")
        self.assertEqual(server.normalize_volume_number("008"), "8")
        self.assertEqual(server.normalize_volume_number("08.5"), "8.5")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "cache.db"
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.db_patch.start()
        server.initialize_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_upsert_updates_without_duplicates(self):
        original = server.Book("Old title", "https://example/book", None, "2026-01-01")
        updated = server.Book("New title", "https://example/book", "https://img/1.jpg", "2026-02-01")

        server.upsert_books([original])
        server.upsert_books([updated])
        books = server.list_books("", "published_desc")

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "New title")
        self.assertEqual(books[0]["image_url"], "https://img/1.jpg")

    def test_title_search_escapes_sql_wildcards(self):
        server.upsert_books(
            [
                server.Book("100% Example", "https://example/a", None, None),
                server.Book("1000 Example", "https://example/b", None, None),
            ]
        )
        books = server.list_books("100%", "title_asc")
        count = server.count_books("100%")
        self.assertEqual([book["title"] for book in books], ["100% Example"])
        self.assertEqual(count, 1)

    def test_counts_and_pages_books_in_database(self):
        server.upsert_books(
            [
                server.Book(
                    f"Book {number:02d}",
                    f"https://example/book-{number}",
                    None,
                    None,
                )
                for number in range(35)
            ]
        )

        books = server.list_books("", "title_asc", limit=10, offset=20)

        self.assertEqual(server.count_books(""), 35)
        self.assertEqual(len(books), 10)
        self.assertEqual(books[0]["title"], "Book 20")
        self.assertEqual(books[-1]["title"], "Book 29")

    def test_filters_series_volumes_and_pending_entries(self):
        series = server.Book("Series", "https://example/series", None, None)
        volume = server.Book("Volume 2", "https://example/volume-2", None, None)
        pending = server.Book("Pending", "https://example/pending", None, None)
        server.upsert_books([series, volume, pending])
        server.update_book_details(
            series.url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
            ),
        )
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series.url, "https://files.example/v2"),
        )

        self.assertEqual(server.count_books("", "series"), 1)
        self.assertEqual(server.count_books("", "volumes"), 1)
        self.assertEqual(server.count_books("", "pending"), 1)
        self.assertEqual(
            [book["url"] for book in server.list_books("", "title_asc", entry_filter="pending")],
            [pending.url],
        )

    def test_updates_second_pass_details(self):
        book = server.Book("Volume 16", "https://example/book", None, "2026-01-01")
        server.upsert_books([book])
        server.update_book_details(
            book.url,
            server.PostDetails(
                "volume",
                "https://example/series",
                "https://files.example/download",
            ),
        )

        saved = server.list_books("", "published_desc")[0]
        self.assertEqual(saved["entry_type"], "volume")
        self.assertEqual(saved["original_post_url"], "https://example/series")
        self.assertEqual(saved["download_url"], "https://files.example/download")
        self.assertIsNotNone(saved["detail_cached_at"])

    def test_replaces_and_exposes_series_volume_list(self):
        book = server.Book("Example Series", "https://example/series", None, None)
        server.upsert_books([book])
        server.update_book_details(
            book.url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [
                    server.SeriesVolume("Volume 01", "https://files.example/v1", 0),
                    server.SeriesVolume("Volume 02", "https://files.example/v2", 1),
                ],
            ),
        )

        saved = server.list_books("", "published_desc")[0]
        self.assertEqual(saved["volume_count"], 2)
        self.assertEqual(saved["series_url"], "https://example/series")
        self.assertEqual(
            [volume["label"] for volume in server.list_series_volumes(book.url)],
            ["Volume 01", "Volume 02"],
        )

    def test_series_catalog_includes_dates_for_cached_volume_posts(self):
        series_url = "https://example/series"
        download_url = "https://files.example/v2"
        volume = server.Book(
            "Example Volume 2 Epub",
            "https://example/volume-2",
            None,
            "2026-06-09",
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series_url, download_url),
        )
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 02", download_url, 0)],
        )

        saved = server.list_series_volumes(series_url)[0]
        self.assertEqual(saved["published_at"], "2026-06-09")
        self.assertEqual(saved["post_url"], volume.url)

    def test_series_refresh_reconciles_unresolved_child_download(self):
        series_url = "https://example/series"
        volume = server.Book(
            "Example Part 5 Volume 2 Epub",
            "https://example/volume-2",
            None,
            "2026-06-09",
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series_url, None),
        )
        server.record_detail_failure(volume.url, ValueError("missing download"))

        server.upsert_books([server.Book("Example Series", series_url, None, None)])
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [
                    server.SeriesVolume(
                        "Part 5 Volume 2",
                        "https://files.example/part-5-v2",
                        0,
                    )
                ],
            ),
        )

        saved = {book["url"]: book for book in server.list_books("", "title_asc")}
        self.assertEqual(
            saved[volume.url]["download_url"],
            "https://files.example/part-5-v2",
        )
        self.assertEqual(server.list_repair_failures(), [])

    def test_finds_only_entries_with_incomplete_details(self):
        pending = server.Book("Pending", "https://example/pending", None, None)
        complete = server.Book("Complete", "https://example/complete", None, None)
        server.upsert_books([pending, complete])
        server.update_book_details(
            complete.url,
            server.PostDetails("volume", "https://example/series", "https://files/complete"),
        )

        self.assertEqual(server.get_incomplete_detail_urls(10), [pending.url])

    def test_series_with_download_but_no_catalog_is_complete(self):
        book = server.Book("Standalone Novel", "https://example/standalone", None, None)
        server.upsert_books([book])
        server.update_book_details(
            book.url,
            server.PostDetails("series", None, "https://files.example/standalone"),
        )

        self.assertEqual(server.get_incomplete_detail_urls(10), [])
        self.assertEqual(server.get_cache_health()["pending_details"], 0)

    def test_pauses_automatic_repair_after_repeated_failures(self):
        pending = server.Book("Pending", "https://example/pending", None, None)
        server.upsert_books([pending])

        for _ in range(server.AUTO_REPAIR_MAX_ATTEMPTS):
            server.record_detail_failure(pending.url, ValueError("broken post"))

        self.assertEqual(
            server.get_incomplete_detail_urls(
                10,
                max_attempts=server.AUTO_REPAIR_MAX_ATTEMPTS,
            ),
            [],
        )
        self.assertEqual(server.get_incomplete_detail_urls(10), [pending.url])
        self.assertEqual(
            server.get_cache_health(),
            {
                "pending_details": 1,
                "failed_retries": 1,
                "paused_retries": 1,
                "stale_series": 0,
            },
        )
        failures = server.list_repair_failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["url"], pending.url)
        self.assertEqual(failures[0]["detail_attempts"], 3)
        self.assertEqual(failures[0]["detail_last_error"], "broken post")

        server.update_book_details(
            pending.url,
            server.PostDetails("volume", "https://example/series", "https://files/pending"),
        )
        with server.connect_db() as connection:
            row = connection.execute(
                """
                SELECT detail_attempts, detail_last_error
                FROM books
                WHERE url = ?
                """,
                (pending.url,),
            ).fetchone()
        self.assertEqual(row["detail_attempts"], 0)
        self.assertIsNone(row["detail_last_error"])

    def test_finds_series_stale_by_age_or_newer_cached_volume(self):
        old_series = "https://example/old-series"
        changed_series = "https://example/changed-series"
        for url in (old_series, changed_series):
            server.upsert_books([server.Book(url.rsplit("/", 1)[-1], url, None, None)])
            server.update_book_details(
                url,
                server.PostDetails(
                    "series",
                    None,
                    f"https://files.example/{url.rsplit('/', 1)[-1]}",
                    [
                        server.SeriesVolume(
                            "Volume 1",
                            f"https://files.example/{url.rsplit('/', 1)[-1]}",
                            0,
                        )
                    ],
                ),
            )

        volume = server.Book(
            "Changed Volume 2",
            "https://example/changed-volume-2",
            None,
            None,
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails(
                "volume",
                changed_series,
                "https://files.example/changed-v2",
            ),
        )
        with server.connect_db() as connection:
            connection.execute(
                "UPDATE series_status SET checked_at = ? WHERE series_url = ?",
                ("2020-01-01T00:00:00+00:00", old_series),
            )
            connection.execute(
                "UPDATE series_status SET checked_at = ? WHERE series_url = ?",
                ("2026-06-09T00:00:00+00:00", changed_series),
            )
            connection.execute(
                "UPDATE books SET cached_at = ? WHERE url = ?",
                ("2026-06-10T00:00:00+00:00", volume.url),
            )

        self.assertEqual(
            set(server.get_stale_series_urls(10)),
            {old_series, changed_series},
        )
        self.assertEqual(server.count_stale_series(), 2)

    def test_fresh_catalog_with_duplicate_labels_is_not_permanently_stale(self):
        series_url = "https://jnovels.com/duplicate-labels/"
        server.upsert_books([server.Book("Duplicate Labels", series_url, None, None)])
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/official",
                [
                    server.SeriesVolume(
                        "Volume 1",
                        "https://files.example/official",
                        0,
                    ),
                    server.SeriesVolume(
                        "Volume 1",
                        "https://files.example/fan",
                        1,
                    ),
                ],
            ),
        )

        self.assertEqual(server.get_stale_series_urls(10), [])
        self.assertEqual(server.count_stale_series(), 0)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "cache.db"
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.db_patch.start()
        server.initialize_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_new_volume_refreshes_original_series_catalog(self):
        volume_url = "https://jnovels.com/example-volume-3-epub/"
        series_url = "https://jnovels.com/example-light-novel-epub/"
        search_book = server.Book("Example Volume 3 Epub", volume_url, None, None)
        volume_details = server.PostDetails(
            "volume",
            series_url,
            "https://files.example/v3",
        )
        series_details = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [
                server.SeriesVolume("Volume 01", "https://files.example/v1", 0),
                server.SeriesVolume("Volume 02", "https://files.example/v2", 1),
                server.SeriesVolume("Volume 03", "https://files.example/v3", 2),
            ],
        )

        def fake_details(url):
            return volume_details if url == volume_url else series_details

        with (
            patch.object(server, "fetch_page", return_value=[search_book]),
            patch.object(server, "fetch_post_details", side_effect=fake_details) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5)

        self.assertEqual(
            fetch_details.call_args_list,
            [call(volume_url), call(series_url)],
        )
        self.assertEqual(
            [volume["label"] for volume in server.list_series_volumes(series_url)],
            ["Volume 01", "Volume 02", "Volume 03"],
        )
        self.assertEqual(server.sync_state["series_completed"], 1)
        self.assertEqual(server.sync_state["series_errors"], 0)

    def test_interrupted_sync_recovers_fetched_page_details(self):
        book_url = "https://jnovels.com/interrupted-volume-1-epub/"
        search_book = server.Book(
            "Interrupted Volume 1 Epub",
            book_url,
            None,
            "2026-06-09",
        )
        recovered_details = server.PostDetails(
            "series",
            None,
            "https://files.example/interrupted-v1",
            [
                server.SeriesVolume(
                    "Volume 1",
                    "https://files.example/interrupted-v1",
                    0,
                )
            ],
        )

        with (
            patch.object(server, "fetch_page", return_value=[search_book]),
            patch.object(
                server,
                "fetch_post_details",
                side_effect=KeyboardInterrupt,
            ),
            patch.object(server.time, "sleep"),
            self.assertRaises(KeyboardInterrupt),
        ):
            server.run_sync(1, 1, 0.5, "catchup")

        saved = {book["url"]: book for book in server.list_books("", "title_asc")}
        self.assertIn(book_url, saved)
        checkpoint = server.load_sync_checkpoint()
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["pending_detail_urls"], [book_url])

        with (
            patch.object(
                server,
                "fetch_post_details",
                return_value=recovered_details,
            ),
            patch.object(server.time, "sleep"),
        ):
            self.assertTrue(server.recover_interrupted_sync(checkpoint))

        saved = {book["url"]: book for book in server.list_books("", "title_asc")}
        self.assertEqual(
            saved[book_url]["download_url"],
            "https://files.example/interrupted-v1",
        )
        self.assertIsNone(server.load_sync_checkpoint())
        self.assertIn("Recovered saved work", server.sync_state["notice"])
        metadata = server.get_metadata()
        self.assertIn("last_sync_interrupted_at", metadata)
        self.assertIn("last_sync_recovered_at", metadata)
        self.assertNotIn("last_sync_at", metadata)

    def test_repair_mode_processes_pending_details_without_searching(self):
        volume_url = "https://jnovels.com/example-volume-2-epub/"
        series_url = "https://jnovels.com/example-light-novel-epub/"
        server.upsert_books(
            [
                server.Book("Example Volume 2 Epub", volume_url, None, "2026-06-09"),
                server.Book("Example Light Novel Epub", series_url, None, "2026-06-01"),
            ]
        )
        volume_details = server.PostDetails(
            "volume",
            series_url,
            "https://files.example/v2",
        )
        series_details = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [
                server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
            ],
        )

        def fake_details(url):
            return volume_details if url == volume_url else series_details

        with (
            patch.object(server, "fetch_page") as fetch_page,
            patch.object(server, "fetch_post_details", side_effect=fake_details) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 10, 0.5, "repair")

        fetch_page.assert_not_called()
        self.assertEqual(
            fetch_details.call_args_list,
            [call(volume_url), call(series_url)],
        )
        saved = {book["url"]: book for book in server.list_books("", "title_asc")}
        self.assertEqual(saved[volume_url]["download_url"], "https://files.example/v2")
        self.assertEqual(saved[series_url]["volume_count"], 2)

    def test_unusable_parse_is_recorded_as_a_repair_failure(self):
        url = "https://jnovels.com/no-download/"
        server.upsert_books([server.Book("No Download", url, None, None)])

        with (
            patch.object(
                server,
                "fetch_post_details",
                return_value=server.PostDetails("series", None, None),
            ),
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5, "repair")

        failure = server.list_repair_failures()[0]
        self.assertEqual(failure["url"], url)
        self.assertIn("No download link found", failure["detail_last_error"])

    def test_missing_child_download_is_recovered_from_parent_catalog(self):
        child_url = "https://jnovels.com/example-part-5-volume-2-epub/"
        series_url = "https://jnovels.com/example-series/"
        server.upsert_books(
            [server.Book("Example Part 5 Volume 2 Epub", child_url, None, None)]
        )
        child_details = server.PostDetails("volume", series_url, None)
        series_details = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [
                server.SeriesVolume(
                    "Part 5 Volume 2",
                    "https://files.example/part-5-v2",
                    0,
                )
            ],
        )

        def fake_details(url):
            return child_details if url == child_url else series_details

        with (
            patch.object(server, "fetch_post_details", side_effect=fake_details),
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5, "repair", [child_url])

        saved = {book["url"]: book for book in server.list_books("", "title_asc")}
        self.assertEqual(saved[child_url]["original_post_url"], series_url)
        self.assertEqual(
            saved[child_url]["download_url"],
            "https://files.example/part-5-v2",
        )
        self.assertEqual(server.list_repair_failures(), [])

    def test_normal_sync_automatically_repairs_a_bounded_backlog(self):
        pending_url = "https://jnovels.com/pending/"
        new_url = "https://jnovels.com/new/"
        server.upsert_books([server.Book("Pending", pending_url, None, "2026-01-01")])
        details = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
        )

        with (
            patch.object(
                server,
                "fetch_page",
                return_value=[server.Book("New", new_url, None, "2026-06-09")],
            ),
            patch.object(server, "fetch_post_details", return_value=details) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5, "catchup")

        self.assertEqual(
            fetch_details.call_args_list,
            [call(new_url), call(pending_url)],
        )
        self.assertEqual(server.get_cache_health()["pending_details"], 0)

    def test_targeted_repair_only_processes_requested_entry(self):
        first_url = "https://jnovels.com/first/"
        second_url = "https://jnovels.com/second/"
        server.upsert_books(
            [
                server.Book("First", first_url, None, None),
                server.Book("Second", second_url, None, None),
            ]
        )
        details = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
        )

        with (
            patch.object(server, "fetch_page") as fetch_page,
            patch.object(server, "fetch_post_details", return_value=details) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5, "repair", [second_url])

        fetch_page.assert_not_called()
        fetch_details.assert_called_once_with(second_url)
        self.assertEqual(server.get_incomplete_detail_urls(10), [first_url])

    def test_series_refresh_mode_only_processes_stale_catalogs(self):
        series_url = "https://jnovels.com/stale-series/"
        server.upsert_books([server.Book("Stale Series", series_url, None, None)])
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
            ),
        )
        with server.connect_db() as connection:
            connection.execute(
                "UPDATE series_status SET checked_at = ? WHERE series_url = ?",
                ("2020-01-01T00:00:00+00:00", series_url),
            )
        refreshed = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [
                server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
            ],
        )

        with (
            patch.object(server, "fetch_page") as fetch_page,
            patch.object(server, "fetch_post_details", return_value=refreshed) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 10, 0.5, "series_refresh")

        fetch_page.assert_not_called()
        fetch_details.assert_called_once_with(series_url)
        self.assertEqual(server.count_stale_series(), 0)
        self.assertEqual(len(server.list_series_volumes(series_url)), 2)

    def test_normal_sync_automatically_refreshes_stale_series(self):
        series_url = "https://jnovels.com/stale-series/"
        new_url = "https://jnovels.com/new-entry/"
        server.upsert_books([server.Book("Stale Series", series_url, None, None)])
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
            ),
        )
        with server.connect_db() as connection:
            connection.execute(
                "UPDATE series_status SET checked_at = ? WHERE series_url = ?",
                ("2020-01-01T00:00:00+00:00", series_url),
            )

        new_details = server.PostDetails(
            "series",
            None,
            "https://files.example/new",
            [server.SeriesVolume("Volume 1", "https://files.example/new", 0)],
        )
        refreshed = server.PostDetails(
            "series",
            None,
            "https://files.example/v1",
            [
                server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
            ],
        )

        def fake_details(url):
            return refreshed if url == series_url else new_details

        with (
            patch.object(
                server,
                "fetch_page",
                return_value=[server.Book("New Entry", new_url, None, None)],
            ),
            patch.object(server, "fetch_post_details", side_effect=fake_details) as fetch_details,
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(1, 1, 0.5, "catchup")

        self.assertEqual(
            fetch_details.call_args_list,
            [call(new_url), call(series_url)],
        )
        self.assertEqual(server.count_stale_series(), 0)

    def test_catchup_stops_on_first_page_overlapping_cache(self):
        known_url = "https://jnovels.com/known/"
        pages = {
            1: [
                server.Book("New 1", "https://jnovels.com/new-1/", None, None),
                server.Book("New 2", "https://jnovels.com/new-2/", None, None),
            ],
            2: [
                server.Book("New 3", "https://jnovels.com/new-3/", None, None),
                server.Book("Known", known_url, None, None),
            ],
        }

        with (
            patch.object(server, "fetch_page", side_effect=lambda page: pages[page]) as fetch,
            patch.object(server.time, "sleep"),
        ):
            books = server.collect_sync_books(
                "catchup",
                start_page=1,
                max_pages=20,
                delay=0.5,
                known_urls={known_url},
            )

        self.assertEqual(
            [book.url for book in books],
            [
                "https://jnovels.com/new-1/",
                "https://jnovels.com/new-2/",
                "https://jnovels.com/new-3/",
            ],
        )
        self.assertEqual(fetch.call_args_list, [call(1), call(2)])

    def test_extend_jumps_near_cached_frontier(self):
        known_urls = {
            f"https://jnovels.com/book-{number}/" for number in range(1, 19)
        }

        def page(number):
            start = (number - 1) * 6 + 1
            return [
                server.Book(
                    f"Book {item}",
                    f"https://jnovels.com/book-{item}/",
                    None,
                    None,
                )
                for item in range(start, start + 6)
            ]

        with (
            patch.object(server, "fetch_page", side_effect=page) as fetch,
            patch.object(server.time, "sleep"),
        ):
            books = server.collect_sync_books(
                "extend",
                start_page=1,
                max_pages=2,
                delay=0.5,
                known_urls=set(known_urls),
            )

        self.assertEqual(
            [book.url for book in books],
            [
                *[f"https://jnovels.com/book-{item}/" for item in range(19, 25)],
                *[f"https://jnovels.com/book-{item}/" for item in range(25, 31)],
            ],
        )
        self.assertEqual(fetch.call_args_list, [call(1), call(2), call(3), call(4), call(5)])


if __name__ == "__main__":
    unittest.main()
