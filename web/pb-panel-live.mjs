import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
// A session whose run is finished still renders the panel; open it and add a
// composer by asking a follow-up, which is the state the user screenshotted.
await p.goto("http://localhost:5299/app/benchmark?session=bd52d2ea9670", { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
const exec = p.getByRole("button", { name: /Execution|View execution/ });
if (await exec.count()) { await exec.first().click(); await p.waitForTimeout(500); }
const follow = p.getByRole("button", { name: "Ask a follow-up" });
if (await follow.count()) { await follow.first().click(); await p.waitForTimeout(900); }
const m = await p.evaluate(() => {
  const r = (e) => e ? { l: Math.round(e.left), r: Math.round(e.right), t: Math.round(e.top), b: Math.round(e.bottom) } : null;
  const ta = document.querySelector("#benchmark-composer");
  const panel = document.querySelector(".pb-sandbox-panel");
  return { composer: r(ta && ta.closest("div.pb-glass").getBoundingClientRect()),
           panel: r(panel && panel.getBoundingClientRect()) };
});
console.log(JSON.stringify(m));
if (m.composer && m.panel) {
  console.log("composer ends at/before panel start:", m.composer.r <= m.panel.l + 1);
  console.log("panel spans past composer top:", m.panel.b > m.composer.t);
}
await p.screenshot({ path: "pb-panel-live.png" });
await b.close();
