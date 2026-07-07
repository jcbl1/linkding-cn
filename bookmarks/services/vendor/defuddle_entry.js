// 聚合 defuddle 模块导出，供 esbuild 打包为 vendor/defuddle.js
const { Defuddle } = require("../../../node_modules/defuddle/dist/node.js");
const { ExtractorRegistry } = require("../../../node_modules/defuddle/dist/extractor-registry.js");
const { parseLinkedomHTML } = require("../../../node_modules/defuddle/dist/utils/linkedom-compat.js");
const { fetchPage, getInitialUA, BOT_UA, extractRawMarkdown, cleanMarkdownContent } = require("../../../node_modules/defuddle/dist/fetch.js");
const { countWords } = require("../../../node_modules/defuddle/dist/utils.js");

module.exports = {
  Defuddle,
  ExtractorRegistry,
  parseLinkedomHTML,
  fetchPage,
  getInitialUA,
  BOT_UA,
  extractRawMarkdown,
  cleanMarkdownContent,
  countWords,
};
