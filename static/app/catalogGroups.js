import { formatDate } from "./formatters.js";

export function catalogGroupFormat(groupName) {
  if (/\bpdf\b/i.test(groupName)) return "pdf";
  if (/\bepub\b/i.test(groupName)) return "epub";
  return null;
}

function visibleCatalogGroupName(groupName, hasVisiblePdfGroup) {
  if (!groupName || hasVisiblePdfGroup) return groupName;
  const withoutEpub = groupName.replace(/\s*\bEPUB\b\s*$/i, "").trim();
  return withoutEpub === groupName ? groupName : withoutEpub;
}

function catalogGroupPriority(groupName) {
  const normalized = groupName.toLowerCase();
  const isLightNovel = normalized.includes("light novel");
  const isRevised = normalized.includes("revised edition");
  const isEpub = /\bepub\b/.test(normalized);
  const isPdf = /\bpdf\b/.test(normalized);
  if (isLightNovel && isEpub) return 0;
  if (isRevised && isEpub) return 1;
  if (isLightNovel && isPdf) return 2;
  if (isLightNovel) return 3;
  if (isRevised) return 4;
  if (isEpub) return 5;
  if (normalized.includes("official")) return 6;
  if (normalized && !normalized.includes("fan") && !isPdf) return 7;
  if (isPdf) return 8;
  if (normalized.includes("fan")) return 9;
  return 10;
}

function groupCatalogVolumes(volumes) {
  const grouped = new Map();
  volumes.forEach((volume) => {
    let groupName = volume.release_group || "";
    let displayLabel = volume.display_label || volume.label;
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
    } else if (
      !groupName
      && volume.is_internal_reference
      && !/\bVolume\s+[0-9]/i.test(displayLabel)
    ) {
      groupName = "Related entries";
    }
    if (!grouped.has(groupName)) grouped.set(groupName, []);
    grouped.get(groupName).push({ ...volume, displayLabel });
  });
  return [...grouped.entries()].sort(
    ([groupNameA], [groupNameB]) =>
      catalogGroupPriority(groupNameA) - catalogGroupPriority(groupNameB),
  );
}

function renderRelatedEntry(volume, showCachedReference) {
  const hasCachedTarget = Boolean(volume.is_internal_reference && volume.post_url);
  const card = document.createElement("article");
  const info = document.createElement("div");
  const label = document.createElement("strong");
  const date = document.createElement("small");
  const actions = document.createElement("div");
  card.className = "catalog-dialog__related-entry";
  info.className = "catalog-dialog__related-info";
  actions.className = "catalog-dialog__related-actions";
  label.textContent = volume.displayLabel;
  date.textContent = hasCachedTarget && volume.published_at
    ? `Cached ${formatDate(volume.published_at)}`
    : hasCachedTarget
      ? "Cached entry"
      : "Not in cache";
  date.classList.toggle("is-known", Boolean(volume.published_at));
  info.append(label, date);

  if (hasCachedTarget) {
    const cacheButton = document.createElement("button");
    cacheButton.type = "button";
    cacheButton.textContent = "View cache";
    cacheButton.addEventListener("click", () => showCachedReference(volume));
    actions.append(cacheButton);
  }

  const sourceLink = document.createElement("a");
  sourceLink.href = volume.download_url;
  sourceLink.target = "_blank";
  sourceLink.rel = "noopener";
  sourceLink.textContent = "JNovels ↗";
  sourceLink.setAttribute(
    "aria-label",
    `Open ${volume.displayLabel} on JNovels`,
  );
  actions.append(sourceLink);
  card.append(info, actions);
  return card;
}

function renderVolumeLink(volume) {
  const link = document.createElement("a");
  const text = document.createElement("span");
  const label = document.createElement("strong");
  const date = document.createElement("small");
  const arrow = document.createElement("span");
  link.className = "catalog-dialog__volume-link";
  link.href = volume.download_url;
  link.target = "_blank";
  link.rel = "noopener";
  label.textContent = volume.displayLabel;
  date.textContent = volume.published_at
    ? `Released ${formatDate(volume.published_at)}`
    : "Release date unavailable";
  date.classList.toggle("is-known", Boolean(volume.published_at));
  arrow.textContent = "↗";
  arrow.className = "catalog-dialog__link-kind";
  arrow.setAttribute("aria-hidden", "true");
  text.append(label, date);
  link.append(text, arrow);
  return link;
}

export function renderCatalogGroups(volumes, hasVisiblePdfGroup, showCachedReference) {
  const fragment = document.createDocumentFragment();
  groupCatalogVolumes(volumes).forEach(([groupName, groupVolumes]) => {
    const group = document.createElement("section");
    const list = document.createElement("div");
    group.className = "catalog-dialog__group";
    list.className = "catalog-dialog__volume-grid";
    list.classList.toggle(
      "is-related",
      groupVolumes.some((volume) => volume.is_internal_reference),
    );
    const visibleGroupName = visibleCatalogGroupName(groupName, hasVisiblePdfGroup);
    if (visibleGroupName) {
      const heading = document.createElement("h3");
      heading.textContent = visibleGroupName;
      group.append(heading);
    }
    groupVolumes.forEach((volume) => {
      list.append(
        volume.is_internal_reference
          ? renderRelatedEntry(volume, showCachedReference)
          : renderVolumeLink(volume),
      );
    });
    group.append(list);
    fragment.append(group);
  });
  return fragment;
}
