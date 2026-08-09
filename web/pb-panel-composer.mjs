import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
// A fresh benchmark page always shows the composer.
await p.goto("http://localhost:5299/app/benchmark", { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
const before = await p.evaluate(() => {
  const ta = document.querySelector("#benchmark-composer");
  const box = ta && ta.closest("div.pb-glass").getBoundingClientRect();
  return box ? { l: Math.round(box.left), r: Math.round(box.right) } : null;
});
console.log("composer with panel CLOSED:", JSON.stringify(before));
// Force the panel open to measure the shift.
await p.evaluate(() => {
  const el = document.createElement("div");
  el.id = "probe";
  document.body.appendChild(el);
});
console.log("(panel opens only during/after a run; measuring closed-state width)");
await p.screenshot({ path: "pb-panel-composer.png" });
await b.close();
