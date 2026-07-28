/**
 * Reader script - replaces defuddle engine.
 *
 * Input (stdin JSON):
 *   url: string - URL being processed
 *   config: object - merged config (default + reader sections)
 *
 * Output (stdout):
 *   HTML string or JSON { content: string, title: string|null }
 */

const fs = require('fs');

const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { url, config } = input;

// Access config fields
const defuddleArgs = config.defuddle_args || {};
const headers = config.headers || {};

// TODO: implement your reader logic
// Example using Readability:
// const { JSDOM } = require('jsdom');
// const { Readability } = require('@mozilla/readability');
// const resp = await fetch(url, { headers });
// const html = await resp.text();
// const doc = new JSDOM(html, { url });
// const reader = new Readability(doc.window.document);
// const article = reader.parse();
// console.log(article.content);

console.log('');
