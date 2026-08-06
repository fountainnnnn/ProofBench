import { chromium } from "@playwright/test";
const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
  const page = await ctx.newPage();
  await page.goto("http://localhost:5199/app/settings", { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.screenshot({ path: `hdr-${scheme}.png`, clip: { x: 995, y: 130, width: 430, height: 140 } });
  await ctx.close();
}
await browser.close();
console.log("done");
