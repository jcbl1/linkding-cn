/**
 * Snapshot browser script (per-site config engine) (SingleFile browser_script format)
 *
 * Reads config from window.__linkding_cleanup_config:
 *   {
 *     "keep": ["main#main-content"],
 *     "remove": ["nav", "footer", "button[aria-label='Back']"],
 *     "lazy": true | ["data-src", "data-actualsrc"],
 *     "carousels": ["faceplate-carousel"],
 *     "removeClasses": { ".RichContent": ["is-collapsed"] },
 *     "setStyles": { ".RichContent-inner": {"maxHeight": "none"} }
 *   }
 *
 * remove/removeClasses/setStyles/lazy/carousels selectors are resolved
 * recursively inside open shadow roots. keep_elements is applied to the
 * normal document body and preserves the matched subtree, including shadow
 * DOM inside it.
 * remove_elements only operates inside kept subtrees and never removes a
 * keep element or any of its ancestors.
 */
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

  addEventListener("single-file-on-before-capture-request", () => {
    const config = window.__linkding_cleanup_config || {};
    const stats = { removed: 0, kept: 0, carousels: 0, media: 0 };

    const shadowHosts = new Map();
    const queryAll = (root, selector) => {
      const matches = Array.from(root.querySelectorAll(selector));
      root.querySelectorAll("*").forEach((el) => {
        if (el.shadowRoot) {
          shadowHosts.set(el.shadowRoot, el);
          matches.push(...queryAll(el.shadowRoot, selector));
        }
      });
      return matches;
    };

    const isWithin = (ancestor, node) => {
      let current = node;
      while (current) {
        if (ancestor === current) return true;
        if (ancestor.contains(current)) return true;
        const parent = current.parentNode;
        if (parent) {
          if (parent.nodeType === 11) {
            current = shadowHosts.get(parent) || parent.host || null;
          } else {
            current = parent;
          }
        } else {
          const root = current.getRootNode();
          current = root && root.nodeType === 11 ? (shadowHosts.get(root) || root.host || null) : null;
        }
      }
      return false;
    };

    const keep = [];
    for (const selector of config.keep || []) {
      keep.push(...queryAll(document, selector));
    }

    const protectedNodes = new Set();
    keep.forEach((node) => {
      while (node && !protectedNodes.has(node)) {
        protectedNodes.add(node);
        const parent = node.parentNode;
        if (parent && parent.nodeType === 11) {
          node = shadowHosts.get(parent) || parent.host || null;
        } else if (parent) {
          node = parent;
        } else {
          const root = node.getRootNode();
          node = root && root.nodeType === 11 ? (shadowHosts.get(root) || root.host || null) : null;
        }
      }
    });

    const collectMedia = (root) => {
      const items = [];
      const walk = (node) => {
        if (node.shadowRoot) walk(node.shadowRoot);
        node.querySelectorAll("img, video, iframe").forEach((el) => items.push(el));
        node.querySelectorAll("*").forEach((el) => {
          if (el.shadowRoot) walk(el.shadowRoot);
        });
      };
      walk(root);
      return items;
    };

    const resolveMediaUrl = (el) => {
      if (el.tagName === "VIDEO") {
        const source = el.querySelector("source");
        return el.getAttribute("src") || (source && source.getAttribute("src")) || null;
      }
      if (el.tagName === "IFRAME") return el.getAttribute("src") || null;
      const attrs = ["src", "data-src", "data-srcset", "data-actualsrc", "data-original", "data-lazy-src", "data-original-src", "data-actual-image", "data-lazy", "data-defer-src"];
      for (const attr of attrs) {
        const value = el.getAttribute(attr);
        if (value) return value;
      }
      const srcset = el.getAttribute("srcset") || el.getAttribute("data-srcset");
      if (srcset) {
        const first = srcset.split(",")[0].trim();
        return first.split(/\s+/)[0] || null;
      }
      return null;
    };

    const ensureCarouselStyles = () => {
      if (document.getElementById("ld-carousel-style")) return;
      const style = document.createElement("style");
      style.id = "ld-carousel-style";
      style.textContent = [
        '[aria-label="ld-carousel"]{scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.35) rgba(0,0,0,.08);align-items:center}',
        '[aria-label="ld-carousel"]::-webkit-scrollbar{width:8px;height:8px;display:block}',
        '[aria-label="ld-carousel"]::-webkit-scrollbar-thumb{background:rgba(0,0,0,.35);border-radius:8px}',
        '[aria-label="ld-carousel"]::-webkit-scrollbar-track{background:rgba(0,0,0,.08)}'
      ].join("");
      document.head.appendChild(style);
    };

    const processCarousel = (container) => {
      const seen = new Set();
      const items = [];
      collectMedia(container).forEach((el) => {
        const url = resolveMediaUrl(el);
        if (el.tagName === "IMG") {
          if (!url || seen.has(url)) return;
          seen.add(url);
          const img = container.ownerDocument.createElement("img");
          img.src = url;
          img.alt = el.getAttribute("alt") || "";
          items.push(img);
        } else if (el.tagName === "VIDEO") {
          if (url && seen.has(url)) return;
          if (!url && !el.querySelector("source")) {
            const poster = el.getAttribute("poster");
            if (!poster || seen.has(poster)) return;
            seen.add(poster);
            const img = container.ownerDocument.createElement("img");
            img.src = poster;
            img.alt = el.getAttribute("alt") || "";
            items.push(img);
            return;
          }
          if (url) seen.add(url);
          const video = el.cloneNode(true);
          if (!video.hasAttribute("controls")) video.setAttribute("controls", "");
          items.push(video);
        } else if (el.tagName === "IFRAME") {
          if (!url || seen.has(url)) return;
          seen.add(url);
          items.push(el.cloneNode(true));
        }
      });
      if (!items.length) return 0;

      ensureCarouselStyles();
      const figure = container.ownerDocument.createElement("figure");
      figure.setAttribute("aria-label", "ld-carousel");
      figure.style.cssText = "display:flex;overflow-x:auto;gap:12px;max-width:100%;";
      items.forEach((item) => {
        item.style.flex = "0 0 auto";
        item.style.maxWidth = "80%";
        item.style.maxHeight = "80vh";
        item.style.width = "auto";
        if (item.tagName !== "IFRAME") {
          item.style.height = "auto";
          item.style.objectFit = "contain";
        }
        figure.appendChild(item);
      });
      container.replaceWith(figure);
      return items.length;
    };

    // Remove specified selectors
    for (const selector of config.remove || []) {
      queryAll(document, selector).forEach((el) => {
        if (!el.isConnected || protectedNodes.has(el)) return;
        if (!keep.some((target) => isWithin(target, el))) return;
        el.remove();
        stats.removed += 1;
      });
    }

    // Remove classes: { ".selector": ["class1", "class2"] }
    for (const [selector, classes] of Object.entries(config.removeClasses || {})) {
      queryAll(document, selector).forEach((el) => {
        for (const cls of (Array.isArray(classes) ? classes : [classes])) el.classList.remove(cls);
      });
    }

    // Set styles: { ".selector": { "prop": "value" } }
    for (const [selector, styles] of Object.entries(config.setStyles || {})) {
      queryAll(document, selector).forEach((el) => {
        for (const [prop, value] of Object.entries(styles)) el.style[prop] = value;
      });
    }

    // Keep only specified selectors (remove everything else)
    if ((config.keep || []).length) {
      stats.kept = keep.length;
      if (keep.length) {
        document.body.querySelectorAll('*').forEach((el) => {
          if (!keep.some((target) => isWithin(target, el) || isWithin(el, target))) {
            el.remove(); stats.removed += 1;
          }
        });
      }
    }

    // Fix lazy-loaded images after keep/remove to limit work to retained content
    if (config.lazy) {
      const attrs = Array.isArray(config.lazy) ? config.lazy : ["data-src", "data-actualsrc", "data-original", "data-lazy-src", "data-original-src", "data-actual-image", "data-lazy", "data-defer-src"];
      queryAll(document, "img").forEach((img) => {
        for (const attr of attrs) {
          const value = img.getAttribute(attr);
          if (value && !img.getAttribute('src')) { img.setAttribute('src', value); break; }
        }
      });
    }

    // Convert configured carousels into a horizontal media list
    for (const selector of config.carousels || []) {
      queryAll(document, selector).forEach((container) => {
        if (!container.isConnected) return;
        const count = processCarousel(container);
        if (count) {
          stats.carousels += 1;
          stats.media += count;
        }
      });
    }

    // Embed stats for diagnostics
    const meta = document.createElement('meta');
    meta.name = 'linkding-cleanup-stats';
    meta.content = JSON.stringify(stats);
    document.head && document.head.appendChild(meta);
  });
})();
