/**
 * Capture the screenshots and the demo recording used by the README.
 *
 * Kept in the repository rather than run ad hoc, because the README's images
 * go stale the moment the UI moves and the only defence is being able to
 * regenerate them in one command:
 *
 *     npm run capture
 *
 * It needs both halves running (npm run api, npm run dev) and at least one
 * paper in the library. Playwright records webm; ffmpeg turns that into the
 * GIF the README embeds, and is skipped with a warning if ffmpeg is absent.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.CAPTURE_BASE || "http://127.0.0.1:3099";
const OUT = process.env.CAPTURE_OUT || "docs/media";
const VIEWPORT = { width: 1440, height: 1000 };
// Which provider the captured screens should answer with. Pinned rather than
// left to the automatic choice, because the README should not depend on
// whichever provider happens not to be rate limited on the day.
const PROVIDER = process.env.CAPTURE_PROVIDER || "cloudflare";

mkdirSync(OUT, { recursive: true });

const problems = [];

function watch(page, name) {
  page.on("pageerror", (e) => problems.push(`${name}: ${String(e).slice(0, 140)}`));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`${name}: ${m.text().slice(0, 140)}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400) {
      problems.push(`${name}: HTTP ${r.status()} ${r.url().replace(BASE, "").slice(0, 70)}`);
    }
  });
}

/**
 * Wait until an answer has actually been written, rather than for a guessed
 * number of seconds. A screenshot taken mid stream shows a spinner, which is
 * the one thing the README should not be advertising.
 */
async function waitForAnswer(page, timeout = 120000) {
  await page.waitForFunction(
    () => {
      // The footer only renders once the final event has landed and the
      // citations have been validated, so it is the signal that the answer is
      // finished rather than merely started. Waiting for the text alone
      // screenshots a complete answer still badged "writing".
      if (
        Array.from(document.querySelectorAll("p")).some((node) =>
          /Every claim above is drawn only from the excerpts/.test(node.textContent || ""),
        )
      ) {
        return true;
      }
      // A provider that refused, usually a rate limit, surfaces as a notice
      // rather than an answer. Waiting the full two minutes for text that is
      // never coming turns one bad provider day into a failed capture.
      return Array.from(document.querySelectorAll("div")).some((node) =>
        /model call failed|rate limit|quota/i.test(node.textContent || ""),
      );
    },
    undefined,
    { timeout },
  );
  await page.waitForTimeout(800);
}

/**
 * Wait for written prose to appear anywhere.
 *
 * The analyse screen renders its result without the citation footer that the
 * ask screen carries, so it needs the weaker test. Using the footer test there
 * waits for something that is never rendered.
 */
async function waitForProse(page, timeout = 180000) {
  await page.waitForFunction(
    () => {
      const node = document.querySelector(".prose-answer");
      if (node && node.textContent && node.textContent.length > 200) return true;
      return Array.from(document.querySelectorAll("div")).some((el) =>
        /model call failed|rate limit|quota|needs a language model/i.test(el.textContent || ""),
      );
    },
    undefined,
    { timeout },
  );
  await page.waitForTimeout(900);
}

/** Click a control once it exists, rather than assuming it already does. */
async function click(page, name, timeout = 30000) {
  const target = page.getByRole("button", { name }).first();
  await target.waitFor({ state: "visible", timeout });
  await target.click();
}

/** A context with the capture provider already chosen in the app's own prefs. */
async function open(browser, extra = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, ...extra });
  await context.addInitScript((provider) => {
    try {
      window.localStorage.setItem(
        "dissect.prefs.v1",
        JSON.stringify({ provider, useLlm: true }),
      );
    } catch {
      // Private mode or blocked storage. The automatic provider is then used,
      // which is a worse screenshot but not a failed one.
    }
  }, PROVIDER);
  return context;
}

async function shot(browser, name, path, prep) {
  const context = await open(browser);
  const page = await context.newPage();
  watch(page, name);
  try {
    await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 90000 });
    // A long element list is still rendering when networkidle fires, so the
    // screen is given a moment to settle before anything is clicked.
    await page.waitForTimeout(1200);
    if (prep) await prep(page);
    await page.waitForTimeout(700);
    await page.screenshot({ path: join(OUT, `${name}.png`) });
    process.stdout.write(`  ${name}.png\n`);
  } catch (error) {
    // One shot failing must not lose the other ten. It is reported at the end
    // rather than thrown, because a partial set is still useful.
    problems.push(`${name}: ${String(error).split("\n")[0].slice(0, 120)}`);
    process.stdout.write(`  ${name}.png FAILED\n`);
  } finally {
    await context.close();
  }
}

/**
 * The demo: paste an arXiv id, watch it parse and index, browse what came out,
 * then ask a question and watch the answer stream in with citations.
 *
 * One take rather than several clips, because the point of the tool is that
 * those steps are one continuous thing.
 */
