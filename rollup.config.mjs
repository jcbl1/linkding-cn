import resolve from '@rollup/plugin-node-resolve';
import commonjs from '@rollup/plugin-commonjs';
import terser from '@rollup/plugin-terser';

const production = !process.env.ROLLUP_WATCH;

export default [{
  input: 'bookmarks/frontend/index.js',
  output: {
    sourcemap: true,
    format: 'iife',
    name: 'linkding',
    file: 'bookmarks/static/bundle.js',
  },
  plugins: [
    resolve({
      browser: true,
    }),
    production && terser(),
  ],
  watch: {
    clearScreen: false,
  },
}, {
  input: 'bookmarks/frontend/reader/reader.js',
  output: {
    sourcemap: true,
    format: 'iife',
    name: 'linkdingReader',
    file: 'bookmarks/static/reader.js',
  },
  plugins: [
    commonjs(),
    resolve({
      browser: true,
    }),
    production && terser(),
  ],
}, {
  input: 'bookmarks/frontend/reader/reader-pending.js',
  output: {
    sourcemap: true,
    format: 'iife',
    name: 'linkdingReaderPending',
    file: 'bookmarks/static/reader-pending.js',
  },
  plugins: [
    commonjs(),
    resolve({
      browser: true,
    }),
    production && terser(),
  ],
}, {
  input: 'bookmarks/frontend/reader/reader-unavailable.js',
  output: {
    sourcemap: true,
    format: 'iife',
    name: 'linkdingReaderUnavailable',
    file: 'bookmarks/static/reader-unavailable.js',
  },
  plugins: [
    commonjs(),
    resolve({
      browser: true,
    }),
    production && terser(),
  ],
}, {
  input: 'site_adapters/frontend/site-adapters.js',
  output: {
    sourcemap: true,
    format: 'iife',
    name: 'linkdingSiteAdapters',
    file: 'site_adapters/static/site-adapters.js',
  },
  plugins: [
    resolve({
      browser: true,
    }),
    production && terser(),
  ],
}];
