import Defuddle from "defuddle";
import { Highlighter, HIGHLIGHT_COLORS } from "./anchoring/highlighter";
import { TextQuoteAnchor } from "./anchoring/index";
import { READER_ICONS } from "./reader-icons";
import { gettext, interpolate } from "../utils/i18n.js";
import "./reader-toolbar.js";
import "./reader-sidebar.js";

const HIGHLIGHT_COLOR_LABELS = {
  yellow: gettext("Yellow"),
  green: gettext("Green"),
  blue: gettext("Blue"),
  pink: gettext("Pink"),
  primary: gettext("Theme"),
};

function parseJsonScriptValue(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  try {
    return JSON.parse(el.textContent);
  } catch {
    return fallback;
  }
}

function normalizeBaseUrl(baseUrl) {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  return `${value.replace(/\/+$/, "")}/`;
}

function joinPath(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${String(path || "").replace(/^\/+/, "")}`;
}

/**
 * Reader mode renderer using Defuddle for content extraction,
 * with toolbar, sidebar, and annotation support.
 */
function renderReader(options = {}) {
  const {
    contentSelector,
    outputFormat = "html",
    defuddleOptions = {},
    bookmarkId,
    assetId,
  } = options;

  const content = document.getElementById("content");
  if (!content) return;

  const apiBase = normalizeBaseUrl(
    parseJsonScriptValue("reader-api-base-url", "/api/")
  );
  const assetsBase = parseJsonScriptValue("reader-assets-base-url", "/assets");
  const bookmarksIndexUrl = parseJsonScriptValue(
    "reader-bookmarks-index-url",
    "/bookmarks"
  );

  // Parse bookmark data injected by Django's json_script
  const bookmarkScript = document.getElementById("bookmark-data");
  let bookmarkData = {};
  if (bookmarkScript) {
    try {
      bookmarkData = JSON.parse(bookmarkScript.textContent);
    } catch (err) {
      console.warn("Failed to parse bookmark data:", err);
    }
  }

  const contentHtml = content.innerHTML;
  const dom = new DOMParser().parseFromString(contentHtml, "text/html");

  const defuddleOptions_ = {
    ...defuddleOptions,
    url: window.location.href,
  };
  if (contentSelector) {
    defuddleOptions_.contentSelector = contentSelector;
  }
  if (outputFormat === "markdown") {
    defuddleOptions_.markdown = true;
  }

  const result = new Defuddle(dom, defuddleOptions_).parse();

  // Title shown in toolbar should always come from bookmark metadata.
  let resolvedTitle = bookmarkData.title || "";
  if (!resolvedTitle) {
    try {
      resolvedTitle = new URL(bookmarkData.url || window.location.href).hostname;
    } catch {
      resolvedTitle = gettext("Reader");
    }
  }
  document.title = resolvedTitle;

  // Build article container (replaces old .reading-time with scroll progress)
  const container = document.createElement("div");
  container.classList.add("container");

  const articleTitle = document.createElement("h1");
  articleTitle.textContent = result.title || "";
  container.append(articleTitle);

  const byline = [result.author, result.site].filter(Boolean);
  if (byline.length > 0) {
    const articleByline = document.createElement("p");
    articleByline.textContent = byline.join(" | ");
    articleByline.classList.add("byline");
    container.append(articleByline);
  }

  const divider = document.createElement("hr");
  container.append(divider);

  const articleContent = document.createElement("div");
  articleContent.id = "article-content";
  articleContent.innerHTML = result.content;
  postProcess(articleContent);
  container.append(articleContent);

  // --- Build the new layout ---
  const layout = document.createElement("div");
  layout.id = "reader-layout";

  const contentArea = document.createElement("div");
  contentArea.id = "reader-content";
  contentArea.classList.add("scrollbar");
  contentArea.appendChild(container);
  layout.appendChild(contentArea);

  content.replaceWith(layout);

  // --- Snapshot URL (open latest snapshot page directly) ---
  const snapshotUrl = bookmarkData.snapshot_id
    ? joinPath(assetsBase, `${bookmarkData.snapshot_id}`)
    : "";

  // --- Toolbar ---
  const toolbar = document.createElement("reader-toolbar");
  toolbar.title = resolvedTitle;
  toolbar.bookmarkUrl = bookmarkData.url || "";
  toolbar.snapshotUrl = snapshotUrl;
  document.body.prepend(toolbar);

  // --- Sidebar ---
  const sidebar = document.createElement("reader-sidebar");
  sidebar.bookmarkData = bookmarkData;
  sidebar.apiBase = apiBase;
  sidebar.assetsBase = assetsBase;
  sidebar.bookmarksIndexUrl = bookmarksIndexUrl;
  layout.appendChild(sidebar);

  // Restore sidebar state from localStorage (default: closed)
  const savedSidebarRaw = localStorage.getItem("reader_sidebar_open");
  const savedSidebarOpen = savedSidebarRaw === "true";
  sidebar.open = savedSidebarOpen;
  toolbar.sidebarOpen = savedSidebarOpen;

  // Toggle sidebar
  toolbar.addEventListener("toggle-sidebar", () => {
    const newState = !sidebar.open;
    sidebar.open = newState;
    toolbar.sidebarOpen = newState;
    localStorage.setItem("reader_sidebar_open", String(newState));
  });

  // On mobile, tap outside sidebar closes it.
  const isMobileViewport = window.matchMedia("(max-width: 768px)");
  document.addEventListener("pointerdown", (e) => {
    if (!isMobileViewport.matches) return;
    if (!sidebar.open) return;
    const path = typeof e.composedPath === "function" ? e.composedPath() : [];
    const insideSidebar = path.includes(sidebar);
    const insideToolbar = path.includes(toolbar);
    if (!insideSidebar && !insideToolbar) {
      sidebar.open = false;
      toolbar.sidebarOpen = false;
      localStorage.setItem("reader_sidebar_open", "false");
    }
  });

  // --- Fetch full bookmark data and assets ---
  if (bookmarkData.id) {
    fetchBookmarkData(bookmarkData.id, sidebar, apiBase);
    fetchAssetList(bookmarkData.id, sidebar, apiBase);
  }

  // --- Highlighter ---
  if (bookmarkId && Number(assetId) > 0) {
    initHighlighter(articleContent, bookmarkId, assetId, sidebar, apiBase);
  }

  // --- Scroll progress ---
  setupScrollProgress(contentArea, toolbar);
}

function postProcess(articleContent) {
  articleContent.querySelectorAll("table").forEach((table) => {
    table.classList.add("table");
  });
}

function getCSRFToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  if (match) return match[1];
  const meta = document.querySelector('meta[name="csrfmiddlewaretoken"]');
  return meta ? meta.content : "";
}

async function fetchBookmarkData(bookmarkId, sidebar, apiBase) {
  try {
    const resp = await fetch(joinPath(apiBase, `bookmarks/${bookmarkId}/`));
    if (resp.ok) {
      const data = await resp.json();
      sidebar.bookmarkData = { ...sidebar.bookmarkData, ...data };
      if (data?.title) {
        document.title = data.title;
        document.dispatchEvent(
          new CustomEvent("bookmark-updated", {
            detail: { title: data.title },
          })
        );
      }
    }
  } catch (err) {
    console.warn("Failed to fetch bookmark data:", err);
  }
}

async function fetchAssetList(bookmarkId, sidebar, apiBase) {
  try {
    const resp = await fetch(
      joinPath(apiBase, `bookmarks/${bookmarkId}/assets/`)
    );
    if (resp.ok) {
      const data = await resp.json();
      sidebar.assetList = Array.isArray(data) ? data : data.results || [];
    }
  } catch (err) {
    console.warn("Failed to fetch asset list:", err);
  }
}

function setupScrollProgress(contentEl, toolbar) {
  let ticking = false;
  const update = () => {
    const scrollTop = contentEl.scrollTop;
    const scrollHeight = contentEl.scrollHeight - contentEl.clientHeight;
    const percent =
      scrollHeight > 0
        ? Math.min(100, Math.round((scrollTop / scrollHeight) * 100))
        : 0;
    toolbar.progress = percent;
  };

  update();
  contentEl.addEventListener("scroll", () => {
    if (!ticking) {
      window.requestAnimationFrame(() => {
        update();
        ticking = false;
      });
      ticking = true;
    }
  });
}

const ANNOTATION_TOOLBAR_MODE_KEY = "reader_annotation_toolbar_mode";
const ANNOTATION_TOOLBAR_MODE_HYBRID = "hybrid";
const ANNOTATION_TOOLBAR_MODE_TAKEOVER = "takeover";
const DEFAULT_HIGHLIGHT_COLOR = "yellow";

function getAnnotationToolbarMode() {
  try {
    const raw = localStorage.getItem(ANNOTATION_TOOLBAR_MODE_KEY);
    if (raw === ANNOTATION_TOOLBAR_MODE_TAKEOVER) {
      return ANNOTATION_TOOLBAR_MODE_TAKEOVER;
    }
  } catch {
    // Ignore localStorage access errors and use hybrid mode.
  }
  return ANNOTATION_TOOLBAR_MODE_HYBRID;
}

function setAnnotationToolbarModeDataset(mode) {
  document.documentElement.dataset.annotationToolbarMode = mode;
}

function toSolidColor(bg) {
  if (typeof bg !== "string") return bg;
  if (bg.includes("color-mix(")) return bg;
  return bg.replace("0.35", "0.8").replace("0.3", "0.8");
}

/**
 * Initialize the annotation highlighter with a unified popup.
 */
function initHighlighter(contentEl, bookmarkId, assetId, sidebar, apiBase) {
  const highlighter = new Highlighter(contentEl, {
    apiBase,
    bookmarkId,
    assetId,
  });

  // Sync annotations to sidebar
  highlighter.onChange((annotations) => {
    sidebar.annotations = Array.from(annotations.values());
  });

  function getReaderScrollContainer() {
    return (
      contentEl.closest("#reader-content") ||
      document.getElementById("reader-content") ||
      null
    );
  }

  function getRangeStartRect(range) {
    try {
      const startRange = range.cloneRange();
      startRange.collapse(true);
      const caretRects = startRange.getClientRects();
      if (caretRects.length > 0) return caretRects[0];
    } catch {
      // Ignore and fall back.
    }
    try {
      const rects = range.getClientRects();
      if (rects.length > 0) return rects[0];
      const box = range.getBoundingClientRect();
      if (box && (box.width > 0 || box.height > 0)) return box;
    } catch {
      // Ignore and let caller fallback.
    }
    return null;
  }

  function scrollRangeIntoReaderView(range) {
    const container = getReaderScrollContainer();
    const targetRect = getRangeStartRect(range);

    if (container && targetRect) {
      const containerRect = container.getBoundingClientRect();
      const offsetInContainer =
        container.scrollTop + (targetRect.top - containerRect.top);
      // Keep target around upper-middle area for better reading continuity.
      const desiredTop = offsetInContainer - container.clientHeight * 0.32;
      const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
      const nextTop = Math.max(0, Math.min(maxTop, desiredTop));
      container.scrollTo({ top: nextTop, behavior: "smooth" });
      return;
    }

    // Fallback for unexpected DOM states.
    const node = range.startContainer;
    const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    }
  }

  // Reload asset list when assets change
  sidebar.addEventListener("reload-assets", (e) => {
    const { bookmarkId: bmId } = e.detail;
    if (bmId) fetchAssetList(bmId, sidebar, apiBase);
  });

  // Handle sidebar annotation actions
  sidebar.addEventListener("annotation-action", async (e) => {
    const { id, action, note } = e.detail;

    if (action === "jump") {
      const ann = highlighter.annotations.get(String(id));
      if (!ann) return;
      try {
        const selector = ann.selector;
        const anchor = TextQuoteAnchor.fromSelector(contentEl, {
          exact: selector.exact || ann.selected_text,
          prefix: selector.prefix,
          suffix: selector.suffix,
        });
        const hint =
          typeof selector.start === "number" ? selector.start : undefined;
        const range = anchor.toRange({ hint });
        // Scroll to exact range position instead of parent element center.
        scrollRangeIntoReaderView(range);
      } catch {
        console.warn(`Could not locate annotation ${id}`);
      }
    } else if (action === "copy") {
      const ann = highlighter.annotations.get(String(id));
      if (ann) {
        let text = ann.selected_text;
        if (ann.note_content) {
          text += "\n\n---\n\n" + ann.note_content;
        }
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
      }
    } else if (action === "edit-note") {
      await highlighter.updateAnnotation(String(id), { note_content: note });
    } else if (action === "delete") {
      await highlighter.deleteAnnotation(String(id));
    } else if (action === "change-color") {
      await highlighter.updateAnnotation(String(id), { color: e.detail.color });
    }
  });

  const toolbarMode = getAnnotationToolbarMode();
  setAnnotationToolbarModeDataset(toolbarMode);
  document.documentElement.classList.toggle(
    "reader-annotation-takeover",
    toolbarMode === ANNOTATION_TOOLBAR_MODE_TAKEOVER
  );

  const hasCoarsePrimaryPointer = window.matchMedia("(pointer: coarse)").matches;
  const hasAnyCoarsePointer = window.matchMedia("(any-pointer: coarse)").matches;
  const isTouchCapable = (navigator.maxTouchPoints || 0) > 0;
  const preferMobileToolbar =
    hasCoarsePrimaryPointer || hasAnyCoarsePointer || isTouchCapable;

  const popup = createHighlightPopup({
    compact: !preferMobileToolbar,
  });
  document.body.appendChild(popup);

  if (
    toolbarMode === ANNOTATION_TOOLBAR_MODE_TAKEOVER &&
    window.matchMedia("(pointer: coarse)").matches
  ) {
    contentEl.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  const state = {
    pendingRange: null,
    pendingAnnotation: null,
    anchorRect: null,
    anchorClientX: null,
    mouseSelecting: false,
    mouseSelectStartX: 0,
    mouseSelectStartY: 0,
    mouseSelectMoved: false,
    lastSelectionPointerX: null,
    lastSelectionPointerTs: -1,
    isOpeningFromHighlightClick: false,
    suppressSelectionChangeUntil: 0,
    lastHighlightPointerUpTs: -1,
    contextToken: 0,
    isOpen: false,
    openedAt: 0,
    isPersisting: false,
    isAnchorInViewport: true,
    lastScrollAt: 0,
    isRestoringSelection: false,
    popupLayout: "anchored",
    dockedEditingSession: false,
    lastToolbarControlPointerDownTs: -1,
    lastToolbarInteractionTs: -1,
    pendingColor: null,
    saveStatus: "idle",
    saveStatusClearTimer: null,
    mode: "idle",
  };

  function setMode(mode) {
    state.mode = mode;
    popup.dataset.state = mode;
  }

  function setPopupLayout(layout) {
    const next =
      preferMobileToolbar && layout === "docked-top"
        ? "docked-top"
        : "anchored";
    state.popupLayout = next;
    popup.dataset.layout = next;
  }

  function isDockedTopLayout() {
    return preferMobileToolbar && state.popupLayout === "docked-top";
  }

  function setPopupMode(isEdit) {
    popup.dataset.mode = isEdit ? "edit" : "new";
    popup._deleteBtn.hidden = !isEdit;
    popup._actionGroup.hidden = !isEdit;
    popup._actionCard.hidden = !isEdit;
    popup._deleteConfirm.hidden = true;
  }

  function setNoteOpen(isOpen) {
    popup.dataset.noteOpen = isOpen ? "true" : "false";
  }

  function updateDraftHighlight() {
    if (!state.pendingRange || state.pendingAnnotation) {
      highlighter.clearDraftHighlight?.();
      return;
    }
    const draftColor = state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
    highlighter.setDraftHighlight?.(state.pendingRange, draftColor);
  }

  function setSaveStatus(status, text = "") {
    state.saveStatus = status;
    popup.dataset.saveState = status;
    if (popup._saveStatus) {
      popup._saveStatus.textContent = text;
    }
    if (state.saveStatusClearTimer) {
      clearTimeout(state.saveStatusClearTimer);
      state.saveStatusClearTimer = null;
    }
    if (status === "saved") {
      state.saveStatusClearTimer = setTimeout(() => {
        setSaveStatus("idle", "");
      }, 1300);
    }
  }

  function updateMobileToolbarOffset() {
    // Mobile toolbar now follows selection like desktop positioning.
    popup.style.removeProperty("--ld-annotation-mobile-top-offset");
  }

  function suppressSelectionChangeFor(ms = 180) {
    state.suppressSelectionChangeUntil = performance.now() + ms;
  }

  function markToolbarInteraction() {
    state.lastToolbarInteractionTs = performance.now();
  }

  function getNoteText() {
    return popup._noteInput.value.trim();
  }

async function resizeNoteInput() {
  const input = popup._noteInput;
  if (!input) return;

  const styles = window.getComputedStyle(input);
  const lineHeight = await measureLineHeightPx(input);
  const paddingTop = parseFloat(styles.paddingTop) || 0;
  const paddingBottom = parseFloat(styles.paddingBottom) || 0;
  const borderTop = parseFloat(styles.borderTopWidth) || 0;
  const borderBottom = parseFloat(styles.borderBottomWidth) || 0;
  const verticalFrame = paddingTop + paddingBottom + borderTop + borderBottom;
  const minHeight = lineHeight + verticalFrame;
  const viewportHeight = Math.max(
    0,
    window.visualViewport?.height || window.innerHeight
  );
  const popupRect = popup.getBoundingClientRect();
  const toolbarRow = popup.querySelector(".ld-annotation-toolbar-row");
  const toolbarRowHeight = toolbarRow
    ? toolbarRow.getBoundingClientRect().height
    : 0;
  const noteAreaStyles = popup._noteArea
    ? window.getComputedStyle(popup._noteArea)
    : null;
  const noteAreaMarginTop = noteAreaStyles
    ? parseFloat(noteAreaStyles.marginTop) || 0
    : 0;
  const safeGap = 12;
  const availablePopupHeight = Math.max(
    minHeight,
    viewportHeight - popupRect.top - safeGap
  );
  const availableForNote = Math.max(
    minHeight,
    availablePopupHeight - toolbarRowHeight - noteAreaMarginTop
  );
  const maxLinesByViewport = Math.max(
    1,
    Math.floor((viewportHeight * 0.45 - verticalFrame) / lineHeight)
  );
  const maxLinesBySpace = Math.max(
    1,
    Math.floor((availableForNote - verticalFrame) / lineHeight)
  );
  const maxLines = Math.min(10, maxLinesByViewport, maxLinesBySpace);
  const maxHeight = lineHeight * maxLines + verticalFrame;

  input.style.height = "auto";
  const contentHeight = Math.max(minHeight, input.scrollHeight);
  const nextHeight = Math.min(maxHeight, contentHeight);
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = contentHeight > maxHeight + 0.5 ? "auto" : "hidden";
}

  function clearPending({ keepSelection = false } = {}) {
    state.pendingRange = null;
    state.pendingAnnotation = null;
    state.anchorRect = null;
    state.anchorClientX = null;
    state.pendingColor = null;
    highlighter.clearDraftHighlight?.();
    if (!keepSelection) {
      window.getSelection()?.removeAllRanges();
    }
  }

  function openPopup() {
    if (state.isOpen) return;
    state.isOpen = true;
    state.openedAt = performance.now();
    state.isAnchorInViewport = true;
    setSaveStatus("idle", "");
    updateMobileToolbarOffset();
    popup.hidden = false;
    popup.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => {
      popup.dataset.open = "true";
    });
  }

  function closePopup() {
    if (!state.isOpen) return;
    state.isOpen = false;
    popup.dataset.open = "false";
    popup.setAttribute("aria-hidden", "true");
    popup.hidden = true;
    setMode("idle");
    setPopupLayout("anchored");
    state.dockedEditingSession = false;
    setSaveStatus("idle", "");
    popup.dataset.anchorVisible = "true";
    popup._deleteConfirm.hidden = true;
  }

  function isPopupTarget(target) {
    return popup.contains(target);
  }

  function getCurrentSelectionRange() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!contentEl.contains(range.commonAncestorContainer)) return null;
    if (!selection.toString().trim().length) return null;
    return range;
  }

  function isSameRange(a, b) {
    if (!a || !b) return false;
    return (
      a.startContainer === b.startContainer &&
      a.startOffset === b.startOffset &&
      a.endContainer === b.endContainer &&
      a.endOffset === b.endOffset
    );
  }

  function restorePendingSelectionIfNeeded() {
    if (!preferMobileToolbar) return false;
    if (!state.pendingRange || state.pendingAnnotation) return false;
    if (!state.isOpen || popup.hidden) return false;
    if (performance.now() - state.lastScrollAt > 260) return false;

    try {
      if (!contentEl.contains(state.pendingRange.commonAncestorContainer)) {
        return false;
      }
      const selection = window.getSelection();
      if (!selection) return false;
      if (
        selection.rangeCount &&
        isSameRange(selection.getRangeAt(0), state.pendingRange)
      ) {
        return true;
      }
      const restored = state.pendingRange.cloneRange();
      state.isRestoringSelection = true;
      suppressSelectionChangeFor(80);
      selection.removeAllRanges();
      selection.addRange(restored);
      requestAnimationFrame(() => {
        state.isRestoringSelection = false;
      });
      return true;
    } catch {
      state.isRestoringSelection = false;
      return false;
    }
  }

  function getLastSelectionRect(range) {
    const rects = range.getClientRects();
    return rects.length > 0 ? rects[rects.length - 1] : range.getBoundingClientRect();
  }

  function getSelectionEndRect(range) {
    try {
      const endRange = range.cloneRange();
      endRange.collapse(false);
      const rects = endRange.getClientRects();
      if (rects.length > 0) return rects[0];
      return endRange.getBoundingClientRect();
    } catch {
      return getLastSelectionRect(range);
    }
  }

  function updateAnchorRectFromPendingRange() {
    if (!state.pendingRange) return;
    try {
      state.anchorRect = getSelectionEndRect(state.pendingRange);
    } catch {
      state.anchorRect = null;
    }
  }

  function updateAnchorRectFromAnnotation(ann) {
    let rect = null;
    const annEl = document.querySelector(`[data-annotation-id="${ann.id}"]`);
    if (annEl) {
      rect = annEl.getBoundingClientRect();
    } else {
      rect = getAnnotationRect(ann, contentEl);
    }
    state.anchorRect = rect;
  }

  function isRectInViewport(rect) {
    if (!rect) return false;
    return (
      rect.bottom >= 0 &&
      rect.top <= window.innerHeight &&
      rect.right >= 0 &&
      rect.left <= window.innerWidth
    );
  }

  function schedulePopupReflow() {
    const token = state.contextToken;
    requestAnimationFrame(() => {
      if (!state.isOpen || token !== state.contextToken) return;
      reflowPopupPosition();
    });
  }

  function showToolbarForSelection(range, anchorClientX = null) {
    state.contextToken += 1;
    state.pendingRange = range.cloneRange();
    state.pendingAnnotation = null;
    state.anchorRect = getSelectionEndRect(range);
    state.anchorClientX = Number.isFinite(anchorClientX) ? anchorClientX : null;
    state.pendingColor = null;
    state.dockedEditingSession = false;
    setPopupLayout("anchored");
    setPopupMode(false);
    popup._noteInput.value = "";
    setNoteOpen(true);
    void resizeNoteInput();
    updateColorButtons(popup, null);
    openPopup();
    positionPopup(popup, state.anchorRect, state.anchorClientX);
    popup.dataset.anchorVisible = "true";
    popup._deleteConfirm.hidden = true;
    updateDraftHighlight();
    setMode("toolbar_open_new");
    schedulePopupReflow();
  }

  function showToolbarForAnnotation(ann, anchorClientX = null) {
    state.contextToken += 1;
    state.pendingRange = null;
    state.pendingAnnotation = ann;
    state.anchorClientX = Number.isFinite(anchorClientX) ? anchorClientX : null;
    state.pendingColor = ann.color || null;
    highlighter.clearDraftHighlight?.();
    updateAnchorRectFromAnnotation(ann);
    state.dockedEditingSession = false;
    setPopupLayout("anchored");
    setPopupMode(true);
    popup._noteInput.value = ann.note_content || "";
    setNoteOpen(true);
    void resizeNoteInput();
    updateColorButtons(popup, ann.color);
    if (state.anchorRect) {
      openPopup();
      positionPopup(popup, state.anchorRect, state.anchorClientX);
      popup.dataset.anchorVisible = "true";
      popup._deleteConfirm.hidden = true;
      setMode("toolbar_open_edit");
      schedulePopupReflow();
    }
  }

  async function savePendingAnnotation() {
    if (state.isPersisting) return false;

    const noteText = getNoteText();
    if (state.pendingAnnotation) {
      const existingNote = state.pendingAnnotation.note_content || "";
      if (noteText !== existingNote) {
        state.isPersisting = true;
        setMode("persisting");
        setSaveStatus("saving", gettext("Saving..."));
        try {
          const updated = await highlighter.updateAnnotation(
            String(state.pendingAnnotation.id),
            {
              note_content: noteText,
            }
          );
          if (updated) {
            setSaveStatus("saved", gettext("Saved"));
          } else {
            setSaveStatus("error", gettext("Save failed"));
          }
        } catch {
          setSaveStatus("error", gettext("Save failed"));
        } finally {
          state.isPersisting = false;
        }
      }
      return true;
    }

    if (state.pendingRange && noteText) {
      state.isPersisting = true;
      setMode("persisting");
      setSaveStatus("saving", gettext("Saving..."));
      try {
        window.getSelection()?.removeAllRanges();
        const colorToSave = state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
        const created = await highlighter.createAnnotation(
          state.pendingRange,
          colorToSave,
          noteText
        );
        if (created) {
          state.pendingRange = null;
          state.pendingAnnotation = created;
          state.pendingColor = created.color || colorToSave;
          highlighter.clearDraftHighlight?.();
          setPopupMode(true);
          updateColorButtons(popup, state.pendingColor);
          updateAnchorRectFromAnnotation(created);
          setSaveStatus("saved", gettext("Saved"));
          if (!isDockedTopLayout()) {
            schedulePopupReflow();
          }
        } else {
          setSaveStatus("error", gettext("Save failed"));
        }
      } catch {
        setSaveStatus("error", gettext("Save failed"));
      } finally {
        state.isPersisting = false;
      }
      return true;
    }

    return false;
  }

  async function closeAndPersist({
    clearSelection = true,
    contextToken = state.contextToken,
  } = {}) {
    await savePendingAnnotation();
    if (contextToken !== state.contextToken) return;
    closePopup();
    clearPending({ keepSelection: !clearSelection });
  }

  async function applyColor(color) {
    const noteText = getNoteText();
    const selectedColor =
      color || state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
    let createdAnnotation = null;
    if (state.pendingAnnotation) {
      const updates = { color: selectedColor };
      if (noteText !== (state.pendingAnnotation.note_content || "")) {
        updates.note_content = noteText;
      }
      state.isPersisting = true;
      setMode("persisting");
      try {
        await highlighter.updateAnnotation(String(state.pendingAnnotation.id), updates);
      } finally {
        state.isPersisting = false;
      }
    } else if (state.pendingRange) {
      state.isPersisting = true;
      setMode("persisting");
      try {
        window.getSelection()?.removeAllRanges();
        createdAnnotation = await highlighter.createAnnotation(
          state.pendingRange,
          selectedColor,
          noteText
        );
      } finally {
        state.isPersisting = false;
      }
    }
    if (createdAnnotation) {
      state.pendingRange = null;
      state.pendingAnnotation = createdAnnotation;
      state.pendingColor = createdAnnotation.color || selectedColor;
      highlighter.clearDraftHighlight?.();
      setPopupMode(true);
    }

    if (state.pendingAnnotation) {
      const latest = highlighter.annotations.get(String(state.pendingAnnotation.id));
      if (latest) {
        state.pendingAnnotation = latest;
      }
      updateColorButtons(popup, selectedColor);
      if (state.pendingAnnotation) {
        updateAnchorRectFromAnnotation(state.pendingAnnotation);
        if (isDockedTopLayout()) {
          popup.dataset.anchorVisible = "true";
        } else if (state.anchorRect && isRectInViewport(state.anchorRect)) {
          popup.dataset.anchorVisible = "true";
          positionPopup(popup, state.anchorRect, state.anchorClientX);
        } else {
          popup.dataset.anchorVisible = "false";
        }
      }
      setMode("toolbar_open_edit");
      schedulePopupReflow();
      return;
    }
    state.pendingColor = selectedColor;
    updateColorButtons(popup, selectedColor);
    updateDraftHighlight();
    closePopup();
    clearPending();
  }

  async function handleDelete() {
    if (!state.pendingAnnotation) return;
    state.isPersisting = true;
    setMode("persisting");
    try {
      await highlighter.deleteAnnotation(String(state.pendingAnnotation.id));
    } finally {
      state.isPersisting = false;
    }
    closePopup();
    clearPending();
  }

  function reflowPopupPosition() {
    if (!state.isOpen || popup.hidden) return;
    updateMobileToolbarOffset();
    void resizeNoteInput();
    if (state.pendingRange) {
      updateAnchorRectFromPendingRange();
    } else if (state.pendingAnnotation) {
      updateAnchorRectFromAnnotation(state.pendingAnnotation);
    }
    updateDraftHighlight();
    if (isDockedTopLayout()) {
      popup.dataset.anchorVisible = "true";
      return;
    }

    if (!state.anchorRect) return;
    const visible = isRectInViewport(state.anchorRect);
    state.isAnchorInViewport = visible;
    popup.dataset.anchorVisible = visible ? "true" : "false";
    if (!visible) return;
    positionPopup(popup, state.anchorRect, state.anchorClientX);
  }

  loadAnnotations(highlighter, apiBase, bookmarkId, assetId);

  // --- Text selection ---
  document.addEventListener("selectionchange", () => {
    if (performance.now() < state.suppressSelectionChangeUntil) return;
    if (state.isRestoringSelection) return;
    if (state.isOpeningFromHighlightClick) return;
    const range = getCurrentSelectionRange();
    if (!range) {
      if (restorePendingSelectionIfNeeded()) {
        schedulePopupReflow();
        return;
      }
      if (
        preferMobileToolbar &&
        state.pendingRange &&
        state.isOpen &&
        !state.pendingAnnotation &&
        !state.isPersisting
      ) {
        schedulePopupReflow();
        return;
      }
      // Safari can clear selection while interacting with toolbar controls.
      if (performance.now() - state.lastToolbarInteractionTs < 520) {
        schedulePopupReflow();
        return;
      }
      if (!isPopupTarget(document.activeElement) && !state.isPersisting) {
        closePopup();
        clearPending({ keepSelection: true });
      }
      return;
    }

    if (state.isPersisting) return;
    if (state.mouseSelecting) return;
    setMode("selection_ready");
    const canReusePointerX =
      Number.isFinite(state.lastSelectionPointerX) &&
      performance.now() - state.lastSelectionPointerTs < 420;
    const anchorX = canReusePointerX ? state.lastSelectionPointerX : null;
    showToolbarForSelection(range, anchorX);
  });

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (!contentEl.contains(e.target)) return;
    if (isPopupTarget(e.target)) return;
    state.mouseSelecting = true;
    state.mouseSelectStartX = e.clientX;
    state.mouseSelectStartY = e.clientY;
    state.mouseSelectMoved = false;
  });

  document.addEventListener("mousemove", (e) => {
    if (!state.mouseSelecting || state.mouseSelectMoved) return;
    const dx = e.clientX - state.mouseSelectStartX;
    const dy = e.clientY - state.mouseSelectStartY;
    if (dx * dx + dy * dy > 9) {
      state.mouseSelectMoved = true;
    }
  });

  document.addEventListener("mouseup", (e) => {
    if (!state.mouseSelecting) return;
    if (Number.isFinite(e.clientX)) {
      state.lastSelectionPointerX = e.clientX;
      state.lastSelectionPointerTs = performance.now();
    } else {
      state.lastSelectionPointerX = null;
      state.lastSelectionPointerTs = -1;
    }
    state.mouseSelecting = false;
    state.mouseSelectMoved = false;
    if (state.isPersisting || state.isOpeningFromHighlightClick) return;
    const range = getCurrentSelectionRange();
    if (!range) return;
    setMode("selection_ready");
    showToolbarForSelection(range, e.clientX);
  });

  // --- Click on existing highlight ---
  function openFromHighlightPointerEvent(e) {
    // Ignore the synthetic click that follows the same pointerup.
    if (
      e.type === "click" &&
      state.lastHighlightPointerUpTs >= 0 &&
      Math.abs(e.timeStamp - state.lastHighlightPointerUpTs) < 280
    ) {
      return;
    }
    if (e.type === "pointerup") {
      state.lastHighlightPointerUpTs = e.timeStamp;
    }

    // If the user is drag-selecting text, don't treat this as a highlight click.
    if (
      state.mouseSelecting &&
      (state.mouseSelectMoved || getCurrentSelectionRange())
    ) {
      return;
    }

    state.isOpeningFromHighlightClick = true;
    const annId = highlighter.getAnnotationIdFromTarget(
      e.target,
      e.clientX,
      e.clientY
    );
    if (annId) {
      const ann = highlighter.annotations.get(annId);
      if (ann) {
        suppressSelectionChangeFor();
        showToolbarForAnnotation(ann, e.clientX);
        e.preventDefault();
        window.getSelection()?.removeAllRanges();
        setTimeout(() => {
          state.isOpeningFromHighlightClick = false;
        }, 0);
        return;
      }
    }
    setTimeout(() => {
      state.isOpeningFromHighlightClick = false;
    }, 0);
  }
  contentEl.addEventListener("pointerup", openFromHighlightPointerEvent);
  contentEl.addEventListener("click", openFromHighlightPointerEvent);

  // Keep popup interactions from collapsing selection.
  popup.addEventListener("mousedown", (e) => {
    markToolbarInteraction();
    const interactive = e.target.closest(
      "textarea, input, button, select, option, [contenteditable='true']"
    );
    if (
      popup.classList.contains("ld-annotation-toolbar-desktop") &&
      !interactive
    ) {
      e.preventDefault();
    }
  });
  popup.addEventListener("pointerdown", (e) => {
    markToolbarInteraction();
    e.stopPropagation();
    if (!preferMobileToolbar || !isDockedTopLayout()) return;
    const toolbarControl = e.target.closest(
      "[data-color], [data-action='delete'], [data-action='delete-confirm'], [data-action='delete-cancel']"
    );
    if (!toolbarControl) return;
    state.lastToolbarControlPointerDownTs = performance.now();
  });
  popup.addEventListener(
    "touchstart",
    () => {
      markToolbarInteraction();
    },
    { passive: true }
  );

  // --- Color button click ---
  popup.addEventListener("click", async (e) => {
    markToolbarInteraction();
    if (
      state.isOpen &&
      performance.now() - state.openedAt < 140 &&
      e.target.closest(
        "[data-action='delete'], [data-action='delete-confirm'], [data-action='delete-cancel']"
      )
    ) {
      return;
    }
    const deleteBtn = e.target.closest("[data-action='delete']");
    if (deleteBtn) {
      if (!state.pendingAnnotation) return;
      popup._deleteConfirm.hidden = !popup._deleteConfirm.hidden;
      return;
    }
    const deleteCancel = e.target.closest("[data-action='delete-cancel']");
    if (deleteCancel) {
      popup._deleteConfirm.hidden = true;
      return;
    }
    const deleteConfirm = e.target.closest("[data-action='delete-confirm']");
    if (deleteConfirm) {
      popup._deleteConfirm.hidden = true;
      await handleDelete();
      return;
    }

    const colorBtn = e.target.closest("[data-color]");
    if (!colorBtn) return;
    popup._deleteConfirm.hidden = true;
    await applyColor(colorBtn.dataset.color);
  });

  popup._noteInput.addEventListener("keydown", async (e) => {
    markToolbarInteraction();
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      await closeAndPersist();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePopup();
      clearPending({ keepSelection: true });
    }
  });

  popup._noteInput.addEventListener("input", () => {
    markToolbarInteraction();
    if (
      state.pendingRange &&
      !state.pendingAnnotation &&
      !state.pendingColor &&
      popup._noteInput.value.trim().length > 0
    ) {
      state.pendingColor = DEFAULT_HIGHLIGHT_COLOR;
      updateColorButtons(popup, state.pendingColor);
      updateDraftHighlight();
    }
    if (state.saveStatus === "error") {
      setSaveStatus("idle", "");
    }
    void resizeNoteInput();
    reflowPopupPosition();
  });

  popup._noteInput.addEventListener("focus", () => {
    markToolbarInteraction();
    if (preferMobileToolbar) {
      state.dockedEditingSession = true;
      setPopupLayout("docked-top");
    }
    setMode("note_editing");
    void resizeNoteInput();
    reflowPopupPosition();
  });

  popup._noteInput.addEventListener("blur", async (e) => {
    markToolbarInteraction();
    if (!state.isOpen) return;
    const blurToToolbarControl =
      performance.now() - state.lastToolbarControlPointerDownTs < 260;
    if (blurToToolbarControl) {
      if (preferMobileToolbar && state.dockedEditingSession) {
        setPopupLayout("docked-top");
        schedulePopupReflow();
      }
      return;
    }
    await savePendingAnnotation();
    if (state.pendingAnnotation) {
      const latest = highlighter.annotations.get(String(state.pendingAnnotation.id));
      if (latest) {
        state.pendingAnnotation = latest;
      }
    }
    if (preferMobileToolbar) {
      const nextActive = e.relatedTarget || document.activeElement;
      const focusStillInside = nextActive && isPopupTarget(nextActive);
      if (state.dockedEditingSession || focusStillInside) {
        setPopupLayout("docked-top");
      } else {
        setPopupLayout("anchored");
      }
      schedulePopupReflow();
    }
  });

  document.addEventListener("keydown", async (e) => {
    if (!state.isOpen) return;
    if (e.key === "Escape") {
      e.preventDefault();
      await closeAndPersist();
    }
  });

  // Close on outside pointer interaction.
  document.addEventListener("pointerdown", async (e) => {
    if (!state.isOpen) return;
    if (performance.now() - state.lastToolbarInteractionTs < 320) return;
    if (!isPopupTarget(e.target)) {
      if (preferMobileToolbar && isDockedTopLayout()) {
        state.dockedEditingSession = false;
      }
      popup._deleteConfirm.hidden = true;
      const contextToken = state.contextToken;
      await closeAndPersist({ contextToken });
    }
  });

  window.addEventListener("resize", reflowPopupPosition, { passive: true });
  document.addEventListener(
    "scroll",
    () => {
      state.lastScrollAt = performance.now();
      if (preferMobileToolbar && state.pendingRange && state.isOpen) {
        restorePendingSelectionIfNeeded();
      }
    },
    {
      passive: true,
      capture: true,
    }
  );
  document.addEventListener("scroll", reflowPopupPosition, {
    passive: true,
    capture: true,
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", reflowPopupPosition, {
      passive: true,
    });
    window.visualViewport.addEventListener("scroll", reflowPopupPosition, {
      passive: true,
    });
  }
  updateMobileToolbarOffset();
}

/**
 * Load annotations from the API.
 */
async function loadAnnotations(highlighter, apiBase, bookmarkId, assetId) {
  if (!bookmarkId || Number(assetId) <= 0) {
    highlighter.load([]);
    return;
  }

  try {
    const response = await fetch(
      joinPath(apiBase, `bookmarks/${bookmarkId}/annotations/`)
    );
    if (!response.ok) return;

    const data = await response.json();
    const annotations = Array.isArray(data) ? data : data.results || [];
    const assetAnnotations = annotations.filter(
      (a) => a.article_asset == assetId
    );
    highlighter.load(assetAnnotations);
  } catch (err) {
    console.error("Failed to load annotations:", err);
  }
}

// ---- Unified Highlight Popup ----

/**
 * Create the unified highlight popup element.
 */
function createHighlightPopup({ compact }) {
  const popup = document.createElement("div");
  popup.id = "ld-highlight-popup";
  popup.className = compact
    ? "ld-annotation-toolbar ld-annotation-toolbar-desktop"
    : "ld-annotation-toolbar ld-annotation-toolbar-mobile";
  popup.dataset.open = "false";
  popup.dataset.mode = "new";
  popup.dataset.noteOpen = "true";
  popup.dataset.state = "idle";
  popup.dataset.layout = "anchored";
  popup.dataset.saveState = "idle";
  popup.dataset.anchorVisible = "true";
  popup.hidden = true;
  popup.setAttribute("role", "dialog");
  popup.setAttribute("aria-label", gettext("Highlight actions"));
  popup.setAttribute("aria-hidden", "true");

  const toolbar = document.createElement("div");
  toolbar.className = "ld-annotation-toolbar-row";

  const colorCard = document.createElement("div");
  colorCard.className = "ld-annotation-card ld-annotation-card-colors";
  const colorGroup = document.createElement("div");
  colorGroup.className = "ld-annotation-color-group";

  // Color buttons
  for (const [name, cfg] of Object.entries(HIGHLIGHT_COLORS)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ld-annotation-color-btn";
    btn.dataset.color = name;
    const colorLabel = HIGHLIGHT_COLOR_LABELS[name] || String(cfg.label || "");
    btn.title = interpolate(gettext("Highlight: %(color)s"), { color: colorLabel });
    btn.setAttribute("aria-label", interpolate(gettext("Highlight with %(color)s"), { color: colorLabel }));
    btn.style.setProperty(
      "--ld-annotation-color",
      toSolidColor(cfg.bg)
    );
    colorGroup.appendChild(btn);
  }
  colorCard.appendChild(colorGroup);
  toolbar.appendChild(colorCard);

  const actionCard = document.createElement("div");
  actionCard.className = "ld-annotation-card ld-annotation-card-actions";
  const actions = document.createElement("div");
  actions.className = "ld-annotation-action-group";
  actions.hidden = true;

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "ld-annotation-action-btn ld-annotation-action-delete";
  deleteBtn.title = gettext("Remove highlight");
  deleteBtn.setAttribute("aria-label", gettext("Remove highlight"));
  deleteBtn.dataset.action = "delete";
  deleteBtn.innerHTML = READER_ICONS.delete;
  deleteBtn.hidden = true;
  actions.appendChild(deleteBtn);

  const deleteWrap = document.createElement("span");
  deleteWrap.className = "annotation-delete-wrap";
  deleteWrap.appendChild(deleteBtn);

  const deleteConfirm = document.createElement("span");
  deleteConfirm.className = "ld-confirm-popup-inline";
  deleteConfirm.hidden = true;
  deleteConfirm.innerHTML = `
    <span class="confirm-popup-question">${gettext("Are you sure?")}</span>
    <span class="confirm-popup-actions">
      <button type="button" class="btn btn-sm" data-action="delete-cancel">${gettext("Cancel")}</button>
      <button type="button" class="btn btn-sm btn-error" data-action="delete-confirm">${gettext("Delete")}</button>
    </span>
  `;
  deleteWrap.appendChild(deleteConfirm);
  actions.appendChild(deleteWrap);

  actionCard.appendChild(actions);
  toolbar.appendChild(actionCard);

  popup.appendChild(toolbar);

  const noteArea = document.createElement("div");
  noteArea.className = "ld-annotation-card ld-annotation-note-area";

  const noteMeta = document.createElement("div");
  noteMeta.className = "ld-annotation-note-meta";

  const saveStatus = document.createElement("span");
  saveStatus.className = "ld-annotation-save-status";
  saveStatus.setAttribute("aria-live", "polite");
  noteMeta.appendChild(saveStatus);
  noteArea.appendChild(noteMeta);

  const noteInput = document.createElement("textarea");
  noteInput.className = "ld-annotation-note-input";
  noteInput.placeholder = gettext("Click to add note");
  noteInput.rows = 1;
  noteInput.setAttribute("aria-label", gettext("Annotation note"));
  noteArea.appendChild(noteInput);
  popup.appendChild(noteArea);

  // Store references
  popup._deleteBtn = deleteBtn;
  popup._deleteConfirm = deleteConfirm;
  popup._actionCard = actionCard;
  popup._actionGroup = actions;
  popup._noteArea = noteArea;
  popup._noteInput = noteInput;
  popup._saveStatus = saveStatus;

  return popup;
}

/**
 * Get bounding rect of an annotation by reconstructing its range.
 */
function getAnnotationRect(ann, contentEl) {
  try {
    const selector = ann.selector;
    const anchor = TextQuoteAnchor.fromSelector(contentEl, {
      exact: selector.exact || ann.selected_text,
      prefix: selector.prefix,
      suffix: selector.suffix,
    });
    const hint =
      typeof selector.start === "number" ? selector.start : undefined;
    const range = anchor.toRange({ hint });
    return range.getBoundingClientRect();
  } catch {
    return null;
  }
}

/**
 * Position popup below a rect, keeping it within the viewport.
 */
function positionPopup(popup, rect, anchorClientX = null) {
  popup.style.visibility = "hidden";
  popup.hidden = false;
  popup.style.left = "0px";
  popup.style.top = "0px";

  const gap = 6;
  const popupWidth = popup.offsetWidth;
  const popupHeight = popup.offsetHeight;
  const margin = popup.classList.contains("ld-annotation-toolbar-mobile") ? 8 : 10;

  const preferredX = Number.isFinite(anchorClientX)
    ? anchorClientX
    : rect.right - popupWidth * 0.25;
  let left = preferredX - popupWidth * 0.5;
  left = Math.max(
    margin,
    Math.min(left, window.innerWidth - popupWidth - margin)
  );

  let top = rect.bottom + gap;

  if (top + popupHeight > window.innerHeight - margin) {
    top = rect.top - popupHeight - gap;
  }
  if (top < margin) top = margin;

  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
  popup.style.visibility = "";
}

async function measureLineHeightPx(input) {
  const styles = window.getComputedStyle(input);
  const parsed = parseFloat(styles.lineHeight);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }

  const probe = document.createElement("span");
  probe.textContent = "M";
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  probe.style.whiteSpace = "pre";
  probe.style.font = styles.font;
  probe.style.lineHeight = styles.lineHeight;
  probe.style.letterSpacing = styles.letterSpacing;
  document.body.appendChild(probe);
  const h = probe.getBoundingClientRect().height;
  document.body.removeChild(probe);
  return h > 0 ? h : 18;
}

/**
 * Update color button borders to reflect current color.
 */
function updateColorButtons(popup, currentColor) {
  popup.querySelectorAll("[data-color]").forEach((btn) => {
    btn.dataset.active = btn.dataset.color === currentColor ? "true" : "false";
  });
}

// Expose to global scope for inline template scripts
window.renderReader = renderReader;
