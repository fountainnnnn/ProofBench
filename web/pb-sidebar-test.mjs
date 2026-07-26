import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1500);

const rail = pg.locator('div[aria-label="Sessions"]');
const visibleOnOverview = await rail.isVisible();
const first = rail.locator("ul li button").first();
const label = (await first.textContent())?.trim().slice(0, 24);
await first.click();
await pg.waitForTimeout(1800);
const url = pg.url();

const highlighted = await pg.evaluate(() => {
  const items = document.querySelectorAll('div[aria-label="Sessions"] ul li');
  let n = 0;
  items.forEach((li) => { if (li.className.includes("surface-2")) n += 1; });
  return n;
});
await pg.screenshot({ path: "qa-sidebar-nav.png" });
console.log(JSON.stringify({
  visibleOnOverview,
  clicked: label,
  navigatedToBenchmarkSession: /\/app\/benchmark\?session=/.test(url),
  activeRowsHighlighted: highlighted,
}, null, 2));
await b.close();
