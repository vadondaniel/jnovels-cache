const state = {
  books: [],
  pollTimer: null,
  searchTimer: null,
  page: 1,
  pageSize: 24,
  pages: 1,
  total: 0,
  loadSequence: 0,
  health: {},
  filter: "all",
};

const defaultSort = "published_desc";
const validSorts = new Set(["published_desc", "published_asc", "title_asc", "title_desc", "cached_desc"]);
const validFilters = new Set(["all", "series", "volumes", "pending"]);

const elements = {
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  grid: document.querySelector("#book-grid"),
  count: document.querySelector("#result-count"),
  lastSync: document.querySelector("#last-sync"),
  filterButtons: [...document.querySelectorAll("[data-filter]")],
  cacheHealth: document.querySelector("#cache-health"),
  syncStatus: document.querySelector("#sync-status"),
  syncToggle: document.querySelector("#sync-toggle"),
  syncDialog: document.querySelector("#sync-dialog"),
  syncClose: document.querySelector("#sync-close"),
  syncCancel: document.querySelector("#sync-cancel"),
  syncForm: document.querySelector("#sync-form"),
  syncMode: document.querySelector("#sync-mode"),
  startPageField: document.querySelector("#start-page-field"),
  startPage: document.querySelector("#start-page"),
  maxPagesLabel: document.querySelector("#max-pages-label"),
  maxPages: document.querySelector("#max-pages"),
  delay: document.querySelector("#delay"),
  syncHelp: document.querySelector("#sync-help"),
  repairDialog: document.querySelector("#repair-dialog"),
  repairSummary: document.querySelector("#repair-summary"),
  repairList: document.querySelector("#repair-list"),
  repairClose: document.querySelector("#repair-close"),
  repairAll: document.querySelector("#repair-all"),
  empty: document.querySelector("#empty-state"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyText: document.querySelector("#empty-text"),
  emptySync: document.querySelector("#empty-sync"),
  pagination: document.querySelector("#pagination"),
  pageFirst: document.querySelector("#page-first"),
  pagePrevious: document.querySelector("#page-previous"),
  pageNumber: document.querySelector("#page-number"),
  pageTotal: document.querySelector("#page-total"),
  pageNext: document.querySelector("#page-next"),
  pageLast: document.querySelector("#page-last"),
  template: document.querySelector("#book-template"),
  toast: document.querySelector("#toast"),
  catalogDialog: document.querySelector("#catalog-dialog"),
  catalogTitle: document.querySelector("#catalog-title"),
  catalogCount: document.querySelector("#catalog-count"),
  catalogList: document.querySelector("#catalog-list"),
  catalogSeriesLink: document.querySelector("#catalog-series-link"),
  catalogClose: document.querySelector("#catalog-close"),
};

function updateFilterButtons() {
  elements.filterButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
  });
}

function applyViewFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = Number.parseInt(params.get("page") || "1", 10);
  const sort = params.get("sort") || defaultSort;
  const entryFilter = params.get("filter") || "all";

  elements.search.value = params.get("q") || "";
  elements.sort.value = validSorts.has(sort) ? sort : defaultSort;
  state.filter = validFilters.has(entryFilter) ? entryFilter : "all";
  state.page = Number.isNaN(page) ? 1 : Math.max(page, 1);
  updateFilterButtons();
}

function syncViewUrl(mode = "replace") {
  const url = new URL(window.location.href);
  ["q", "sort", "filter", "page"].forEach((key) => url.searchParams.delete(key));

  const query = elements.search.value.trim();
  if (query) url.searchParams.set("q", query);
  if (elements.sort.value !== defaultSort) url.searchParams.set("sort", elements.sort.value);
  if (state.filter !== "all") url.searchParams.set("filter", state.filter);
  if (state.page > 1) url.searchParams.set("page", String(state.page));

  const method = mode === "push" ? "pushState" : "replaceState";
  window.history[method]({}, "", url);
}

