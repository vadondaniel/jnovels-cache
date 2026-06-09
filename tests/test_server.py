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


class ParserTests(unittest.TestCase):
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
            [server.SeriesVolume("Volume 01", "https://files.example/main-one", 0)],
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
        self.assertEqual([book["title"] for book in books], ["100% Example"])

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


if __name__ == "__main__":
    unittest.main()
