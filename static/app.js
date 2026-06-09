const state = {
  books: [],
  pollTimer: null,
  searchTimer: null,
};

const elements = {
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  grid: document.querySelector("#book-grid"),
  count: document.querySelector("#result-count"),
  lastSync: document.querySelector("#last-sync"),
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
  empty: document.querySelector("#empty-state"),
  emptySync: document.querySelector("#empty-sync"),
  template: document.querySelector("#book-template"),
  toast: document.querySelector("#toast"),
  catalogDialog: document.querySelector("#catalog-dialog"),
  catalogTitle: document.querySelector("#catalog-title"),
  catalogCount: document.querySelector("#catalog-count"),
  catalogList: document.querySelector("#catalog-list"),
  catalogSeriesLink: document.querySelector("#catalog-series-link"),
  catalogClose: document.querySelector("#catalog-close"),
};

function formatDate(value, options = { year: "numeric", month: "short", day: "numeric" }) {
  if (!value) return "Date unknown";
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

function renderBooks(books) {
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
      || (book.entry_type === "series" ? "Series" : "Not indexed");
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
      catalogButton.textContent = `Catalog · ${book.volume_count}`;
      catalogButton.addEventListener("click", () => openCatalog(book));
    } else {
      catalogButton.hidden = true;
    }

    if (book.entry_type === "series" && book.volume_count) {
      coverLink.href = "#";
      titleLink.href = "#";
      coverLink.setAttribute("aria-label", `Open cached catalog for ${book.series_title}`);
      titleLink.title = "Open cached catalog";
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
  elements.count.textContent = `${books.length} ${books.length === 1 ? "title" : "titles"}`;
  elements.empty.hidden = books.length !== 0 || Boolean(elements.search.value.trim());
}

async function openCatalog(book) {
  elements.catalogTitle.textContent = book.series_title;
  elements.catalogCount.textContent = "Loading cached volumes...";
  elements.catalogList.replaceChildren();
  elements.catalogSeriesLink.href = book.series_url;
  elements.catalogDialog.showModal();

  try {
    const payload = await request(`/api/series?${new URLSearchParams({ url: book.series_url })}`);
    elements.catalogCount.textContent =
      `${payload.count} ${payload.count === 1 ? "volume" : "volumes"} cached`;
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
          : "Release date not cached";
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
    elements.catalogCount.textContent = "Could not load this catalog.";
    showToast(error.message, true);
  }
}

elements.catalogClose.addEventListener("click", () => elements.catalogDialog.close());
elements.catalogDialog.addEventListener("click", (event) => {
  if (event.target === elements.catalogDialog) elements.catalogDialog.close();
});

async function loadBooks() {
  const params = new URLSearchParams({
    q: elements.search.value.trim(),
    sort: elements.sort.value,
  });
  const payload = await request(`/api/books?${params}`);
  state.books = payload.books;
  renderBooks(state.books);
}

function updateStatus(payload) {
  const sync = payload.sync;
  const lastSync = payload.metadata.last_sync_at;
  elements.lastSync.textContent = lastSync ? `Last synced ${formatDate(lastSync)}` : "Not synced yet";
  elements.syncToggle.disabled = sync.running;
  elements.syncToggle.classList.toggle("is-running", sync.running);

  if (sync.running) {
    if (sync.phase === "series") {
      elements.syncStatus.textContent =
        `Caching series ${Math.min(sync.series_completed + 1, sync.series_total)} of ${sync.series_total}`;
    } else if (sync.phase === "details") {
      elements.syncStatus.textContent =
        `Indexing post ${Math.min(sync.details_completed + 1, sync.details_total)} of ${sync.details_total}`;
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
        ? `${payload.count} total cached`
        : "";
  }
}

function updateSyncMode() {
  const mode = elements.syncMode.value;
  elements.startPageField.hidden = mode !== "manual";
  if (mode === "catchup") {
    elements.maxPagesLabel.textContent = "Safety page limit";
    elements.maxPages.value = "20";
    elements.syncHelp.textContent =
      "Starts from the newest page and stops automatically when cached entries are reached.";
  } else if (mode === "extend") {
    elements.maxPagesLabel.textContent = "Older pages to add";
    elements.maxPages.value = "10";
    elements.syncHelp.textContent =
      "Finds the current cache frontier, then continues into older uncached result pages.";
  } else {
    elements.maxPagesLabel.textContent = "Pages to scan";
    elements.maxPages.value = "6";
    elements.syncHelp.textContent =
      "Scans an exact page range, including already cached entries.";
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
      showToast(payload.sync.error ? "Sync stopped with an error." : "Cache sync complete.", Boolean(payload.sync.error));
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
  state.searchTimer = setTimeout(() => loadBooks().catch((error) => showToast(error.message, true)), 160);
});

elements.sort.addEventListener("change", () => {
  loadBooks().catch((error) => showToast(error.message, true));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    elements.search.focus();
  }
});

updateSyncMode();
Promise.all([loadBooks(), loadStatus()]).catch((error) => showToast(error.message, true));
