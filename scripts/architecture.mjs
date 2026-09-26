/**
 * Render docs/architecture.html to the image the README embeds, and to a PDF.
 *
 *     npm run diagram
 *
 * Kept in the repository for the same reason as scripts/capture.mjs: a diagram
 * of an architecture goes stale the moment a module moves, and the only defence
 * is being able to regenerate it in one command rather than reopening whatever
 * tool drew it.
 *
 * Two outputs, because they are read in different places. GitHub renders a PNG
 * inline in a README and will not render a PDF at all, so the PNG is the one
 * that is embedded. The PDF keeps the text selectable and searchable, and prints
 * at a readable size, which the PNG does not.
 */

import { mkdirSync } from "node:fs";
import { statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { chromium } from "playwright";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = join(ROOT, "docs", "architecture.html");
const PNG = join(ROOT, "docs", "media", "architecture.png");
const PDF = join(ROOT, "docs", "architecture.pdf");

// Twice the CSS width. A README image is displayed at roughly 850px on GitHub,
// so rendering at 1x leaves the 10px file labels unreadable the moment anyone
// opens the full size image, which is the only reason to open it.
const SCALE = 2;

mkdirSync(dirname(PNG), { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1680, height: 1200 },
  deviceScaleFactor: SCALE,
});

const problems = [];
page.on("pageerror", (error) => problems.push(String(error).slice(0, 160)));
page.on("console", (message) => {
  if (message.type() === "error") problems.push(message.text().slice(0, 160));
});

await page.goto(pathToFileURL(SOURCE).href, { waitUntil: "networkidle" });
// Web fonts are not used, but layout still settles a frame after load and a
// screenshot taken before it clips the last panel.
await page.waitForTimeout(400);

const size = await page.evaluate(() => ({
  width: document.body.scrollWidth,
  height: document.body.scrollHeight,
}));

await page.screenshot({ path: PNG, fullPage: true });

// The PDF is printed at the page's own dimensions rather than paginated onto
// A4, so the diagram stays one continuous sheet and nothing is cut across a
// page break.
await page.pdf({
  path: PDF,
  printBackground: true,
  width: `${size.width}px`,
  height: `${size.height}px`,
  pageRanges: "1",
  margin: { top: "0", right: "0", bottom: "0", left: "0" },
});

await browser.close();

const mb = (file) => (statSync(file).size / 1e6).toFixed(2);
process.stdout.write(
  `  ${size.width} x ${size.height} css px, rendered at ${SCALE}x\n` +
    `  docs/media/architecture.png  ${mb(PNG)} MB\n` +
    `  docs/architecture.pdf        ${mb(PDF)} MB\n`,
);

if (problems.length) {
  process.stdout.write(`\nProblems:\n${problems.join("\n")}\n`);
  process.exitCode = 1;
}
