/**
 * 快照渲染脚本（构建期确定引擎：CloakBrowser 或 Playwright-core）
 *
 * 引擎由环境变量 LD_BROWSER_ENGINE 决定，不再运行时探测。
 *
 * 输入（stdin JSON）：
 *   {
 *     "url": "...", "outputPath": "...", "cookieFile": "",
 *     "cleanup": { "remove": [], "removeHidden": false, "script": "" },
 *     "licenseKey": ""
 *   }
 *
 * 清理逻辑：
 *   有 script → 完全接管
 *   没有但有 remove → 内置默认清理
 *   removeHidden → 通过 CSS 隐藏标记
 */

const { readFileSync, writeFileSync, existsSync } = require("fs");
const { execFileSync } = require("child_process");

const input = JSON.parse(readFileSync(0, "utf-8"));
const { url, outputPath, cookieFile = "", cleanup = {}, licenseKey = "" } = input;

if (!url || !outputPath) {
  console.error("Missing required fields: url, outputPath");
  process.exit(1);
}

/** 查找系统 chromium 路径 */
function findChromium() {
  const cfgPath = process.env.CHROMIUM_PATH || "";
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

  if (cookieFile && existsSync(cookieFile)) {
    const cookies = JSON.parse(readFileSync(cookieFile, "utf-8"));
    if (cookies.length > 0) await context.addCookies(cookies);
  }

  const page = await context.newPage();

  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });

    // 等待图片 + 滚动触发懒加载
    await page.evaluate(async () => {
      const imgs = Array.from(document.querySelectorAll("img"));
      await Promise.allSettled(imgs.map(img => {
        if (img.complete) return Promise.resolve();
        return new Promise(r => { img.onload = r; img.onerror = r; setTimeout(r, 5000); });
      }));
      for (let i = 0; i < 5; i++) {
        window.scrollBy(0, window.innerHeight);
        await new Promise(r => setTimeout(r, 500));
      }
      window.scrollTo(0, 0);
      await new Promise(r => setTimeout(r, 1000));
    });

    // === 清理：先声明式 remove，再自定义 script ===
    const { remove = [], removeHidden = false, script: customScript = "" } = cleanup;

    // 1. 声明式移除
    if (remove.length > 0) {
      await page.evaluate(selectors => {
        for (const sel of selectors) {
          document.querySelectorAll(sel).forEach(el => el.remove());
        }
      }, remove);
    }
    if (removeHidden) {
      await page.evaluate(() => {
        document.querySelectorAll("*").forEach(el => {
          const style = window.getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden") el.remove();
        });
      });
    }

    // 2. 自定义脚本
    if (customScript && existsSync(customScript)) {
      await page.evaluate(readFileSync(customScript, "utf-8"));
    }

    await page.waitForTimeout(500);

    // 图片本地化
    await page.evaluate(async () => {
      const imgs = Array.from(document.querySelectorAll("img[src]"));
      for (const img of imgs) {
        const src = img.src;
        if (!src || src.startsWith("data:")) continue;
        try {
          const resp = await fetch(src);
          if (!resp.ok) continue;
          const blob = await resp.blob();
          const dataUrl = await new Promise(resolve => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.readAsDataURL(blob);
          });
          img.src = dataUrl;
        } catch {}
      }
    });

    // 注入 <base> 标签
    await page.evaluate(baseUrl => {
      if (!document.querySelector("base")) {
        if (!document.head) { const h = document.createElement("head"); document.documentElement.prepend(h); }
        const base = document.createElement("base");
        base.href = baseUrl;
        document.head.prepend(base);
      }
    }, url);

    const html = await page.content();
    writeFileSync(outputPath, html, "utf-8");
    console.log(`${(html.length / 1024).toFixed(0)} KB`);
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(err => { console.error(err.message); process.exit(1); });
