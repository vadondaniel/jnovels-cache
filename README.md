# JNovels Cache

A small local web app that caches metadata from the public JNovels EPUB search
pages. It stores titles, post URLs, image URLs, publication dates, cache
timestamps, series-index links, and current-volume download links in a local
SQLite database.

## Run

Double-click `run.bat`, or run:

```powershell
python server.py --open
```

The app opens at <http://127.0.0.1:8765>.

## Use

1. Select **Sync cache**.
2. Choose a sync mode:
   - **Check for new entries** starts at page 1 and stops when it reaches
     already-cached results.
   - **Add older entries** estimates the current cached frontier and adds the
     requested number of older uncached pages.
   - **Scan a page range** checks an explicit starting page and page count.
3. Select **Start sync**. The app scans search pages, then visits each result
   post to identify its entry type and associated links.
4. Search and sort the cached results without waiting for JNovels search pages.
   Results are shown 24 at a time; use the page field to jump directly to any
   result page.

Cards can expose three actions:

- **Download** opens the external download URL found in the post.
- **Series page** opens the original/main series post for standalone volume entries.
- **Volumes** opens a separate local dialog containing every cached volume
  download for that series. When the corresponding standalone volume post is
  cached, the volume list also shows its publication date. Groups such as
  **Official translation** and **Fan translation** are preserved when the
  source page distinguishes them.
- **Entry page** opens the specific JNovels post represented by the cached card.

The crawler makes requests sequentially and enforces a minimum delay of 0.5
seconds. Re-running a sync updates existing records instead of duplicating them.
Only metadata and URLs are cached; EPUB files and cover images are not
downloaded.

## Test

```powershell
python -m unittest discover -s tests -v
```
