/**
 * Snapshot browser script (per-site config engine) (SingleFile browser_script format)
 *
 * Reads config from window.__linkding_cleanup_config:
 *   {
 *     "keep": ["main#main-content"],
 *     "remove": ["nav", "footer", "button[aria-label='Back']"],
 *     "lazy": true | ["data-src", "data-actualsrc"],
 *     "removeClasses": { ".RichContent": ["is-collapsed"] },
 *     "setStyles": { ".RichContent-inner": {"maxHeight": "none"} }
 *   }
 *
 * remove/removeClasses/setStyles/lazy selectors are resolved recursively
 * inside open shadow roots. keep_elements is applied to the normal document
 * body and preserves the matched subtree, including shadow DOM inside it.
 * remove_elements only operates inside kept subtrees and never removes a
 * keep element or any of its ancestors.
 */
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

  addEventListener("single-file-on-before-capture-request", () => {
    const config = window.__linkding_cleanup_config || {};
    const stats = { removed: 0, kept: 0 };

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

    // Fix lazy-loaded images
    if (config.lazy) {
      const attrs = Array.isArray(config.lazy) ? config.lazy : ["data-src", "data-actualsrc", "data-original", "data-lazy-src", "data-original-src", "data-actual-image", "data-lazy", "data-defer-src"];
      queryAll(document, "img").forEach((img) => {
        for (const attr of attrs) {
          const value = img.getAttribute(attr);
          if (value && !img.getAttribute('src')) { img.setAttribute('src', value); break; }
        }
      });
    }

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

    // Embed stats for diagnostics
    const meta = document.createElement('meta');
    meta.name = 'linkding-cleanup-stats';
    meta.content = JSON.stringify(stats);
    document.head && document.head.appendChild(meta);
  });
})();
