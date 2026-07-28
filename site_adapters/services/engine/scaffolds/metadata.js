/**
 * Metadata script - replaces default extraction engine.
 *
 * Input (stdin JSON):
 *   url: string - URL being processed
 *   config: object - merged config (default + metadata sections)
 *   html_content: string - fetched HTML content
 *
 * Output (stdout JSON):
 *   { title: string|null, description: string|null, image: string|null }
 */

const fs = require('fs');

const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { url, config, html_content } = input;

// Access config fields
const selectTitle = config.select_title || [];
const selectDescription = config.select_description || [];
const selectImage = config.select_image || [];

// TODO: implement your extraction logic
// Example using cheerio:
// const cheerio = require('cheerio');
// const $ = cheerio.load(html_content);
// const title = selectTitle[0] ? $(selectTitle[0]).text() : null;

const result = {
  title: null,
  description: null,
  image: null,
};

console.log(JSON.stringify(result));
