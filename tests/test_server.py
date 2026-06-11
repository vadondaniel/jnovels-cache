import tempfile
import unittest
from email.message import Message
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

LEGACY_FORMAT_SERIES_HTML = """
<article>
  <div class="post-content clear">
    <div>PDF LIGHT</div>
    <ol>
      <li>VOLUME 01 <a href="https://files.example/pdf-light-1">Download</a></li>
      <li>VOLUME 02 <a href="https://files.example/pdf-light-2">Download</a></li>
    </ol>
    <div>PDF DARK</div>
    <ol>
      <li>VOLUME 01 <a href="https://files.example/pdf-dark-1">Download</a></li>
    </ol>
    <div>EPUB</div>
    <ol>
      <li>VOLUME 01 <a href="https://files.example/epub-1">Download</a></li>
      <li>VOLUME 02 <a href="https://files.example/epub-2">Download</a></li>
    </ol>
  </div>
</article>
"""

LEGACY_WEBNOVEL_HTML = """
<article>
  <div class="post-content clear">
    <div>PDF</div>
    <ol>
      <li>Example Webnovel (completed) <a href="https://files.example/pdf">DOWNLOAD</a></li>
    </ol>
    <div>EPUB</div>
    <ol>
      <li>Example Webnovel (completed) <a href="https://files.example/epub">DOWNLOAD</a></li>
    </ol>
  </div>
</article>
"""

LEGACY_BARE_DOWNLOADS_HTML = """
<article>
  <div class="post-content clear">
    <ol>
      <li>VOLUME 01 — <a href="https://files.invalid/pdf-1">PDF</a> |
          <a href="https://enagato.com/epub-1">EPUB</a></li>
      <li>VOLUME 02 — <a href="https://files.invalid/pdf-2">PDF</a> |
          <a href="https://enagato.com/epub-2">EPUB</a></li>
    </ol>
  </div>
</article>
"""

LEGACY_NAMED_DOWNLOADS_HTML = """
<article>
  <div class="post-content clear">
    <ol>
      <li><a href="https://enagato.com/one">Kingdom Hearts: The Novel</a></li>
      <li><a href="https://enagato.com/two">Kingdom Hearts: Chain of Memories</a></li>
    </ol>
  </div>
</article>
"""

LEGACY_FORUM_DOWNLOAD_HTML = """
<article>
  <div class="post-content clear">
    <p><a href="https://mp4directs.com/forums/example.123/">EXAMPLE SERIES</a></p>
  </div>
</article>
"""

LEGACY_PARAGRAPH_CATALOG_HTML = """
<article>
  <div class="post-content clear">
    <p><strong>Download Link – EPUB File</strong></p>
    <p>Volume 1 – <a href="https://files.example/epub-1">Enrollment Chapter</a></p>
    <p>Volume 2 – <a href="https://files.example/epub-2">Competition Chapter</a></p>
    <p><strong>Download Link – PDF</strong></p>
    <p>Volume 1 – <a href="https://files.example/pdf-1">Enrollment Chapter</a></p>
  </div>
</article>
"""

LEGACY_INLINE_METADATA_HTML = """
<article>
  <div class="post-content clear">
    <p>Synopsis A book-loving student is reborn in a world where books are rare.</p>
    <h5 class="seriesother associated">Associated Names</h5>
    <div id="editassociated">
      Alternate Bookworm<br>
      本好きの下剋上
    </div>
    <ol><li>Volume 1 <a href="https://files.example/v1">Download</a></li></ol>
  </div>
</article>
"""

