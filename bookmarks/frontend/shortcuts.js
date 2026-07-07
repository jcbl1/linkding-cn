document.addEventListener("keydown", (event) => {
  const targetNodeName = event.target.nodeName;
  const isInputTarget =
    targetNodeName === "INPUT" ||
    targetNodeName === "SELECT" ||
    targetNodeName === "TEXTAREA";

  if (isInputTarget) {
    return;
  }

  const isArrowUp = event.key === "ArrowUp";
  const isArrowDown = event.key === "ArrowDown";
  if (isArrowUp || isArrowDown) {
    event.preventDefault();

    const items = [...document.querySelectorAll("ul.bookmark-list > li")];
    const path = event.composedPath();
    const currentItem = path.find((item) => items.includes(item));

    let nextItem;
    if (currentItem) {
      nextItem = isArrowUp
        ? currentItem.previousElementSibling
        : currentItem.nextElementSibling;
    } else {
      nextItem = items[0];
    }
    nextItem?.querySelector("a.title-link")?.focus();
  }

  if (event.key === "e") {
    const list = document.querySelector(".bookmark-list");
    if (!list) return;
    const current = list.dataset.notesGlobal === "true";
    const next = !current;
    list.dataset.notesGlobal = String(next);
    list.querySelectorAll("li[ld-bookmark-item]").forEach((item) => {
      item.dataset.notesEnabled = String(next);
      item.classList.toggle("show-notes", next);
    });
  }

  if (event.key === "s") {
    const searchInput = document.querySelector('input[type="search"]');
    if (searchInput) {
      searchInput.focus();
      event.preventDefault();
    }
  }

  if (event.key === "n") {
    window.location.assign("/bookmarks/new");
  }
});
