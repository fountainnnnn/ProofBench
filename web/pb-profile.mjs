import { chromium } from "@playwright/test";
const b = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const ctx = await b.newContext({ viewport: { width: 900, height: 900 }, colorScheme: scheme });
  const pg = await ctx.newPage();
  await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
  await pg.waitForTimeout(1200);
  await pg.screenshot({ path: `qa-profile-${scheme}.png`, clip: { x: 0, y: 800, width: 250, height: 100 } });
  await ctx.close();
}
await b.close();
console.log("cropped");
