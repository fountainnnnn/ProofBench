import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1200);

const sep = pg.getByRole("separator", { name: "Resize sidebar" });
const box = await sep.boundingBox();
const read = async () => pg.evaluate(() => {
  const el = document.querySelector('[role="separator"] span');
  const cs = getComputedStyle(el);
  return { w: cs.width, bg: cs.backgroundColor };
});
const rest = await read();
await sep.hover();
await pg.waitForTimeout(300);
const hover = await read();
await pg.screenshot({ path: "qa-sep-hover.png", clip: { x: 200, y: 60, width: 200, height: 320 } });

// keyboard focus state
await sep.focus();
await pg.waitForTimeout(300);
const focus = await read();
await pg.screenshot({ path: "qa-sep-focus.png", clip: { x: 200, y: 60, width: 200, height: 320 } });

console.log(JSON.stringify({ hitAreaWidth: Math.round(box.width), rest, hover, focus }, null, 2));
await b.close();
