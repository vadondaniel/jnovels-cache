from __future__ import annotations

import sqlite3
import urllib.parse

from server_modules.config import BASE_URL, EXCLUDED_SEARCH_PATHS


def initialize_books_fts(connection: sqlite3.Connection) -> bool:
    try:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title,
                associated_names,
                content='books',
                content_rowid='id',
                tokenize='unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS books_fts_ai
            AFTER INSERT ON books BEGIN
                INSERT INTO books_fts(rowid, title, associated_names)
                VALUES (new.id, new.title, COALESCE(new.associated_names, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS books_fts_ad
            AFTER DELETE ON books BEGIN
                INSERT INTO books_fts(books_fts, rowid, title, associated_names)
                VALUES (
                    'delete',
                    old.id,
                    old.title,
                    COALESCE(old.associated_names, '')
                );
            END;

            CREATE TRIGGER IF NOT EXISTS books_fts_au
            AFTER UPDATE OF title, associated_names ON books BEGIN
                INSERT INTO books_fts(books_fts, rowid, title, associated_names)
                VALUES (
                    'delete',
                    old.id,
                    old.title,
                    COALESCE(old.associated_names, '')
                );
                INSERT INTO books_fts(rowid, title, associated_names)
                VALUES (new.id, new.title, COALESCE(new.associated_names, ''));
            END;
            """
        )
        connection.execute("INSERT INTO books_fts(books_fts) VALUES ('rebuild')")
    except sqlite3.OperationalError:
        return False
    return True


def initialize_schema(connection: sqlite3.Connection) -> bool:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            image_url TEXT,
            published_at TEXT,
            cached_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            entry_type TEXT,
            original_post_url TEXT,
            download_url TEXT,
            detail_cached_at TEXT,
            detail_attempts INTEGER NOT NULL DEFAULT 0,
            detail_last_attempt_at TEXT,
            detail_last_error TEXT,
            associated_names TEXT,
            genres TEXT,
            synopsis TEXT,
            source_order INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
        CREATE INDEX IF NOT EXISTS idx_books_published ON books(published_at);

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS series_volumes (
            id INTEGER PRIMARY KEY,
            series_url TEXT NOT NULL,
            label TEXT NOT NULL,
            download_url TEXT NOT NULL,
            position INTEGER NOT NULL,
            cached_at TEXT NOT NULL,
            release_group TEXT,
            UNIQUE(series_url, download_url)
        );

        CREATE INDEX IF NOT EXISTS idx_series_volumes_url
        ON series_volumes(series_url, position);

        CREATE TABLE IF NOT EXISTS series_status (
            series_url TEXT PRIMARY KEY,
            checked_at TEXT NOT NULL,
            metadata_checked_at TEXT,
            unavailable_at TEXT
        );

        CREATE TABLE IF NOT EXISTS series_preferences (
            series_url TEXT PRIMARY KEY,
            favorite INTEGER NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS series_index_entries (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(books)").fetchall()
    }
    migrations = {
        "entry_type": "ALTER TABLE books ADD COLUMN entry_type TEXT",
        "original_post_url": "ALTER TABLE books ADD COLUMN original_post_url TEXT",
        "download_url": "ALTER TABLE books ADD COLUMN download_url TEXT",
        "detail_cached_at": "ALTER TABLE books ADD COLUMN detail_cached_at TEXT",
        "detail_attempts": "ALTER TABLE books ADD COLUMN detail_attempts INTEGER NOT NULL DEFAULT 0",
        "detail_last_attempt_at": "ALTER TABLE books ADD COLUMN detail_last_attempt_at TEXT",
        "detail_last_error": "ALTER TABLE books ADD COLUMN detail_last_error TEXT",
        "associated_names": "ALTER TABLE books ADD COLUMN associated_names TEXT",
        "genres": "ALTER TABLE books ADD COLUMN genres TEXT",
        "synopsis": "ALTER TABLE books ADD COLUMN synopsis TEXT",
        "source_order": "ALTER TABLE books ADD COLUMN source_order INTEGER",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            connection.execute(statement)
    volume_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(series_volumes)").fetchall()
    }
    if "release_group" not in volume_columns:
        connection.execute("ALTER TABLE series_volumes ADD COLUMN release_group TEXT")
    status_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(series_status)").fetchall()
    }
    if "metadata_checked_at" not in status_columns:
        connection.execute("ALTER TABLE series_status ADD COLUMN metadata_checked_at TEXT")
    if "unavailable_at" not in status_columns:
        connection.execute("ALTER TABLE series_status ADD COLUMN unavailable_at TEXT")
    if "source_modified_at" not in status_columns:
        connection.execute("ALTER TABLE series_status ADD COLUMN source_modified_at TEXT")
    if "source_change_pending" not in status_columns:
        connection.execute(
            "ALTER TABLE series_status ADD COLUMN source_change_pending INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_books_original_post_cached
        ON books(original_post_url, cached_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_books_original_post_published
        ON books(original_post_url, published_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_books_download_url
        ON books(download_url)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_books_entry_type_published
        ON books(entry_type, published_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_series_preferences_flags
        ON series_preferences(series_url, hidden, favorite)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_series_volumes_download
        ON series_volumes(download_url, position)
        """
    )
    fts_available = initialize_books_fts(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO series_status (series_url, checked_at)
        SELECT series_url, MAX(cached_at)
        FROM series_volumes
        GROUP BY series_url
        """
    )
    connection.executemany(
        "DELETE FROM books WHERE url = ?",
        [(urllib.parse.urljoin(BASE_URL, path),) for path in EXCLUDED_SEARCH_PATHS],
    )
    return fts_available
