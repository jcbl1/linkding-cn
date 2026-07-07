/**
 * defuddle Node.js 包装脚本
 *
 * 通过 stdin 接收 JSON 参数，调用 defuddle 模块 API，
 * 支持 contentSelector 等 CLI 未暴露的 DefuddleOptions。
 *
 * 输入格式：
 *   { "htmlPath": "/tmp/page.html", "url": "https://...", "options": { ... } }
 *   或
 *   { "url": "https://...", "options": { ... } }  （直接解析 URL）
 *
 * 输出格式：标准 defuddle JSON 结果
 */

const { readFileSync } = require("fs");
const {
  Defuddle,
  ExtractorRegistry,
  parseLinkedomHTML,
  fetchPage,
  getInitialUA,
  BOT_UA,
  extractRawMarkdown,
  cleanMarkdownContent,
  countWords,
} = require("./defuddle.js");

const input = JSON.parse(readFileSync(0, "utf-8"));

const { htmlPath, url, options = {} } = input;

// 用户传了 contentSelector，说明对该域名有自定义提取需求，
// 移除匹配当前 URL 的内置提取器，让自定义 contentSelector 生效。
if (options.contentSelector && url) {
  const domain = new URL(url).hostname;
  ExtractorRegistry.mappings = ExtractorRegistry.mappings.filter(({ patterns }) => {
    return !patterns.some(pattern => {
      if (pattern instanceof RegExp) return pattern.test(url);
      return domain.includes(pattern);
    });
  });
}

const defuddleOpts = {
  ...options,
  url: url,
};

async function main() {
  if (!htmlPath && !url) {
    process.stderr.write("Either htmlPath or url must be provided");
    process.exit(1);
  }

  let html;
  if (htmlPath) {
    html = readFileSync(htmlPath, "utf-8");
  } else {
    // 直接解析 URL：复现 CLI 的抓取 + bot UA 重试逻辑
    const initialUA = getInitialUA(url);
    html = await fetchPage(url, initialUA, options.language);
  }

  const doc = parseLinkedomHTML(html, url);
  let result = await Defuddle(doc, url, defuddleOpts);

  // 如果从 URL 直接解析且无内容，用 bot UA 重试（复现 CLI 行为）
  if (!htmlPath && url && result.wordCount === 0) {
    try {
      const botHtml = await fetchPage(url, BOT_UA, options.language);
      const rawMarkdown = extractRawMarkdown(botHtml);
      if (rawMarkdown) {
        const botDoc = parseLinkedomHTML(botHtml, url);
        const botResult = await Defuddle(botDoc, url, defuddleOpts);
        botResult.content = cleanMarkdownContent(rawMarkdown);
        botResult.wordCount = countWords(botResult.content);
        result = botResult;
      } else {
        const botDoc = parseLinkedomHTML(botHtml, url);
        const botResult = await Defuddle(botDoc, url, defuddleOpts);
        if (botResult.wordCount > 0) {
          result = botResult;
        }
      }
    } catch {
      // bot UA 可能被拦截，使用原始结果
    }
  }

  process.stdout.write(JSON.stringify(result));
}

main().catch((err) => {
  process.stderr.write(err.message || String(err));
  process.exit(1);
});
