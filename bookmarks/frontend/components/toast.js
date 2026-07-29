/**
 * ToastManager - 全站统一的轻量反馈提示。
 *
 * ============================================================================
 * UI 模式分层（Toast vs Notice vs Prompt）
 * ============================================================================
 *
 * 1. Toast（本模块）
 *    - 用途：非阻塞的操作反馈，无需用户决策
 *    - 行为：自动消失（默认 3s），可手动关闭
 *    - 场景：保存成功、复制完成、网络错误
 *    - API：showToast(message, { tone, duration })
 *
 * 2. Notice（.toast-notice）
 *    - 用途：需要用户主动确认的持久提示
 *    - 行为：常驻不消失，必须点击关闭
 *    - 场景：功能变更公告、数据库 Toast 模型
 *    - 实现：服务端渲染 + 表单提交关闭
 *
 * 3. Prompt（.reader-resume-toast 等，不在本模块中）
 *    - 用途：需要用户做出决策的交互
 *    - 行为：常驻 + 多个操作按钮 + 复杂交互
 *    - 场景：恢复阅读进度、同步冲突处理、添加高亮书签
 *    - 实现：Reader 模块自行管理，见 reader-mode.css
 *
 * 设计决策：Toast 和 Notice 都是"单向告知"，只是持续时间不同。
 * Prompt 是"双向交互"，需要用户选择，因此独立实现。
 * ============================================================================
 *
 * 用法：
 *  import { showToast } from "./components/toast.js";
 *  showToast("Saved", { tone: "success" });
 *  showToast("Network failed", { tone: "error", duration: 0 }); // 持续显示
 */

import { gettext as _gt } from "../utils/i18n.js";

const DEFAULT_DURATION_MS = 3000;
const MAX_VISIBLE_TOASTS = 5;

const VALID_TONES = new Set(["success", "error", "warning", "info"]);

let stack = null;
let counter = 0;

function ensureStack() {
  if (stack && stack.isConnected) return stack;
  stack = document.createElement("div");
  stack.className = "toast-stack";
  stack.setAttribute("aria-live", "polite");
  stack.setAttribute("aria-atomic", "false");
  document.body.appendChild(stack);
  return stack;
}

function normalizeTone(tone) {
  return VALID_TONES.has(tone) ? tone : "info";
}

function buildToastNode(message, tone, id) {
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone} toast-floating`;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  toast.dataset.toastId = String(id);

  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = String(message);
  toast.appendChild(text);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "toast-close";
  closeBtn.setAttribute("aria-label", _gt("Dismiss"));
  closeBtn.innerHTML =
    '<svg width="14" height="14" aria-hidden="true"><use href="#ld-icon-close"></use></svg>';
  closeBtn.addEventListener("click", () => dismissToast(id));
  toast.appendChild(closeBtn);

  return toast;
}

function trimIfOverflow() {
  if (!stack) return;
  const items = stack.querySelectorAll(".toast-floating");
  if (items.length <= MAX_VISIBLE_TOASTS) return;
  const overflow = items.length - MAX_VISIBLE_TOASTS;
  for (let i = 0; i < overflow; i++) {
    dismissToast(items[i].dataset.toastId, { immediate: true });
  }
}

function scheduleRemoval(id, duration) {
  if (!duration || duration <= 0) return null;
  return window.setTimeout(() => dismissToast(id), duration);
}

export function showToast(message, options = {}) {
  if (!message) return null;
  const tone = normalizeTone(options.tone);
  const duration =
    typeof options.duration === "number" ? options.duration : DEFAULT_DURATION_MS;

  const root = ensureStack();
  const id = ++counter;
  const node = buildToastNode(message, tone, id);
  root.appendChild(node);

  // 进入动画
  requestAnimationFrame(() => {
    if (node.isConnected) node.classList.add("toast-visible");
  });

  trimIfOverflow();
  const timer = scheduleRemoval(id, duration);
  node.dataset.toastTimer = timer != null ? String(timer) : "";
  return id;
}

export function dismissToast(id, { immediate = false } = {}) {
  if (!stack) return;
  const node = stack.querySelector(`[data-toast-id="${id}"]`);
  if (!node) return;

  if (node.dataset.toastTimer) {
    clearTimeout(Number(node.dataset.toastTimer));
    node.dataset.toastTimer = "";
  }

  if (immediate) {
    node.remove();
    return;
  }

  node.classList.remove("toast-visible");
  node.classList.add("toast-leaving");
  const cleanup = () => {
    node.removeEventListener("transitionend", cleanup);
    node.remove();
  };
  node.addEventListener("transitionend", cleanup);
  // 兜底：动画异常时仍能移除
  window.setTimeout(cleanup, 400);
}

export function dismissAllToasts() {
  if (!stack) return;
  Array.from(stack.querySelectorAll(".toast-floating")).forEach((node) => {
    if (node.dataset.toastTimer) clearTimeout(Number(node.dataset.toastTimer));
    node.remove();
  });
}

// ---------------------------------------------------------------------------
// Django messages → Toast 的自动转换
//
// 服务端通过 <div data-toast-message data-toast-tone="success">...</div>
// 渲染消息，本模块在页面加载时扫描这些节点并转为浮层 toast。
// ---------------------------------------------------------------------------

const SERVER_TONE_MAP = {
  success: "success",
  error: "error",
  warning: "warning",
  info: "info",
  debug: "info",
};

function consumeServerMessages() {
  const nodes = document.querySelectorAll("[data-toast-message]");
  if (nodes.length === 0) return;
  nodes.forEach((node) => {
    const tone = SERVER_TONE_MAP[node.dataset.toastTone] || "info";
    const text = (node.textContent || "").trim();
    if (text) showToast(text, { tone });
    node.remove();
  });
}

// DOM ready 时执行一次
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", consumeServerMessages, { once: true });
} else {
  requestAnimationFrame(consumeServerMessages);
}

// Turbo 导航事件
document.addEventListener("turbo:load", consumeServerMessages);
document.addEventListener("turbo:render", consumeServerMessages);

// 暴露到全局，供内联脚本使用（如登录页面错误提示）
window.showToast = showToast;
