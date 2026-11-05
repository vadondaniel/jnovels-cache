import { request } from "./api.js";
import { openCatalog } from "./catalog.js";
import { openDetails } from "./details.js";
import { formatDate } from "./formatters.js";
import { showToast } from "./notifications.js";
import { state } from "./state.js";

export function renderBookCard(
  book,
  index,
  {
    template,
    loadBooks,
    loadStatus,
    showCachedReference,
  },
) {
  const card = template.content.firstElementChild.cloneNode(true);
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
      const result = await request("/api/refresh-series", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series_url: seriesActionUrl, delay: 1 }),
      });
      showToast(result.queued ? `Series refresh queued · position ${result.queue_position}.` : "Series refresh started.");
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
  return card;
}
