/**
 * 快照渲染脚本（引擎由 LD_BROWSER_ENGINE 在运行时选择：CloakBrowser 或 Playwright-core）
 *
 * 输入（stdin JSON）：
 *   {
 *     "url": "...", "outputPath": "...", "cookieFile": "",
 *     "cleanup": { "remove": [], "removeHidden": false, "script": "" },
 *     "licenseKey": ""
 *   }
 *
 * 清理逻辑：
 *   先执行声明式 remove（可命中 open shadow roots）
 *   removeHidden 移除计算样式为隐藏的元素
 *   自定义 script 随后执行
 *   之后本地化图片并注入 <base>
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
  const candidates = [cfgPath, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/opt/homebrew/bin/chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].filter(Boolean);
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
 * 根据 LD_BROWSER_ENGINE 启动浏览器（运行时选择，不再 try-catch 回退）
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
        const queryAll = (root, selector) => {
          const matches = Array.from(root.querySelectorAll(selector));
          root.querySelectorAll("*").forEach(el => {
            if (el.shadowRoot) matches.push(...queryAll(el.shadowRoot, selector));
          });
          return matches;
        };
        for (const sel of selectors) {
          queryAll(document, sel).forEach(el => el.remove());
        }
      }, remove);
    }
    if (removeHidden) {
      await page.evaluate(() => {
        const queryAll = (root, selector) => {
          const matches = Array.from(root.querySelectorAll(selector));
          root.querySelectorAll("*").forEach(el => {
            if (el.shadowRoot) matches.push(...queryAll(el.shadowRoot, selector));
          });
          return matches;
        };
        queryAll(document, "*").forEach(el => {
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
      const queryAll = (root, selector) => {
        const matches = Array.from(root.querySelectorAll(selector));
        root.querySelectorAll("*").forEach(el => {
          if (el.shadowRoot) matches.push(...queryAll(el.shadowRoot, selector));
        });
        return matches;
      };
      const imgs = Array.from(queryAll(document, "img[src]"));
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
