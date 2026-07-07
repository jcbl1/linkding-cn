/**
 * Reading progress — save/restore scroll position across sessions.
 *
 * ReadingProgressController manages a single bookmark's reading progress
 * via localStorage (crash recovery) and server-side PATCH (cross-device sync).
 */
import { TextPositionAnchor, TextQuoteAnchor } from "./anchoring/index.js";
import {
  fetchReadingProgress,
  patchReadingProgress,
  sendReadingProgressBeacon,
} from "./reader-api.js";
import { gettext, interpolate } from "../utils/i18n.js";

// 无内容元素 + 媒体元素：对其使用 element_selector（tag+index+offset）定位。
const VOID_OR_MEDIA_TAGS = new Set([
  "IMG", "VIDEO", "AUDIO", "CANVAS", "SVG", "MATH",
  "IFRAME", "EMBED", "OBJECT", "HR",
]);

// ---- Scroll metrics helpers ----

function formatProgressPercent(ratio) {
  const pct = ratio * 100;
  return pct < 1 ? pct.toFixed(1) : String(Math.round(pct));
}

function getScrollableHeight(el) {
  return Math.max(0, el.scrollHeight - el.clientHeight);
}

function isAtReadingEnd(el) {
  const h = getScrollableHeight(el);
  return h <= 24 || el.scrollTop >= h - 24;
}

export function getScrollMetrics(el) {
  const h = getScrollableHeight(el);
  const raw = h > 0 ? Math.min(1, Math.max(0, el.scrollTop / h)) : 1;
  return {
    progress: isAtReadingEnd(el) ? 1 : raw,
    scroll_top: Math.round(el.scrollTop),
    scroll_height: Math.round(el.scrollHeight),
    client_width: Math.round(el.clientWidth),
    client_height: Math.round(el.clientHeight),
  };
}

/**
 * 管理单个书签的阅读进度保存与恢复。
 *
 * 保存策略：
 *  1. 打开页面 → 并行获取服务端进度 + 等待 DOM。有 ≥600px 滚动距离 → 弹恢复 toast。
 *  2. 每帧采集视口顶部的文字/元素锚点。
 *  3. active 状态下：1s debounce 写 localStorage（崩溃恢复），同时 PATCH 服务端。
 *     持续滚动时每 5s（MAX_SYNC_AGE_MS）强制同步一次，防止 debounce 无限重置。
 *  4. 页面隐藏 → sendBeacon 从内存 lastPayload 发送（含 base_date_modified）。
 *  5. 页面恢复可见 → 静默拉取服务端最新进度更新 baseDateModified；
 *     如有远端显著进度变更 → 弹远端 toast（可更新、不自动消失）。
 *  6. PATCH 409 → 更新 baseDateModified；active 状态弹新 toast，remote 状态更新当前 toast。
 *  7. 跨布局恢复使用 TextQuoteSelector（exact + prefix/suffix）。
 */
export class ReadingProgressController {
  static IDLE_SAVE_MS = 2000;
  static MAX_SYNC_AGE_MS = 5000;
  static SCROLL_CANCEL_PX = 600;
  static MIN_RESUME_PROGRESS = 0.02;
  static MIN_RESUME_SCROLL_TOP = 600;

  constructor(contentEl, readingRoot, bookmarkId, assetId, apiBase) {
    this.contentEl = contentEl;
    this.readingRoot = readingRoot;
    this.bookmarkId = bookmarkId;
    this.assetId = assetId;
    this.apiBase = apiBase;

    this.lastPayload = null;
    this._hasNewData = false;
    this.baseDateModified = null;

    this.toast = null;
    this.suppressScrollUntil = 0;
    this.initialScrollTop = contentEl.scrollTop;

    this.idleSaveTimer = null;
    this.scrollTicking = false;
    this._syncInFlight = false;
    this._lastSavedAt = 0; // 上次成功 PATCH 的时间戳，用于 MAX_SYNC_AGE_MS 强制同步
    this._progressRetryScheduled = false;

    // 同步状态机：控制保存/beacon/toast 行为
    //   "loading" — 初始化中，不保存、不弹 toast
    //   "resume"  — 恢复提示中，不保存、滚动超阈值自动转 active
    //   "active"  — 正常保存中，可弹远端 toast
    //   "remote"  — 远端进度提示中，不保存、滚动时更新 toast
    //   "closed"  — 用户关闭了远端提示，不保存、不弹 toast
    this.state = "loading";

    this._exiting = false;

    // bfcache 恢复时用于检测视口尺寸是否变化
    this._lastViewport = null;

    this._bindEvents();
    this._init();
  }

