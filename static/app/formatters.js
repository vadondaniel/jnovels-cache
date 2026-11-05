export function formatDate(value, options = { year: "numeric", month: "short", day: "numeric" }) {
  if (!value) return "Date unavailable";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

export function formatRemainingTime(seconds) {
  const rounded = Math.max(Math.ceil(seconds), 1);
  if (rounded < 60) return `${rounded} sec`;
  const minutes = Math.ceil(rounded / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

export function estimateSyncTime(sync) {
  if (sync.phase === "source") return "";
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
