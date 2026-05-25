import { LitElement, html } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { HIGHLIGHT_COLORS } from "./anchoring/highlighter";
import { READER_ICONS } from "./reader-icons";
import { gettext, ngettext, interpolate } from "../utils/i18n.js";

function getCSRFToken() {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  if (m) return m[1];
  const meta = document.querySelector('meta[name="csrfmiddlewaretoken"]');
  return meta ? meta.content : "";
}

function normalizeBaseUrl(baseUrl) {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  return `${value.replace(/\/+$/, "")}/`;
}

function joinPath(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${String(path || "").replace(/^\/+/, "")}`;
}

export class ReaderSidebar extends LitElement {
  createRenderRoot() { return this; }

  static properties = {
    open: { type: Boolean }, annotations: { type: Array }, bookmarkData: { type: Object },
    assetList: { type: Array }, activeTab: { type: String }, apiBase: { type: String },
    assetsBase: { type: String }, bookmarksIndexUrl: { type: String },
    _editingTags: { type: Boolean, state: true }, _tagInputValue: { type: String, state: true },
    _tagSuggestions: { type: Array, state: true }, _tagSelectedIdx: { type: Number, state: true },
    _allTags: { type: Array, state: true }, _colorPickerId: { type: String, state: true },
    _confirmDelAnnId: { type: String, state: true }, _confirmDelFileId: { type: String, state: true },
    _renameAssetId: { type: String, state: true }, _renameValue: { type: String, state: true },
    _copyToastId: { type: String, state: true }, _confirmDelBookmark: { type: Boolean, state: true },
    _confirmArchive: { type: Boolean, state: true },
  };

  constructor() {
    super();
    this.open = true; this.annotations = []; this.bookmarkData = {}; this.assetList = [];
    const savedTab = localStorage.getItem("reader_sidebar_tab") || "annotations";
    this.activeTab = savedTab === "info" ? "details" : savedTab;
    this.apiBase = "/api/";
    this.assetsBase = "/assets";
    this.bookmarksIndexUrl = "/bookmarks";
    this._editingTags = false;
    this._tagInputValue = ""; this._tagSuggestions = []; this._tagSelectedIdx = -1;
    this._allTags = []; this._colorPickerId = null; this._confirmDelAnnId = null;
    this._confirmDelFileId = null; this._renameAssetId = null; this._renameValue = "";
    this._copyToastId = null; this._confirmDelBookmark = false;
    this._confirmArchive = false;
  }

  updated(changed) {
    if (changed.has("activeTab")) localStorage.setItem("reader_sidebar_tab", this.activeTab);
    if (changed.has("_editingTags") && this._editingTags) {
      this.updateComplete.then(() => { const el = this.querySelector(".info-tag-input"); if (el) el.focus(); });
    }
  }

  connectedCallback() {
    super.connectedCallback();
    this._out = (e) => {
      if (this._colorPickerId && !e.target.closest(".annotation-color-dot")) this._colorPickerId = null;
      if (this._confirmDelAnnId && !e.target.closest(".ld-confirm-popup-inline") && !e.target.closest(".annotation-action-delete")) this._confirmDelAnnId = null;
      if (this._confirmDelFileId && !e.target.closest(".ld-confirm-popup-inline")) this._confirmDelFileId = null;
      if (this._confirmArchive && !e.target.closest(".ld-confirm-popup-inline") && !e.target.closest(".info-archive-row")) this._confirmArchive = false;
      if (this._confirmDelBookmark && !e.target.closest(".ld-confirm-popup-inline") && !e.target.closest(".info-delete-row")) this._confirmDelBookmark = false;
    };
    document.addEventListener("mousedown", this._out);
  }

  disconnectedCallback() { super.disconnectedCallback(); document.removeEventListener("mousedown", this._out); }

  async _patchBookmark(f, v) {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/`), {
        method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ [f]: v }),
      });
      if (r.ok) { const u = await r.json(); this.bookmarkData = { ...this.bookmarkData, ...u }; document.dispatchEvent(new CustomEvent("bookmark-updated", { detail: u })); }
    } catch {}
  }

  async _patchAsset(assetId, body) {
    const bm = this.bookmarkData || {}; if (!bm.id) return false;
    try { const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/assets/${assetId}/`), { method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() }, body: JSON.stringify(body) }); if (r.ok) { this._reloadAssets(); return true; } } catch {} return false;
  }

  async _deleteAsset(assetId) {
    const bm = this.bookmarkData || {};
    if (!bm.id) return false;
    try {
      const r = await fetch(
        joinPath(this.apiBase, `bookmarks/${bm.id}/assets/${assetId}/`),
        { method: "DELETE", headers: { "X-CSRFToken": getCSRFToken() } }
      );
      if (!r.ok) {
        console.error("Failed to delete asset:", r.status, r.statusText);
        return false;
      }
      this._reloadAssets();
      return true;
    } catch (err) {
      console.error("Failed to delete asset:", err);
      return false;
    }
  }

  async _trashBookmark() {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/trash/`), {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      window.location.href = this.bookmarksIndexUrl;
    } catch {}
  }

  _reloadAssets() { this.dispatchEvent(new CustomEvent("reload-assets", { bubbles: true, composed: true, detail: { bookmarkId: this.bookmarkData?.id } })); }
  _emitAnn(id, action, extra = {}) { this.dispatchEvent(new CustomEvent("annotation-action", { bubbles: true, composed: true, detail: { id, action, ...extra } })); }
  _handleAnnCopy(annId) { this._emitAnn(annId, "copy"); this._copyToastId = String(annId); setTimeout(() => this._copyToastId = null, 1500); }
  _handleAnnColor(annId, color) { this._colorPickerId = null; this._emitAnn(annId, "change-color", { color }); }

  // ---- Edit helpers ----

  _saveField(field, value) {
    const v = value.trim();
    if (v !== (this.bookmarkData[field] || "")) {
      this.bookmarkData = { ...this.bookmarkData, [field]: v };
      this._patchBookmark(field, v);
    }
  }

  _saveAnnotationNote(annId, value) {
    this._emitAnn(annId, "edit-note", { note: value.trim() });
  }

  _escapeBlur(e) { if (e.key === "Escape") { e.preventDefault(); e.target.blur(); } }

  // --- Tags ---

  async _clickTags() {
    this._tagInputValue = (this.bookmarkData.tag_names || []).join(" ");
    this._editingTags = true;
    if (!this._allTags.length) { try { const r = await fetch(joinPath(this.apiBase, "tags/?limit=5000")); if (r.ok) { const d = await r.json(); this._allTags = (d.results || d).map(t => t.name); } } catch {} }
  }

  _onTagInput() { const i = this.querySelector(".info-tag-input"); if (!i) return; this._tagInputValue = i.value; const w = i.value.slice(0, i.selectionStart).split(/\s+/); const c = (w[w.length - 1] || "").toLowerCase(); this._tagSuggestions = c.length && this._allTags.length ? this._allTags.filter(t => t.toLowerCase().startsWith(c) && t !== c).slice(0, 6) : []; this._tagSelectedIdx = -1; }
  _onTagKeydown(e) { if (e.key === "Escape") { this._tagSuggestions = []; return; } if (e.key === "ArrowDown") { e.preventDefault(); this._tagSelectedIdx = Math.min(this._tagSelectedIdx + 1, this._tagSuggestions.length - 1); } else if (e.key === "ArrowUp") { e.preventDefault(); this._tagSelectedIdx = Math.max(this._tagSelectedIdx - 1, -1); } else if (e.key === "Enter" && this._tagSelectedIdx >= 0) { e.preventDefault(); this._insertTagSuggestion(this._tagSuggestions[this._tagSelectedIdx]); } }
  _insertTagSuggestion(tag) { const i = this.querySelector(".info-tag-input"); if (!i) return; const cp = i.selectionStart; const v = this._tagInputValue; const bf = v.slice(0, cp); const af = v.slice(cp); const ws = bf.split(/\s+/); ws[ws.length - 1] = tag; const nb = ws.join(" "); this._tagInputValue = nb + af; this._tagSuggestions = []; this._tagSelectedIdx = -1; this.updateComplete.then(() => { i.focus(); i.setSelectionRange(nb.length, nb.length); }); }
  _saveTags() { const i = this.querySelector(".info-tag-input"); this._editingTags = false; this._tagSuggestions = []; this._patchBookmark("tag_names", (i ? i.value : this._tagInputValue).split(/\s+/).map(s => s.trim()).filter(Boolean)); }

  // --- File list ---

  _startRename(assetId, currentName) { this._renameAssetId = String(assetId); this._renameValue = currentName; this.updateComplete.then(() => { const el = this.querySelector(".info-file-rename-input"); if (el) { el.focus(); el.select(); } }); }
  _saveRename(assetId) { const n = this._renameValue.trim(); if (n) this._patchAsset(assetId, { display_name: n }); this._renameAssetId = null; this._renameValue = ""; }

  _fmtDate(iso) { if (!iso) return ""; try { const d = new Date(iso); return `${d.getFullYear()}/${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")}`; } catch { return ""; } }
  _fmtDT(iso) { if (!iso) return ""; try { const d = new Date(iso); return `${d.getFullYear()}/${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}:${String(d.getSeconds()).padStart(2,"0")}`; } catch { return ""; } }
  _fmtSize(b) { if (!b) return ""; if (b > 1048576) return (b/1048576).toFixed(1)+" MB"; if (b > 1024) return (b/1024).toFixed(0)+" KB"; return b+" B"; }

  // ---- Render ----

  _renderAnnotationItem(ann) {
    const colorBg = HIGHLIGHT_COLORS[ann.color]?.bg || HIGHLIGHT_COLORS.yellow.bg;
    const colorSolid = colorBg.includes("color-mix(")
      ? colorBg
      : colorBg.replace(/[\d.]+\)$/, "0.8)");
    return html`
      <div class="annotation-item" data-id="${ann.id}">
        <div class="annotation-text-row">
          <div class="annotation-color-bar" style="background-color: ${colorBg}"></div>
          <div class="annotation-text" @click=${() => this._emitAnn(ann.id, "jump")}>${ann.selected_text}</div>
        </div>
        <textarea class="annotation-textarea" .value=${ann.note_content || ""}
          placeholder=${gettext("Click to add note")} rows="1"
          @blur=${(e) => this._saveAnnotationNote(ann.id, e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
        <div class="annotation-actions">
          <span class="annotation-color-dot" style="background-color: ${colorSolid}" title=${gettext("Change color")}
            @click=${(e) => { e.stopPropagation(); this._colorPickerId = this._colorPickerId === String(ann.id) ? null : String(ann.id); }}></span>
          ${this._colorPickerId === String(ann.id) ? html`
            <span class="color-picker-popup" @mousedown=${(e) => e.stopPropagation()}>
              ${Object.entries(HIGHLIGHT_COLORS).map(([name, cfg]) => html`
                <span class="color-picker-option ${name === ann.color ? "selected" : ""}"
                  style="background-color: ${cfg.bg.includes("color-mix(") ? cfg.bg : cfg.bg.replace(/[\d.]+\)$/, "0.8)")}"
                  @click=${() => this._handleAnnColor(ann.id, name)}></span>
              `)}
            </span>
          ` : html``}
          <button class="annotation-action-btn" title=${gettext("Copy")} @click=${() => this._handleAnnCopy(ann.id)} .innerHTML=${READER_ICONS["copy"]}></button>
          ${this._copyToastId === String(ann.id) ? html`<span class="copy-toast">${gettext("Copied")}</span>` : html``}
          <span class="annotation-spacer"></span>
          <span class="annotation-delete-wrap">
            <button class="annotation-action-btn annotation-action-delete" title=${gettext("Delete")}
              @click=${(e) => { e.stopPropagation(); this._confirmDelAnnId = String(ann.id); }}
              .innerHTML=${READER_ICONS["delete"]}></button>
            ${this._confirmDelAnnId === String(ann.id) ? html`
              <span class="ld-confirm-popup-inline">
                <span class="confirm-popup-question">${gettext("Are you sure?")}</span>
                <span class="confirm-popup-actions">
                  <button class="btn btn-sm" @click=${() => this._confirmDelAnnId = null}>${gettext("Cancel")}</button>
                  <button class="btn btn-sm btn-error" @click=${() => { this._confirmDelAnnId = null; this._emitAnn(ann.id, "delete"); }}>${gettext("Delete")}</button>
                </span>
              </span>
            ` : html``}
          </span>
        </div>
      </div>
    `;
  }

  _renderAnnotationsTab() {
    if (!this.annotations?.length) return html`<div class="sidebar-empty"><span class="sidebar-empty-icon" .innerHTML=${READER_ICONS["empty"]}></span><p>${gettext("No highlights yet")}</p><p class="sidebar-empty-hint">${gettext("Select text in the article to add highlights")}</p></div>`;
    const nc = this.annotations.filter(a => a.note_content).length;
    const highlightsText = interpolate(
      ngettext("%(count)s highlight", "%(count)s highlights", this.annotations.length),
      { count: this.annotations.length }
    );
    const notesText = nc > 0
      ? ` · ${interpolate(ngettext("%(count)s note", "%(count)s notes", nc), { count: nc })}`
      : "";
    return html`
      <div class="sidebar-section-header"><span>${highlightsText}${notesText}</span></div>
      <div class="annotation-list">${repeat(this.annotations, a => a.id, a => this._renderAnnotationItem(a))}</div>
    `;
  }

  _renderInfoTab() {
    const bm = this.bookmarkData || {};
    const files = this.assetList || [];

    return html`<div class="sidebar-bookmark-info">
      ${bm.preview_image_url ? html`<div class="info-section"><img class="info-preview-image" src="${bm.preview_image_url}" alt=${gettext("Preview")} /></div>` : html``}

      <div class="info-section">
        <input class="info-input info-title-input" .value=${bm.title || ""}
          placeholder=${gettext("Click to edit title")}
          @blur=${(e) => this._saveField("title", e.target.value)}
          @keydown=${this._escapeBlur} />
      </div>

      <div class="info-section">
        <a class="info-url" href="${bm.url || "#"}" target="_blank" rel="noopener">
          ${bm.favicon_url ? html`<img class="info-favicon" src="${bm.favicon_url}" width="16" height="16" />` : html``}
          <span class="info-url-text">${bm.url || "—"}</span>
          <span class="info-external-icon" .innerHTML=${READER_ICONS["external-link"]}></span>
        </a>
      </div>

      <div class="info-section info-dates">
        ${bm.date_added ? html`<span class="info-date" title=${interpolate(gettext("Added %(time)s"), { time: this._fmtDT(bm.date_added) })}><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-added"]}></span>${this._fmtDate(bm.date_added)}</span>` : html``}
        ${bm.date_modified ? html`<span class="info-date" title=${interpolate(gettext("Modified %(time)s"), { time: this._fmtDT(bm.date_modified) })}><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-modified"]}></span>${this._fmtDate(bm.date_modified)}</span>` : html``}
      </div>

      <div class="info-section">
        ${this._editingTags ? html`
          <div class="info-tag-autocomplete">
            <textarea class="info-input info-tag-input" rows="1"
              .value=${this._tagInputValue} @input=${this._onTagInput} @keydown=${this._onTagKeydown} @blur=${() => this._saveTags()}
              placeholder=${gettext("tag1 tag2 ...")} autocomplete="off" autocapitalize="off"></textarea>
            ${this._tagSuggestions.length ? html`<ul class="tag-suggestions">${this._tagSuggestions.map((t, i) => html`<li class="tag-suggestion ${i === this._tagSelectedIdx ? "selected" : ""}" @mousedown=${e => { e.preventDefault(); this._insertTagSuggestion(t); }}>${t}</li>`)}</ul>` : html``}
          </div>
        ` : html`<div class="info-value info-editable info-tags-display" @click=${() => this._clickTags()}>
          ${bm.tag_names?.length ? html`<span class="info-tags">${bm.tag_names.map(t => html`<span class="info-tag">#${t}</span>`)}</span>` : html`<span class="info-placeholder">${gettext("Click to edit tags")}</span>`}
        </div>`}
      </div>

      <!-- Status + Delete (between tags and description) -->
      <div class="info-section">
        <div class="info-switches">
          <label class="form-switch"><input type="checkbox" .checked=${bm.unread || false} @change=${e => this._patchBookmark("unread", e.target.checked)} /><i class="form-icon"></i> ${gettext("Unread")}</label>
          <label class="form-switch"><input type="checkbox" .checked=${bm.shared || false} @change=${e => this._patchBookmark("shared", e.target.checked)} /><i class="form-icon"></i> ${gettext("Shared")}</label>
        </div>
        <div class="info-archive-row">
          ${this._confirmArchive ? html`
            <span class="ld-confirm-popup-inline ld-confirm-popup-inline--static">
              <span class="confirm-popup-question">${gettext("Archive bookmark?")}</span>
              <span class="confirm-popup-actions">
                <button class="btn btn-sm" @click=${() => this._confirmArchive = false}>${gettext("Cancel")}</button>
                <button class="btn btn-sm btn-error" @click=${() => { this._confirmArchive = false; this._patchBookmark("is_archived", true); }}>${gettext("Archive")}</button>
              </span>
            </span>
          ` : html`<button class="btn btn-sm" @click=${() => this._confirmArchive = true}>${gettext("Archive bookmark")}</button>`}
        </div>
        <div class="info-delete-row">
          ${this._confirmDelBookmark ? html`
            <span class="ld-confirm-popup-inline ld-confirm-popup-inline--static">
              <span class="confirm-popup-question">${gettext("Move to trash?")}</span>
              <span class="confirm-popup-actions">
                <button class="btn btn-sm" @click=${() => this._confirmDelBookmark = false}>${gettext("Cancel")}</button>
                <button class="btn btn-sm btn-error" @click=${() => { this._confirmDelBookmark = false; this._trashBookmark(); }}>${gettext("Trash")}</button>
              </span>
            </span>
          ` : html`<button class="btn btn-error btn-sm" @click=${() => this._confirmDelBookmark = true}>${gettext("Delete bookmark")}</button>`}
        </div>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Description")}</div>
        <textarea class="info-textarea" .value=${bm.description || ""}
          placeholder=${gettext("Click to edit description")} rows="1"
          @blur=${(e) => this._saveField("description", e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Notes")}</div>
        <textarea class="info-textarea" .value=${bm.notes || ""}
          placeholder=${gettext("Click to edit notes")} rows="1"
          @blur=${(e) => this._saveField("notes", e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Files")}</div>
        ${files.length ? html`<div class="info-files">${files.map(a => {
          const isRen = this._renameAssetId === String(a.id);
          const fileUrl = a.file ? joinPath(this.assetsBase, `${a.id}`) : null;
          const name = a.display_name || gettext("File");
          const isArticle = String(a.id) === String(bm.latest_article);
          return html`<div class="info-file-item">
            ${isRen ? html`<input class="info-input info-file-rename-input" type="text" .value=${this._renameValue} @input=${e => this._renameValue = e.target.value} @keydown=${e => { if (e.key === "Enter") this._saveRename(a.id); if (e.key === "Escape") { this._renameAssetId = null; this._renameValue = ""; } }} @blur=${() => this._saveRename(a.id)} />`
            : html`<span class="info-file-name" title="${name}" @click=${() => { if (fileUrl) window.open(fileUrl, "_blank"); }}>
              <span class="truncate">${fileUrl ? html`<a href="${fileUrl}" target="_blank" class="info-file-link" @click=${(e) => e.stopPropagation()}>${name}</a>` : html`<span>${name}</span>`}${a.status === "pending" ? html`<span class="info-file-status"> (${gettext("queued")})</span>` : html``}${a.status === "failure" ? html`<span class="info-file-status info-file-failed"> (${gettext("failed")})</span>` : html``}</span>
              ${a.file_size ? html`<span class="info-file-size">${this._fmtSize(a.file_size)}</span>` : html``}
            </span>`}
            <div class="info-file-actions">
              <button class="annotation-action-btn" title=${gettext("Rename")} @click=${() => this._startRename(a.id, name)} .innerHTML=${READER_ICONS["rename"]}></button>
              <span class="info-file-delete-wrap">
                <button class="annotation-action-btn annotation-action-delete ${isArticle ? "disabled" : ""}"
                  title="${isArticle ? gettext("Cannot delete article asset") : gettext("Remove")}"
                  ?disabled=${isArticle}
                  @click=${() => { if (!isArticle) this._confirmDelFileId = String(a.id); }}
                  .innerHTML=${READER_ICONS["delete"]}></button>
                ${!isArticle && this._confirmDelFileId === String(a.id) ? html`
                  <span class="ld-confirm-popup-inline ld-confirm-popup-inline--file">
                    <span class="confirm-popup-question">${gettext("Are you sure?")}</span>
                    <span class="confirm-popup-actions">
                      <button class="btn btn-sm" @click=${() => this._confirmDelFileId = null}>${gettext("Cancel")}</button>
                      <button class="btn btn-sm btn-error" @click=${() => { this._confirmDelFileId = null; this._deleteAsset(a.id); }}>${gettext("Remove")}</button>
                    </span>
                  </span>
                ` : html``}
              </span>
            </div>
          </div>`;
        })}</div>` : html`<div class="info-placeholder">${gettext("No files")}</div>`}
      </div>
    </div>`;
  }

  render() {
    return html`
      <div id="reader-sidebar" data-open=${this.open ? "true" : "false"}>
        <div id="sidebar-tabs">
          <button data-tab="annotations" data-active=${this.activeTab === "annotations" ? "true" : "false"} @click=${() => (this.activeTab = "annotations")}>
            <span class="tab-icon" .innerHTML=${READER_ICONS["tab-annotations"]}></span>${gettext("Highlights")}
          </button>
          <button data-tab="details" data-active=${this.activeTab === "details" ? "true" : "false"} @click=${() => (this.activeTab = "details")}>
            <span class="tab-icon" .innerHTML=${READER_ICONS["tab-details"]}></span>${gettext("Details")}
          </button>
        </div>
        <div id="sidebar-annotations" class="sidebar-tab-content scrollbar" data-active=${this.activeTab === "annotations" ? "true" : "false"}>${this._renderAnnotationsTab()}</div>
        <div id="sidebar-bookmark-info" class="sidebar-tab-content scrollbar" data-active=${this.activeTab === "details" ? "true" : "false"}>${this._renderInfoTab()}</div>
      </div>
    `;
  }
}

customElements.define("reader-sidebar", ReaderSidebar);
