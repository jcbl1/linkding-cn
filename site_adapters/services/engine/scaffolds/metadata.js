/**
 * Metadata hook scripts for site-adapters.
 *
 * Input (stdin JSON):
 *   hook: "before" | "after" | "replace"  — which hook function to call
 *   url: string                           — URL being processed
 *   config: object                        — merged config (user-facing keys)
 *   html_path: string | null              — path to saved HTML (if available)
 *   result: object | null                 — metadata result dict (for after hooks)
 *   output_path: string | null            — file output path (for snapshot hooks)
 *
 * Output (stdout JSON):
 *   For before hooks: null (continue) or string (snapshot HTML)
 *   For replace hooks: { title, description, image, url }
 *   For after hooks: null (modify result in place via the returned object)
 *
 * Define functions: before(url, config), replace(url, config), after(result, url, config)
 * Only define the hooks you actually use.
 */

const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { hook, url, config, html_path, result, output_path } = input;

let html_content = null;
if (html_path && fs.existsSync(html_path)) {
  html_content = fs.readFileSync(html_path, 'utf8');
}

// --- Hook functions (define only what you need) ---

function before(url, config) {
  /*
   * Executes before the main metadata pipeline.
   * Modify config in-place to affect downstream stages.
   *
   * Example - set a custom request URL:
   *   config.request_url = 'https://api.example.com/v2/articles';
   */
  return null;
}

function replace(url, config) {
  /*
   * Completely replaces the built-in metadata engine.
   * The framework makes NO HTTP request. You fetch and parse the page.
   *
   * Example - fetch with custom headers:
   *   const resp = await fetch(url, { headers: config.headers || {} });
   *   const html = await resp.text();
   *   return { title: extractTitle(html), description: null, image: null, url };
   */
  return { title: null, description: null, image: null, url };
}

function after(result, url, config) {
  /*
   * Executes after metadata extraction. Modify result in-place.
   *
   * Example - strip site name:
   *   result.title = result.title.replace(' - Example', '');
   */
  return null;
}

// --- Dispatch ---
(async () => {
  let output = null;
  try {
    switch (hook) {
      case 'before':
        output = before(url, config);
        break;
      case 'replace':
        output = await replace(url, config);
        break;
      case 'after':
        after(result, url, config);
        break;
      default:
        throw new Error('Unknown hook: ' + hook);
    }
  } catch (e) {
    fs.writeSync(process.stderr.fd, 'Script error: ' + e.message + '\n');
    output = null;
  }
  console.log(JSON.stringify(output));
})();
