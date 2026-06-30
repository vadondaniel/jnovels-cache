import { request } from "./api.js";
import {
  catalogGroupFormat,
  renderCatalogGroups,
} from "./catalogGroups.js";
import { renderSeriesInformation } from "./details.js";
import { elements } from "./elements.js";
import { showToast } from "./notifications.js";

export async function openCatalog(book, { showCachedReference }) {
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
  const hasSeriesPage = /^https?:\/\//i.test(book.series_page_url || "");
  elements.catalogSeriesLink.hidden = !hasSeriesPage;
  if (hasSeriesPage) elements.catalogSeriesLink.href = book.series_page_url;
  elements.catalogDialog.showModal();

  try {
    const payload = await request(`/api/series?${new URLSearchParams({ url: book.series_url })}`);
    const releaseGroups = new Set(
      payload.volumes.map((volume) => volume.release_group).filter(Boolean),
    );
    const hasVisiblePdfGroup = payload.volumes.some(
      (volume) => catalogGroupFormat(volume.release_group || "") === "pdf",
    );
    const numberedVolumes = new Set(
      payload.volumes
        .map((volume) => volume.label.match(/^Volume\s+([0-9]+(?:\.[0-9]+)?)/i)?.[1])
        .filter(Boolean),
    ).size;
    if (
      !isDownloadCatalog
      && (releaseGroups.size > 1 || numberedVolumes < payload.count)
    ) {
      elements.catalogEyebrow.textContent = "Cached releases";
      elements.catalogCount.textContent =
        `${payload.count} cached releases · ${numberedVolumes} volumes`;
    } else {
      elements.catalogCount.textContent =
        `${payload.count} cached ${payload.count === 1 ? itemName : `${itemName}s`}`;
    }
    elements.catalogList.append(
      renderCatalogGroups(payload.volumes, hasVisiblePdfGroup, showCachedReference),
    );
  } catch (error) {
    elements.catalogCount.textContent = "Could not load these volumes.";
    showToast(error.message, true);
  }
}

export function bindCatalogDialog() {
  elements.catalogClose.addEventListener("click", () => elements.catalogDialog.close());
  elements.catalogDialog.addEventListener("click", (event) => {
    if (event.target === elements.catalogDialog) elements.catalogDialog.close();
  });
}