function formatDate(value, options = { year: "numeric", month: "short", day: "numeric" }) {
  if (!value) return "Date unavailable";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.classList.add("is-visible");
  window.setTimeout(() => elements.toast.classList.remove("is-visible"), 3500);
}

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function renderBooks(books, total) {
  elements.grid.replaceChildren();
  const fragment = document.createDocumentFragment();

  books.forEach((book, index) => {
    const card = elements.template.content.firstElementChild.cloneNode(true);
    const coverLink = card.querySelector(".book-card__cover-link");
    const titleLink = card.querySelector("h2 a");
    const image = card.querySelector("img");
    const fallback = card.querySelector(".book-card__fallback");
    const type = card.querySelector(".book-card__type");
    const downloadLink = card.querySelector(".book-card__action--download");
    const seriesLink = card.querySelector(".book-card__action--series");
    const postLink = card.querySelector(".book-card__action--post");
    const catalogButton = card.querySelector(".book-card__action--catalog");

    titleLink.textContent = book.series_title;
    card.querySelector(".book-card__date").textContent = formatDate(book.published_at);
    card.style.animationDelay = `${Math.min(index * 18, 240)}ms`;

    type.textContent = book.volume_label
      || (book.entry_type === "series" ? "Series" : "Details pending");
    type.classList.toggle("is-pending", !book.entry_type);

    postLink.href = book.url;
    if (book.download_url) {
      downloadLink.href = book.download_url;
    } else {
      downloadLink.hidden = true;
    }
    if (book.original_post_url) {
      seriesLink.href = book.original_post_url;
    } else {
      seriesLink.hidden = true;
    }
    if (book.volume_count) {
      catalogButton.textContent = `Volumes · ${book.volume_count}`;
      catalogButton.addEventListener("click", () => openCatalog(book));
    } else {
      catalogButton.hidden = true;
    }

    if (book.entry_type === "series" && book.volume_count) {
      coverLink.href = "#";
      titleLink.href = "#";
      coverLink.setAttribute("aria-label", `Browse cached volumes for ${book.series_title}`);
      titleLink.title = "Browse cached volumes";
      const openBookCatalog = (event) => {
        event.preventDefault();
        openCatalog(book);
      };
      coverLink.addEventListener("click", openBookCatalog);
      titleLink.addEventListener("click", openBookCatalog);
    } else {
      const primaryUrl = book.download_url || book.url;
      coverLink.href = primaryUrl;
      titleLink.href = primaryUrl;
      const action = book.download_url ? "Download" : "Open post for";
      coverLink.setAttribute("aria-label", `${action} ${book.series_title}`);
      titleLink.title = book.download_url ? "Open download" : "Open post";
    }

    if (book.image_url) {
      image.src = book.image_url;
      image.alt = `Cover for ${book.title}`;
      image.addEventListener("error", () => {
        image.hidden = true;
        fallback.style.display = "grid";
      });
    } else {
      image.hidden = true;
      fallback.style.display = "grid";
    }
    fragment.append(card);
  });

  elements.grid.append(fragment);
  const hasQuery = Boolean(elements.search.value.trim());
  const hasFilter = state.filter !== "all";
  elements.empty.hidden = total !== 0;
  elements.emptyTitle.textContent = hasQuery || hasFilter ? "No matching entries" : "The cache is empty";
  elements.emptyText.textContent = hasQuery || hasFilter
    ? "Try a different search or filter."
    : "Sync the newest JNovels EPUB entries to get started.";
  elements.emptySync.hidden = hasQuery || hasFilter;
}

function renderPagination(payload) {
  state.page = payload.page;
  state.pageSize = payload.page_size;
  state.pages = payload.pages;
  state.total = payload.count;

  if (payload.count) {
    const first = (payload.page - 1) * payload.page_size + 1;
    const last = first + payload.books.length - 1;
    elements.count.textContent =
      `${first}–${last} of ${payload.count} ${payload.count === 1 ? "entry" : "entries"}`;
  } else {
    elements.count.textContent = "0 entries";
  }

  elements.pagination.hidden = payload.pages <= 1;
  elements.pageNumber.value = String(payload.page);
  elements.pageNumber.max = String(payload.pages);
  elements.pageTotal.textContent = `of ${payload.pages}`;
  elements.pageFirst.disabled = payload.page === 1;
  elements.pagePrevious.disabled = payload.page === 1;
  elements.pageNext.disabled = payload.page === payload.pages;
  elements.pageLast.disabled = payload.page === payload.pages;
}

