import { chromium } from "@playwright/test";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const pg = await ctx.newPage();
const w = () => pg.evaluate(() => Math.round(document.querySelector('aside[aria-label="Primary navigation"]').getBoundingClientRect().width));

await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1200);
const initial = await w();

// 1. drag the separator right by 90px
const sep = pg.getByRole("separator", { name: "Resize sidebar" });
const box = await sep.boundingBox();
await pg.mouse.move(box.x + box.width / 2, box.y + 300);
await pg.mouse.down();
await pg.mouse.move(box.x + box.width / 2 + 90, box.y + 300, { steps: 12 });
await pg.mouse.up();
await pg.waitForTimeout(300);
const afterDrag = await w();
await pg.screenshot({ path: "qa-sidebar-wide.png" });

// 2. keyboard resize
await sep.focus();
await pg.keyboard.press("ArrowLeft");
await pg.keyboard.press("ArrowLeft");
await pg.waitForTimeout(200);
const afterKeys = await w();

// 3. persistence across reload
await pg.reload({ waitUntil: "networkidle" });
await pg.waitForTimeout(1000);
const afterReload = await w();

// 4. collapse
await pg.getByRole("button", { name: "Collapse sidebar" }).click();
await pg.waitForTimeout(400);
const collapsed = await w();
await pg.screenshot({ path: "qa-sidebar-collapsed.png" });

// 5. collapse persists + expand restores
await pg.reload({ waitUntil: "networkidle" });
await pg.waitForTimeout(1000);
const collapsedAfterReload = await w();
await pg.getByRole("button", { name: "Expand sidebar" }).click();
await pg.waitForTimeout(400);
const expanded = await w();

console.log(JSON.stringify({ initial, afterDrag, afterKeys, afterReload, collapsed, collapsedAfterReload, expanded }, null, 2));
await b.close();
