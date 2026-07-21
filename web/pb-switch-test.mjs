import { chromium } from "@playwright/test";

const running = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`http://localhost:5199/app/benchmark?session=${running}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const header = page.locator("h1", { hasText: "Benchmark" });
const headerVisibleBefore = await header.isVisible();

// wait a few seconds of streaming; header and rail must remain on screen
await page.waitForTimeout(4000);
const headerVisibleAfterStream = await header.isVisible();
const railVisible = await page.locator('section[aria-label="Sessions"]').isVisible();

// click a different session row in the rail
const rows = page.locator('section[aria-label="Sessions"] ul li button').first();
const targetTitle = (await rows.textContent())?.trim().slice(0, 20);
await rows.click();
await page.waitForTimeout(1500);
const url = page.url();
await page.screenshot({ path: "pb-shot-after-switch.png" });

console.log(JSON.stringify({
  headerVisibleBefore,
  headerVisibleAfterStream,
  railVisible,
  clickedRow: targetTitle,
  urlAfterSwitch: url,
  switched: !url.includes(running),
}, null, 2));
await browser.close();
