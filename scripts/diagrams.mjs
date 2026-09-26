/**
 * Render the diagram sheets in docs/ to the images the README embeds, and to PDFs.
 *
 *     npm run diagram
 *
 * Kept in the repository for the same reason as scripts/capture.mjs: a diagram
 * of an architecture goes stale the moment a module moves, and the only defence
 * is being able to regenerate it in one command rather than reopening whatever
 * tool drew it.
 *
 * Two outputs per sheet, because they are read in different places. GitHub
 * renders a PNG inline in a README and will not render a PDF at all, so the PNG
 * is the one that is embedded. The PDF keeps the text selectable and searchable
 * and prints at a readable size, which the PNG does not.
 *
 * Both sheets share docs/diagram.css, so they cannot drift apart visually.
 */

import { mkdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { chromium } from "playwright";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const SHEETS = [
  {
    source: "architecture.html",
    png: "media/architecture.png",
    pdf: "architecture.pdf",
    label: "how the application fits together",
  },
  {
    source: "extraction.html",
    png: "media/extraction.png",
    pdf: "extraction.pdf",
    label: "what happens inside the parse box",
  },
];

// Twice the CSS width. A README image is displayed at roughly 850px on GitHub,
// so rendering at 1x leaves the 10px file labels unreadable the moment anyone
// opens the full size image, which is the only reason to open it.
const SCALE = 2;

const problems = [];
const browser = await chromium.launch();

for (const sheet of SHEETS) {
  const source = join(ROOT, "docs", sheet.source);
  const png = join(ROOT, "docs", sheet.png);
  const pdf = join(ROOT, "docs", sheet.pdf);
  mkdirSync(dirname(png), { recursive: true });

  const page = await browser.newPage({
    viewport: { width: 1680, height: 1200 },
    deviceScaleFactor: SCALE,
  });
  page.on("pageerror", (error) => problems.push(`${sheet.source}: ${String(error).slice(0, 160)}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`${sheet.source}: ${message.text().slice(0, 160)}`);
  });
  // A stylesheet that failed to load would silently render an unstyled wall of
  // text, which still screenshots successfully and looks like nothing is wrong.
  page.on("requestfailed", (request) =>
    problems.push(`${sheet.source}: could not load ${request.url().split("/").pop()}`),
  );

  await page.goto(pathToFileURL(source).href, { waitUntil: "networkidle" });
  // No web fonts are used, but layout still settles a frame after load and a
  // screenshot taken before it clips the last panel.
  await page.waitForTimeout(400);

  const size = await page.evaluate(() => ({
    width: document.body.scrollWidth,
    height: document.body.scrollHeight,
    // The sheet's own background, which only diagram.css sets. Measuring the
    // width instead does not work: an unstyled body simply fills the viewport,
    // so it reports exactly the width a correct render would.
    background: getComputedStyle(document.body).backgroundColor,
  }));

  if (size.background !== "rgb(251, 250, 249)") {
    problems.push(
      `${sheet.source}: body background is ${size.background}, so diagram.css did not apply`,
    );
  }

  await page.screenshot({ path: png, fullPage: true });

  // Printed at the page's own dimensions rather than paginated onto A4, so the
  // sheet stays continuous and nothing is cut across a page break.
  await page.pdf({
    path: pdf,
    printBackground: true,
    width: `${size.width}px`,
    height: `${size.height}px`,
    pageRanges: "1",
    margin: { top: "0", right: "0", bottom: "0", left: "0" },
  });

  await page.close();

  const mb = (file) => (statSync(file).size / 1e6).toFixed(2);
  process.stdout.write(
    `  ${sheet.source.padEnd(20)} ${size.width} x ${size.height} css px  (${sheet.label})\n` +
      `    docs/${sheet.png.padEnd(24)} ${mb(png)} MB\n` +
      `    docs/${sheet.pdf.padEnd(24)} ${mb(pdf)} MB\n`,
  );
}

await browser.close();

if (problems.length) {
  process.stdout.write(`\nProblems:\n${problems.join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write("\nBoth sheets rendered, no console errors.\n");
}
