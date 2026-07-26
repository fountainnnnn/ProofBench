import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1500, height: 1150 } });
await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1600);
const clip = await pg.evaluate(() => {
  const out = {};
  document.querySelectorAll("section[aria-label] ul").forEach((ul) => {
    const label = ul.closest("section").getAttribute("aria-label");
    out[label] = { scrollH: ul.scrollHeight, clientH: ul.clientHeight, clipped: ul.scrollHeight > ul.clientHeight + 1 };
  });
  return out;
});
await pg.screenshot({ path: "qa-overview-tall.png" });
console.log(JSON.stringify(clip, null, 2));
await b.close();
