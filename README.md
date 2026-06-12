# JNovels Cache

A local web app for browsing cached JNovels EPUB posts and series catalogs.
It stores metadata and links, not EPUB files.

## Requirements

- Python 3.10 or newer
- Internet access while syncing

## Start

On Windows, double-click `run.bat`.

For a tray icon with controls to open the app, restart the server, configure
Windows startup, or exit, use `run-tray.bat`.

Or start it from a terminal:

```powershell
python server.py --open
```

The app is available at <http://127.0.0.1:8765>.

To use a different port:

```powershell
python server.py --port 9000 --open
```

## Sync

Open **Sync cache** and choose the mode that matches what you need:

| Mode | Use it for |
| --- | --- |
| **Check for new entries** | Add recent posts, stopping when cached results are reached. |
| **Add older entries** | Continue building the cache from older search pages. |
| **Discover indexed series** | Add a limited batch of series pages missing from the local cache. |
| **Repair missing details** | Retry entries missing download links, series links, or catalogs. |
| **Refresh series data** | Update catalogs and fill missing genres, associated names, and synopses. |
| **Scan a page range** | Rescan specific JNovels search pages. |

Completed work is saved throughout a sync. If the server is interrupted, it
recovers that progress when restarted.

## Local Data

Cached data is stored in `jnovels_cache.db`.

- To back up the cache, stop the app and copy `jnovels_cache.db`.
- To reset the cache, stop the app and delete `jnovels_cache.db`.

## Troubleshooting

**The browser does not open**

Open <http://127.0.0.1:8765> manually.

**The server does not start**

Confirm Python is installed:

```powershell
python --version
```

If port 8765 is already in use, start the app with another port:

```powershell
python server.py --port 9000 --open
```

**An entry has no download or catalog**

Open the cache health indicator and retry the entry, or run **Repair missing
details**. Use **Refresh series data** when a catalog or its descriptive
information is missing.
