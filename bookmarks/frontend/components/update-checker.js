import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";

const CHECK_INTERVAL = 24 * 60 * 60 * 1000; // 24 hours
const STORAGE_KEY = "ld:update-checker";

const GITHUB_API =
  "https://api.github.com/repos/WooHooDai/linkding-cn/releases/latest";

// ── Storage (single key) ──

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore
  }
}

function getCurrentVersion() {
  return document.documentElement.dataset.appVersion || "";
}

function parseVersion(v) {
  return v.replace(/^v/, "").trim();
}

function shouldCheck(state) {
  if (!state.lastCheck) return true;
  return Date.now() - state.lastCheck > CHECK_INTERVAL;
}

// ── Network ──

async function fetchLatestRelease() {
  const resp = await fetch(GITHUB_API);
  if (!resp.ok) return null;
  const data = await resp.json();
  return {
    version: data.tag_name || "",
    name: data.name || "",
    body: data.body || "",
    url: data.html_url || "",
    publishedAt: data.published_at || "",
  };
}

async function renderMarkdown(text) {
  try {
    const resp = await fetch("/api/render-markdown/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
      body: JSON.stringify({ markdown: text }),
    });
    if (!resp.ok) return null;
    return (await resp.json()).html || null;
  } catch {
    return null;
  }
}

function getCookie(name) {
  for (const c of document.cookie.split(";")) {
    const [k, v] = c.trim().split("=");
    if (k === name) return decodeURIComponent(v);
  }
  return null;
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

// ── Modal ──

let activeEscapeHandler = null;

function escapeHtml(str) {
  const el = document.createElement("span");
  el.textContent = str;
  return el.innerHTML;
}

function showUpdateModal(release, onDismiss) {
  closeUpdateModal();

  const overlay = document.createElement("div");
  overlay.className = "ld-update-modal-overlay";
  overlay.innerHTML = `
    <div class="ld-update-modal">
      <div class="ld-update-modal-header">
        <h3>${escapeHtml(release.name || release.version)}</h3>
        <span class="ld-update-modal-date">${escapeHtml(formatDate(release.publishedAt))}</span>
      </div>
      <div class="ld-update-modal-body markdown">${
        release.html || `<p>${gettext("Unable to load release notes.")}</p>`
      }</div>
      <div class="ld-update-modal-footer">
        <a href="${escapeHtml(release.url)}" target="_blank" rel="noopener" class="btn btn-link">${gettext("View on GitHub")}</a>
        <button type="button" class="btn btn-primary ld-update-modal-gotit">${gettext("Got it")}</button>
      </div>
    </div>
  `;

  document.body.style.overflow = "hidden";

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeUpdateModal();
  });

  activeEscapeHandler = (e) => {
    if (e.key === "Escape") closeUpdateModal();
  };
  document.addEventListener("keydown", activeEscapeHandler);

  overlay.querySelector(".ld-update-modal-gotit").addEventListener("click", () => {
    closeUpdateModal();
    if (onDismiss) onDismiss();
  });

  document.body.appendChild(overlay);
}

function closeUpdateModal() {
  document.querySelector(".ld-update-modal-overlay")?.remove();
  document.body.style.overflow = "";
  if (activeEscapeHandler) {
    document.removeEventListener("keydown", activeEscapeHandler);
    activeEscapeHandler = null;
  }
}

// ── Behavior ──

class UpdateCheckerBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.element = element;
    this._init();
  }

  async _init() {
    this.dots = document.querySelectorAll("[ld-update-dot]");
    this.menuItems = document.querySelectorAll("[ld-update-menu-item]");
    this.state = loadState();

    this._syncVisibility();

    // "New version" menu item click → open modal
    this.menuItems.forEach((item) => {
      const trigger = item.querySelector(".ld-update-trigger");
      if (trigger) {
        trigger.addEventListener("click", (e) => {
          e.preventDefault();
          const s = loadState();
          if (s.release) showUpdateModal(s.release, () => this._dismiss());
        });
      }
    });

    // Auto-check
    if (shouldCheck(this.state)) {
      try {
        await this._checkForUpdates();
      } catch (e) {
        console.error("[UpdateChecker]", e);
      }
    }
  }

  async _checkForUpdates() {
    const current = parseVersion(getCurrentVersion());
    if (!current) return;

    const release = await fetchLatestRelease();
    this.state.lastCheck = Date.now();

    if (!release) {
      saveState(this.state);
      return;
    }

    const latest = parseVersion(release.version);
    if (!latest || latest === current) {
      delete this.state.release;
      saveState(this.state);
      this._syncVisibility();
      return;
    }

    let html = "";
    if (release.body) {
      html = (await renderMarkdown(release.body)) || "";
    }

    this.state.release = {
      version: release.version,
      name: release.name,
      html,
      url: release.url,
      publishedAt: release.publishedAt,
    };
    saveState(this.state);

    this._syncVisibility();
  }

  _syncVisibility() {
    const { release, dismissedVersion } = this.state;
    const current = parseVersion(getCurrentVersion());
    const hasUpdate = release && parseVersion(release.version) !== current;
    const dotDismissed =
      hasUpdate && parseVersion(release.version) === parseVersion(dismissedVersion || "");

    // Dots: show when there's an update and user hasn't dismissed
    this.dots.forEach((el) => {
      el.style.display = hasUpdate && !dotDismissed ? "" : "none";
    });
    // Menu items: show whenever there's an update (until user actually updates)
    this.menuItems.forEach((el) => {
      el.style.display = hasUpdate ? "" : "none";
    });
  }

  _dismiss() {
    // Only dismiss the dots, keep menu items visible
    const { release } = this.state;
    if (release) {
      this.state.dismissedVersion = release.version;
      saveState(this.state);
    }
    this.dots.forEach((el) => {
      el.style.display = "none";
    });
  }
}

document.addEventListener("turbo:before-cache", closeUpdateModal);

registerBehavior("ld-update-checker", UpdateCheckerBehavior);
