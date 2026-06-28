import { elements } from "./elements.js";
import {
  defaultSort,
  state,
  validFilters,
  validSorts,
} from "./state.js";

export function updateFilterButtons() {
  let availableCategoryCount = 0;
  elements.filterButtons.forEach((button) => {
    const filter = button.dataset.filter;
    button.hidden = filter !== "all" && !(state.filterCounts[filter] || 0);
    if (filter !== "all" && !button.hidden) availableCategoryCount += 1;
    button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
  });
  elements.entryFilters.hidden = availableCategoryCount < 2;
}

export function updateSourceSearchLink() {
  const url = new URL("https://jnovels.com/");
  const query = elements.search.value.trim();
  url.searchParams.set("s", query ? `epub ${query}` : "epub");
  elements.sourceSearch.href = url;
}

export function updateSearchClearButton() {
  elements.searchClear.hidden = !elements.search.value;
}

export function applyViewFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = Number.parseInt(params.get("page") || "1", 10);
  const sort = params.get("sort") || defaultSort;
  const entryFilter = params.get("filter") || "all";

  elements.search.value = params.get("q") || "";
  state.entryUrl = params.get("entry") || null;
  updateSearchClearButton();
  elements.sort.value = validSorts.has(sort) ? sort : defaultSort;
  state.filter = validFilters.has(entryFilter) ? entryFilter : "all";
  state.page = Number.isNaN(page) ? 1 : Math.max(page, 1);
  updateSourceSearchLink();
  updateFilterButtons();
}

export function syncViewUrl(mode = "replace") {
  const url = new URL(window.location.href);
  ["q", "sort", "filter", "page", "entry"].forEach((key) => url.searchParams.delete(key));

  const query = elements.search.value.trim();
  if (query) url.searchParams.set("q", query);
  if (elements.sort.value !== defaultSort) url.searchParams.set("sort", elements.sort.value);
  if (state.filter !== "all") url.searchParams.set("filter", state.filter);
  if (state.page > 1) url.searchParams.set("page", String(state.page));
  if (state.entryUrl) url.searchParams.set("entry", state.entryUrl);

  const method = mode === "push" ? "pushState" : "replaceState";
  window.history[method]({}, "", url);
}
