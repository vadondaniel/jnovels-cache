import { request } from "./app/api.js";
import { bindCatalogDialog, openCatalog } from "./app/catalog.js";
import { openDetails } from "./app/details.js";
import { elements } from "./app/elements.js";
import { estimateSyncTime, formatDate } from "./app/formatters.js";
import { showToast } from "./app/notifications.js";
import { renderPagination } from "./app/pagination.js";
import {
  searchDebounceMs,
  state,
  validFilters,
} from "./app/state.js";
import {
  applyViewFromUrl,
  syncViewUrl,
  updateFilterButtons,
  updateSearchClearButton,
  updateSourceSearchLink,
} from "./app/view.js";

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
    const detailsButton = card.querySelector(".book-card__details");
    const favoriteButton = card.querySelector(".book-card__quick-action--favorite");
    const hideButton = card.querySelector(".book-card__quick-action--hide");
    const refreshButton = card.querySelector(".book-card__quick-action--refresh");
    const seriesActionUrl = book.series_url || book.original_post_url || book.url;

    titleLink.textContent = book.series_title;
    card.querySelector(".book-card__date").textContent = formatDate(book.published_at);
    card.style.animationDelay = `${Math.min(index * 18, 240)}ms`;
    card.classList.toggle("is-favorite", book.is_favorite);

    type.textContent = book.volume_label
      || (book.is_standalone
        ? "Standalone"
        : book.entry_type === "series"
          ? "Series"
          : book.entry_type === "volume"
            ? "Volume"
            : "Details pending");
    type.classList.toggle("is-pending", !book.entry_type);

    favoriteButton.classList.toggle("is-active", book.is_favorite);
    favoriteButton.setAttribute("aria-pressed", String(book.is_favorite));
    favoriteButton.setAttribute(
      "aria-label",
      book.is_favorite ? "Remove series from favorites" : "Favorite series",
    );
    favoriteButton.title = favoriteButton.getAttribute("aria-label");
    favoriteButton.addEventListener("click", async () => {
      favoriteButton.disabled = true;
      try {
        await request("/api/series-preference", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            series_url: seriesActionUrl,
            favorite: !book.is_favorite,
          }),
        });
        if (
          state.filter === "favorites"
          && book.is_favorite
          && (state.filterCounts.favorites || 0) <= 1
        ) {
          state.filter = "all";
        }
        await loadBooks({ historyMode: "replace" });
        showToast(book.is_favorite ? "Removed from favorites." : "Series favorited.");
      } catch (error) {
        favoriteButton.disabled = false;
        showToast(error.message, true);
      }
    });

    hideButton.classList.toggle("is-active", book.is_hidden);
    hideButton.setAttribute("aria-pressed", String(book.is_hidden));
    hideButton.setAttribute(
      "aria-label",
      book.is_hidden ? "Restore hidden series" : "Hide series",
    );
    hideButton.title = hideButton.getAttribute("aria-label");
    hideButton.addEventListener("click", async () => {
      hideButton.disabled = true;
      try {
        await request("/api/series-preference", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            series_url: seriesActionUrl,
            hidden: !book.is_hidden,
          }),
        });
        if (
          state.filter === "hidden"
          && book.is_hidden
          && (state.filterCounts.hidden || 0) <= 1
        ) {
          state.filter = "all";
        }
        await loadBooks({ historyMode: "replace" });
        showToast(book.is_hidden ? "Series restored." : "Series hidden. Use Hidden to restore it.");
      } catch (error) {
        hideButton.disabled = false;
        showToast(error.message, true);
      }
    });

    refreshButton.addEventListener("click", async () => {
      refreshButton.disabled = true;
      refreshButton.classList.add("is-running");
      try {
        await request("/api/refresh-series", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ series_url: seriesActionUrl, delay: 1 }),
        });
        showToast("Series refresh started.");
        await loadStatus();
      } catch (error) {
        refreshButton.disabled = false;
        refreshButton.classList.remove("is-running");
        showToast(error.message, true);
      }
    });

    postLink.href = book.url;
    if (book.download_url) {
      downloadLink.href = book.download_url;
    } else {
      downloadLink.hidden = true;
    }
    if (/^https?:\/\//i.test(book.series_page_url || "")) {
      seriesLink.href = book.series_page_url;
    } else {
      seriesLink.hidden = true;
    }
    if (book.volume_count > 1) {
      const catalogName = book.catalog_kind === "downloads" ? "Downloads" : "Volumes";
      const catalogCount = book.catalog_count || book.volume_count;
      catalogButton.textContent = `${catalogName} · ${catalogCount}`;
      catalogButton.addEventListener("click", () => openCatalog(book, { showCachedReference }));
    } else {
      catalogButton.hidden = true;
    }
    if (
      book.volume_count <= 1
      && (book.synopsis || book.associated_names.length || book.genres.length)
    ) {
      detailsButton.addEventListener("click", () => openDetails(book));
    } else {
      detailsButton.hidden = true;
    }

    if (book.entry_type === "series" && book.volume_count > 1) {
      coverLink.href = "#";
      titleLink.href = "#";
      const catalogName = book.catalog_kind === "downloads" ? "downloads" : "volumes";
      coverLink.setAttribute("aria-label", `Browse cached ${catalogName} for ${book.series_title}`);
      titleLink.title = `Browse cached ${catalogName}`;
      const openBookCatalog = (event) => {
        event.preventDefault();
        openCatalog(book, { showCachedReference });
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

async function showCachedReference(volume) {
  if (!volume.post_url) return;

  elements.catalogDialog.close();
  elements.search.value = volume.displayLabel || "";
  updateSearchClearButton();
  updateSourceSearchLink();
  state.filter = "all";
  state.entryUrl = volume.post_url;
  state.page = 1;
  await loadBooks({ scroll: true, historyMode: "push" });
}

bindCatalogDialog();
elements.detailsClose.addEventListener("click", () => elements.detailsDialog.close());
elements.detailsDialog.addEventListener("click", (event) => {
  if (event.target === elements.detailsDialog) elements.detailsDialog.close();
});

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

function updateStatus(payload) {
  const sync = payload.sync;
  const health = payload.health || {};
  state.health = health;
  state.cacheCount = payload.count || 0;
  updateSyncModes();
  if (sync.notice && sync.notice !== state.syncNotice) {
    state.syncNotice = sync.notice;
    showToast(sync.notice);
  }
  const lastSync = payload.metadata.last_sync_at;
  elements.lastSync.textContent = lastSync
    ? `Last synced ${formatDate(lastSync, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })}`
    : "Not synced yet";
  elements.syncToggle.disabled = sync.running;
  elements.syncToggle.classList.toggle("is-running", sync.running);
  elements.cacheHealth.hidden =
    sync.running || (!health.pending_details && !health.refreshable_series);
  elements.repairAll.hidden = !health.pending_details;
  elements.refreshSeries.hidden = !health.refreshable_series;
  const failedText = health.failed_retries ? ` · ${health.failed_retries} failed` : "";
  const healthParts = [];
  if (health.pending_details) healthParts.push(`${health.pending_details} pending${failedText}`);
  if (health.stale_series) healthParts.push(`${health.stale_series} stale series`);
  const metadataBackfill = Math.max(
    (health.refreshable_series || 0) - (health.stale_series || 0),
    0,
  );
  if (metadataBackfill) healthParts.push(`${metadataBackfill} series missing info`);
  elements.cacheHealth.textContent = healthParts.join(" · ");
  elements.cacheHealth.title = health.paused_retries
    ? `${health.paused_retries} entries paused after repeated failures. Open repair options.`
    : "Open repair options";

  if (sync.running) {
    elements.syncStatus.hidden = false;
    const detailLines = [];
    const queuedDetails = sync.queued_details || 0;
    const queuedSeries = sync.queued_series || 0;
    if (sync.phase === "index") {
      elements.syncStatusLabel.textContent = "Checking series index";
    } else if (sync.phase === "series") {
      elements.syncStatusLabel.textContent =
        `Updating series ${Math.min(sync.series_completed + 1, sync.series_total)} of ${sync.series_total}`;
      detailLines.push(`${queuedSeries} series remaining`);
    } else if (sync.phase === "details") {
      elements.syncStatusLabel.textContent =
        `Checking entry ${Math.min(sync.details_completed + 1, sync.details_total)} of ${sync.details_total}`;
      detailLines.push(`${queuedDetails} entries remaining`);
      if (queuedSeries) detailLines.push(`${queuedSeries} series queued next`);
    } else {
      const prefix = sync.mode === "extend"
        ? "Finding older uncached entries"
        : sync.mode === "catchup"
          ? "Checking newest entries"
          : "Scanning";
      elements.syncStatusLabel.textContent =
        `${prefix} · page ${sync.page} · ${sync.new_items} new`;
      const remainingPages = Math.max(
        (sync.pages_requested || 0) - (sync.pages_completed || 0),
        0,
      );
      detailLines.push(`${sync.pages_completed || 0} pages checked`);
      if (remainingPages) detailLines.push(`Up to ${remainingPages} pages remaining`);
      if (queuedDetails) detailLines.push(`${queuedDetails} entries queued next`);
    }
    const eta = estimateSyncTime(sync);
    if (eta) detailLines.push(eta);
    elements.syncStatusDetail.textContent = detailLines.join("\n");
    elements.syncStatus.tabIndex = 0;
    elements.syncStatus.classList.add("is-running");
  } else {
    elements.syncStatus.classList.remove("is-running");
    elements.syncStatus.hidden = !sync.error;
    elements.syncStatusLabel.textContent = sync.error
      ? `Last sync failed: ${sync.error}`
      : "";
    elements.syncStatusDetail.textContent = "";
    elements.syncStatus.removeAttribute("tabindex");
  }
}

function updateSyncModes(preferredMode = null) {
  const currentMode = preferredMode || elements.syncMode.value;
  const pending = state.health.pending_details || 0;
  const refreshableSeries = state.health.refreshable_series || 0;
  const indexedSeriesTotal = state.health.indexed_series_total || 0;
  const missingIndexedSeries = state.health.indexed_series_missing || 0;
  const seriesIndexLabel = missingIndexedSeries
    ? `Discover indexed series (${missingIndexedSeries})`
    : "Check series index";
  const seriesIndexModes = !indexedSeriesTotal || missingIndexedSeries
    ? [["series_discovery", seriesIndexLabel]]
    : [];
  const modes = state.cacheCount
    ? [
        ["catchup", "Check for new entries"],
        ["extend", "Add older entries"],
        ...seriesIndexModes,
        ...(pending ? [["repair", `Repair incomplete entries (${pending})`]] : []),
        ...(refreshableSeries
          ? [["series_refresh", `Refresh series data (${refreshableSeries})`]]
          : []),
        ["manual", "Scan specific pages"],
      ]
    : [
        ["catchup", "Build initial cache"],
        ...seriesIndexModes,
        ["manual", "Scan specific pages"],
      ];

  elements.syncMode.replaceChildren(
    ...modes.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
  const availableModes = new Set(modes.map(([value]) => value));
  elements.syncMode.value = availableModes.has(currentMode) ? currentMode : modes[0][0];
  updateSyncMode();
}

function updateSyncMode() {
  const mode = elements.syncMode.value;
  elements.startPageField.hidden = mode !== "manual";
  if (mode === "catchup") {
    elements.maxPagesLabel.textContent = "Page limit";
    elements.maxPages.value = "20";
    elements.maxPages.max = "200";
    elements.syncHelp.textContent =
      state.cacheCount
        ? "Checks newest entries first and stops after reaching entries already in the cache."
        : "Starts the cache from the newest entries and continues up to the page limit.";
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
  } else if (mode === "series_refresh") {
    elements.maxPagesLabel.textContent = "Series limit";
    elements.maxPages.value = "250";
    elements.maxPages.max = "2000";
    elements.syncHelp.textContent =
      "Updates stale catalogs and fills missing genres, associated names, and synopses.";
  } else if (mode === "series_discovery") {
    elements.maxPagesLabel.textContent = "Series limit";
    elements.maxPages.value = state.cacheCount ? "50" : "20";
    elements.maxPages.max = "2000";
    elements.syncHelp.textContent =
      "Checks JNovels' series index and caches a bounded batch of series pages missing locally.";
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
      document.querySelectorAll(".book-card__quick-action--refresh").forEach((button) => {
        button.disabled = false;
        button.classList.remove("is-running");
      });
      if (payload.sync.cache_changed) await loadBooks();
      const message = payload.sync.error
        ? "Sync failed."
        : payload.sync.cache_changed
          ? "Cache updated."
          : "No changes found.";
      showToast(message, Boolean(payload.sync.error));
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
    const staleSeries = state.health.stale_series || 0;
    const staleText = staleSeries
      ? ` · ${staleSeries} stale ${staleSeries === 1 ? "series" : "series"}`
      : "";
    elements.repairSummary.textContent = payload.count
      ? `${pending} pending entries · ${payload.count} with recorded failures${staleText}`
      : `${pending} entries are waiting for details${staleText}. No recorded failures.`;

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
  updateSyncModes("repair");
  openSyncOptions();
});
elements.refreshSeries.addEventListener("click", () => {
  elements.repairDialog.close();
  updateSyncModes("series_refresh");
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

async function startPageLoadCatchup() {
  const payload = await request("/api/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      automatic: true,
      mode: "catchup",
      start_page: 1,
      max_pages: 20,
      delay: 1,
    }),
  });
  if (payload.started) await loadStatus();
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

applyViewFromUrl();
updateSyncModes();
Promise.all([loadBooks({ historyMode: "replace" }), loadStatus()])
  .then(() => startPageLoadCatchup().catch((error) => {
    console.warn("Automatic catch-up sync could not start:", error);
  }))
  .catch((error) => showToast(error.message, true));
