import { elements } from "./elements.js";

export function openDetails(book) {
  elements.detailsTitle.textContent = book.series_title;

  elements.detailsGenres.replaceChildren();
  elements.detailsGenresSection.hidden = !book.genres.length;
  book.genres.forEach((genre) => {
    const tag = document.createElement("span");
    tag.textContent = genre;
    elements.detailsGenres.append(tag);
  });

  elements.detailsNames.replaceChildren();
  elements.detailsNamesSection.hidden = !book.associated_names.length;
  book.associated_names.forEach((name) => {
    const item = document.createElement("li");
    item.textContent = name;
    elements.detailsNames.append(item);
  });

  elements.detailsSynopsis.replaceChildren();
  elements.detailsSynopsisSection.hidden = !book.synopsis;
  (book.synopsis || "").split(/\n\s*\n/).forEach((paragraph) => {
    if (!paragraph) return;
    const text = document.createElement("p");
    text.textContent = paragraph;
    elements.detailsSynopsis.append(text);
  });

  elements.detailsDialog.showModal();
}

export function renderSeriesInformation(container, book) {
  container.replaceChildren();
  const fragment = document.createDocumentFragment();

  if (book.genres.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const genres = document.createElement("div");
    heading.textContent = "Genres";
    genres.className = "details-dialog__genres";
    book.genres.forEach((genre) => {
      const tag = document.createElement("span");
      tag.textContent = genre;
      genres.append(tag);
    });
    section.append(heading, genres);
    fragment.append(section);
  }

  if (book.associated_names.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const names = document.createElement("ul");
    heading.textContent = "Associated names";
    book.associated_names.forEach((name) => {
      const item = document.createElement("li");
      item.textContent = name;
      names.append(item);
    });
    section.append(heading, names);
    fragment.append(section);
  }

  if (book.synopsis) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const synopsis = document.createElement("div");
    heading.textContent = "Synopsis";
    synopsis.className = "details-dialog__synopsis";
    book.synopsis.split(/\n\s*\n/).forEach((paragraph) => {
      const text = document.createElement("p");
      text.textContent = paragraph;
      synopsis.append(text);
    });
    section.append(heading, synopsis);
    fragment.append(section);
  }

  container.append(fragment);
}
