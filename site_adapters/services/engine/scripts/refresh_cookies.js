/**
 * Cookie 刷新脚本（构建期确定引擎：CloakBrowser 或 Playwright-core）
 *
 * 引擎由环境变量 LD_BROWSER_ENGINE 决定，不再运行时探测。
 *
 * 输入（stdin JSON）：
 *   { "url": "...", "cookie_file": "...", "wait_cookie": "...", "chromium_path": "", "timeout": 30000 }
 *
 * 合并策略：新 cookie 更新/新增，保留已有 cookie（如登录态）。
 */

const { execFileSync } = require("child_process");
const { readFileSync, writeFileSync, existsSync, mkdirSync } = require("fs");
const { dirname } = require("path");

const input = JSON.parse(readFileSync(0, "utf-8"));
const {
  url,
  cookie_file,
  outputPath,
  wait_cookie = "",
  waitCookie = "",
  chromium_path = "",
  timeout = 30000,
  licenseKey = "",
} = input;
const outPath = cookie_file || outputPath;
const waitForCookie = wait_cookie || waitCookie;

if (!url || !outPath) {
  console.error("Missing required fields: url, cookie_file");
  process.exit(1);
}

// 读取已有 cookie
let existingCookies = [];
try {
  if (existsSync(outPath)) {
    const raw = readFileSync(outPath, "utf-8").trim();
    if (raw && raw !== "[]") existingCookies = JSON.parse(raw);
  }
} catch {}

// 确保输出目录存在
const dir = dirname(outPath);
if (!existsSync(dir)) mkdirSync(dir, { recursive: true });

function findChromium() {
  const cfgPath = chromium_path || process.env.CHROMIUM_PATH || "";
  const candidates = [cfgPath, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/opt/homebrew/bin/chromium"].filter(Boolean);
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  for (const bin of ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]) {
    try {
      const p = execFileSync("which", [bin], { encoding: "utf-8" }).trim();
      if (p) return p;
    } catch {}
  }
  return "/usr/bin/chromium";
}

/**
 * 根据 LD_BROWSER_ENGINE 启动浏览器（构建期已确定，不再 try-catch 回退）
 */
async function getLauncher() {
  const engine = process.env.LD_BROWSER_ENGINE || "cloakbrowser";

  if (engine === "cloakbrowser") {
    const cb = await import("cloakbrowser");
    const opts = {};
    const key = process.env.CLOAKBROWSER_LICENSE_KEY || licenseKey;
    if (key) opts.license_key = key;
    return { launch: cb.launch, opts };
  }

  // chromium 模式
  const pw = require("playwright-core");
  const execPath = findChromium();
  return {
    launch: (opts) => pw.chromium.launch({
      headless: true,
      executablePath: execPath,
      args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
      ...opts,
    }),
    opts: {},
  };
}

(async () => {
  const { launch, opts } = await getLauncher();
  const browser = await launch(opts);
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    await page.goto(url, { waitUntil: "networkidle", timeout });

    if (waitForCookie) {
      const deadline = Date.now() + timeout;
      let found = false;
      while (Date.now() < deadline) {
        const cookies = await context.cookies();
        if (cookies.some(c => c.name === waitForCookie)) { found = true; break; }
        await new Promise(r => setTimeout(r, 1000));
      }
      if (!found) console.error(`Cookie "${waitForCookie}" not found within timeout`);
    }

    const freshCookies = await context.cookies();

    // 合并
    const merged = new Map();
    for (const c of existingCookies) merged.set(c.name, c);
    for (const c of freshCookies) merged.set(c.name, c);

    const result = [...merged.values()];
    writeFileSync(outPath, JSON.stringify(result, null, 2));
    console.log(`${result.length} cookies (${freshCookies.length} fresh, ${result.length - freshCookies.length} preserved)`);
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(err => { console.error(err.message); process.exit(1); });
