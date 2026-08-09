import { chromium } from "@playwright/test";

const id = process.argv[2];
const name = process.argv[3] || "session";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`http://localhost:5299/app/benchmark?session=${id}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: `pb-shot-${name}.png` });
await browser.close();
console.log(`pb-shot-${name}.png`);