LEGACY_HEADING_METADATA_HTML = """
<article>
  <div class="post-content clear">
    <h4>Description</h4>
    <p>First synopsis paragraph.</p>
    <p>Second synopsis paragraph.</p>
    <h4>Alternative Names</h4>
    <p>Alternate One<br>Alternate Two</p>
    <h4>Related Series</h4>
    <p>Unrelated text that must not enter metadata.</p>
    <ol><li>Volume 1 <a href="https://files.example/v1">Download</a></li></ol>
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

METADATA_HTML = """
<article>
  <div class="post-content clear">
    <h4 class="seriesinfo"><span>Description</span></h4>
    <div id="editdescription">
      <div id="editdescription">
        <p>First synopsis paragraph.</p>
        <p>Second synopsis paragraph.</p>
        <h5 class="seriesother associated">Associated Names</h5>
        <div id="editassociated">
          Example Japanese Title<br>
          例の日本語タイトル
        </div>
      </div>
    </div>
    <p><strong>GENRE</strong>:</p>
    <p>
      <a href="https://jnovels.com/category/light-novels/action/">Action</a>,
      <a href="https://jnovels.com/category/light-novels/fantasy/">Fantasy, </a>
      <a href="https://jnovels.com/category/news/">News</a>
    </p>
    <ol><li>VOLUME 01 <a href="https://files.example/one">DOWNLOAD</a></li></ol>
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

    def test_parses_volume_before_light_novel_suffix(self):
        self.assertEqual(
            server.parse_book_identity(
                "Overlord Volume 14 Light Novel Epub",
                "https://jnovels.com/overlord-volume-14-light-novel-epub/",
            ),
            ("Overlord", "Vol. 14"),
        )

    def test_separates_volume_subtitle_from_series_identity(self):
        self.assertEqual(
            server.parse_book_identity(
                "How a Realist Hero Rebuilt the Kingdom Volume 21: "
                "Short Story Chronicles Epub",
                "https://jnovels.com/how-a-realist-hero-volume-21-epub/",
            ),
            ("How a Realist Hero Rebuilt the Kingdom", "Vol. 21"),
        )

    def test_compacts_split_volume_qualifiers_in_badges(self):
        cases = [
            ("Rebuild World Volume 6 Part 1 Epub", "Vol. 6.1"),
            ("The Misfit of Demon King Academy Volume 10 Act 2 Epub", "Vol. 10.2"),
            (
                "Min-Maxing My TRPG Build in Another World Volume 9 Canto II Epub",
                "Vol. 9.2",
            ),
            (
                "Agents of the Four Seasons Volume 6: Dance of Autumn, Part I Epub",
                "Vol. 6.1",
            ),
            ("Example Volume 4 II Epub", "Vol. 4.2"),
            ("Death’s Daughter and the Ebony Blade Volume 7 Finale Epub", "Vol. 7 Finale"),
        ]

        for title, expected_badge in cases:
            with self.subTest(title=title):
                _, badge = server.parse_book_identity(
                    title,
                    "https://jnovels.com/example-volume-1-epub/",
                )
                self.assertEqual(badge, expected_badge)

    def test_parses_format_token_before_canto_qualifier(self):
        self.assertEqual(
            server.parse_book_identity(
                "Min-Maxing My TRPG Build in Another World Volume 4 Epub Canto II",
                "https://jnovels.com/min-maxing-volume-4-canto-2-epub/",
            ),
            ("Min-Maxing My TRPG Build in Another World", "Vol. 4.2"),
        )

    def test_removes_legacy_format_tags_from_display_identity(self):
        self.assertEqual(
            server.parse_book_identity(
                "[WEBNOVEL][PDF][EPUB] Genius Doctor: Black Belly Miss",
                "https://jnovels.com/webnovelpdfepub-genius-doctor/",
            ),
            ("Genius Doctor: Black Belly Miss", None),
        )
        self.assertEqual(
            server.parse_book_identity(
                "[PDF][EPUB]The Redo of a Healing Magician Light Novel",
                "https://jnovels.com/pdfepubthe-redo-of-a-healing-magician-light-novels/",
            ),
            ("The Redo of a Healing Magician", None),
        )

    def test_removes_legacy_catalog_wrappers_from_display_identity(self):
        cases = [
            (
                "Isekai ni Tensei Shitanda kedo Ore All Volumes EPUB",
                "Isekai ni Tensei Shitanda kedo Ore",
            ),
            (
                "DOWNLOAD BACCANO ALL VOLUMES EPUB",
                "Baccano",
            ),
            (
                "DOWNLOAD IN ANOTHER WORLD WITH SMARTPHONE ALL LN VOLUMES EPUB",
                "In Another World with Smartphone",
            ),
            (
                "DOWNLOAD KINGDOM HEARTS ALL VOLUME EPUB",
                "Kingdom Hearts",
            ),
        ]

        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    server.parse_book_identity(
                        title,
                        "https://jnovels.com/example-all-volumes-epub/",
                    ),
                    (expected, None),
                )

    def test_legacy_wrapper_cleanup_preserves_volume_number(self):
        self.assertEqual(
            server.parse_book_identity(
                "DOWNLOAD Example Story Volume 3 EPUB",
                "https://jnovels.com/example-story-volume-3-epub/",
            ),
            ("Example Story", "Vol. 3"),
        )

    def test_keeps_format_visible_for_pdf_only_entries(self):
        self.assertEqual(
            server.parse_book_identity(
                "Death March to the Parallel World Rhapsody Volume 19 Pdf",
                "https://jnovels.com/death-march-volume-19-pdf/",
            ),
            ("Death March to the Parallel World Rhapsody PDF", "Vol. 19"),
        )
        self.assertEqual(
            server.parse_book_identity(
                "DOWNLOAD DEATH MARCH ALL VOLUMES PDF",
                "https://jnovels.com/download-death-march-all-volume-pdf/",
            ),
            ("Death March PDF", None),
        )

    def test_does_not_mark_mixed_pdf_epub_entries_as_pdf_only(self):
        self.assertEqual(
            server.parse_book_identity(
                "[PDF][EPUB] Example Story",
                "https://jnovels.com/pdfepub-example-story/",
            ),
            ("Example Story", None),
        )

    def test_identifies_pdf_epub_and_mixed_pages(self):
        self.assertEqual(
            server.book_page_format(
                "Example Volume 1 PDF",
                "https://jnovels.com/example-volume-1-pdf/",
            ),
            "pdf",
        )
        self.assertEqual(
            server.book_page_format(
                "Example Volume 1 EPUB",
                "https://jnovels.com/example-volume-1-epub/",
            ),
            "epub",
        )
        self.assertEqual(
            server.book_page_format(
                "[PDF][EPUB] Example",
                "https://jnovels.com/pdfepub-example/",
            ),
            "mixed",
        )

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

    def test_preserves_legacy_pdf_and_epub_format_groups(self):
        parser = server.PostDetailsParser("https://jnovels.com/legacy-formats/")
        parser.feed(LEGACY_FORMAT_SERIES_HTML)

        details = parser.details()

        self.assertEqual(
            [(volume.release_group, volume.label) for volume in details.volumes],
            [
                ("PDF (light)", "Volume 1"),
                ("PDF (light)", "Volume 2"),
                ("PDF (dark)", "Volume 1"),
                ("EPUB", "Volume 1"),
                ("EPUB", "Volume 2"),
            ],
        )
        self.assertEqual(details.download_url, "https://files.example/epub-1")

    def test_extracts_completed_webnovel_downloads_by_format(self):
        parser = server.PostDetailsParser("https://jnovels.com/webnovelpdfepub-example/")
        parser.feed(LEGACY_WEBNOVEL_HTML)

        details = parser.details()

        self.assertEqual(
            [
                (volume.release_group, volume.label, volume.download_url)
                for volume in details.volumes
            ],
            [
                ("PDF", "Complete edition", "https://files.example/pdf"),
                ("EPUB", "Complete edition", "https://files.example/epub"),
            ],
        )
        self.assertEqual(details.download_url, "https://files.example/epub")

    def test_extracts_legacy_paragraph_catalogs(self):
        parser = server.PostDetailsParser("https://jnovels.com/legacy-paragraphs/")
        parser.feed(LEGACY_PARAGRAPH_CATALOG_HTML)

        details = parser.details()

        self.assertEqual(
            [
                (volume.release_group, volume.label, volume.download_url)
                for volume in details.volumes
            ],
            [
                ("EPUB", "Volume 1", "https://files.example/epub-1"),
                ("EPUB", "Volume 2", "https://files.example/epub-2"),
                ("PDF", "Volume 1", "https://files.example/pdf-1"),
            ],
        )
        self.assertEqual(details.download_url, "https://files.example/epub-1")

    def test_extracts_inline_legacy_synopsis_and_associated_names(self):
        parser = server.PostDetailsParser("https://jnovels.com/bookworm/")
        parser.feed(LEGACY_INLINE_METADATA_HTML)

        details = parser.details()

        self.assertEqual(
            details.synopsis,
            "A book-loving student is reborn in a world where books are rare.",
        )
        self.assertEqual(
            details.associated_names,
            ["Alternate Bookworm", "本好きの下剋上"],
        )

    def test_extracts_heading_based_legacy_metadata_until_next_section(self):
        parser = server.PostDetailsParser("https://jnovels.com/legacy-metadata/")
        parser.feed(LEGACY_HEADING_METADATA_HTML)

        details = parser.details()

        self.assertEqual(
            details.synopsis,
            "First synopsis paragraph.\n\nSecond synopsis paragraph.",
        )
        self.assertEqual(details.associated_names, ["Alternate One Alternate Two"])

    def test_ignores_self_referential_original_post_link(self):
        post_url = "https://jnovels.com/example-volume-2-epub/"
        parser = server.PostDetailsParser(post_url)
        parser.feed(
            """
            <article><div class="post-content clear">
              <p><a href="/example-volume-2-epub/">Refer to original post</a></p>
              <p><a href="https://files.example/v2">Volume 02</a></p>
            </div></article>
            """
        )

        details = parser.details()

        self.assertEqual(details.entry_type, "series")
        self.assertIsNone(details.original_post_url)
        self.assertEqual(
            [(volume.label, volume.download_url) for volume in details.volumes],
            [("Volume 2", "https://files.example/v2")],
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

    def test_extracts_epub_from_legacy_pdf_epub_list_items(self):
        parser = server.PostDetailsParser("https://jnovels.com/legacy/")
        parser.feed(LEGACY_BARE_DOWNLOADS_HTML)

        details = parser.details()

        self.assertEqual(
            details.volumes,
            [
                server.SeriesVolume(
                    "Volume 1",
                    "https://enagato.com/epub-1",
                    0,
                    "EPUB",
                ),
                server.SeriesVolume(
                    "Volume 2",
                    "https://enagato.com/epub-2",
                    1,
                    "EPUB",
                ),
            ],
        )
        self.assertEqual(details.download_url, "https://enagato.com/epub-1")

    def test_extracts_named_legacy_download_list(self):
        parser = server.PostDetailsParser("https://jnovels.com/kingdom-hearts/")
        parser.feed(LEGACY_NAMED_DOWNLOADS_HTML)

        details = parser.details()

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in details.volumes],
            [
                ("Kingdom Hearts: The Novel", "https://enagato.com/one"),
                ("Kingdom Hearts: Chain of Memories", "https://enagato.com/two"),
            ],
        )

    def test_accepts_legacy_forum_landing_page_as_download(self):
        parser = server.PostDetailsParser("https://jnovels.com/example/")
        parser.feed(LEGACY_FORUM_DOWNLOAD_HTML)

        details = parser.details()

        self.assertEqual(
            details.download_url,
            "https://mp4directs.com/forums/example.123/",
        )
        self.assertEqual(
            details.volumes,
            [
                server.SeriesVolume(
                    "Complete edition",
                    "https://mp4directs.com/forums/example.123/",
                    0,
                )
            ],
        )

    def test_extracts_catalog_from_titles_ending_in_volume_numbers(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(
            """
            <div class="post-content">
              <ol>
                <li>Example Series 1 ----- <a href="https://files.example/one">DOWNLOAD</a></li>
                <li>Example Series 15 ----- <a href="https://files.example/fifteen">DOWNLOAD</a></li>
              </ol>
            </div>
            """
        )

        self.assertEqual(
            [volume.label for volume in parser.details().volumes],
            ["Volume 1", "Volume 15"],
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

    def test_extracts_synopsis_associated_names_and_genres(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(METADATA_HTML)

        details = parser.details()
        self.assertEqual(
            details.synopsis,
            "First synopsis paragraph.\n\nSecond synopsis paragraph.",
        )
        self.assertEqual(
            details.associated_names,
            ["Example Japanese Title", "例の日本語タイトル"],
        )
        self.assertEqual(details.genres, ["Action", "Fantasy"])

    def test_recognizes_decimal_and_prefixed_volume_labels(self):
        self.assertTrue(server.is_volume_label("VOLUME 04.5"))
        self.assertTrue(server.is_volume_label("YEAR 2 VOLUME 10"))
        self.assertTrue(server.is_volume_label("Vol. 12.5"))
        self.assertEqual(
            server.extract_volume_label("CLASSROOM 2ND YEAR VOLUME 02 EPUB"),
            "Year 2 Volume 2",
        )
        self.assertEqual(
            server.extract_volume_label("YR3 VOLUME 01"),
            "Year 3 Volume 1",
        )
        self.assertEqual(
            server.extract_volume_label("Short Story Collection Volume 02"),
            "Short Story Collection Volume 2",
        )
        self.assertEqual(
            server.extract_volume_label("VOLUME 09 Canto II"),
            "Volume 9 Canto II",
        )
        self.assertEqual(
            server.extract_volume_label("VOLUME 04 II"),
            "Volume 4 II",
        )
        self.assertEqual(
            server.extract_volume_label("VOLUME 02 - PART 01"),
            "Volume 2 Part 1",
        )
        self.assertEqual(
            server.extract_volume_label("VOLUME 10 ACT 02"),
            "Volume 10 Act 2",
        )
        self.assertEqual(
            server.extract_volume_label("VOLUME 07 FINALE"),
            "Volume 7 Finale",
        )

    def test_keeps_latest_link_for_indistinguishable_duplicate_labels(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(
            """
            <div class="post-content">
              <ol>
                <li>Volume 4 <a href="https://files.example/old">Download</a></li>
                <li>Volume 4 <a href="https://files.example/new">Download</a></li>
              </ol>
            </div>
            """
        )

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in parser.details().volumes],
            [("Volume 4", "https://files.example/new")],
        )

    def test_ignores_later_ungrouped_embedded_catalogs(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-side-story/")
        parser.feed(
            """
            <div class="post-content">
              <ol><li>VOLUME 01 <a href="https://files.example/side">DOWNLOAD</a></li></ol>
              <ol>
                <li>Main Series Volume 01 <a href="https://files.example/main-1">DOWNLOAD</a></li>
                <li>Main Series Volume 02 <a href="https://files.example/main-2">DOWNLOAD</a></li>
              </ol>
            </div>
            """
        )

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in parser.details().volumes],
            [("Volume 1", "https://files.example/side")],
        )

    def test_keeps_internal_linked_releases_in_primary_catalog(self):
        parser = server.PostDetailsParser("https://jnovels.com/example-series/")
        parser.feed(
            """
            <div class="post-content">
              <ol>
                <li>Short Story Collection Volume 01 <a href="https://files.example/short">DOWNLOAD</a></li>
                <li>Example Fanbook <a href="/example-fanbook/">Open</a></li>
                <li>Example Royal Academy Stories - First Year <a href="/royal-academy/">Open</a></li>
              </ol>
            </div>
            """
        )

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in parser.details().volumes],
            [
                ("Short Story Collection Volume 1", "https://files.example/short"),
                ("Fanbook", "https://jnovels.com/example-fanbook/"),
                (
                    "Royal Academy Stories - First Year",
                    "https://jnovels.com/royal-academy/",
                ),
            ],
        )

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

    def test_release_sort_preserves_source_order_within_same_date(self):
        newest = server.Book(
            "Zeta",
            "https://example/newest",
            None,
            "2026-06-09",
        )
        oldest = server.Book(
            "Alpha",
            "https://example/oldest",
            None,
            "2026-06-09",
        )

        server.upsert_books([newest, oldest], source_page=1)

        descending = server.list_books("", "published_desc")
        ascending = server.list_books("", "published_asc")

        self.assertEqual(
            [book["url"] for book in descending],
            [newest.url, oldest.url],
        )
        self.assertEqual(
            [book["url"] for book in ascending],
            [oldest.url, newest.url],
        )

    def test_refresh_updates_same_day_source_order(self):
        first = server.Book("First", "https://example/first", None, "2026-06-09")
        second = server.Book("Second", "https://example/second", None, "2026-06-09")
        latest = server.Book("Latest", "https://example/latest", None, "2026-06-09")
        server.upsert_books([first, second], source_page=1)

        server.upsert_books([latest, second, first], source_page=1)

        books = server.list_books("", "published_desc")
        self.assertEqual(
            [book["url"] for book in books],
            [latest.url, second.url, first.url],
        )

    def test_cache_signature_ignores_maintenance_timestamps(self):
        book = server.Book("Example", "https://example/book", None, "2026-06-09")
        server.upsert_books([book], source_page=1)
        original = server.cache_content_signature()

        with server.connect_db() as connection:
            connection.execute(
                """
                UPDATE books
                SET cached_at = '2030-01-01T00:00:00+00:00',
                    last_seen_at = '2030-01-01T00:00:00+00:00',
                    detail_cached_at = '2030-01-01T00:00:00+00:00',
                    detail_last_attempt_at = '2030-01-01T00:00:00+00:00'
                WHERE url = ?
                """,
                (book.url,),
            )

        self.assertEqual(server.cache_content_signature(), original)

    def test_cache_signature_changes_with_visible_content(self):
        book = server.Book("Example", "https://example/book", None, "2026-06-09")
        server.upsert_books([book], source_page=1)
        original = server.cache_content_signature()

        with server.connect_db() as connection:
            connection.execute(
                "UPDATE books SET title = 'Updated example' WHERE url = ?",
                (book.url,),
            )

        self.assertNotEqual(server.cache_content_signature(), original)

    def test_keeps_pdf_only_entry_without_epub_counterpart(self):
        pdf = server.Book(
            "Example Story Volume 1 PDF",
            "https://jnovels.com/example-story-volume-1-pdf/",
            None,
            None,
        )

        server.upsert_books([pdf])

        self.assertEqual([book["url"] for book in server.list_books("", "title_asc")], [pdf.url])

    def test_prefers_epub_over_pdf_duplicate_in_same_batch(self):
        pdf = server.Book(
            "Example Story Volume 1 PDF",
            "https://jnovels.com/example-story-volume-1-pdf/",
            None,
            None,
        )
        epub = server.Book(
            "Example Story Volume 1 EPUB",
            "https://jnovels.com/example-story-volume-1-epub/",
            None,
            None,
        )

        server.upsert_books([pdf, epub])

        self.assertEqual(
            [book["url"] for book in server.list_books("", "title_asc")],
            [epub.url],
        )

    def test_later_epub_removes_pdf_series_and_its_pdf_children(self):
        pdf_series = server.Book(
            "DOWNLOAD EXAMPLE STORY ALL VOLUMES PDF",
            "https://jnovels.com/download-example-story-all-volumes-pdf/",
            None,
            None,
        )
        pdf_child = server.Book(
            "Example Story Volume 2 PDF",
            "https://jnovels.com/example-story-volume-2-pdf/",
            None,
            None,
        )
        epub_series = server.Book(
            "DOWNLOAD EXAMPLE STORY ALL VOLUMES EPUB",
            "https://jnovels.com/download-example-story-all-volumes-epub/",
            None,
            None,
        )
        server.upsert_books([pdf_series, pdf_child])
        server.update_book_details(
            pdf_series.url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/pdf-v1",
                [
                    server.SeriesVolume(
                        "Volume 1",
                        "https://files.example/pdf-v1",
                        0,
                    )
                ],
            ),
        )
        server.update_book_details(
            pdf_child.url,
            server.PostDetails(
                "volume",
                pdf_series.url,
                "https://files.example/pdf-v2",
            ),
        )

        server.upsert_books([epub_series])

        with server.connect_db() as connection:
            pdf_books = connection.execute(
                "SELECT url FROM books WHERE url IN (?, ?)",
                (pdf_series.url, pdf_child.url),
            ).fetchall()
            pdf_catalog = connection.execute(
                "SELECT 1 FROM series_volumes WHERE series_url = ?",
                (pdf_series.url,),
            ).fetchall()
            pdf_status = connection.execute(
                "SELECT 1 FROM series_status WHERE series_url = ?",
                (pdf_series.url,),
            ).fetchall()
        self.assertEqual(pdf_books, [])
        self.assertEqual(pdf_catalog, [])
        self.assertEqual(pdf_status, [])
        self.assertEqual(
            [book["url"] for book in server.list_books("", "title_asc")],
            [epub_series.url],
        )

    def test_schema_indexes_stale_series_lookup(self):
        with server.connect_db() as connection:
            indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(books)").fetchall()
            }

        self.assertIn("idx_books_original_post_cached", indexes)

    def test_catalog_index_cache_is_invalidated_after_replacement(self):
        series_url = "https://jnovels.com/example-light-novel-epub/"
        server.upsert_books(
            [server.Book("Example Light Novel Epub", series_url, None, None)]
        )
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
        )

        self.assertEqual(server.list_books("", "title_asc")[0]["volume_count"], 1)

        server.replace_series_volumes(
            series_url,
            [
                server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
            ],
        )

        self.assertEqual(server.list_books("", "title_asc")[0]["volume_count"], 2)

    def test_card_catalog_count_uses_current_download_category(self):
        series_url = "https://jnovels.com/example-series/"
        child_url = "https://jnovels.com/example-series-volume-2/"
        server.upsert_books(
            [
                server.Book("Example Series", series_url, None, None),
                server.Book("Example Series Volume 2 EPUB", child_url, None, None),
            ]
        )
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/official-1",
                [
                    server.SeriesVolume(
                        f"Volume {number}",
                        f"https://files.example/official-{number}",
                        number - 1,
                        "Official translation",
                    )
                    for number in range(1, 4)
                ]
                + [
                    server.SeriesVolume(
                        f"Volume {number}",
                        f"https://files.example/fan-{number}",
                        number + 2,
                        "Fan translation",
                    )
                    for number in range(1, 3)
                ],
            ),
        )
        server.update_book_details(
            child_url,
            server.PostDetails(
                "volume",
                series_url,
                "https://files.example/fan-2",
            ),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }

        self.assertEqual(saved[series_url]["volume_count"], 5)
        self.assertEqual(saved[series_url]["catalog_count"], 3)
        self.assertEqual(saved[child_url]["volume_count"], 5)
        self.assertEqual(saved[child_url]["catalog_count"], 2)

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

    def test_search_terms_are_order_independent_and_noncontiguous(self):
        matching = server.Book(
            "How a Realist Hero Rebuilt the Kingdom Volume 21",
            "https://example/matching",
            None,
            None,
        )
        partial = server.Book(
            "How a Realist Villain Rebuilt the Empire",
            "https://example/partial",
            None,
            None,
        )
        server.upsert_books([matching, partial])

        books = server.list_books("kingdom realist 21", "title_asc")

        self.assertEqual([book["url"] for book in books], [matching.url])
        self.assertEqual(server.count_books("kingdom, realist! 21"), 1)

    def test_numeric_search_term_does_not_match_every_series_sibling(self):
        series = server.Book(
            "Example Series All Volumes EPUB",
            "https://example/series",
            None,
            None,
        )
        volume_20 = server.Book(
            "Example Series Volume 20 EPUB",
            "https://example/volume-20",
            None,
            None,
        )
        volume_21 = server.Book(
            "Example Series Volume 21 EPUB",
            "https://example/volume-21",
            None,
            None,
        )
        server.upsert_books([series, volume_20, volume_21])
        for volume in (volume_20, volume_21):
            server.update_book_details(
                volume.url,
                server.PostDetails(
                    "volume",
                    series.url,
                    f"https://files.example/{volume.url.rsplit('-', 1)[-1]}",
                ),
            )

        books = server.list_books("series 21", "title_asc")

        self.assertEqual([book["url"] for book in books], [volume_21.url])

    def test_search_matches_latest_title_across_series_family(self):
        series = server.Book(
            "DOWNLOAD DEATH MARCH ALL VOLUMES EPUB",
            "https://jnovels.com/download-death-march-all-volumes-epub/",
            None,
            None,
        )
        old_volume = server.Book(
            "Death March kara Hajimaru Isekai Kyousoukyoku Volume 6 EPUB",
            "https://jnovels.com/death-march-volume-6-epub/",
            None,
            "2018-09-19",
        )
        latest_volume = server.Book(
            "Death March to the Parallel World Rhapsody Volume 24 EPUB",
            "https://jnovels.com/death-march-volume-24-epub/",
            None,
            "2025-12-04",
        )
        server.upsert_books([series, old_volume, latest_volume])
        for volume in (old_volume, latest_volume):
            server.update_book_details(
                volume.url,
                server.PostDetails(
                    "volume",
                    series.url,
                    f"https://files.example/{volume.url.rsplit('-', 2)[-2]}",
                ),
            )

        matches = server.list_books(
            "Parallel World Rhapsody",
            "title_asc",
        )

        self.assertEqual(
            {book["url"] for book in matches},
            {series.url, old_volume.url, latest_volume.url},
        )

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
        self.assertEqual(
            server.count_book_filters(),
            {
                "all": 3,
                "series": 1,
                "volumes": 1,
                "pending": 1,
            },
        )

    def test_series_display_title_uses_its_latest_volume_title(self):
        series = server.Book(
            "Isekai ni Tensei Shitanda kedo Ore All Volumes EPUB",
            "https://jnovels.com/isekai-ni-tensei-shitanda-kedo-ore-all-volumes-epub/",
            None,
            None,
        )
        volume = server.Book(
            "Me a Genius I Was Reborn into Another World and I Think "
            "They've Got the Wrong Idea! Volume 2 EPUB",
            "https://jnovels.com/me-a-genius-volume-2-epub/",
            None,
            None,
        )
        latest_volume = server.Book(
            "Isekai ni Tensei Shitanda kedo Ore, Tensai tte "
            "Kanchigai Saretenai Volume 3 EPUB",
            "https://jnovels.com/isekai-volume-3-epub/",
            None,
            "2018-08-10",
        )
        server.upsert_books([series, volume, latest_volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series.url, "https://files.example/v2"),
        )
        server.update_book_details(
            latest_volume.url,
            server.PostDetails("volume", series.url, "https://files.example/v3"),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }

        self.assertEqual(
            saved[volume.url]["series_title"],
            "Isekai ni Tensei Shitanda kedo Ore, Tensai tte Kanchigai Saretenai",
        )
        self.assertEqual(
            saved[series.url]["series_title"],
            "Isekai ni Tensei Shitanda kedo Ore, Tensai tte Kanchigai Saretenai",
        )
        self.assertEqual(saved[volume.url]["volume_label"], "Vol. 2")
        self.assertEqual(
            saved[volume.url]["title"],
            "Me a Genius I Was Reborn into Another World and I Think "
            "They've Got the Wrong Idea! Volume 2 EPUB",
        )

    def test_latest_volume_title_replaces_abbreviated_parent_title(self):
        series = server.Book(
            "DOWNLOAD DEATH MARCH ALL VOLUMES EPUB",
            "https://jnovels.com/download-death-march-all-volumes-epub/",
            None,
            None,
        )
        volume = server.Book(
            "Death March to the Parallel World Rhapsody Volume 19 EPUB",
            "https://jnovels.com/death-march-volume-19-epub/",
            None,
            None,
        )
        server.upsert_books([series, volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series.url, "https://files.example/v19"),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }

        self.assertEqual(
            saved[volume.url]["series_title"],
            "Death March to the Parallel World Rhapsody",
        )
        self.assertEqual(
            saved[series.url]["series_title"],
            "Death March to the Parallel World Rhapsody",
        )
        self.assertEqual(saved[volume.url]["volume_label"], "Vol. 19")

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

    def test_series_metadata_is_searchable_and_propagated_to_volume_entries(self):
        series_url = "https://example/series"
        volume = server.Book("Example Volume 2", "https://example/volume-2", None, None)
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", series_url, "https://files.example/two"),
        )

        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/one",
                [server.SeriesVolume("Volume 1", "https://files.example/one", 0)],
                ["Alternate Example Name", "別名"],
                ["Fantasy", "Romance"],
                "A useful synopsis.",
            ),
        )

        saved = server.list_books("Alternate Example", "title_asc")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["associated_names"], ["Alternate Example Name", "別名"])
        self.assertEqual(saved[0]["genres"], ["Fantasy", "Romance"])
        self.assertEqual(saved[0]["synopsis"], "A useful synopsis.")

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

    def test_series_catalog_uses_series_post_date_for_its_first_download(self):
        series_url = "https://example/series"
        download_url = "https://files.example/v1"
        server.upsert_books(
            [server.Book("Example Series", series_url, None, "2022-08-15")]
        )
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                download_url,
                [server.SeriesVolume("Volume 1", download_url, 0)],
            ),
        )

        saved = server.list_series_volumes(series_url)[0]

        self.assertEqual(saved["published_at"], "2022-08-15")
        self.assertEqual(saved["post_url"], series_url)

    def test_infers_parent_for_broken_self_linked_volume_post(self):
        series_url = "https://jnovels.com/isekai-ni-tensei-shitanda-kedo-ore-all-volumes-epub/"
        child_url = (
            "https://jnovels.com/me-a-genius-i-was-reborn-into-another-world-"
            "and-i-think-theyve-got-the-wrong-idea-volume-2-epub/"
        )
        server.upsert_books(
            [
                server.Book(
                    "Isekai ni Tensei Shitanda kedo Ore All Volumes EPUB",
                    series_url,
                    None,
                    None,
                ),
                server.Book(
                    "Me, a Genius? I Was Reborn into Another World and I Think "
                    "They've Got the Wrong Idea! Volume 2 EPUB",
                    child_url,
                    None,
                    None,
                ),
            ]
        )
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [
                    server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                    server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
                ],
            ),
        )

        server.update_book_details(
            child_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/child-v2",
                parent_hints=[
                    "DOWNLOAD Isekai ni Tensei Shitanda kedo Ore, Tensai tte "
                    "Kanchigai Saretenai Volume 2 EPUB"
                ],
            ),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }[child_url]
        self.assertEqual(saved["entry_type"], "volume")
        self.assertEqual(saved["original_post_url"], series_url)

    def test_infers_legacy_parent_from_matching_catalog_download(self):
        series_url = "https://jnovels.com/download-death-march-all-volumes-epub/"
        child_url = "https://jnovels.com/death-march-volume-5-epub/"
        download_url = "https://files.example/v5"
        server.upsert_books(
            [
                server.Book(
                    "DOWNLOAD DEATH MARCH ALL VOLUMES EPUB",
                    series_url,
                    None,
                    None,
                ),
                server.Book(
                    "Death March kara Hajimaru Isekai Kyousoukyoku Volume 5 EPUB",
                    child_url,
                    None,
                    "2018-05-29",
                ),
            ]
        )
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 5", download_url, 0)],
        )

        server.update_book_details(
            child_url,
            server.PostDetails(
                "series",
                None,
                download_url,
                [server.SeriesVolume("Volume 5", download_url, 0)],
            ),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }[child_url]
        self.assertEqual(saved["entry_type"], "volume")
        self.assertEqual(saved["original_post_url"], series_url)
        self.assertEqual(
            saved["series_title"],
            "Death March kara Hajimaru Isekai Kyousoukyoku",
        )

    def test_infers_short_legacy_parent_when_redirect_and_catalog_urls_differ(self):
        series_url = "https://jnovels.com/download-death-march-all-volumes-epub/"
        child_url = "https://jnovels.com/death-march-volume-5-epub/"
        server.upsert_books(
            [
                server.Book(
                    "DOWNLOAD DEATH MARCH ALL VOLUMES EPUB",
                    series_url,
                    None,
                    None,
                ),
                server.Book(
                    "Death March kara Hajimaru Isekai Kyousoukyoku Volume 5 EPUB",
                    child_url,
                    None,
                    "2018-05-29",
                ),
            ]
        )
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://catalog.example/v1",
                [
                    server.SeriesVolume(
                        f"Volume {number}",
                        f"https://catalog.example/v{number}",
                        number - 1,
                    )
                    for number in range(1, 7)
                ],
            ),
        )

        server.update_book_details(
            child_url,
            server.PostDetails(
                "series",
                None,
                "https://legacy-redirect.example/v5",
                [
                    server.SeriesVolume(
                        "Volume 5",
                        "https://legacy-redirect.example/v5",
                        0,
                    )
                ],
            ),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }[child_url]
        self.assertEqual(saved["entry_type"], "volume")
        self.assertEqual(saved["original_post_url"], series_url)

    def test_infers_orphaned_parent_catalog_from_associated_name_alias(self):
        legacy_url = "https://jnovels.com/download-eiyuu-kyoushitsu-epub/"
        parent_url = "https://jnovels.com/classroom-for-heroes-light-novel-epub/"
        latest_url = "https://jnovels.com/classroom-for-heroes-volume-6-epub/"
        server.upsert_books(
            [
                server.Book(
                    "Download Eiyuu Kyoushitsu EPUB",
                    legacy_url,
                    None,
                    "2019-04-13",
                ),
                server.Book(
                    "Classroom for Heroes Volume 6 Epub",
                    latest_url,
                    None,
                    "2026-04-16",
                ),
            ]
        )
        server.update_book_details(
            latest_url,
            server.PostDetails(
                "volume",
                parent_url,
                "https://files.example/official-6",
            ),
        )
        server.replace_series_volumes(
            parent_url,
            [
                server.SeriesVolume(
                    f"Volume {number}",
                    f"https://files.example/official-{number}",
                    number - 1,
                )
                for number in range(1, 7)
            ],
        )
        server.replace_series_volumes(
            legacy_url,
            [
                server.SeriesVolume(
                    "Volume 1",
                    "https://files.example/legacy-1",
                    0,
                )
            ],
        )

        server.update_book_details(
            legacy_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/legacy-1",
                [
                    server.SeriesVolume(
                        "Volume 1",
                        "https://files.example/legacy-1",
                        0,
                    )
                ],
                associated_names=[
                    "Class Room For Heroes Hero Classroom 英雄教室"
                ],
            ),
        )
        server.update_book_details(
            legacy_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/legacy-1",
                [
                    server.SeriesVolume(
                        "Volume 1",
                        "https://files.example/legacy-1",
                        0,
                    )
                ],
                associated_names=[
                    "Class Room For Heroes Hero Classroom 英雄教室"
                ],
            ),
        )

        saved = {
            book["url"]: book for book in server.list_books("", "title_asc")
        }[legacy_url]
        self.assertEqual(saved["entry_type"], "series")
        self.assertIsNone(saved["original_post_url"])
        self.assertEqual(saved["series_url"], legacy_url)
        self.assertEqual(saved["volume_count"], 7)
        self.assertEqual(saved["catalog_count"], 1)
        volumes = server.list_series_volumes(legacy_url)
        self.assertEqual(
            [
                (volume["release_group"], volume["label"])
                for volume in volumes
            ],
            [
                *[
                    ("Official translation", f"Volume {number}")
                    for number in range(1, 7)
                ],
                ("Fan translation", "Volume 1"),
            ],
        )
        with server.connect_db() as connection:
            obsolete_count = connection.execute(
                "SELECT COUNT(*) FROM series_volumes WHERE series_url = ?",
                (parent_url,),
            ).fetchone()[0]
            reparented = connection.execute(
                "SELECT original_post_url FROM books WHERE url = ?",
                (latest_url,),
            ).fetchone()[0]
        self.assertEqual(obsolete_count, 0)
        self.assertEqual(reparented, legacy_url)

    def test_ignores_app_store_links_in_legacy_catalog(self):
        parser = server.PostDetailsParser("https://jnovels.com/example/")
        parser.feed(
            """
            <div class="post-content">
              <ul>
                <li><a href="https://play.google.com/store/apps/details?id=example">
                  Download reader app
                </a></li>
                <li><a href="https://enagato.com/fan-volume-1">
                  Volume 1 EPUB
                </a></li>
              </ul>
            </div>
            """
        )

        details = parser.details()

        self.assertEqual(
            [(volume.label, volume.download_url) for volume in details.volumes],
            [("Volume 1", "https://enagato.com/fan-volume-1")],
        )

    def test_parent_refresh_merges_cached_child_missing_from_source_catalog(self):
        series_url = "https://jnovels.com/example-all-volumes-epub/"
        child_url = "https://jnovels.com/example-volume-3-epub/"
        server.upsert_books(
            [
                server.Book("Example All Volumes EPUB", series_url, None, None),
                server.Book("Example Volume 3 EPUB", child_url, None, "2026-01-03"),
            ]
        )
        server.update_book_details(
            child_url,
            server.PostDetails("volume", series_url, "https://files.example/v3"),
        )

        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v1",
                [
                    server.SeriesVolume("Volume 1", "https://files.example/v1", 0),
                    server.SeriesVolume("Volume 2", "https://files.example/v2", 1),
                ],
            ),
        )

        self.assertEqual(
            [volume["label"] for volume in server.list_series_volumes(series_url)],
            ["Volume 1", "Volume 2", "Volume 3"],
        )

    def test_cleanup_reparents_children_of_misclassified_series_volume(self):
        correct_parent = "https://jnovels.com/example-all-volumes-epub/"
        wrong_parent = "https://jnovels.com/example-volume-10-epub/"
        child_url = "https://jnovels.com/example-volume-11-epub/"
        server.upsert_books(
            [
                server.Book("Example All Volumes EPUB", correct_parent, None, None),
                server.Book("Example Volume 10 EPUB", wrong_parent, None, None),
                server.Book("Example Volume 11 EPUB", child_url, None, None),
            ]
        )
        server.update_book_details(
            wrong_parent,
            server.PostDetails(
                "series",
                None,
                "https://files.example/v10",
                [
                    server.SeriesVolume(
                        "Volume 11",
                        "https://files.example/v11",
                        0,
                    )
                ],
            ),
        )
        server.update_book_details(
            wrong_parent,
            server.PostDetails(
                "volume",
                correct_parent,
                "https://files.example/v10",
            ),
        )
        server.update_book_details(
            child_url,
            server.PostDetails(
                "volume",
                wrong_parent,
                "https://files.example/v11",
            ),
        )

        self.assertGreater(server.cleanup_misclassified_series_tracking(), 0)

        with server.connect_db() as connection:
            child = connection.execute(
                "SELECT original_post_url FROM books WHERE url = ?",
                (child_url,),
            ).fetchone()
            wrong_status = connection.execute(
                "SELECT 1 FROM series_status WHERE series_url = ?",
                (wrong_parent,),
            ).fetchone()
        self.assertEqual(child["original_post_url"], correct_parent)
        self.assertIsNone(wrong_status)
        self.assertEqual(
            [volume["label"] for volume in server.list_series_volumes(correct_parent)],
            ["Volume 10", "Volume 11"],
        )

    def test_catalog_dates_match_series_url_variants_by_identity_and_label(self):
        catalog_url = "https://jnovels.com/example-series-light-novel-epub/"
        volume = server.Book(
            "Example Series Volume 2 Epub",
            "https://jnovels.com/example-series-volume-2-epub/",
            None,
            "2026-06-09",
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails(
                "volume",
                "https://jnovels.com/example-series-light-novel-epubs/",
                "https://files.example/post-download",
            ),
        )
        server.replace_series_volumes(
            catalog_url,
            [server.SeriesVolume("Volume 2", "https://files.example/catalog-download", 0)],
        )

        saved = server.list_series_volumes(catalog_url)[0]
        self.assertEqual(saved["published_at"], "2026-06-09")
        self.assertEqual(saved["post_url"], volume.url)

    def test_catalog_dates_use_direct_parent_when_child_title_differs(self):
        series_url = "https://jnovels.com/example-original-title-epub/"
        volume = server.Book(
            "Completely Different English Title Volume 2 Epub",
            "https://jnovels.com/different-title-volume-2-epub/",
            None,
            "2026-06-09",
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails(
                "volume",
                series_url,
                "https://files.example/post-download",
            ),
        )
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 2", "https://files.example/catalog-download", 0)],
        )

        saved = server.list_series_volumes(series_url)[0]
        self.assertEqual(saved["published_at"], "2026-06-09")
        self.assertEqual(saved["post_url"], volume.url)

    def test_catalog_date_is_shared_by_pdf_and_epub_variants(self):
        series_url = "https://jnovels.com/example-webnovel/"
        epub_download = "https://files.example/epub"
        server.upsert_books(
            [server.Book("Example Webnovel", series_url, None, "2026-06-09")]
        )
        server.update_book_details(
            series_url,
            server.PostDetails(
                "series",
                None,
                epub_download,
                [
                    server.SeriesVolume(
                        "Complete edition",
                        "https://files.example/pdf",
                        0,
                        "PDF",
                    ),
                    server.SeriesVolume(
                        "Complete edition",
                        epub_download,
                        1,
                        "EPUB",
                    ),
                ],
            ),
        )

        saved = server.list_series_volumes(series_url)
        self.assertEqual(
            [volume["published_at"] for volume in saved],
            ["2026-06-09", "2026-06-09"],
        )
        self.assertEqual(
            [volume["post_url"] for volume in saved],
            [series_url, series_url],
        )

    def test_uses_best_matching_catalog_when_source_parent_has_no_catalog(self):
        correct_series = "https://jnovels.com/download-example-series-all-volumes-epub/"
        wrong_parent = "https://jnovels.com/example-series-volume-10-epub/"
        volume = server.Book(
            "Example Series Volume 14 Epub",
            "https://jnovels.com/example-series-volume-14-epub/",
            None,
            None,
        )
        server.upsert_books([volume])
        server.update_book_details(
            volume.url,
            server.PostDetails("volume", wrong_parent, "https://files.example/v14"),
        )
        server.replace_series_volumes(
            correct_series,
            [
                server.SeriesVolume(
                    f"Volume {number}",
                    f"https://files.example/v{number}",
                    number - 1,
                )
                for number in range(1, 15)
            ],
        )

        saved = server.list_books("", "title_asc")[0]
        self.assertEqual(saved["series_url"], correct_series)
        self.assertEqual(saved["volume_count"], 14)

    def test_uses_catalog_containing_the_exact_download_for_related_releases(self):
        umbrella = "https://jnovels.com/example-main-series/"
        child_parent = "https://jnovels.com/example-short-stories/"
        child = server.Book(
            "Example Short Story Collection Volume 2 Epub",
            "https://jnovels.com/example-short-story-volume-2-epub/",
            None,
            None,
        )
        server.upsert_books([child])
        server.update_book_details(
            child.url,
            server.PostDetails(
                "volume",
                child_parent,
                "https://files.example/short-story-2",
            ),
        )
        server.replace_series_volumes(
            umbrella,
            [
                server.SeriesVolume("Volume 1", "https://files.example/main-1", 0),
                server.SeriesVolume(
                    "Short Story Collection Volume 2",
                    "https://files.example/short-story-2",
                    1,
                ),
            ],
        )

        saved = server.list_books("", "title_asc")[0]
        self.assertEqual(saved["series_url"], umbrella)
        self.assertEqual(saved["volume_count"], 2)

    def test_builds_fallback_catalog_from_cached_volume_posts(self):
        parent = "https://jnovels.com/deleted-series-page/"
        for number in (2, 3):
            book = server.Book(
                f"Deleted Series Volume {number} Epub",
                f"https://jnovels.com/deleted-series-volume-{number}-epub/",
                None,
                f"2026-0{number}-01",
            )
            server.upsert_books([book])
            server.update_book_details(
                book.url,
                server.PostDetails(
                    "volume",
                    parent,
                    f"https://files.example/{number}",
                ),
            )

        volumes = server.list_series_volumes(parent)
        self.assertEqual(
            [(volume["label"], volume["download_url"]) for volume in volumes],
            [
                ("Volume 2", "https://files.example/2"),
                ("Volume 3", "https://files.example/3"),
            ],
        )
        self.assertEqual(server.list_books("", "title_asc")[0]["volume_count"], 2)

    def test_series_identity_strips_abbreviated_volume_suffixes(self):
        self.assertEqual(
            server.series_identity_key(
                "86—EIGHTY SIX Alter, Vol. 2 Fight, Magical Girl Reina Epub",
                "https://jnovels.com/86-eighty-six-alter-vol-2-epub/",
            ),
            "86 eighty six alter",
        )

    def test_marks_single_volume_series_as_standalone(self):
        book = server.Book("One Off", "https://example/one-off", None, None)
        server.upsert_books([book])
        server.update_book_details(
            book.url,
            server.PostDetails(
                "series",
                None,
                "https://files.example/one",
                [server.SeriesVolume("Volume 1", "https://files.example/one", 0)],
            ),
        )

        self.assertTrue(server.list_books("", "title_asc")[0]["is_standalone"])

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
                "refreshable_series": 0,
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

    def test_health_counts_series_missing_metadata_as_refreshable(self):
        series_url = "https://jnovels.com/example-light-novel-epub/"
        server.upsert_books(
            [server.Book("Example Light Novel Epub", series_url, None, None)]
        )
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
        )

        health = server.get_cache_health()

        self.assertEqual(health["stale_series"], 0)
        self.assertEqual(health["refreshable_series"], 1)

    def test_unavailable_series_keeps_catalog_without_rejoining_refresh_queue(self):
        series_url = "https://jnovels.com/dead-series-page/"
        server.replace_series_volumes(
            series_url,
            [server.SeriesVolume("Volume 1", "https://files.example/v1", 0)],
        )

        server.mark_series_unavailable(series_url)
        server.initialize_db()

        self.assertEqual(server.get_series_refresh_urls(10), [])
        self.assertEqual(
            server.get_cache_health(),
            {
                "pending_details": 0,
                "failed_retries": 0,
                "paused_retries": 0,
                "stale_series": 0,
                "refreshable_series": 0,
            },
        )
        self.assertEqual(
            [volume["label"] for volume in server.list_series_volumes(series_url)],
            ["Volume 1"],
        )

    def test_legacy_reparse_migration_normalizes_and_queues_old_formats(self):
        url = "https://jnovels.com/webnovelpdfepub-example/"
        server.upsert_books([server.Book("[WEBNOVEL][PDF][EPUB] Example", url, None, None)])
        with server.connect_db() as connection:
            connection.execute(
                """
                UPDATE books
                SET entry_type = 'volume', original_post_url = url
                WHERE url = ?
                """,
                (url,),
            )
            connection.execute(
                """
                INSERT INTO series_status (series_url, checked_at, metadata_checked_at)
                VALUES (?, ?, ?)
                """,
                (url, "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"),
            )
            connection.execute(
                "DELETE FROM metadata WHERE key = ?",
                (server.LEGACY_REPARSE_KEY,),
            )

        self.assertGreaterEqual(server.prepare_legacy_reparse(), 2)

        with server.connect_db() as connection:
            book = connection.execute(
                "SELECT entry_type, original_post_url FROM books WHERE url = ?",
                (url,),
            ).fetchone()
            status = connection.execute(
                "SELECT metadata_checked_at FROM series_status WHERE series_url = ?",
                (url,),
            ).fetchone()
        self.assertEqual(book["entry_type"], "series")
        self.assertIsNone(book["original_post_url"])
        self.assertIsNone(status["metadata_checked_at"])

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

    def test_series_metadata_backfill_is_selected_only_until_checked(self):
        unchecked = "https://example/unchecked-series"
        checked = "https://example/checked-series"
        for url in (unchecked, checked):
            server.replace_series_volumes(
                url,
                [server.SeriesVolume("Volume 1", f"{url}/download", 0)],
            )

        self.assertEqual(
            set(server.get_series_metadata_backfill_urls(10)),
            {unchecked, checked},
        )

        server.mark_series_metadata_checked(checked)

        self.assertEqual(
            server.get_series_metadata_backfill_urls(10),
            [unchecked],
        )
        self.assertIn(unchecked, server.get_series_refresh_urls(10))
        self.assertNotIn(checked, server.get_series_refresh_urls(10))

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

    def test_search_page_404_after_first_page_is_end_of_results(self):
        error = server.urllib.error.HTTPError(
            server.search_url(907),
            404,
            "Not Found",
            Message(),
            None,
        )
        with patch.object(server.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(server.fetch_page(907), [])

    def test_search_page_404_on_first_page_is_still_an_error(self):
        error = server.urllib.error.HTTPError(
            server.search_url(1),
            404,
            "Not Found",
            Message(),
            None,
        )
        with (
            patch.object(server.urllib.request, "urlopen", side_effect=error),
            self.assertRaises(server.urllib.error.HTTPError),
        ):
            server.fetch_page(1)

    def test_sync_finishes_cleanly_at_end_of_search_pages(self):
        with (
            patch.object(server, "fetch_page", return_value=[]),
            patch.object(server.time, "sleep"),
        ):
            server.run_sync(907, 1, 0.5, "manual")

        self.assertIsNone(server.sync_state["error"])
        self.assertEqual(server.sync_state["search_end_page"], 907)
        self.assertEqual(
            server.sync_state["notice"],
            "Reached the end of JNovels search results at page 907.",
        )

    def test_start_sync_thread_marks_sync_running_before_worker_starts(self):
        with patch.object(server.threading, "Thread") as thread_class:
            started = server.start_sync_thread(4, 6, 0.5, "manual")

        try:
            self.assertTrue(started)
            self.assertTrue(server.sync_state["running"])
            self.assertEqual(server.sync_state["page"], 4)
            self.assertEqual(server.sync_state["pages_requested"], 6)
            self.assertEqual(server.sync_state["request_delay"], 0.5)
            self.assertIsNotNone(server.sync_state["phase_started_at"])
            thread_class.return_value.start.assert_called_once_with()
        finally:
            server.sync_state["running"] = False
            server.sync_lock.release()

    def test_auto_catchup_is_limited_to_once_per_ten_minutes(self):
        with (
            patch.object(server, "last_auto_sync_started", None),
            patch.object(server.time, "monotonic", side_effect=[100.0, 200.0]),
            patch.object(server, "start_sync_thread", return_value=True) as start_sync,
        ):
            first = server.start_auto_catchup(20, 1.0)
            second = server.start_auto_catchup(20, 1.0)

        self.assertEqual(first, (True, None, 0))
        self.assertEqual(second, (False, "rate_limited", 500))
        start_sync.assert_called_once_with(1, 20, 1.0, "catchup")

    def test_busy_auto_catchup_does_not_consume_the_cooldown(self):
        with (
            patch.object(server, "last_auto_sync_started", None),
            patch.object(server.time, "monotonic", side_effect=[100.0, 100.0]),
            patch.object(
                server,
                "start_sync_thread",
                side_effect=[False, True],
            ) as start_sync,
        ):
            busy = server.start_auto_catchup()
            started = server.start_auto_catchup()

        self.assertEqual(busy, (False, "sync_running", 0))
        self.assertEqual(started, (True, None, 0))
        self.assertEqual(start_sync.call_count, 2)

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
        assert checkpoint is not None
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

    def test_extend_uses_scanned_frontier_instead_of_retained_entry_count(self):
        known_url = "https://jnovels.com/known/"

        def page(number):
            if number == 1 or 904 <= number <= 906:
                return [server.Book("Known", known_url, None, None)]
            if number == 907:
                return []
            self.fail(f"Unexpected page {number}")

        with (
            patch.object(server, "fetch_page", side_effect=page) as fetch,
            patch.object(server.time, "sleep"),
        ):
            books = server.collect_sync_books(
                "extend",
                start_page=1,
                max_pages=20,
                delay=0.5,
                known_urls={known_url},
                cached_frontier=906,
            )

        self.assertEqual(books, [])
        self.assertEqual(
            fetch.call_args_list,
            [call(1), call(904), call(905), call(906), call(907)],
        )

    def test_extend_crosses_excluded_gap_before_counting_new_pages(self):
        known_url = "https://jnovels.com/known/"

        def page(number):
            if number == 1:
                return [server.Book("Known", known_url, None, None)]
            if 2 <= number <= 5:
                return [
                    server.Book(
                        f"Excluded {number}",
                        f"https://jnovels.com/excluded-{number}/",
                        None,
                        None,
                    )
                ]
            return [
                server.Book(
                    f"New {number}",
                    f"https://jnovels.com/new-{number}/",
                    None,
                    None,
                )
            ]

        def exclude_page(_page, books):
            books[:] = [
                book for book in books if not book.url.startswith("https://jnovels.com/excluded-")
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
                known_urls={known_url},
                on_page=exclude_page,
                cached_frontier=4,
            )

        self.assertEqual(
            [book.url for book in books],
            [
                "https://jnovels.com/new-6/",
                "https://jnovels.com/new-7/",
            ],
        )
        self.assertEqual(
            fetch.call_args_list,
            [call(1), call(2), call(3), call(4), call(5), call(6), call(7)],
        )

    def test_frontier_advances_once_for_repeated_observed_front_shift(self):
        source_orders = {
            "https://jnovels.com/old-a/": 1000,
            "https://jnovels.com/old-b/": 1001,
        }
        observed = server.observed_source_page_shift(21, source_orders)
        frontier, applied = server.apply_observed_front_shift(906, 0, observed)
        frontier, applied = server.apply_observed_front_shift(
            frontier,
            applied,
            server.observed_source_page_shift(21, source_orders),
        )

        self.assertEqual(observed, 20)
        self.assertEqual(frontier, 926)
        self.assertEqual(applied, 20)


if __name__ == "__main__":
    unittest.main()
