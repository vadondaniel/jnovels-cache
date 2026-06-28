export const state = {
  books: [],
  pollTimer: null,
  searchTimer: null,
  page: 1,
  pageSize: 24,
  pages: 1,
  total: 0,
  loadSequence: 0,
  health: {},
  cacheCount: 0,
  filter: "all",
  filterCounts: {},
  syncNotice: null,
  entryUrl: null,
};

export const defaultSort = "published_desc";
export const searchDebounceMs = 400;
export const validSorts = new Set([
  "published_desc",
  "published_asc",
  "title_asc",
  "title_desc",
  "cached_desc",
]);
export const validFilters = new Set([
  "all",
  "series",
  "volumes",
  "pending",
  "favorites",
  "hidden",
]);
