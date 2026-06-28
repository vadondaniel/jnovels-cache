import { elements } from "./elements.js";
import { estimateSyncTime, formatDate } from "./formatters.js";
import { showToast } from "./notifications.js";
import { state } from "./state.js";

export function updateStatus(payload) {
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

export function updateSyncModes(preferredMode = null) {
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

export function updateSyncMode() {
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
