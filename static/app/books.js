import { renderBookCard } from "./bookCards.js";
import { bindCatalogDialog } from "./catalog.js";
import { elements } from "./elements.js";
import { state } from "./state.js";
import {
  updateSearchClearButton,
  updateSourceSearchLink,
} from "./view.js";

let loadBooksCallback = null;
let loadStatusCallback = null;

export function bindBookControls({ loadBooks, loadStatus }) {
  loadBooksCallback = loadBooks;
  loadStatusCallback = loadStatus;
  bindCatalogDialog();
  elements.detailsClose.addEventListener("click", () => elements.detailsDialog.close());
  elements.detailsDialog.addEventListener("click", (event) => {
    if (event.target === elements.detailsDialog) elements.detailsDialog.close();
  });
}

export function renderBooks(books, total) {
  elements.grid.replaceChildren();
  const fragment = document.createDocumentFragment();

  books.forEach((book, index) => {
    fragment.append(
      renderBookCard(book, index, {
        template: elements.template,
        loadBooks: loadBooksCallback,
        loadStatus: loadStatusCallback,
        showCachedReference,
      }),
    );
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
  await loadBooksCallback({ scroll: true, historyMode: "push" });
}
