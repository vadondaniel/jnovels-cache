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
  cacheCount: 0,
  filter: "all",
  filterCounts: {},
  syncNotice: null,
};

const defaultSort = "published_desc";
const validSorts = new Set(["published_desc", "published_asc", "title_asc", "title_desc", "cached_desc"]);
const validFilters = new Set(["all", "series", "volumes", "pending", "favorites", "hidden"]);

const elements = {
  search: document.querySelector("#search"),
  sourceSearch: document.querySelector("#source-search"),
  sort: document.querySelector("#sort"),
  grid: document.querySelector("#book-grid"),
  count: document.querySelector("#result-count"),
  lastSync: document.querySelector("#last-sync"),
  filterButtons: [...document.querySelectorAll("[data-filter]")],
  entryFilters: document.querySelector("#entry-filters"),
  cacheHealth: document.querySelector("#cache-health"),
  syncStatus: document.querySelector("#sync-status"),
  syncStatusLabel: document.querySelector("#sync-status-label"),
  syncStatusDetail: document.querySelector("#sync-status-detail"),
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
  refreshSeries: document.querySelector("#refresh-series"),
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
  catalogEyebrow: document.querySelector("#catalog-eyebrow"),
  catalogTitle: document.querySelector("#catalog-title"),
  catalogCount: document.querySelector("#catalog-count"),
  catalogList: document.querySelector("#catalog-list"),
  catalogDetails: document.querySelector("#catalog-details"),
  catalogDetailsBody: document.querySelector("#catalog-details-body"),
  catalogSeriesLink: document.querySelector("#catalog-series-link"),
  catalogClose: document.querySelector("#catalog-close"),
  detailsDialog: document.querySelector("#details-dialog"),
  detailsTitle: document.querySelector("#details-title"),
  detailsGenresSection: document.querySelector("#details-genres-section"),
  detailsGenres: document.querySelector("#details-genres"),
  detailsNamesSection: document.querySelector("#details-names-section"),
  detailsNames: document.querySelector("#details-names"),
  detailsSynopsisSection: document.querySelector("#details-synopsis-section"),
  detailsSynopsis: document.querySelector("#details-synopsis"),
  detailsClose: document.querySelector("#details-close"),
};

function updateFilterButtons() {
  let availableCategoryCount = 0;
  elements.filterButtons.forEach((button) => {
    const filter = button.dataset.filter;
    button.hidden = filter !== "all" && !(state.filterCounts[filter] || 0);
    if (filter !== "all" && !button.hidden) availableCategoryCount += 1;
    button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
  });
  elements.entryFilters.hidden = availableCategoryCount < 2;
}

