import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:5299/app/benchmark?session=8324d29b2471", { waitUntil: "networkidle" });
await p.waitForTimeout(2000);
console.log("panel open before:", await p.locator(".pb-sandbox-panel").count());
await p.getByRole("button", { name: "Run again" }).first().click();
let sawPanel = 0, at = null;
for (let i = 0; i < 40; i++) {
  await p.waitForTimeout(500);
  if (await p.locator(".pb-sandbox-panel").count()) { sawPanel = 1; at = (i + 1) * 0.5; break; }
}
console.log(`panel opened by itself: ${sawPanel ? "yes, after " + at + "s" : "NO"}`);
const m = await p.evaluate(() => {
  const r = (e) => e ? { l: Math.round(e.left), r: Math.round(e.right) } : null;
  return { thread: r(document.querySelector(".pb-benchmark-thread")?.getBoundingClientRect()),
           panel: r(document.querySelector(".pb-sandbox-panel")?.getBoundingClientRect()) };
});
console.log(JSON.stringify(m));
await p.screenshot({ path: "pb-autopanel.png" });
await b.close();
