import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1600, height: 1000 } });
await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1600);
const r = await pg.evaluate(() => {
  const out = {};
  document.querySelectorAll("section[aria-label]").forEach((el) => {
    const b = el.getBoundingClientRect();
    out[el.getAttribute("aria-label")] = { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height) };
  });
  return out;
});
console.log(JSON.stringify(r, null, 2));
await b.close();
