/**
 * 清理 SVG body 中的 XSS 向量（on* 事件、javascript: 协议、危险元素）
 */
export function sanitizeSvgBody(svg) {
  if (typeof svg !== "string") return "";
  return svg
    .replace(/<\s*\/?\s*(script|iframe|object|embed|form|input|style|link|meta)\b[^>]*>/gi, "")
    .replace(/\bon\w+\s*=/gi, "")
    .replace(/javascript\s*:/gi, "");
}

/**
 * 快捷标签默认图标 `#` — 引用 SVG sprite 中的 ld-icon-hash
 */
export function hashIconSvg(size = 16, cssClass = "action-icon") {
  return `<svg class="${cssClass}" width="${size}" height="${size}"><use href="#ld-icon-hash"></use></svg>`;
}
