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
 * remove_elements runs across the document, but when keep_elements is
 * configured it only operates inside kept subtrees and never removes a keep
 * element or any of its ancestors.
 */
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

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

  const cleanupFn = async () => {
    const config = window.__linkding_cleanup_config || {};
    const stats = { removed: 0, kept: 0, carousels: 0, media: 0 };

    const PRESERVE_WHITE_SPACE = new Set(["pre", "pre-wrap", "break-spaces", "pre-line"]);
    const BLOCK_TAGS = new Set([
      "address", "article", "aside", "blockquote", "body", "caption", "dd",
      "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption",
      "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
      "header", "hgroup", "hr", "html", "legend", "li", "main", "menu",
      "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
      "th", "thead", "tr", "ul",
    ]);
    const PRESERVED_INLINE_TAGS = new Set([
      "a", "abbr", "b", "br", "cite", "code", "em", "i", "img", "mark",
      "q", "s", "small", "strong", "sub", "sup", "time", "u",
    ]);
    const CAN_CONTAIN_BLOCK = new Set([
      "article", "aside", "blockquote", "body", "dd", "details", "div",
      "figcaption", "figure", "footer", "form", "header", "li", "main",
      "nav", "ol", "section", "td", "th", "ul",
    ]);

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

    const hasBlockDescendant = (node) => {
      if (!node.querySelectorAll) return false;
      return Array.from(node.querySelectorAll("*")).some((el) =>
        BLOCK_TAGS.has(el.tagName.toLowerCase())
      );
    };

    const rebuildParagraphs = (root) => {
      const doc = root.ownerDocument;
      const original = Array.from(root.childNodes);
      root.replaceChildren();
      let currentParagraph = null;

      const ensureParagraph = () => {
        if (!currentParagraph) {
          currentParagraph = doc.createElement("p");
          root.appendChild(currentParagraph);
        }
        return currentParagraph;
      };

      const closeParagraph = () => {
        currentParagraph = null;
      };

      const appendInline = (node) => {
        ensureParagraph().appendChild(node);
      };

      const processText = (text) => {
        if (!text) return;
        if (!/\n|\r/.test(text)) {
          if (text.trim() || currentParagraph) appendInline(doc.createTextNode(text));
          return;
        }
        const lines = text.split(/\r?\n/);
        for (let index = 0; index < lines.length; index++) {
          const line = lines[index];
          if (!line.trim()) {
            closeParagraph();
            continue;
          }
          appendInline(doc.createTextNode(line.replace(/^[ \t]+/, "")));
          if (index < lines.length - 1 && lines[index + 1].trim()) {
            ensureParagraph().appendChild(doc.createElement("br"));
          }
        }
      };

      const processNode = (node) => {
        if (node.nodeType === Node.TEXT_NODE) {
          processText(node.textContent || "");
          return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;

        const tag = node.tagName.toLowerCase();
        if (["pre", "code", "script", "style", "textarea"].includes(tag)) {
          closeParagraph();
          root.appendChild(node);
          return;
        }
        if (BLOCK_TAGS.has(tag) || hasBlockDescendant(node)) {
          closeParagraph();
          root.appendChild(node);
          return;
        }
        if (PRESERVED_INLINE_TAGS.has(tag)) {
          appendInline(node);
          return;
        }
        Array.from(node.childNodes).forEach(processNode);
      };

      original.forEach(processNode);
    };

    const normalizePreservedWhitespace = () => {
      const preserving = queryAll(document, "*").filter((el) =>
        PRESERVE_WHITE_SPACE.has(getComputedStyle(el).whiteSpace)
      );
      const roots = preserving.filter((el) =>
        !preserving.some((parent) => parent !== el && parent.contains(el))
      );

      roots.forEach((root) => {
        const tag = root.tagName.toLowerCase();
        if (["html", "head", "pre", "code", "script", "style", "textarea"].includes(tag)) return;
        if (!CAN_CONTAIN_BLOCK.has(tag)) return;
        if (!/[\n\r]/.test(root.textContent || "")) return;
        rebuildParagraphs(root);
      });
    };

    normalizePreservedWhitespace();

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

    // Ignore common lazy-load placeholders when resolving real media URLs.
    const isPlaceholderSrc = (value) => {
      if (!value) return true;
      const trimmed = value.trim();
      if (!trimmed || trimmed === "data:,") return true;
      return /^data:image\/(?:gif|png);base64,(?:R0lGODlhAQAB|iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB)/i.test(
        trimmed
      );
    };

    // Prefer the highest-resolution srcset candidate.
    const selectSrcset = (value) => {
      let best = null;
      for (const raw of String(value || "").split(",")) {
        const [urlPart, descriptor = ""] = raw.trim().split(/\s+/);
        const url = urlPart && urlPart.trim();
        if (!url || isPlaceholderSrc(url)) continue;
        let rank = 1;
        if (/w$/i.test(descriptor)) {
          rank = parseInt(descriptor, 10) || 0;
        } else if (/x$/i.test(descriptor)) {
          rank = (parseFloat(descriptor) || 0) * 1000;
        }
        if (!best || rank > best.rank) best = { url, rank };
      }
      return best ? best.url : null;
    };

    const toAbsoluteUrl = (value) => {
      if (!value || /^(?:data:|blob:|about:|#)/i.test(value)) return value;
      try {
        return new URL(value, window.location.href).href;
      } catch {
        return value;
      }
    };

    // Default lazy-load attribute names, used when config.lazy is true (boolean).
    // When config.lazy is an array, those attributes are used instead.
    const DEFAULT_LAZY_ATTRS = ["data-src", "data-actualsrc", "data-original", "data-lazy-src", "data-original-src", "data-actual-image", "data-lazy", "data-defer-src", "src"];

    // Shared by lazy-image fixing and carousel extraction.
    // When lazyAttrs is provided, only those attributes are checked.
    const resolveMediaUrl = (el, lazyAttrs) => {
      if (el.tagName === "VIDEO") {
        const source = el.querySelector("source");
        const candidates = [
          el.currentSrc,
          el.getAttribute("src"),
          source && (source.currentSrc || source.getAttribute("src") || source.getAttribute("data-src")),
        ];
        const value = candidates.find((candidate) => !isPlaceholderSrc(candidate));
        return value ? toAbsoluteUrl(value) : null;
      }
      if (el.tagName === "IFRAME") return toAbsoluteUrl(el.getAttribute("src"));

      const srcset = el.getAttribute("srcset") || el.getAttribute("data-srcset");
      const srcsetUrl = selectSrcset(srcset);
      if (srcsetUrl) return toAbsoluteUrl(srcsetUrl);

      const attrs = Array.isArray(lazyAttrs) && lazyAttrs.length ? lazyAttrs : DEFAULT_LAZY_ATTRS;
      for (const attr of attrs) {
        const value = el.getAttribute(attr);
        if (value && !isPlaceholderSrc(value)) return toAbsoluteUrl(value);
      }

      const currentSrc = el.currentSrc;
      if (currentSrc && !isPlaceholderSrc(currentSrc)) return toAbsoluteUrl(currentSrc);
      return null;
    };

    const setImportantStyles = (element, styles) => {
      for (const [property, value] of Object.entries(styles)) {
        element.style.setProperty(property, value, "important");
      }
    };

    // Use the rendered media box when possible, then natural/container size.
    const getMediaSize = (el, fallbackRect) => {
      let width = 0;
      let height = 0;
      try {
        const rect = el.getBoundingClientRect();
        if (rect.width && rect.height) {
          width = rect.width;
          height = rect.height;
        }
      } catch {}
      if (!width) width = el.offsetWidth || 0;
      if (!height) height = el.offsetHeight || 0;
      if ((!width || !height) && el.naturalWidth) {
        width = width || el.naturalWidth;
        height = height || el.naturalHeight;
      }
      if ((!width || !height) && fallbackRect) {
        width = width || fallbackRect.width || 0;
        height = height || fallbackRect.height || 0;
      }
      return { width: Math.round(width) || 0, height: Math.round(height) || 0 };
    };

    const prepareCarouselMediaForMeasurement = (el) => {
      if (el.tagName === "IFRAME") {
        el.style.width = "100%";
        el.style.height = "100%";
        return;
      }
      el.style.display = "block";
      el.style.width = "100%";
      el.style.height = "100%";
      el.style.maxWidth = "none";
      el.style.maxHeight = "none";
      el.style.objectFit = "contain";
    };

    const applyCarouselItemStyle = (item, size) => {
      item.style.cssText = [
        "box-sizing:border-box",
        "display:block",
        "flex:0 0 auto",
        "width:auto",
        "height:100%",
        "max-height:100%",
        "max-width:none",
        "min-height:0",
        "object-fit:contain",
        "object-position:center",
      ].join(";");
      if (item.tagName === "IFRAME" && size.width) {
        item.style.width = `${size.width}px`;
      }
      if (size.width && size.height) {
        item.setAttribute("width", String(size.width));
        item.setAttribute("height", String(size.height));
      } else {
        item.removeAttribute("width");
        item.removeAttribute("height");
      }
    };

    const getInlineMaxHeight = (element) => {
      let current = element;
      while (current && current.nodeType === 1) {
        const styleText = current.getAttribute("style") || "";
        const match = styleText.match(/max-height:\s*([\d.]+)px/i);
        if (match) return parseFloat(match[1]);
        const parent = current.parentNode;
        if (parent && parent.nodeType === 11) {
          current = parent.host || null;
        } else {
          current = parent;
        }
      }
      return null;
    };

    // Some carousel wrappers constrain a descendant (for example Reddit's
    // faceplate-carousel). Preserve that ceiling in the snapshot.
    const getDescendantMaxHeight = (root) => {
      let maxHeight = null;
      const walk = (node) => {
        node.querySelectorAll("*").forEach((el) => {
          if (el.shadowRoot) walk(el.shadowRoot);
          const styleText = el.getAttribute("style") || "";
          const inline = styleText.match(/max-height:\s*([\d.]+)px/i);
          if (inline) {
            const parsed = parseFloat(inline[1]);
            if (parsed > 0 && (maxHeight === null || parsed < maxHeight)) {
              maxHeight = parsed;
            }
          }
          const computed = parseFloat(getComputedStyle(el).maxHeight);
          if (computed > 0 && (maxHeight === null || computed < maxHeight)) {
            maxHeight = computed;
          }
        });
      };
      walk(root);
      return maxHeight;
    };

    // Keep the original container as the layout host and isolate the media
    // list inside an open shadow root so page CSS cannot leak into it.
    const mountCarousel = (container, figure, capturedWidth = 0) => {
      if (capturedWidth) {
        // Keep shrink-wrapped containers (flex/grid items, inline-grid, etc.)
        // from collapsing once their original children are removed.
        setImportantStyles(container, {
          "box-sizing": "border-box",
          "min-width": `${capturedWidth}px`,
        });
      }
      const root = container.shadowRoot || container;
      while (root.firstChild) root.removeChild(root.firstChild);
      const host = container.ownerDocument.createElement("ld-carousel");
      setImportantStyles(host, {
        "box-sizing": "border-box",
        display: "block",
        width: "100%",
        height: "100%",
        "min-height": "0",
        "max-width": "100%",
      });
      root.appendChild(host);
      host.attachShadow({ mode: "open" }).appendChild(figure);
    };

    const processCarousel = (container) => {
      const seen = new Set();
      const items = [];
      let containerRect = null;
      let fixedMaxHeight = null;
      try {
        const rect = container.getBoundingClientRect();
        if (rect.width || rect.height) {
          containerRect = { width: rect.width, height: rect.height };
        }
        const computed = getComputedStyle(container);
        const parsedMaxHeight = parseFloat(computed.maxHeight);
        fixedMaxHeight =
          parsedMaxHeight > 0
            ? parsedMaxHeight
            : getInlineMaxHeight(container);
        if (!fixedMaxHeight) fixedMaxHeight = getDescendantMaxHeight(container);
      } catch {}
      collectMedia(container).forEach((el) => {
        prepareCarouselMediaForMeasurement(el);
        const url = resolveMediaUrl(el, Array.isArray(config.lazy) ? config.lazy : null);
        if (el.tagName === "IMG") {
          if (!url || seen.has(url)) return;
          seen.add(url);
          const img = container.ownerDocument.createElement("img");
          img.src = url;
          img.alt = el.getAttribute("alt") || "";
          applyCarouselItemStyle(img, getMediaSize(el, containerRect));
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
            applyCarouselItemStyle(img, getMediaSize(el, containerRect));
            items.push(img);
            return;
          }
          if (url) seen.add(url);
          const video = el.cloneNode(true);
          if (!video.hasAttribute("controls")) video.setAttribute("controls", "");
          applyCarouselItemStyle(video, getMediaSize(el, containerRect));
          items.push(video);
        } else if (el.tagName === "IFRAME") {
          if (!url || seen.has(url)) return;
          seen.add(url);
          const frame = el.cloneNode(true);
          applyCarouselItemStyle(frame, getMediaSize(el, containerRect));
          items.push(frame);
        }
      });
      if (!items.length) return 0;

      const figure = container.ownerDocument.createElement("figure");
      figure.setAttribute("aria-label", "ld-carousel");
      const capturedHeight =
        containerRect && containerRect.height
          ? Math.round(containerRect.height)
          : 0;
      // Prefer dynamic height in the snapshot, but keep fixed containers that
      // explicitly constrain their carousel. Reader uses the height attribute.
      const containerHeightStyle = (container.style.height || "").trim();
      const figureHeight =
        fixedMaxHeight && capturedHeight
          ? `${Math.min(capturedHeight, Math.round(fixedMaxHeight))}px`
          : containerHeightStyle &&
            containerHeightStyle !== "auto" &&
            !containerHeightStyle.includes("calc(") &&
            !containerHeightStyle.includes("%")
          ? containerHeightStyle
          : "100%";
      const figureMaxHeight =
        fixedMaxHeight && capturedHeight
          ? `${Math.min(capturedHeight, Math.round(fixedMaxHeight))}px`
          : figureHeight;
      figure.style.cssText = [
        "box-sizing:border-box",
        "display:flex",
        "flex-direction:row",
        "overflow-x:auto",
        "overflow-y:hidden",
        "gap:12px",
        "width:100%",
        `height:${figureHeight}`,
        `max-height:${figureMaxHeight}`,
        "max-width:100%",
        "min-height:0",
        "margin:0",
        "align-items:center",
        "scrollbar-width:thin",
        "scrollbar-color:rgba(0,0,0,.35) rgba(0,0,0,.08)",
        "scrollbar-gutter:stable",
      ].join(";");
      if (capturedHeight) {
        figure.setAttribute("height", String(capturedHeight));
      }
      items.forEach((item) => {
        figure.appendChild(item);
      });
      mountCarousel(container, figure, containerRect && containerRect.width);
      return items.length;
    };

    // Remove specified selectors
    for (const selector of config.remove || []) {
      queryAll(document, selector).forEach((el) => {
        if (!el.isConnected || protectedNodes.has(el)) return;
        if (keep.length && !keep.some((target) => isWithin(target, el))) return;
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
    // Supports !important (via setProperty) and CSS custom properties (--var).
    // ":root" selector routes to documentElement for custom property assignment.
    // Note: ":root" only matches documentElement; for ":root selector" combos
    // like ":root .foo", use the selector as-is (queryAll handles it).
    for (const [selector, styles] of Object.entries(config.setStyles || {})) {
      const elements = selector === ":root"
        ? [document.documentElement]
        : queryAll(document, selector);
      elements.forEach((el) => {
        for (const [prop, value] of Object.entries(styles)) {
          const val = String(value);
          if (prop.startsWith("--") || val.includes("!important")) {
            const cleanVal = val.replace("!important", "").trim();
            const priority = val.includes("!important") ? "important" : "";
            el.style.setProperty(prop, cleanVal, priority);
          } else {
            el.style[prop] = val;
          }
        }
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
      queryAll(document, "img").forEach((img) => {
        const currentSrc = img.getAttribute("src");
        if (!isPlaceholderSrc(currentSrc)) return;
        if (Array.isArray(config.lazy)) {
          for (const attr of config.lazy) {
            const value = img.getAttribute(attr);
            if (value && !isPlaceholderSrc(value)) {
              img.setAttribute("src", toAbsoluteUrl(value));
              break;
            }
          }
        } else {
          const resolved = resolveMediaUrl(img);
          if (resolved) img.setAttribute("src", resolved);
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
  };

  // Wait for specified elements to appear before running cleanup.
  // Each entry in waitElements is a selector string; "|" separates OR alternatives.
  // e.g. [".a | .b", ".c"] means: (".a" OR ".b") AND ".c" must be present.
  const waitForElements = async () => {
    const cfg = window.__linkding_cleanup_config || {};
    const waitElements = Array.isArray(cfg.waitElements) ? cfg.waitElements : [];
    if (!waitElements.length) return;

    const timeoutSec = (cfg.waitElementsTimeout || 0) * 1000; // ms
    const deadline = timeoutSec > 0 ? Date.now() + timeoutSec : 0;

    for (const entry of waitElements) {
      // Split on "|" for OR semantics
      const alternatives = String(entry).split("|").map(s => s.trim()).filter(Boolean);
      if (!alternatives.length) continue;

      // Check if any alternative already matches
      const check = () => alternatives.some(sel => queryAll(document, sel).length > 0);

      if (check()) continue;

      // Poll with MutationObserver
      await new Promise(resolve => {
        if (check()) { resolve(); return; }

        const observer = new MutationObserver(() => {
          if (check()) { observer.disconnect(); resolve(); }
        });
        observer.observe(document.documentElement, {
          childList: true, subtree: true, attributes: true,
        });

        if (deadline > 0) {
          const remaining = deadline - Date.now();
          if (remaining <= 0) { observer.disconnect(); resolve(); return; }
          setTimeout(() => { observer.disconnect(); resolve(); }, remaining);
        }
        // If no timeout, wait indefinitely (shouldn't happen in practice)
      });
    }
  };

  const runWithWait = async () => { await waitForElements(); await cleanupFn(); };

  // Register cleanup so the before-hook boilerplate can await it.
  window.__linkdingCleanup = runWithWait;

  // Also listen to the capture request event. When no before-hook boilerplate
  // is present (standalone mode), we handle preventDefault + response ourselves.
  // When the boilerplate IS present, it calls window.__linkdingCleanup directly
  // and dispatches the response event, so this listener is a no-op fallback.
  addEventListener("single-file-on-before-capture-request", (event) => {
    // If the boilerplate already registered (window.__linkdingHooks exists),
    // let it handle the async flow — our cleanup runs via __linkdingCleanup.
    if (window.__linkdingHooks) return;

    // Standalone mode: manage preventDefault + response ourselves.
    event.preventDefault();
    runWithWait().finally(() => {
      dispatchEvent(new CustomEvent("single-file-on-before-capture-response"));
    });
  });
})();