function updateSourceSearchLink() {
  const url = new URL("https://jnovels.com/");
  const query = elements.search.value.trim();
  url.searchParams.set("s", query ? `epub ${query}` : "epub");
  elements.sourceSearch.href = url;
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
  updateSourceSearchLink();
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
    const detailsButton = card.querySelector(".book-card__details");
    const favoriteButton = card.querySelector(".book-card__quick-action--favorite");
    const hideButton = card.querySelector(".book-card__quick-action--hide");
    const refreshButton = card.querySelector(".book-card__quick-action--refresh");
    const seriesActionUrl = book.series_url || book.original_post_url || book.url;

    titleLink.textContent = book.series_title;
    card.querySelector(".book-card__date").textContent = formatDate(book.published_at);
    card.style.animationDelay = `${Math.min(index * 18, 240)}ms`;

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
    if (book.original_post_url) {
      seriesLink.href = book.original_post_url;
    } else {
      seriesLink.hidden = true;
    }
    if (book.volume_count > 1) {
      const catalogName = book.catalog_kind === "downloads" ? "Downloads" : "Volumes";
      catalogButton.textContent = `${catalogName} · ${book.catalog_count}`;
      catalogButton.addEventListener("click", () => openCatalog(book));
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

function openDetails(book) {
  elements.detailsTitle.textContent = book.series_title;

  elements.detailsGenres.replaceChildren();
  elements.detailsGenresSection.hidden = !book.genres.length;
  book.genres.forEach((genre) => {
    const tag = document.createElement("span");
    tag.textContent = genre;
    elements.detailsGenres.append(tag);
  });

  elements.detailsNames.replaceChildren();
  elements.detailsNamesSection.hidden = !book.associated_names.length;
  book.associated_names.forEach((name) => {
    const item = document.createElement("li");
    item.textContent = name;
    elements.detailsNames.append(item);
  });

  elements.detailsSynopsis.replaceChildren();
  elements.detailsSynopsisSection.hidden = !book.synopsis;
  (book.synopsis || "").split(/\n\s*\n/).forEach((paragraph) => {
    if (!paragraph) return;
    const text = document.createElement("p");
    text.textContent = paragraph;
    elements.detailsSynopsis.append(text);
  });

  elements.detailsDialog.showModal();
}

function renderSeriesInformation(container, book) {
  container.replaceChildren();
  const fragment = document.createDocumentFragment();

  if (book.genres.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const genres = document.createElement("div");
    heading.textContent = "Genres";
    genres.className = "details-dialog__genres";
    book.genres.forEach((genre) => {
      const tag = document.createElement("span");
      tag.textContent = genre;
      genres.append(tag);
    });
    section.append(heading, genres);
    fragment.append(section);
  }

  if (book.associated_names.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const names = document.createElement("ul");
    heading.textContent = "Associated names";
    book.associated_names.forEach((name) => {
      const item = document.createElement("li");
      item.textContent = name;
      names.append(item);
    });
    section.append(heading, names);
    fragment.append(section);
  }

  if (book.synopsis) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const synopsis = document.createElement("div");
    heading.textContent = "Synopsis";
    synopsis.className = "details-dialog__synopsis";
    book.synopsis.split(/\n\s*\n/).forEach((paragraph) => {
      const text = document.createElement("p");
      text.textContent = paragraph;
      synopsis.append(text);
    });
    section.append(heading, synopsis);
    fragment.append(section);
  }

  container.append(fragment);
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
  const isDownloadCatalog = book.catalog_kind === "downloads";
  const itemName = isDownloadCatalog ? "download" : "volume";
  elements.catalogEyebrow.textContent = isDownloadCatalog
    ? "Cached downloads"
    : "Cached volumes";
  elements.catalogCount.textContent = `Loading ${itemName}s...`;
  elements.catalogList.replaceChildren();
  const hasDetails = Boolean(
    book.synopsis || book.associated_names.length || book.genres.length,
  );
  elements.catalogDetails.hidden = !hasDetails;
  elements.catalogDetails.open = false;
  if (hasDetails) renderSeriesInformation(elements.catalogDetailsBody, book);
  elements.catalogSeriesLink.href = book.series_url;
  elements.catalogDialog.showModal();

  try {
    const payload = await request(`/api/series?${new URLSearchParams({ url: book.series_url })}`);
    const releaseGroups = new Set(
      payload.volumes.map((volume) => volume.release_group).filter(Boolean),
    );
    if (!isDownloadCatalog && releaseGroups.size > 1) {
      const numberedVolumes = new Set(
        payload.volumes
          .map((volume) => volume.label.match(/^Volume\s+([0-9]+(?:\.[0-9]+)?)/i)?.[1])
          .filter(Boolean),
      ).size;
      elements.catalogEyebrow.textContent = "Cached releases";
      elements.catalogCount.textContent =
        `${payload.count} cached releases · ${numberedVolumes} volumes`;
    } else {
      elements.catalogCount.textContent =
        `${payload.count} cached ${payload.count === 1 ? itemName : `${itemName}s`}`;
    }
    const fragment = document.createDocumentFragment();
    const grouped = new Map();
    payload.volumes.forEach((volume) => {
      let groupName = volume.release_group || "";
      let displayLabel = volume.label;
      const partMatch = displayLabel.match(/^Part (\d+) (.+)$/i);
      if (!groupName && partMatch) {
        groupName = `Part ${Number(partMatch[1])}`;
        displayLabel = partMatch[2];
      } else if (!groupName && /^Short Story Collection /i.test(displayLabel)) {
        groupName = "Short stories";
        displayLabel = displayLabel.replace(/^Short Story Collection /i, "");
      } else if (
        !groupName
        && (/^Fanbook(?:\s|$)/i.test(displayLabel) || /^Royal Academy Stories/i.test(displayLabel))
      ) {
        groupName = "Related books";
      }
      if (!grouped.has(groupName)) grouped.set(groupName, []);
      grouped.get(groupName).push({ ...volume, displayLabel });
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
        label.textContent = volume.displayLabel;
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
elements.detailsClose.addEventListener("click", () => elements.detailsDialog.close());
elements.detailsDialog.addEventListener("click", (event) => {
  if (event.target === elements.detailsDialog) elements.detailsDialog.close();
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

function formatRemainingTime(seconds) {
  const rounded = Math.max(Math.ceil(seconds), 1);
  if (rounded < 60) return `${rounded} sec`;
  const minutes = Math.ceil(rounded / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

function estimateSyncTime(sync) {
  const phaseStarted = Date.parse(sync.phase_started_at || "");
  const elapsed = Number.isFinite(phaseStarted)
    ? Math.max((Date.now() - phaseStarted) / 1000, 0)
    : 0;
  const delay = Math.max(Number(sync.request_delay) || 0, 0.5);
  const fallbackSeconds = delay + 1;

  if (sync.phase === "details") {
    const completed = sync.details_completed || 0;
    const secondsPerItem = completed ? Math.max(elapsed / completed, delay) : fallbackSeconds;
    const remaining = (sync.queued_details || 0) + (sync.queued_series || 0);
    return remaining
      ? `Estimated remaining: ${formatRemainingTime(remaining * secondsPerItem)}`
      : "";
  }

  if (sync.phase === "series") {
    const completed = sync.series_completed || 0;
    const secondsPerSeries = completed ? Math.max(elapsed / completed, delay) : fallbackSeconds;
    const remaining = sync.queued_series || 0;
    return remaining
      ? `Estimated remaining: ${formatRemainingTime(remaining * secondsPerSeries)}`
      : "";
  }

  const completedPages = sync.pages_completed || 0;
  const remainingPages = Math.max(
    (sync.pages_requested || 0) - completedPages,
    0,
  );
  const secondsPerPage = completedPages
    ? Math.max(elapsed / completedPages, delay)
    : fallbackSeconds;
  const queuedChecks = sync.queued_details || 0;
  const remainingSeconds =
    remainingPages * secondsPerPage + queuedChecks * fallbackSeconds;
  return remainingSeconds
    ? `Estimated remaining: up to ${formatRemainingTime(remainingSeconds)}`
    : "";
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
    if (sync.phase === "series") {
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
  const modes = state.cacheCount
    ? [
        ["catchup", "Check for new entries"],
        ["extend", "Add older entries"],
        ...(pending ? [["repair", `Repair incomplete entries (${pending})`]] : []),
        ...(refreshableSeries
          ? [["series_refresh", `Refresh series data (${refreshableSeries})`]]
          : []),
        ["manual", "Scan specific pages"],
      ]
    : [
        ["catchup", "Build initial cache"],
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
  state.page = 1;
  updateSourceSearchLink();
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

applyViewFromUrl();
updateSyncModes();
Promise.all([loadBooks({ historyMode: "replace" }), loadStatus()])
  .then(() => startPageLoadCatchup().catch((error) => {
    console.warn("Automatic catch-up sync could not start:", error);
  }))
  .catch((error) => showToast(error.message, true));
