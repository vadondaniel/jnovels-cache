import { elements } from "./elements.js";
import { state } from "./state.js";

export function renderPagination(payload) {
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
