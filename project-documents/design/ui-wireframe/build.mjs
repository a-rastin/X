import { build } from "esbuild";
import { readFileSync, writeFileSync } from "node:fs";
await build({
  entryPoints: ["src/main.tsx"],
  bundle: true,
  minify: true,
  format: "iife",
  outfile: "dist/app.js",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".woff2": "dataurl", ".woff": "dataurl" },
  logLevel: "info",
});
const js = readFileSync("dist/app.js", "utf8").replace(
  /<\/script/gi,
  "<\\/script",
);
const css = readFileSync("dist/app.css", "utf8").replace(
  /([0-9.]+)(?:dvh|svh|lvh|vh)/g,
  (_, n) => `${Number(n) * 9}px`,
);
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>INSIGHT · Design review</title><style>${css}</style></head><body><x-insight-preview id="root"></x-insight-preview><script>${js}</script></body></html>`;
writeFileSync("wireframe.html", html);
