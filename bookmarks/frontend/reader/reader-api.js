/**
 * Reader API utilities — shared fetch wrappers for annotations,
 * reading progress, bookmark data, and asset lists.
 */
import { describeRange } from "./anchoring/index.js";

export function getCSRFToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  if (match) return match[1];
  const meta = document.querySelector('meta[name="csrfmiddlewaretoken"]');
  return meta ? meta.content : "";
}

export function normalizeBaseUrl(baseUrl) {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  return `${value.replace(/\/+$/, "")}/`;
}

export function joinPath(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${String(path || "").replace(/^\/+/, "")}`;
}

export function parseJsonScriptValue(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  try {
    return JSON.parse(el.textContent);
  } catch {
    return fallback;
  }
}

// ---- Annotation API ----

export async function patchAnnotation(apiBase, id, updates) {
  const response = await fetch(joinPath(apiBase, `annotations/${id}/`), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

export async function deleteAnnotation(apiBase, id) {
  const response = await fetch(joinPath(apiBase, `annotations/${id}/`), {
    method: "DELETE",
    headers: { "X-CSRFToken": getCSRFToken() },
  });
  if (!response.ok) throw new Error(`API error: ${response.status}`);
}

// ---- Reading Progress API ----

export async function fetchReadingProgress(apiBase, bookmarkId) {
  const response = await fetch(
    joinPath(apiBase, `bookmarks/${bookmarkId}/reading-progress/`),
  );
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

export async function patchReadingProgress(apiBase, bookmarkId, updates) {
  const response = await fetch(
    joinPath(apiBase, `bookmarks/${bookmarkId}/reading-progress/`),
    {
      method: "PATCH",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
      },
      body: JSON.stringify(updates),
    },
  );
  if (response.status === 409) {
    const error = new Error("Reading progress was updated elsewhere");
    error.status = response.status;
    try {
      error.data = await response.json();
    } catch {
      error.data = null;
    }
    throw error;
  }
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

function appendReadingProgressFormValue(formData, key, value) {
  if (value === undefined) return;
  if (value === null) {
    formData.append(key, "");
  } else if (typeof value === "object") {
    formData.append(key, JSON.stringify(value));
  } else {
    formData.append(key, String(value));
  }
}

export function sendReadingProgressBeacon(apiBase, bookmarkId, updates) {
  if (!navigator.sendBeacon) return false;

  const formData = new URLSearchParams();
  Object.entries(updates).forEach(([key, value]) => {
    appendReadingProgressFormValue(formData, key, value);
  });
  formData.append("csrfmiddlewaretoken", getCSRFToken());

  return navigator.sendBeacon(
    joinPath(apiBase, `bookmarks/${bookmarkId}/reading-progress/`),
    formData,
  );
}

// ---- Bookmark & Asset API ----

export async function fetchBookmarkData(bookmarkId, sidebar, apiBase) {
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
          }),
        );
      }
    }
  } catch (err) {
    console.warn("Failed to fetch bookmark data:", err);
  }
}

export async function fetchAssetList(bookmarkId, sidebar, apiBase) {
  try {
    const resp = await fetch(
      joinPath(apiBase, `bookmarks/${bookmarkId}/assets/`),
    );
    if (resp.ok) {
      const data = await resp.json();
      sidebar.assetList = Array.isArray(data) ? data : data.results || [];
    }
  } catch (err) {
    console.warn("Failed to fetch asset list:", err);
  }
}

// ---- Annotation Helpers ----

function normalizeAnnotationText(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

function isConfidentAnnotationRange(ann, range) {
  const rangeText = normalizeAnnotationText(range.toString());
  const selector = ann.selector || {};
  const exactText = normalizeAnnotationText(selector.exact);
  const selectedText = normalizeAnnotationText(ann.selected_text);
  return rangeText && (rangeText === exactText || rangeText === selectedText);
}

export async function restoreAnnotationToAsset(highlighter, apiBase, assetId, ann) {
  const range = highlighter.resolveAnnotationRange(ann);
  if (!isConfidentAnnotationRange(ann, range)) {
    throw new Error("Resolved range did not match stored annotation text");
  }

  const { position, quote } = describeRange(highlighter.root, range);
  return patchAnnotation(apiBase, ann.id, {
    article_asset: assetId,
    selector: { ...quote, start: position.start, end: position.end },
    selected_text: range.toString(),
  });
}
