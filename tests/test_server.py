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
    <div><strong>OFFICIAL TRANSLATION</strong></div>
    <ol>
      <li>Example volume 1 epub <a href="https://files.example/official-1">DOWNLOAD</a></li>
    </ol>
    <div><strong>FAN TRANSLATION</strong></div>
    <ol>
      <li>Example volume 1 epub <a href="https://files.example/fan-1">DOWNLOAD</a></li>
    </ol>
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