  // ---- Capture ----

  capture() {
    if (document.visibilityState !== "visible") return;
    const metrics = getScrollMetrics(this.contentEl);
    const payload = { article_asset: this.assetId, ...metrics };
    if (!isAtReadingEnd(this.contentEl)) {
      // 先清空所有锚点字段，防止滚动到新类型元素时残留旧数据
      payload.text_position_start = null;
      payload.text_quote_exact = "";
      payload.text_quote_prefix = "";
      payload.text_quote_suffix = "";
      payload.element_selector = null;
      const anchor = this._buildAnchor();
      Object.assign(payload, anchor);
    }
    this.lastPayload = payload;
    this._hasNewData = true;
  }

  /*
   * 采集视口顶部的定位锚点：
   * - 文字：caretRangeFromPoint 精确定位字符偏移 → TextQuoteSelector
   * - void/media 元素（IMG、VIDEO 等）：tag + index + offset 定位
   */
  _buildAnchor() {
    try {
      const root = this.readingRoot;
      const containerRect = this.contentEl.getBoundingClientRect();
      const top = containerRect.top;
      // 优先用 caretRangeFromPoint 精确定位文字：浏览器直接告诉我们
      // "屏幕坐标 (x, y) 处是哪个文本节点的第几个字符"，比 getClientRects 估算精确。
      if (document.caretRangeFromPoint) {
        const caret = document.caretRangeFromPoint(
          containerRect.left + containerRect.width / 2,
          top + 2, // +2px 避免取到行顶的边界歧义
        );
        if (
          caret &&
          caret.startContainer.nodeType === Node.TEXT_NODE &&
          root.contains(caret.startContainer)
        ) {
          // 计算 caret 在 root 全文中的字符偏移
          const preCaret = document.createRange();
          preCaret.setStart(root, 0);
          preCaret.setEnd(caret.startContainer, caret.startOffset);
          const charOffset = preCaret.toString().length;
          return this._buildQuotePayload(root, charOffset);
        }
      }
      // 视口顶部不是文字（图片、视频等）→ 遍历 DOM 找第一个有高度的 void/media 元素
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ALL);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (
          node.nodeType === Node.ELEMENT_NODE &&
          VOID_OR_MEDIA_TAGS.has(node.nodeName)
        ) {
          const rect = node.getBoundingClientRect();
          if (rect.height > 0 && rect.bottom > top) {
            const tag = node.nodeName;
            const sameTags = root.querySelectorAll(tag);
            const index = Array.prototype.indexOf.call(sameTags, node);
            if (index < 0) continue;
            const elOffset = Math.max(0, Math.min(1,
              (top - rect.top) / rect.height,
            ));
            return { element_selector: { tag, index, offset: elOffset } };
          }
        }
      }
      return null;
    } catch (err) {
      console.warn("Failed to build reading anchor:", err);
      return null;
    }
  }

  _buildQuotePayload(root, charOffset) {
    const end = Math.min(charOffset + 160, root.textContent.length);
    const snippet = root.textContent.slice(charOffset, end);
    if (!snippet.trim()) return null;
    try {
      const anchor = TextPositionAnchor.fromSelector(root, {
        start: charOffset,
        end,
      });
      const quote = TextQuoteAnchor.fromRange(
        root,
        anchor.toRange(),
      ).toSelector();
      return {
        text_position_start: charOffset,
        text_quote_exact: quote.exact,
        text_quote_prefix: quote.prefix || "",
        text_quote_suffix: quote.suffix || "",
      };
    } catch {
      return null;
    }
  }

  // ---- Restore ----

  /*
   * 恢复策略（按优先级）：
   * 1. 已读完 → 直接滚到底部
   * 2. 同布局 → scroll_top 像素级精确恢复（避免 TextQuoteSelector 的 rect 小数漂移）
   * 3. 跨布局 + 有文字锚点 → TextQuoteSelector 精确定位，滚到视口顶部
   * 4. 跨布局 + 有元素锚点（IMG 等）→ tag+index 找到元素，offset 在内部精确定位
   * 5. 跨布局 + 无锚点 → progress 比值近似恢复
   */
  restore(progress) {
    const el = this.contentEl;
    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);

    if (isAtReadingEnd(el)) {
      el.scrollTop = maxTop;
      return;
    }
    // 同布局 → scroll_top 像素级精确恢复
    // 布局不变时 scroll_top 是最精确的策略；TextQuoteSelector 的 getBoundingClientRect()
    // 会产生小数偏移，多次保存-恢复后会导致 scrollTop 漂移
    const sameW = Math.abs(el.clientWidth - (progress.client_width || 0)) <= 4;
    const sameH = Math.abs(el.clientHeight - (progress.client_height || 0)) <= 4;
    if (sameW && sameH && progress.scroll_top) {
      el.scrollTop = Math.min(progress.scroll_top, maxTop);
      return;
    }
    // 跨布局 + 有文字锚点 → TextQuoteSelector 精确定位
    if (progress.text_quote_exact) {
      try {
        const selector = {
          type: "TextQuoteSelector",
          exact: progress.text_quote_exact,
        };
        if (progress.text_quote_prefix) selector.prefix = progress.text_quote_prefix;
        if (progress.text_quote_suffix) selector.suffix = progress.text_quote_suffix;
        const hint = typeof progress.text_position_start === "number"
          ? progress.text_position_start : undefined;
        const range = TextQuoteAnchor.fromSelector(
          this.readingRoot, selector,
        ).toRange({ hint });
        const rect = range.getClientRects()[0] || range.getBoundingClientRect();
        if (rect) {
          const containerRect = el.getBoundingClientRect();
          el.scrollTop = Math.max(0, el.scrollTop + rect.top - containerRect.top);
          return;
        }
      } catch { /* 回退到其他策略 */ }
    }
    // 跨布局 + 有元素锚点（IMG、VIDEO 等）→ 按 tag+index 找到元素，用 offset 精确定位
    if (progress.element_selector) {
      const { tag, index, offset } = progress.element_selector;
      if (typeof index === "number") {
        const sameTags = this.readingRoot.querySelectorAll(tag);
        const target = sameTags[index];
        if (target) {
          const rect = target.getBoundingClientRect();
          const containerRect = el.getBoundingClientRect();
          // 元素顶部滚到视口顶部，再叠加 offset 在元素内部定位
          el.scrollTop = Math.max(0, el.scrollTop + rect.top - containerRect.top);
          if (typeof offset === "number" && rect.height > 0) {
            el.scrollTop += Math.round(rect.height * offset);
          }
          return;
        }
      }
    }
    // 跨布局 + 无锚点 → progress 比值近似恢复
    el.scrollTop = Math.round(
      maxTop * Math.min(1, Math.max(0, progress.progress || 0)),
    );
  }

  // ---- localStorage（崩溃恢复用，成功同步后即清理）----

  static _LS_KEY = "ld:reader:progress";
  static _LS_MAX_ENTRIES = 1000;
  static _LS_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;  // 7天

  _writeLocalStorage({ sync = false } = {}) {
    if (!this.lastPayload) return;
    try {
      const map = this._readStorageMap();
      map[String(this.bookmarkId)] = {
        ...this.lastPayload,
        ...(this.baseDateModified ? { base_date_modified: this.baseDateModified } : {}),
        ts: Date.now(),
      };
      this._expireStorageMap(map);
      localStorage.setItem(ReadingProgressController._LS_KEY, JSON.stringify(map));
    } catch {
      /* quota exceeded, private mode, etc. */
    }
    void this._syncToServer({ force: sync });
  }

  _readLocalStorage() {
    try {
      const map = this._readStorageMap();
      return map[String(this.bookmarkId)] || null;
    } catch {
      return null;
    }
  }

  _clearLocalStorage() {
    try {
      const map = this._readStorageMap();
      delete map[String(this.bookmarkId)];
      if (Object.keys(map).length === 0) {
        localStorage.removeItem(ReadingProgressController._LS_KEY);
      } else {
        localStorage.setItem(ReadingProgressController._LS_KEY, JSON.stringify(map));
      }
    } catch {
      // 静默忽略
    }
  }

  _readStorageMap() {
    try {
      const raw = localStorage.getItem(ReadingProgressController._LS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  // 写入时：O(n) 只清理过期条目，不排序不限量
  _expireStorageMap(map) {
    const cutoff = Date.now() - ReadingProgressController._LS_MAX_AGE_MS;
    for (const [k, v] of Object.entries(map)) {
      if ((v.ts || 0) < cutoff) delete map[k];
    }
  }

  // 页面加载时：完整清理（过期 + 排序 + 截断到上限）
  _trimStorageMap() {
    try {
      const raw = localStorage.getItem(ReadingProgressController._LS_KEY);
      if (!raw) return;
      const map = JSON.parse(raw);
      const now = Date.now();
      const entries = Object.entries(map)
        .filter(([, v]) => now - (v.ts || 0) < ReadingProgressController._LS_MAX_AGE_MS)
        .sort((a, b) => (b[1].ts || 0) - (a[1].ts || 0));
      const trimmed = {};
      for (const [k, v] of entries.slice(0, ReadingProgressController._LS_MAX_ENTRIES)) {
        trimmed[k] = v;
      }
      localStorage.setItem(ReadingProgressController._LS_KEY, JSON.stringify(trimmed));
    } catch {
      // 静默忽略
    }
  }

  // ---- Save ----

  // 滚动停止 2 秒后写 localStorage → 触发 _syncToServer PATCH 服务端。
  // 仅 active 状态生效。持续滚动时由 MAX_SYNC_AGE_MS（5s）兜底强制同步。
  _scheduleIdleSave() {
    if (this.state !== "active") return;
    if (this.idleSaveTimer) window.clearTimeout(this.idleSaveTimer);
    this.idleSaveTimer = window.setTimeout(() => {
      this.idleSaveTimer = null;
      this._writeLocalStorage();
    }, ReadingProgressController.IDLE_SAVE_MS);
  }

  // remote 状态下每 10 秒轮询服务端最新进度 → 更新 _remoteProgress 和 toast 文案。
  // 前台标签页有效；后台标签页浏览器会降频，切回时由 visibilitychange → _syncFromServer 兜底。
  _startRemotePolling() {
    this._stopRemotePolling();
    this._remotePollTimer = setInterval(async () => {
      if (this.state !== "remote") { this._stopRemotePolling(); return; }
      try {
        const data = await this._fetchServerProgress();
        if (data && this.state === "remote") {
          this._remoteProgress = data;
          this.baseDateModified = data.date_modified || null;
          this._updateRemoteToastText();
        }
      } catch {
        // 静默忽略
      }
    }, 10000);
  }

  _stopRemotePolling() {
    if (this._remotePollTimer) {
      clearInterval(this._remotePollTimer);
      this._remotePollTimer = null;
    }
  }

  _hasMovedPastThreshold() {
    const delta = Math.abs(this.contentEl.scrollTop - this.initialScrollTop);
    return delta >= ReadingProgressController.SCROLL_CANCEL_PX;
  }

  /**
   * 恢复 toast：初始化时有可恢复进度（scroll_top ≥ 600px）时显示。
   * 文案："继续阅读（X%）"，按钮：Continue / Cancel。
   * 滚动超过 SCROLL_CANCEL_PX 自动消失并进入 active 状态。
   * 累积滚动 200px 后半透明，悬浮/点击恢复不透明。
   */
  _showResumeToast(progress) {
    this._removeToast();

    const pct = formatProgressPercent(progress?.progress || 0);
    const text = interpolate(
      gettext("Continue reading (%(progress)s%)"),
      { progress: pct },
    );

    const toast = document.createElement("div");
    toast.className = "reader-resume-toast reader-resume-toast--resume";
    toast.setAttribute("role", "status");
    toast.innerHTML = `
      <span class="reader-resume-toast-text"></span>
      <span class="reader-resume-toast-buttons">
        <button type="button" class="btn btn-sm btn-link reader-resume-cancel"></button>
        <button type="button" class="btn btn-sm reader-resume-continue"></button>
      </span>
    `;
    toast.querySelector(".reader-resume-toast-text").textContent = text;
    toast.querySelector(".reader-resume-continue").textContent =
      gettext("Continue");
    toast.querySelector(".reader-resume-cancel").textContent =
      gettext("Cancel");

    toast.querySelector(".reader-resume-continue").addEventListener("click", () => {
      if (progress) this.restore(progress);
      this._enterSaving({ saveImmediately: true, suppressScroll: true });
    });

    toast.querySelector(".reader-resume-cancel").addEventListener("click", () => {
      this._dismissResumeToast();
    });

    // 悬浮/点击 toast 恢复透明度
    toast.addEventListener("mouseenter", () => toast.classList.remove("dimmed"));
    toast.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      toast.classList.remove("dimmed");
    });

    this.toast = toast;
    this.state = "resume";
    this.initialScrollTop = this.contentEl.scrollTop;
    this._toastScrollTop = this.contentEl.scrollTop;
    document.body.appendChild(toast);
  }

  _dismissResumeToast() {
    this._removeToast();
    this.state = "active";
    this.initialScrollTop = this.contentEl.scrollTop;
  }

  /**
   * 远端进度 toast：其他设备有显著进度更新时显示。
   * 文案："检测到最新进度 X% ？"，按钮：关闭 / 覆盖 / 前往，带问号帮助弹窗。
   * 不自动消失、不因滚动消失。用户必须明确选择：
   *   - 前往：跳转到 _remoteProgress 位置，回到 active（后续冲突仍弹 toast）
   *   - 覆盖：以本设备为准，回到 active
   *   - 关闭：进入 closed 状态，不再保存、不再弹 toast
   * 进入时启动 10s 定时轮询（_startRemotePolling）拉取最新进度更新 toast。
   * 累积滚动 200px 后半透明，悬浮/点击恢复。离开 remote 状态时停止轮询。
   */
  _showRemoteToast(progress) {
    this._removeToast();
    this.state = "remote";
    this._remoteProgress = progress;

    const helpHtml =
      `<b>${gettext("Go to")}</b>: ${gettext("jump to server progress, discard local.")}<br>` +
      `<b>${gettext("Override")}</b>: ${gettext("push local progress to server.")}<br>` +
      `<b>${gettext("Close")}</b>: ${gettext("local progress won't sync this time.")}`;

    const toast = document.createElement("div");
    toast.className = "reader-resume-toast reader-resume-toast--remote";
    toast.setAttribute("role", "status");
    toast.innerHTML = `
      <span class="reader-resume-toast-text-group">
        <span class="reader-resume-toast-text"></span>
        <button type="button" class="reader-resume-help-btn" aria-label="${gettext("Help")}">
          <svg width="20" height="20"><use href="#ld-icon-info"></use></svg>
        </button>
      </span>
      <span class="reader-resume-toast-buttons">
        <button type="button" class="btn btn-sm btn-link reader-resume-close-btn"></button>
        <button type="button" class="btn btn-sm btn-link reader-resume-cancel"></button>
        <button type="button" class="btn btn-sm reader-resume-continue"></button>
      </span>
    `;

    // popover 放在 toast 外面，避免被 flex 布局裁剪
    const helpPopover = document.createElement("div");
    helpPopover.className = "reader-resume-help-popover";
    helpPopover.hidden = true;
    helpPopover.innerHTML = helpHtml;

    this.toast = toast;
    this._toastScrollTop = this.contentEl.scrollTop;
    this._updateRemoteToastText();
    this._startRemotePolling();

    toast.querySelector(".reader-resume-continue").textContent = gettext("Go to");
    toast.querySelector(".reader-resume-cancel").textContent = gettext("Override");
    toast.querySelector(".reader-resume-close-btn").textContent = gettext("Close");

    // "?" 弹出帮助：fixed 定位，根据 toast 位置计算
    const helpBtn = toast.querySelector(".reader-resume-help-btn");
    helpBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (helpPopover.hidden) {
        // 先隐藏设 auto 宽度，测量自然宽度
        helpPopover.style.width = "auto";
        helpPopover.hidden = false;
        const naturalWidth = helpPopover.offsetWidth;
        const toastRect = toast.getBoundingClientRect();
        const width = Math.min(naturalWidth, toastRect.width);
        helpPopover.style.width = `${width}px`;
        helpPopover.style.top = `${toastRect.bottom + 4}px`;
        // 水平居中于 toast
        helpPopover.style.left = `${toastRect.left + (toastRect.width - width) / 2}px`;
      } else {
        helpPopover.hidden = true;
      }
    });
    const closePopover = (e) => {
      if (!helpPopover.contains(e.target) && e.target !== helpBtn) {
        helpPopover.hidden = true;
      }
    };
    this._closePopover = closePopover;
    document.addEventListener("click", closePopover);

    // 前往：跳转到远端最新进度，回到 active（后续冲突仍弹 toast）
    toast.querySelector(".reader-resume-continue").addEventListener("click", () => {
      if (this._remoteProgress) {
        if (this._remoteProgress.date_modified) {
          this.baseDateModified = this._remoteProgress.date_modified;
        }
        this.restore(this._remoteProgress);
      }
      this._enterSaving({ saveImmediately: true, suppressScroll: true });
    });

    // 覆盖：以本设备为准，回到 active（后续冲突仍弹 toast）
    toast.querySelector(".reader-resume-cancel").addEventListener("click", () => {
      if (this._remoteProgress?.date_modified) {
        this.baseDateModified = this._remoteProgress.date_modified;
      }
      this._enterSaving({ saveImmediately: true });
    });

    // 关闭：进入 closed 状态，不保存、不弹 toast
    toast.querySelector(".reader-resume-close-btn").addEventListener("click", () => {
      this._remoteProgress = null;
      this._removeToast();
      this.state = "closed";
      this.initialScrollTop = this.contentEl.scrollTop;
    });

    // 点击/悬浮在 toast 上恢复透明度
    toast.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      toast.classList.remove("dimmed");
    });
    toast.addEventListener("mouseenter", () => {
      toast.classList.remove("dimmed");
    });

    this._helpPopover = helpPopover;
    document.body.appendChild(helpPopover);
    document.body.appendChild(toast);
  }

  /**
   * 更新远端 toast 的进度文本（toast 已存在时调用）。
   */
  _updateRemoteToastText() {
    if (!this.toast || this.state !== "remote") return;
    const pct = formatProgressPercent(this._remoteProgress?.progress || 0);
    this.toast.querySelector(".reader-resume-toast-text").textContent =
      interpolate(gettext("Detected latest progress %(progress)s%"), { progress: pct });
  }

  _removeToast() {
    this._stopRemotePolling();
    if (this._closePopover) {
      document.removeEventListener("click", this._closePopover);
      this._closePopover = null;
    }
    if (this._helpPopover) {
      this._helpPopover.remove();
      this._helpPopover = null;
    }
    if (this.toast) {
      this.toast.remove();
      this.toast = null;
    }
  }

  // ---- Init & events ----

  // ---- Init ----

  /**
   * 初始化：并行获取服务端进度 + 等待 DOM，完成后做状态决策。
   * 调用时机：构造函数、bfcache 恢复（pageshow persisted）。
   * 状态决策：已滚动超阈值 → active；有可恢复进度 → resume toast；其他 → active。
   */
  async _init() {
    this._trimStorageMap();
    const [serverData] = await Promise.all([
      this._fetchServerProgress(),
      this._waitForDom(),
    ]);

    // 存储服务端 date_modified，用于后续 PATCH/beacon 的冲突检测
    if (serverData?.date_modified) {
      this.baseDateModified = serverData.date_modified;
    }

    // 服务端无数据 / 进度为 0 → 尝试 localStorage 兜底（仅影响 toast 决策）
    let progressData = serverData;
    if (!progressData || progressData.progress === 0) {
      const cached = this._readLocalStorage();
      if (cached && cached.progress > 0) {
        progressData = { ...progressData, ...cached };
        // localStorage 中的 base_date_modified 可用于冲突检测
        if (cached.base_date_modified && !this.baseDateModified) {
          this.baseDateModified = cached.base_date_modified;
        }
      }
    }

    this._progressData = progressData;

    // 服务端失败且 localStorage 有数据 → 延迟重试获取 baseDateModified
    if (!this.baseDateModified && progressData?.progress > 0) {
      this._scheduleProgressRetry();
    }

    // 状态决策：用绝对滚动距离而非百分比（长文章 2% 可能是数千字）
    const scrollPos = progressData?.scroll_top || 0;
    const atEnd = (progressData?.progress || 0) >= 0.98;
    if (this._hasMovedPastThreshold()) {
      // 用户在服务端响应前已大幅滚动 → 跳过 toast，直接保存
      this._enterSaving({ saveImmediately: true });
    } else if (scrollPos >= ReadingProgressController.MIN_RESUME_SCROLL_TOP && !atEnd) {
      // 有可恢复进度 → 弹 toast
      this._showResumeToast(progressData);
    } else {
      // 无进度 / 已读完 → 直接保存
      this._enterSaving();
    }
  }

  /**
   * visibilitychange visible 时调用，覆盖后台标签页降频期间的进度更新。
   */
  async _syncFromServer() {
    this._exiting = false;
    let data;
    try {
      data = await this._fetchServerProgress();
      if (data?.date_modified) {
        this.baseDateModified = data.date_modified;
      }
    } catch {
      // 静默忽略
    }
    // 有待保存数据 → 触发同步
    if (this._hasNewData) {
      this._syncToServer();
    }
    // 远端进度 toast 已在显示 → 更新数据和文本
    if (data && this.state === "remote") {
      this._remoteProgress = data;
      this._updateRemoteToastText();
    } else if (data && this._isRemoteProgressUpdate(data)) {
      this._showRemoteToast(data);
    }
    // 不覆盖 _progressData：它只由 _init() 和 _syncToServer 成功后更新，
    // 代表"我最后确认同步到服务端的位置"。fetch 的 data 不写入，
    // 避免 beacon 竞态导致旧值覆盖，使 _isRemoteProgressUpdate 误报。
  }

  /**
   * 判断服务端数据是否是来自其他设备的有意义更新（用于 _syncFromServer）。
   * 条件：scroll_top ≥ 600px、未读完、与本地 scroll_top 差值 ≥ 600px。
   * 不弹新 toast 的情况：remote（已弹）、closed（用户关闭）。
   */
  _isRemoteProgressUpdate(serverData) {
    // remote/closed 状态不弹新 toast
    if (this.state === "remote" || this.state === "closed") return false;
    const serverScroll = serverData.scroll_top || 0;
    const localScroll = this._progressData?.scroll_top || 0;
    const atEnd = (serverData.progress || 0) >= 0.98;
    return (
      serverScroll >= ReadingProgressController.MIN_RESUME_SCROLL_TOP &&
      !atEnd &&
      Math.abs(serverScroll - localScroll) >= ReadingProgressController.MIN_RESUME_SCROLL_TOP
    );
  }

  async _fetchServerProgress() {
    try {
      return await this._withTimeout(
        fetchReadingProgress(this.apiBase, this.bookmarkId),
        3000,
      );
    } catch (err) {
      console.warn("Failed to load reading progress:", err);
      return null;
    }
  }

  _waitForDom() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(resolve);
      });
    });
  }

  // 一次性延迟重试：10 秒后重新从服务端拉取，拿到 baseDateModified
  _scheduleProgressRetry() {
    if (this._progressRetryScheduled) return;
    this._progressRetryScheduled = true;
    setTimeout(async () => {
      try {
        const data = await this._fetchServerProgress();
        if (data?.date_modified) {
          this.baseDateModified = data.date_modified;
        }
      } catch {
        // 静默忽略，下次 visibilitychange 会再次尝试
      }
    }, 10000);
  }

  // ---- State transitions ----

  /**
   * 进入 active 状态（正常保存模式）。
   * 移除当前 toast，重置 initialScrollTop。saveImmediately 时立即写入并同步。
   * 调用方：_init（无进度/已读完/已滚动）、resume toast Continue/Cancel、
   *         远端 toast Go to/Override、滚动超阈值自动消失。
   */
  _enterSaving({ saveImmediately = false, suppressScroll = false } = {}) {
    this._removeToast();
    this.state = "active";
    this.initialScrollTop = this.contentEl.scrollTop;
    if (suppressScroll) this.suppressScrollUntil = Date.now() + 800;
    if (saveImmediately && this.lastPayload) {
      this._writeLocalStorage({ sync: true });
    }
  }

  _bindEvents() {
    this.contentEl.addEventListener("scroll", () => {
      if (Date.now() < this.suppressScrollUntil || this.scrollTicking) return;
      this.scrollTicking = true;
      requestAnimationFrame(() => {
        this.scrollTicking = false;
        this.capture();

        // resume toast：滚动超过阈值自动消失，开始保存
        // remote toast：不因滚动消失，必须明确选择
        if (this.state === "resume" && this._hasMovedPastThreshold()) {
          this._enterSaving({ saveImmediately: true });
        }
        this._scheduleIdleSave();
        // 持续滚动时，超过 MAX_SYNC_AGE_MS 强制同步（防止 debounce 无限重置）
        if (
          this.state === "active" &&
          this._lastSavedAt &&
          Date.now() - this._lastSavedAt >= ReadingProgressController.MAX_SYNC_AGE_MS
        ) {
          this._writeLocalStorage({ sync: true });
        }
        // toast 显示期间，累积滚动超过 200px 后降低透明度
        if ((this.state === "remote" || this.state === "resume") && this.toast) {
          const scrolled = Math.abs(this.contentEl.scrollTop - (this._toastScrollTop || 0));
          if (scrolled >= 200) this.toast.classList.add("dimmed");
        }
        // 远端 toast：关闭弹窗
        if (this.state === "remote") {
          if (this._helpPopover) this._helpPopover.hidden = true;
        }
      });
    });

    // hidden → 记录视口尺寸 + beacon 保存未同步数据
    // visible → 拉取服务端最新进度（覆盖后台标签页降频期间的更新）
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        this._lastViewport = {
          w: this.contentEl.clientWidth,
          h: this.contentEl.clientHeight,
        };
        this._beaconOnExit();
      } else {
        void this._syncFromServer();
      }
    });

    window.addEventListener("pagehide", () => {
      this._beaconOnExit();
    });

    // bfcache 恢复时重新初始化（JS 状态是缓存的旧数据）
    // 视口尺寸未变化时跳过 _init()：bfcache 已精确恢复 DOM 和 scrollTop，
    // 无需重新定位或弹恢复提示
    window.addEventListener("pageshow", (e) => {
      if (!e.persisted) return;
      this._exiting = false;
      this._progressRetryScheduled = false;

      const prev = this._lastViewport;
      const curW = this.contentEl.clientWidth;
      const curH = this.contentEl.clientHeight;
      if (prev && Math.abs(curW - prev.w) <= 4 && Math.abs(curH - prev.h) <= 4) {
        // 视口未变 → 浏览器已精确恢复位置，直接进入保存状态
        this._enterSaving();
        this.capture();
        return;
      }

      this.state = "loading";
      this.initialScrollTop = this.contentEl.scrollTop;
      this._init();
    });
  }

  /**
   * 页面退出时发送 beacon（visibilitychange hidden + pagehide）。
   * 用 _exiting 守卫确保只发一次。仅 active 状态发送（从内存 lastPayload 读取）。
   * remote/closed/loading/resume 状态不发送，防止覆盖服务端进度或发送未确认的数据。
   */
  _beaconOnExit() {
    if (this._exiting) return;
    this._exiting = true;
    if (this.state !== "active" || !this.lastPayload) return;
    const payload = { ...this.lastPayload };
    if (this.baseDateModified) payload.base_date_modified = this.baseDateModified;
    sendReadingProgressBeacon(this.apiBase, this.bookmarkId, payload);
    // 同步 _progressData，防止 beacon 竞态：beacon 是 fire-and-forget，
    // 若不同步，切回时 fetch 返回旧值会触发 _isRemoteProgressUpdate 误报
    this._progressData = { ...this._progressData, ...payload };
  }

  // PATCH 当前进度到服务端。调用方：_scheduleIdleSave、_writeLocalStorage(sync=true)。
  // 仅 active 状态执行。_syncInFlight 保证同时只有一个请求在飞。
  // force=true 跳过 _hasNewData 检查（saveImmediately 场景）。
  // 成功：更新 baseDateModified、清 localStorage、记录 _lastSavedAt。
  // 409：拉取最新数据 → active 状态弹新 toast，remote 状态更新当前 toast。
  async _syncToServer({ force = false } = {}) {
    if (this.state !== "active" || !this.lastPayload) return;
    if (!force && !this._hasNewData) return;
    if (this._syncInFlight) return;
    this._syncInFlight = true;
    try {
      const payload = { ...this.lastPayload };
      if (this.baseDateModified) payload.base_date_modified = this.baseDateModified;
      const saved = await this._withTimeout(
        patchReadingProgress(this.apiBase, this.bookmarkId, payload),
      );
      this.baseDateModified = saved.date_modified || null;
      this._hasNewData = false;
      this._lastSavedAt = Date.now();
      this._clearLocalStorage();
      // 同步 _progressData，防止 _isRemoteProgressUpdate 将自己的保存误判为远端变更
      this._progressData = { ...this._progressData, ...this.lastPayload };
    } catch (err) {
      if (err?.status === 409) {
        // 冲突：409 本身就是不一致的证明，直接弹远端进度 toast
        try {
          const latest = await this._withTimeout(
            fetchReadingProgress(this.apiBase, this.bookmarkId),
          );
          if (latest) {
            this.baseDateModified = latest.date_modified || null;
            if (this.state === "remote") {
              this._remoteProgress = latest;
              this._updateRemoteToastText();
            } else if (this.state !== "closed") {
              this._showRemoteToast(latest);
            }
          }
        } catch {
          // fetch 超时或失败，静默忽略
        }
        // 保持 _hasNewData = true，下次 idle save 用新 baseDateModified 重试
        return;
      }
      console.warn("Failed to sync reading progress:", err);
    } finally {
      this._syncInFlight = false;
    }
  }

  _withTimeout(promise, ms = 5000) {
    return Promise.race([
      promise,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("timeout")), ms),
      ),
    ]);
  }

}
