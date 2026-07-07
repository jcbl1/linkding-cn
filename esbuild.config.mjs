import esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["bookmarks/services/vendor/defuddle_entry.js"],
  bundle: true,
  platform: "node",
  target: "node20",
  format: "cjs",
  outfile: "bookmarks/services/vendor/defuddle.js",
});
