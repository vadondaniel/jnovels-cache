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
  syncForm: document.querySelector("#sync-form"),
  startPage: document.querySelector("#start-page"),
  maxPages: document.querySelector("#max-pages"),
  delay: document.querySelector("#delay"),
  empty: document.querySelector("#empty-state"),
  emptySync: document.querySelector("#empty-sync"),
  template: document.querySelector("#book-template"),
  toast: document.querySelector("#toast"),
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

    coverLink.href = book.url;
    titleLink.href = book.url;
    titleLink.textContent = book.title;
    card.querySelector(".book-card__date").textContent = formatDate(book.published_at);
    card.querySelector(".book-card__cached").textContent = `Cached ${formatDate(book.cached_at)}`;
    card.style.animationDelay = `${Math.min(index * 18, 240)}ms`;

    type.textContent = book.entry_type === "volume" ? "Volume" : book.entry_type === "series" ? "Series" : "Not indexed";
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
    elements.syncStatus.textContent = sync.phase === "details"
      ? `Indexing post ${sync.details_completed + 1} of ${sync.details_total}`
      : `Scanning page ${sync.page} · ${sync.items_seen} results seen`;
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
  elements.syncForm.hidden = false;
  elements.syncForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  elements.startPage.focus();
}

elements.syncToggle.addEventListener("click", () => {
  elements.syncForm.hidden = !elements.syncForm.hidden;
  if (!elements.syncForm.hidden) elements.startPage.focus();
});

elements.emptySync.addEventListener("click", openSyncOptions);

elements.syncForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_page: Number(elements.startPage.value),
        max_pages: Number(elements.maxPages.value),
        delay: Number(elements.delay.value),
      }),
    });
    elements.syncForm.hidden = true;
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

Promise.all([loadBooks(), loadStatus()]).catch((error) => showToast(error.message, true));
