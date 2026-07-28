#!/usr/bin/env node
/* Refreshes the product screenshots the landing page ships.
 *
 * The hero's credibility rests on the shot being the CURRENT console — a stale
 * screenshot is the fastest way to make a real product look like a mockup — so
 * this is a script rather than a one-off, to be re-run whenever the console's
 * look changes.
 *
 *   npm run brand:shots        # needs the dev server + API running
 *
 * Captured at 2x so the crop stays sharp inside a card, in light theme because
 * that is what the landing page is built around.
 */
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.PROOFBENCH_WEB || "http://localhost:5199";
const OUT = path.join(WEB, "src", "assets", "product");

const SHOTS = [
  { route: "/app/overview", file: "shot-overview.png" },
  { route: "/app/benchmark", file: "shot-benchmark.png" },
  { route: "/app/runs", file: "shot-runs.png" },
];

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "light",
});
const page = await ctx.newPage();

for (const shot of SHOTS) {
  await page.goto(BASE + shot.route, { waitUntil: "networkidle" });
  // Let the fit-to-height passes and the brand icons settle before capturing.
  await page.waitForTimeout(1400);
  await page.screenshot({ path: path.join(OUT, shot.file) });
  console.log(`captured ${shot.file}  <- ${shot.route}`);
}

await ctx.close();
await browser.close();