async function openCatalog(book) {
  elements.catalogTitle.textContent = book.series_title;
  elements.catalogCount.textContent = "Loading volumes...";
  elements.catalogList.replaceChildren();
  elements.catalogSeriesLink.href = book.series_url;
  elements.catalogDialog.showModal();

  try {
    const payload = await request(`/api/series?${new URLSearchParams({ url: book.series_url })}`);
    elements.catalogCount.textContent =
      `${payload.count} cached ${payload.count === 1 ? "volume" : "volumes"}`;
    const fragment = document.createDocumentFragment();
    const grouped = new Map();
    payload.volumes.forEach((volume) => {
      const groupName = volume.release_group || "";
      if (!grouped.has(groupName)) grouped.set(groupName, []);
      grouped.get(groupName).push(volume);
    });
    grouped.forEach((volumes, groupName) => {
      const group = document.createElement("section");
      const list = document.createElement("div");
      group.className = "catalog-dialog__group";
      list.className = "catalog-dialog__volume-grid";
      if (groupName) {
        const heading = document.createElement("h3");
        heading.textContent = groupName;
        group.append(heading);
      }
      volumes.forEach((volume) => {
        const link = document.createElement("a");
        const text = document.createElement("span");
        const label = document.createElement("strong");
        const date = document.createElement("small");
        const arrow = document.createElement("span");
        link.href = volume.download_url;
        link.target = "_blank";
        link.rel = "noopener";
        label.textContent = volume.label;
        date.textContent = volume.published_at
          ? `Released ${formatDate(volume.published_at)}`
          : "Release date unavailable";
        date.classList.toggle("is-known", Boolean(volume.published_at));
        arrow.textContent = "↗";
        arrow.setAttribute("aria-hidden", "true");
        text.append(label, date);
        link.append(text, arrow);
        list.append(link);
      });
      group.append(list);
      fragment.append(group);
    });
    elements.catalogList.append(fragment);
  } catch (error) {
    elements.catalogCount.textContent = "Could not load these volumes.";
    showToast(error.message, true);
  }
}

elements.catalogClose.addEventListener("click", () => elements.catalogDialog.close());
elements.catalogDialog.addEventListener("click", (event) => {
  if (event.target === elements.catalogDialog) elements.catalogDialog.close();
});

