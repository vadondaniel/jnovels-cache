import { request } from "./app/api.js";
import { bindBookControls, renderBooks } from "./app/books.js";
import { elements } from "./app/elements.js";
import { showToast } from "./app/notifications.js";
import { renderPagination } from "./app/pagination.js";
import {
  searchDebounceMs,
  state,
  validFilters,
} from "./app/state.js";
import {
  bindSyncControls,
  loadStatus,
  startPageLoadCatchup,
  updateSyncModes,
} from "./app/sync.js";
import {
  applyViewFromUrl,
  syncViewUrl,
  updateFilterButtons,
  updateSearchClearButton,
  updateSourceSearchLink,
} from "./app/view.js";

async function loadBooks({ scroll = false, historyMode = null } = {}) {
  const sequence = ++state.loadSequence;
  const params = new URLSearchParams({
    q: state.entryUrl ? "" : elements.search.value.trim(),
    sort: elements.sort.value,
    filter: state.filter,
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  if (state.entryUrl) params.set("entry_url", state.entryUrl);
  const payload = await request(`/api/books?${params}`);
  if (sequence !== state.loadSequence) return;
  state.filterCounts = payload.filter_counts || {};
  if (!(state.filterCounts[state.filter] || 0) && state.filter !== "all") {
    state.filter = "all";
  }
  updateFilterButtons();
  state.books = payload.books;
  renderBooks(state.books, payload.count);
  renderPagination(payload);
  if (historyMode) syncViewUrl(historyMode);
  if (scroll) elements.grid.scrollIntoView({ behavior: "smooth", block: "start" });
}

elements.search.addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.entryUrl = null;
  state.page = 1;
  updateSearchClearButton();
  updateSourceSearchLink();
  state.searchTimer = setTimeout(
    () => loadBooks({ historyMode: "replace" }).catch((error) => showToast(error.message, true)),
    searchDebounceMs,
  );
});

elements.searchClear.addEventListener("click", () => {
  if (!elements.search.value && !state.entryUrl) return;
  clearTimeout(state.searchTimer);
  elements.search.value = "";
  state.entryUrl = null;
  state.page = 1;
  updateSearchClearButton();
  updateSourceSearchLink();
  elements.search.focus();
  loadBooks({ historyMode: "replace" }).catch((error) => showToast(error.message, true));
});

elements.sort.addEventListener("change", () => {
  state.page = 1;
  loadBooks({ historyMode: "push" }).catch((error) => showToast(error.message, true));
});

elements.filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const nextFilter = button.dataset.filter;
    if (!validFilters.has(nextFilter) || nextFilter === state.filter) return;
    state.filter = nextFilter;
    state.entryUrl = null;
    state.page = 1;
    updateFilterButtons();
    loadBooks({ historyMode: "push" }).catch((error) => showToast(error.message, true));
  });
});

function goToPage(page, historyMode = "push") {
  const nextPage = Math.min(Math.max(page, 1), state.pages);
  if (nextPage === state.page) {
    elements.pageNumber.value = String(state.page);
    return;
  }
  state.page = nextPage;
  loadBooks({ scroll: true, historyMode }).catch((error) => showToast(error.message, true));
}

elements.pageFirst.addEventListener("click", () => goToPage(1));
elements.pagePrevious.addEventListener("click", () => goToPage(state.page - 1));
elements.pageNext.addEventListener("click", () => goToPage(state.page + 1));
elements.pageLast.addEventListener("click", () => goToPage(state.pages));

function jumpToEnteredPage() {
  const page = Number.parseInt(elements.pageNumber.value, 10);
  if (Number.isNaN(page)) {
    elements.pageNumber.value = String(state.page);
    return;
  }
  goToPage(page);
}

elements.pageNumber.addEventListener("change", jumpToEnteredPage);
elements.pageNumber.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    jumpToEnteredPage();
    elements.pageNumber.blur();
  }
});

window.addEventListener("popstate", () => {
  clearTimeout(state.searchTimer);
  applyViewFromUrl();
  loadBooks().catch((error) => showToast(error.message, true));
});

bindBookControls({ loadBooks, loadStatus });
bindSyncControls({ loadBooks });
applyViewFromUrl();
updateSyncModes();
Promise.all([loadBooks({ historyMode: "replace" }), loadStatus()])
  .then(() => startPageLoadCatchup().catch((error) => {
    console.warn("Automatic catch-up sync could not start:", error);
  }))
  .catch((error) => showToast(error.message, true));
