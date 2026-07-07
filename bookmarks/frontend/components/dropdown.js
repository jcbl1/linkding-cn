import { HeadlessElement } from "../utils/element.js";

const NARROW_BREAKPOINT = 426;

class Dropdown extends HeadlessElement {
  constructor() {
    super();
    this.opened = false;
    this._scrollRafId = null;
    this.onClick = this.onClick.bind(this);
    this.onOutsideClick = this.onOutsideClick.bind(this);
    this.onEscape = this.onEscape.bind(this);
    this.onFocusOut = this.onFocusOut.bind(this);
    this.onScroll = this.onScroll.bind(this);
  }

  init() {
    this.style.setProperty("--dropdown-focus-display", "none");
    this.addEventListener("keydown", this.onEscape);
    this.addEventListener("focusout", this.onFocusOut);

    this.toggle = this.querySelector(".dropdown-toggle");
    this.toggle?.setAttribute("aria-expanded", "false");
    this.toggle?.addEventListener("click", this.onClick);
  }

  disconnectedCallback() {
    this.close();
    this.toggle?.removeEventListener("click", this.onClick);
    this.removeEventListener("keydown", this.onEscape);
    this.removeEventListener("focusout", this.onFocusOut);
  }

  open() {
    this.opened = true;
    this.classList.add("active");
    this.toggle?.setAttribute("aria-expanded", "true");
    this._positionMenu();
    document.addEventListener("click", this.onOutsideClick);
    window.addEventListener("scroll", this.onScroll, true);
  }

  close() {
    this.opened = false;
    this.classList.remove("active");
    this.toggle?.setAttribute("aria-expanded", "false");
    this._resetMenuPosition();
    document.removeEventListener("click", this.onOutsideClick);
    window.removeEventListener("scroll", this.onScroll, true);
    if (this._scrollRafId) {
      cancelAnimationFrame(this._scrollRafId);
      this._scrollRafId = null;
    }
  }

  onScroll() {
    if (this._scrollRafId) return;
    this._scrollRafId = requestAnimationFrame(() => {
      this._scrollRafId = null;
      if (this.opened) {
        this._positionMenu();
      }
    });
  }

  _positionMenu() {
    const menu = this.querySelector(".menu");
    if (!menu) return;

    // 提升祖先 sticky 元素的 z-index，使菜单浮于同级 sticky 元素（如分页器）之上
    const stickyAncestor = this.closest("[data-sticky-on]");
    if (stickyAncestor) {
      stickyAncestor.style.zIndex = "30";
    }

    if (menu.classList.contains("menu-panel")) {
      if (window.innerWidth <= NARROW_BREAKPOINT) {
        const gap = 4;
        const rect = this.toggle.getBoundingClientRect();
        menu.style.top = (rect.bottom + gap) + "px";
      }
      return;
    }

    // 水平：防止右侧溢出
    const toggleRect = this.toggle.getBoundingClientRect();
    const menuWidth = menu.offsetWidth;
    const viewportWidth = document.documentElement.clientWidth;
    const overflow = toggleRect.right + menuWidth - toggleRect.left - viewportWidth;
    if (overflow > 0) {
      menu.style.left = -overflow + "px";
      menu.style.right = "auto";
    }

    // 垂直：根据 toggle 到视口底部的可用空间限制菜单高度
    const available = document.documentElement.clientHeight - toggleRect.bottom - 8;
    menu.style.maxHeight = Math.max(available, 200) + "px";
  }

  _resetMenuPosition() {
    const menu = this.querySelector(".menu");
    if (menu) {
      menu.style.left = "";
      menu.style.right = "";
      menu.style.maxHeight = "";
      menu.style.top = "";
    }
    const stickyAncestor = this.closest("[data-sticky-on]");
    if (stickyAncestor) {
      stickyAncestor.style.zIndex = "";
    }
  }

  onClick() {
    if (this.opened) {
      this.close();
    } else {
      this.open();
    }
  }

  onOutsideClick(event) {
    if (!this.contains(event.target)) {
      this.close();
    }
  }

  onEscape(event) {
    if (event.key === "Escape" && this.opened) {
      event.preventDefault();
      this.close();
      this.toggle?.focus();
    }
  }

  onFocusOut(event) {
    if (!this.contains(event.relatedTarget)) {
      this.close();
    }
  }
}

customElements.define("ld-dropdown", Dropdown);
