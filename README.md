# JNovels Cache

A local web app for browsing cached JNovels EPUB posts and series catalogs.
It stores metadata and links only; it does not download EPUB files or cover
images.

## Requirements

- Python 3.10 or newer
- Internet access while syncing

No third-party Python packages are required.

## Start

On Windows, double-click `run.bat`.

Alternatively, run:

```powershell
python server.py --open
```

The app is available at <http://127.0.0.1:8765>. Keep the terminal window open
while using it. Stop the server with `Ctrl+C`.

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
| **Repair missing details** | Retry entries missing download links, series links, or catalogs. |
| **Refresh stale series** | Update previously cached series catalogs with newer volumes. |
| **Scan a page range** | Rescan specific JNovels search pages. |

Syncing can take time because pages are requested sequentially. Existing
entries are updated rather than duplicated.

If the server stops during a sync, saved work is resumed the next time it
starts. Run the same sync again if the interrupted search did not reach all
intended pages.

## Browse

- Search cached titles from the main search field.
- Use **Series**, **Volumes**, and **Pending** to filter results.
- Use the page control to jump directly to a result page.
- **Download** opens the cached external EPUB download link.
- **Volumes** opens the cached downloads for a series.
- **Series page** and **Entry page** open the relevant JNovels pages.

The pending or stale status indicator opens repair options and individual
failure details.

## Local Data

All cached data is stored in `jnovels_cache.db` beside `server.py`.

- Back up the cache by copying `jnovels_cache.db` while the server is stopped.
- Reset the cache by stopping the server and deleting `jnovels_cache.db`.
  A new empty database is created on the next start.

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
details**. Use **Refresh stale series** when a known series is missing newer
volumes.

## Tests

```powershell
python -m pytest -q
```