async function record(browser, paperId) {
  const dir = join(OUT, "_video");
  rmSync(dir, { recursive: true, force: true });
  const context = await open(browser, { recordVideo: { dir, size: VIEWPORT } });
  const page = await context.newPage();
  watch(page, "demo");

  await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(1300);

  // Typed slowly enough to read on the recording. Not submitted: fetching a
  // real paper mid take would add half a minute of progress bar.
  const box = page.getByPlaceholder("2407.01449");
  await box.click();
  await box.type("1512.03385", { delay: 65 });
  await page.waitForTimeout(1000);

  await page.goto(`${BASE}/paper/${paperId}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  await page.getByRole("link", { name: "All views", exact: true }).first().click();
  await page.waitForTimeout(1200);
  await click(page, /^Figures/);
  await page.waitForTimeout(2400);

  // Page 4 rather than page 1: it carries a figure, a heading and a display
  // equation, so the extraction overlay has something to show.
  await page.goto(`${BASE}/paper/${paperId}/reader?page=4`, { waitUntil: "networkidle" });
  await page.waitForTimeout(3400);

  await page.getByRole("link", { name: "Ask", exact: true }).first().click();
  await page.waitForTimeout(1000);
  const ask = page.getByPlaceholder("What was the learning rate");
  await ask.click();
  await ask.type("What optimizer did they use?", { delay: 50 });
  await click(page, /Answer with citations/);
  // The recording shows the answer arriving, not necessarily finishing: the
  // completed answer is in ask.png, and waiting out the last tokens here adds
  // twenty seconds of GIF for very little.
  await page
    .waitForFunction(
      () => {
        const node = document.querySelector(".prose-answer");
        return Boolean(node && node.textContent && node.textContent.length > 120);
      },
      undefined,
      { timeout: 90000 },
    )
    .catch(() => {});
  await page.waitForTimeout(4500);

  await context.close();

  const files = readdirSync(dir).filter((f) => f.endsWith(".webm"));
  if (!files.length) {
    problems.push("demo: no video was recorded");
    return;
  }
  const webm = join(OUT, "demo.webm");
  renameSync(join(dir, files[0]), webm);
  rmSync(dir, { recursive: true, force: true });
  process.stdout.write("  demo.webm\n");

  try {
    // Two pass: build a palette first, or the gradients in the UI band badly.
    const palette = join(OUT, "_palette.png");
    // 8fps at 880px keeps the GIF near 3 MB. A README that opens with a
    // 5 MB animation is a README people scroll past while it loads.
    const filters = "fps=7,scale=860:-1:flags=lanczos";
    // A hard cap on length, so an unusually slow provider cannot turn the
    // README's opening image into a seven megabyte download.
    const limit = ["-t", "45"];
    execFileSync("ffmpeg", ["-y", ...limit, "-i", webm, "-vf", `${filters},palettegen=stats_mode=diff:max_colors=96`, palette], { stdio: "ignore" });
    execFileSync("ffmpeg", ["-y", ...limit, "-i", webm, "-i", palette, "-lavfi", `${filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4`, join(OUT, "demo.gif")], { stdio: "ignore" });
    rmSync(palette, { force: true });
    process.stdout.write("  demo.gif\n");
  } catch {
    process.stdout.write("  ffmpeg not found, keeping demo.webm only\n");
  }
}

const paperId = process.env.CAPTURE_PAPER || "1";
const browser = await chromium.launch();

await shot(browser, "library", "/");
await shot(browser, "overview", `/paper/${paperId}`);
await shot(browser, "all-views", `/paper/${paperId}/elements`, async (page) => {
  await click(page, /^Tables/);
  await page.waitForTimeout(1200);
});
await shot(browser, "figures", `/paper/2/elements`, async (page) => {
  await click(page, /^Figures/);
  await page.waitForTimeout(1600);
});
await shot(browser, "find", `/paper/${paperId}/elements`, async (page) => {
  await page.getByPlaceholder("BLEU, 41.0").fill("41.0");
  await click(page, "Find every occurrence");
  await page.waitForTimeout(2200);
});
await shot(browser, "reader", `/paper/${paperId}/reader?page=4`, async (page) => {
  await page.waitForTimeout(2600);
});
await shot(browser, "references", `/paper/${paperId}/references`);
await shot(browser, "ask", `/paper/${paperId}/ask`, async (page) => {
  await page.getByPlaceholder("What was the learning rate").fill("What optimizer was used and how was the learning rate scheduled?");
  await click(page, /Answer with citations/);
  await waitForAnswer(page);
});
await shot(browser, "analyse", `/paper/${paperId}/analyse`, async (page) => {
  await click(page, /Limitations/);
  await waitForProse(page);
});
await shot(browser, "compare", "/ask");
await shot(browser, "settings", "/settings");

if (!process.env.CAPTURE_NO_VIDEO) await record(browser, paperId);

await browser.close();

if (problems.length) {
  process.stdout.write(`\nProblems:\n${problems.join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write("\nNo console errors, no failed requests.\n");
}
