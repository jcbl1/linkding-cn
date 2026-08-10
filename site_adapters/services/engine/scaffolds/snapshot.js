/**
 * Snapshot hook scripts for site-adapters.
 *
 * Input (stdin JSON):
 *   hook: "before" | "after" | "replace"  — which hook function to call
 *   url: string                           — URL being processed
 *   config: object                        — merged config (user-facing keys)
 *   html_path: string | null              — path to saved HTML
 *   output_path: string | null            — file path for snapshot output
 *
 * Output (stdout JSON):
 *   For before hooks: null (fetch URL normally) or string (HTML to feed SingleFile)
 *   For replace hooks: null (result written to output_path)
 *   For after hooks: null (modify output_path in-place)
 *
 * Define functions: before(url, config), replace(url, config, output_path),
 *                     after(output_path, config)
 */

const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { hook, url, config, output_path } = input;

// --- Hook functions (define only what you need) ---

function before(url, config) {
  /*
   * Executes before SingleFile. Return HTML string to feed it to SingleFile
   * instead of re-fetching the URL; return null to let SingleFile fetch normally.
   */
  return null;
}

async function replace(url, config, output_path) {
  /*
   * Completely replaces the SingleFile engine.
   * Write a complete HTML file to output_path.
   *
   * Example with Puppeteer:
   *   const puppeteer = require('puppeteer');
   *   const browser = await puppeteer.launch();
   *   const page = await browser.newPage();
   *   await page.goto(url, { waitUntil: 'networkidle0' });
   *   const html = await page.content();
   *   fs.writeFileSync(output_path, html);
   *   await browser.close();
   */
}

function after(output_path, config) {
  /*
   * Executes after the snapshot HTML is written. Modify the file in-place.
   *
   * Example - inject dark mode:
   *   let html = fs.readFileSync(output_path, 'utf8');
   *   html = html.replace('</head>', '<style>body{background:#111}</style></head>');
   *   fs.writeFileSync(output_path, html);
   */
}

// --- Dispatch ---
(async () => {
  try {
    switch (hook) {
      case 'before':
        const html = before(url, config);
        console.log(JSON.stringify(html));
        break;
      case 'replace':
        await replace(url, config, output_path);
        console.log('null');
        break;
      case 'after':
        after(output_path, config);
        console.log('null');
        break;
      default:
        throw new Error('Unknown hook: ' + hook);
    }
  } catch (e) {
    fs.writeSync(process.stderr.fd, 'Script error: ' + e.message + '\n');
    console.log('null');
  }
})();
