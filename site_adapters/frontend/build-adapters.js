// Build adapters.js for the user-facing Settings > Adapters page.
// Replaces ES module import with inline gettext, then minifies via terser.
const fs = require('fs');
const path = require('path');
const { minify } = require('terser');

const root = path.resolve(__dirname, '..', '..');
const src = path.join(root, 'site_adapters', 'frontend', 'adapters.js');
const out = path.join(root, 'site_adapters', 'static', 'adapters.js');
const map = path.join(root, 'site_adapters', 'static', 'adapters.js.map');

let code = fs.readFileSync(src, 'utf-8');

const gettextFn = [
  'var gettext = (function() {',
  '  var _gt = window.gettext;',
  '  return function(msg) {',
  '    return (typeof _gt === "function") ? _gt(msg) : msg;',
  '  };',
  '})();',
].join('\n');

code = code.replace(
  /^import\s*\{[^}]*gettext[^}]*\}\s*from\s*["'][^"']+["'];?\s*$/m,
  gettextFn,
);

minify({ 'adapters.js': code }, {
  sourceMap: { filename: 'adapters.js', url: 'adapters.js.map' },
  format: { comments: false },
}).then(result => {
  fs.writeFileSync(out, result.code, 'utf-8');
  if (result.map) fs.writeFileSync(map, result.map, 'utf-8');
  console.log('Built adapters.js (' + result.code.length + ' bytes)');
}).catch(err => {
  console.error(err);
  process.exit(1);
});
