import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
