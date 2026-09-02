/**
 * 获取 CSRF token，优先从 meta tag 读取，fallback 到 cookie。
 *
 * cookie 名由 Django `CSRF_COOKIE_NAME` 设置（当前为 `ld_csrftoken`），
 * 改 cookie 名时只需同步此处的正则，meta tag 不受影响。
 */
export function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrfmiddlewaretoken"]');
  if (meta) return meta.content;
  return document.cookie.match(/ld_csrftoken=([^;]+)/)?.[1] || "";
}

/** 通用 cookie 读取，用于需要按名精确匹配的场景 */
export function getCookie(name) {
  for (const c of document.cookie.split(";")) {
    const [k, v] = c.trim().split("=");
    if (k === name) return decodeURIComponent(v);
  }
  return null;
}