async function loadBooks({ scroll = false, historyMode = null } = {}) {
  const sequence = ++state.loadSequence;
  const params = new URLSearchParams({
    q: elements.search.value.trim(),
    sort: elements.sort.value,
    filter: state.filter,
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  const payload = await request(`/api/books?${params}`);
  if (sequence !== state.loadSequence) return;
  state.books = payload.books;
  renderBooks(state.books, payload.count);
  renderPagination(payload);
  if (historyMode) syncViewUrl(historyMode);
  if (scroll) elements.grid.scrollIntoView({ behavior: "smooth", block: "start" });
}

function updateStatus(payload) {
  const sync = payload.sync;
  const health = payload.health || {};
  state.health = health;
  const lastSync = payload.metadata.last_sync_at;
  elements.lastSync.textContent = lastSync ? `Last synced ${formatDate(lastSync)}` : "Not synced yet";
  elements.syncToggle.disabled = sync.running;
  elements.syncToggle.classList.toggle("is-running", sync.running);
  elements.cacheHealth.hidden = sync.running || !health.pending_details;
  const failedText = health.failed_retries ? ` · ${health.failed_retries} failed` : "";
  elements.cacheHealth.textContent = `${health.pending_details || 0} pending${failedText}`;
  elements.cacheHealth.title = health.paused_retries
    ? `${health.paused_retries} entries paused after repeated failures. Open repair options.`
    : "Open repair options";

  if (sync.running) {
    if (sync.phase === "series") {
      elements.syncStatus.textContent =
        `Updating series ${Math.min(sync.series_completed + 1, sync.series_total)} of ${sync.series_total}`;
    } else if (sync.phase === "details") {
      elements.syncStatus.textContent =
        `Checking entry ${Math.min(sync.details_completed + 1, sync.details_total)} of ${sync.details_total}`;
    } else {
      const prefix = sync.mode === "extend"
        ? "Finding older uncached entries"
        : sync.mode === "catchup"
          ? "Checking newest entries"
          : "Scanning";
      elements.syncStatus.textContent =
        `${prefix} · page ${sync.page} · ${sync.new_items} new`;
    }
    elements.syncStatus.classList.add("is-running");
  } else {
    elements.syncStatus.classList.remove("is-running");
    elements.syncStatus.textContent = sync.error
      ? `Last sync failed: ${sync.error}`
      : lastSync
        ? `${payload.count} cached ${payload.count === 1 ? "entry" : "entries"}`
        : "";
  }
}

function updateSyncMode() {
  const mode = elements.syncMode.value;
  elements.startPageField.hidden = mode !== "manual";
  if (mode === "catchup") {
    elements.maxPagesLabel.textContent = "Page limit";
    elements.maxPages.value = "20";
    elements.maxPages.max = "200";
    elements.syncHelp.textContent =
      "Checks newest entries first and stops after reaching entries already in the cache.";
  } else if (mode === "extend") {
    elements.maxPagesLabel.textContent = "Pages to add";
    elements.maxPages.value = "10";
    elements.maxPages.max = "200";
    elements.syncHelp.textContent =
      "Finds the oldest cached search page, then adds the next older pages.";
  } else if (mode === "repair") {
    elements.maxPagesLabel.textContent = "Entry limit";
    elements.maxPages.value = "1000";
    elements.maxPages.max = "2000";
    elements.syncHelp.textContent =
      "Rechecks cached entries with missing details, then refreshes any linked series pages.";
  } else {
    elements.maxPagesLabel.textContent = "Pages to scan";
    elements.maxPages.value = "6";
    elements.maxPages.max = "200";
    elements.syncHelp.textContent =
      "Checks an exact page range, including entries already in the cache.";
  }
}

elements.syncMode.addEventListener("change", updateSyncMode);

async function loadStatus() {
  try {
    const payload = await request("/api/status");
    const wasRunning = elements.syncStatus.classList.contains("is-running");
    updateStatus(payload);

    if (payload.sync.running) {
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(loadStatus, 850);
    } else if (wasRunning) {
      await loadBooks();
      showToast(payload.sync.error ? "Sync failed." : "Cache updated.", Boolean(payload.sync.error));
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

function openSyncOptions() {
  if (!elements.syncDialog.open) elements.syncDialog.showModal();
  elements.syncMode.focus();
}

elements.syncToggle.addEventListener("click", openSyncOptions);

async function retryEntry(url) {
  await request("/api/repair-entry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, delay: Number(elements.delay.value) }),
  });
  elements.repairDialog.close();
  showToast("Repair started.");
  await loadStatus();
}

async function openRepairStatus() {
  elements.repairSummary.textContent = "Loading repair status...";
  elements.repairList.replaceChildren();
  if (!elements.repairDialog.open) elements.repairDialog.showModal();

  try {
    const payload = await request("/api/repair-failures");
    const pending = state.health.pending_details || 0;
    elements.repairSummary.textContent = payload.count
      ? `${pending} pending entries · ${payload.count} with recorded failures`
      : `${pending} entries are waiting for details. No recorded failures.`;

    if (!payload.count) {
      const empty = document.createElement("p");
      empty.className = "repair-dialog__empty";
      empty.textContent = "Automatic repair will continue during normal syncs.";
      elements.repairList.append(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    payload.failures.forEach((failure) => {
      const row = document.createElement("article");
      const body = document.createElement("div");
      const title = document.createElement("a");
      const meta = document.createElement("p");
      const error = document.createElement("p");
      const retry = document.createElement("button");

      row.className = "repair-failure";
      title.href = failure.url;
      title.target = "_blank";
      title.rel = "noopener";
      title.textContent = failure.title;
      meta.className = "repair-failure__meta";
      meta.textContent =
        `${failure.detail_attempts} ${failure.detail_attempts === 1 ? "attempt" : "attempts"} · Last tried ${formatDate(failure.detail_last_attempt_at)}`;
      error.className = "repair-failure__error";
      error.textContent = failure.detail_last_error;
      retry.type = "button";
      retry.textContent = "Retry";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        try {
          await retryEntry(failure.url);
        } catch (requestError) {
          retry.disabled = false;
          showToast(requestError.message, true);
        }
      });

      body.append(title, meta, error);
      row.append(body, retry);
      fragment.append(row);
    });
    elements.repairList.append(fragment);
  } catch (error) {
    elements.repairSummary.textContent = "Could not load repair status.";
    showToast(error.message, true);
  }
}

elements.cacheHealth.addEventListener("click", openRepairStatus);
elements.repairClose.addEventListener("click", () => elements.repairDialog.close());
elements.repairDialog.addEventListener("click", (event) => {
  if (event.target === elements.repairDialog) elements.repairDialog.close();
});
elements.repairAll.addEventListener("click", () => {
  elements.repairDialog.close();
  elements.syncMode.value = "repair";
  updateSyncMode();
  openSyncOptions();
});
elements.syncClose.addEventListener("click", () => elements.syncDialog.close());
elements.syncCancel.addEventListener("click", () => elements.syncDialog.close());
elements.syncDialog.addEventListener("click", (event) => {
  if (event.target === elements.syncDialog) elements.syncDialog.close();
});

elements.emptySync.addEventListener("click", openSyncOptions);

elements.syncForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: elements.syncMode.value,
        start_page: Number(elements.startPage.value),
        max_pages: Number(elements.maxPages.value),
        delay: Number(elements.delay.value),
      }),
    });
    elements.syncDialog.close();
    showToast("Sync started.");
    await loadStatus();
  } catch (error) {
    showToast(error.message, true);
  }
});

elements.search.addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.page = 1;
  state.searchTimer = setTimeout(
    () => loadBooks({ historyMode: "replace" }).catch((error) => showToast(error.message, true)),
    160,
  );
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

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    elements.search.focus();
  }
});

applyViewFromUrl();
updateSyncMode();
Promise.all([loadBooks({ historyMode: "replace" }), loadStatus()])
  .catch((error) => showToast(error.message, true));
