/**
 * Snapshot browser script (per-site config engine) (SingleFile browser_script format)
 *
 * Reads config from window.__linkding_cleanup_config:
 *   {
 *     "keep": ["article"],
 *     "remove": ["nav", "footer"],
 *     "lazy": true | ["data-src", "data-actualsrc"],
 *     "removeClasses": { ".RichContent": ["is-collapsed"] },
 *     "setStyles": { ".RichContent-inner": {"maxHeight": "none"} }
 *   }
 */
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

  addEventListener("single-file-on-before-capture-request", () => {
    const config = window.__linkding_cleanup_config || {};
    const stats = { removed: 0, kept: 0 };

    // Fix lazy-loaded images
    if (config.lazy) {
      const attrs = Array.isArray(config.lazy) ? config.lazy : ["data-src", "data-actualsrc", "data-original", "data-lazy-src", "data-original-src", "data-actual-image", "data-lazy", "data-defer-src"];
      document.querySelectorAll('img').forEach((img) => {
        for (const attr of attrs) {
          const value = img.getAttribute(attr);
          if (value && !img.getAttribute('src')) { img.setAttribute('src', value); break; }
        }
      });
    }

    // Remove specified selectors
    for (const selector of config.remove || []) {
      document.querySelectorAll(selector).forEach((el) => { el.remove(); stats.removed += 1; });
    }

    // Remove classes: { ".selector": ["class1", "class2"] }
    for (const [selector, classes] of Object.entries(config.removeClasses || {})) {
      document.querySelectorAll(selector).forEach((el) => {
        for (const cls of (Array.isArray(classes) ? classes : [classes])) el.classList.remove(cls);
      });
    }

    // Set styles: { ".selector": { "prop": "value" } }
    for (const [selector, styles] of Object.entries(config.setStyles || {})) {
      document.querySelectorAll(selector).forEach((el) => {
        for (const [prop, value] of Object.entries(styles)) el.style[prop] = value;
      });
    }

    // Keep only specified selectors (remove everything else)
    if ((config.keep || []).length) {
      const keep = [];
      for (const selector of config.keep) keep.push(...document.querySelectorAll(selector));
      stats.kept = keep.length;
      if (keep.length) {
        document.body.querySelectorAll('*').forEach((el) => {
          if (!keep.some((target) => target === el || target.contains(el) || el.contains(target))) {
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
