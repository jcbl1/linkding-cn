/**
 * 侧边栏状态初始化
 *
 * 规则：localStorage 优先，data-sidebar-default 兜底。
 * 在 turbo:load 时执行，确保每次页面加载都同步状态。
 */
function initSidebarState() {
  const page = document.querySelector(".bookmarks-page, .highlights-page");
  if (!page) return;

  const isHighlights = page.classList.contains("highlights-page");
  const storageKey = isHighlights ? "ld:sidebar-state:highlights" : "ld:sidebar-state:bookmarks";

  let stored;
  try { stored = localStorage.getItem(storageKey); } catch (e) { stored = null; }

  // localStorage 优先；不存在时：移动端默认关闭（覆盖式侧边栏），桌面端读取服务器设置
  const shouldOpen = stored !== null
    ? stored === "1"
    : window.innerWidth > 840 && page.dataset.sidebarDefault === "1";

  page.classList.remove("sidebar-open", "sidebar-closed", "sidebar-visible");
  if (shouldOpen) {
    page.classList.add("sidebar-open", "sidebar-visible");
  } else {
    page.classList.add("sidebar-closed");
  }
}

document.addEventListener("turbo:load", initSidebarState);
