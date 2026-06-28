import { request } from "./api.js";
import { elements } from "./elements.js";
import { formatDate } from "./formatters.js";
import { showToast } from "./notifications.js";
import { state } from "./state.js";
import {
  updateStatus,
  updateSyncMode,
  updateSyncModes,
} from "./syncStatus.js";

export { updateSyncModes } from "./syncStatus.js";

let loadBooksCallback = null;

export async function loadStatus() {
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
      if (payload.sync.cache_changed && loadBooksCallback) await loadBooksCallback();
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

export async function startPageLoadCatchup() {
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

export function bindSyncControls({ loadBooks }) {
  loadBooksCallback = loadBooks;
  elements.syncMode.addEventListener("change", updateSyncMode);
  elements.syncToggle.addEventListener("click", openSyncOptions);
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
}
