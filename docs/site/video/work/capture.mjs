// Render frames of index.html: node capture.mjs stills t1,t2,...  |  node capture.mjs frames 20.5
import { chromium } from "/Users/abhishekshah/Desktop/Projects/driftline/apps/console/node_modules/playwright/index.mjs";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [mode, arg] = process.argv.slice(2);
const here = path.dirname(new URL(import.meta.url).pathname);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.goto(`file://${here}/index.html`);
await page.evaluate(() => window.ready);
async function shot(t, file) {
  await page.evaluate((t) => window.renderAt(t), t);
  await page.screenshot({ path: file, type: mode === "frames" ? "jpeg" : "png", quality: mode === "frames" ? 95 : undefined });
}
if (mode === "stills") {
  mkdirSync(`${here}/stills`, { recursive: true });
  for (const t of arg.split(",").map(Number)) await shot(t, `${here}/stills/t${t.toFixed(2)}.png`);
} else {
  mkdirSync(`${here}/frames`, { recursive: true });
  const n = Math.round(Number(arg) * 30);
  for (let i = 0; i < n; i++) await shot(i / 30, `${here}/frames/f${String(i).padStart(4, "0")}.jpg`);
  console.log(`${n} frames`);
}
await browser.close();
