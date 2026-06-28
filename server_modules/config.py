from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "jnovels_cache.sqlite"
BASE_URL = "https://jnovels.com"
USER_AGENT = "JNovelsCache/1.0 (+local personal metadata cache)"
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100
AUTO_REPAIR_LIMIT = 20
AUTO_REPAIR_MAX_ATTEMPTS = 3
AUTO_SERIES_REFRESH_LIMIT = 5
AUTO_SERIES_INDEX_LIMIT = 3
AUTO_SYNC_COOLDOWN_SECONDS = 10 * 60
SERIES_STALE_DAYS = 30
SERIES_INDEX_REFRESH_HOURS = 24
SYNC_CHECKPOINT_KEY = "active_sync"
LEGACY_REPARSE_KEY = "legacy_format_reparse_v1"
LEGACY_VOLUME_RECONCILE_KEY = "legacy_volume_reconcile_v1"
CATALOG_RECONCILE_KEY = "catalog_reconcile_v7"
SERIES_METADATA_PARSER_KEY = "series_metadata_parser_version"
SERIES_METADATA_PARSER_VERSION = "9"
SERIES_INDEX_URL = f"{BASE_URL}/light-novel-epub-jp/"
SERIES_INDEX_REFRESH_KEY = "series_index_last_checked_at"
EXCLUDED_SEARCH_PATHS = {
    "/light-novel-epub-jp/",
    "/top-light-novels-to-read/",
}
SERIES_RELEASE_GROUP_OVERRIDES = {
    "https://jnovels.com/no-game-no-life-all-volumes-epub": "Official translation",
    "https://jnovels.com/classroom-of-the-elite-light-novel-all-volumes-epub": (
        "Official translation"
    ),
}
PARTIAL_GROUP_PRESERVE_SERIES = {
    "https://jnovels.com/download-eiyuu-kyoushitsu-epub",
}
MERGED_CATALOG_GROUP_OVERRIDES = {
    "https://jnovels.com/download-eiyuu-kyoushitsu-epub": (
        "Official translation",
        "Fan translation",
    ),
}
SAO_INDEX_URL = "https://jnovels.com/download-sword-art-online-light-novel-epub/"
SHARED_INDEX_SUBSERIES = {
    "sword art online progressive": SAO_INDEX_URL,
    "sword art online alternative gun gale online": SAO_INDEX_URL,
}
