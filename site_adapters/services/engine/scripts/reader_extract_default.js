/**
 * 默认文章提取脚本
 *
 * 输入（stdin JSON）：
 *   { "htmlPath": "/tmp/page.html", "url": "https://...",
 *     "contentSelector": [".article-body"],   ← defuddle 原始参数
 *     "removeExactSelectors": [".ad"],         ← defuddle 原始参数
 *     "includeReplies": false, ... }
 *
 * 输出：defuddle JSON（title, content, description, author, site, wordCount）
 *
 * 所有 defuddle 支持的参数直接透传，不做映射。
 */

const { readFileSync } = require("fs");

const input = JSON.parse(readFileSync(0, "utf-8"));
const { htmlPath, url, script, ...defuddleOpts } = input;

(async () => {
  const { Defuddle } = require("./defuddle.js");

  defuddleOpts.url = url;

  let html = "";
  if (htmlPath) html = readFileSync(htmlPath, "utf-8");

  const defuddle = new Defuddle(html, defuddleOpts);
  const result = defuddle.parse();
  process.stdout.write(JSON.stringify({
    title: result.title || "",
    content: result.content || "",
    description: result.description || "",
    author: result.author || "",
    site: result.site || "",
    wordCount: result.wordCount || 0,
  }));
})().catch(err => { console.error(err.message); process.exit(1); });
