/**
 * Snapshot SingleFile browser hook (site-adapters).
 *
 * This template is the default for snapshot JavaScript hooks.
 * `before` runs inside the SingleFile browser context.
 * `after` runs against the saved snapshot HTML after SingleFile writes it.
 *
 * ---------------------------------------------------------------------------
 * Built-in engine declaration
 * ---------------------------------------------------------------------------
 *
 * const builtin_engine = "singlefile";
 *
 * Allowed values:
 *   "singlefile" - before runs inside SingleFile; after runs on saved HTML
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
 * The framework wraps `before` into the SingleFile browser script. `after`
 * is executed separately against the saved HTML. The current implementation
 * uses Linkedom.
 *
 *   before(url, config) runs when `single-file-on-before-capture-request`
 *   fires, before SingleFile captures the page.
 *
 *   after(url, config) runs after the snapshot HTML file is written. DOM
 *   changes are serialized back into the saved file.
 *
 * `before` may be async; the framework calls `preventDefault()` and waits for
 * the matching `-response` event. `after` may also be async and is awaited by
 * the Node runner.
 *
 * `before` runs in the page, so browser DOM APIs are available. `after` runs
 * in Node with a Linkedom `document`/`window`, so DOM changes are possible but
 * Linkedom is not a real browser: property reflection is incomplete. Use
 * `setAttribute()`/`getAttribute()` for attributes that must persist, e.g.
 * `video.setAttribute("src", url)` rather than `video.src = url`. Node APIs
 * such as `require`, `fs`, and `process` are not exposed to user code. For
 * external Node hooks that need those APIs, use `snapshot_node.js`.
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
   * Runs after the snapshot HTML file is written. DOM changes are saved back.
   * The current implementation runs in a Linkedom DOM, not a real browser:
   * property reflection is incomplete, so use setAttribute()/getAttribute()
   * for attributes that must persist.
   *
   * Example - inject dark mode:
   *   const style = document.createElement('style');
   *   style.textContent = 'body { background: #111; color: #eee; }';
   *   document.head.appendChild(style);
   */
}
