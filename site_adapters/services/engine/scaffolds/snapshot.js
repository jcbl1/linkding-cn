/**
 * Snapshot SingleFile browser hook (site-adapters).
 *
 * This template runs inside the SingleFile browser context. It is the default
 * template for snapshot JavaScript before/after hooks.
 *
 * ---------------------------------------------------------------------------
 * Built-in engine declaration
 * ---------------------------------------------------------------------------
 *
 * const builtin_engine = "singlefile";
 *
 * Allowed values:
 *   "singlefile" - run inside SingleFile (this template)
 *   "" or null  - external Node mode (use snapshot_node.js)
 *   any other value is an error.
 *
 * This variable is only used by snapshot JavaScript scripts. Python snapshot
 * scripts are always external and do not declare it.
 *
 * ---------------------------------------------------------------------------
 * Runtime behavior
 * ---------------------------------------------------------------------------
 *
 * The framework wraps this file, dispatches
 * `single-file-user-script-init`, and registers the SingleFile capture events.
 * You only define `before` and `after`.
 *
 *   before(url, config) runs when `single-file-on-before-capture-request`
 *   fires, before SingleFile captures the page.
 *
 *   after(url, config) runs when `single-file-on-after-capture-request`
 *   fires, after SingleFile processes the DOM and before serialization.
 *
 * Both functions may be async. The framework always calls `preventDefault()`
 * and waits for the matching `-response` event, so sync and async code both
 * work.
 *
 * This template runs in the page, so `document`, `window`, `fetch`, and DOM
 * APIs are available. Node APIs such as `require`, `fs`, and `process` are
 * NOT available here. For external Node hooks that need those APIs, use the
 * `snapshot_node.js` scaffold.
 *
 * ---------------------------------------------------------------------------
 * Config keys available in every hook
 * ---------------------------------------------------------------------------
 *
 * The framework injects the sanitized config and URL. Common keys:
 *
 *   headers            object         HTTP request headers
 *   timeout            number|null    Timeout in seconds
 *   proxy              string|null    HTTP proxy URL
 *   request_url        string|null    Resolved request URL
 *   user_cookie        string|null    Best available cookie string
 *   keep_elements      string[]       CSS selectors to keep
 *   remove_elements    string[]       CSS selectors to remove
 *   process_lazy_images boolean|string[]
 *   remove_classes     object         CSS classes to remove
 *   set_styles         object         Inline styles to set
 *   singlefile_args    object         SingleFile CLI args
 *   toggles            object         User-toggleable controls
 */

const builtin_engine = "singlefile";

async function before(url, config) {
  /**
   * Runs before SingleFile captures the page.
   * Modify the live DOM; changes are included in the snapshot.
   *
   * Example - expand collapsed content:
   *   document.querySelectorAll('.RichContent.is-collapsed').forEach((el) => {
   *     el.classList.remove('is-collapsed');
   *   });
   *
   * Example - lazy-load images from custom attributes:
   *   document.querySelectorAll('img[data-src]').forEach((img) => {
   *     if (!img.getAttribute('src')) {
   *       img.setAttribute('src', img.getAttribute('data-src'));
   *     }
   *   });
   */
}

async function after(url, config) {
  /**
   * Runs after SingleFile processes the DOM, before serialization.
   * Use it for DOM-level final adjustments.
   *
   * Example - inject dark mode:
   *   const style = document.createElement('style');
   *   style.textContent = 'body { background: #111; color: #eee; }';
   *   document.head.appendChild(style);
   */
}
