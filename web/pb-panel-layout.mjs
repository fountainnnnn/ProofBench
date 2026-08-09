import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:5299/app/benchmark?session=bd52d2ea9670", { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
const btn = p.getByRole("button", { name: /Execution|View execution/ });
if (await btn.count()) {
  await btn.first().click();
  await p.waitForTimeout(700);
}
const m = await p.evaluate(() => {
  const q = (s) => document.querySelector(s)?.getBoundingClientRect();
  const thread = q(".pb-benchmark-thread");
  const panel  = q(".pb-sandbox-panel");
  const comp   = document.querySelector("#benchmark-composer")?.closest(".pb-glass")?.getBoundingClientRect();
  const r = (x) => x ? { l: Math.round(x.left), r: Math.round(x.right), t: Math.round(x.top), b: Math.round(x.bottom) } : null;
  return { thread: r(thread), panel: r(panel), composer: r(comp) };
});
console.log(JSON.stringify(m, null, 1));
if (m.panel && m.composer) {
  console.log("composer ends before panel starts:", m.composer.r <= m.panel.l);
  console.log("panel extends below composer top:", m.panel.b >= m.composer.t);
}
await p.screenshot({ path: "pb-panel-layout.png" });
await b.close();
