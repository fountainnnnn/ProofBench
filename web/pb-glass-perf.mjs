import { chromium } from "@playwright/test";
const b = await chromium.launch();
async function fps(disableGlass) {
  const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await pg.goto("http://localhost:5199/app/benchmark", { waitUntil: "networkidle" });
  await pg.waitForTimeout(1200);
  if (disableGlass) {
    await pg.addStyleTag({ content: ".pb-glass,.pb-glass-float,.pb-page-header{backdrop-filter:none!important;-webkit-backdrop-filter:none!important;}" });
    await pg.waitForTimeout(400);
  }
  const r = await pg.evaluate(() => new Promise((res) => {
    const t = []; let last = performance.now(); let n = 0;
    function tick(now) { t.push(now - last); last = now; if (++n < 150) requestAnimationFrame(tick);
      else { const s = t.slice(10).sort((a, b) => a - b); res({ fps: Math.round(1000 / (s.reduce((a, c) => a + c, 0) / s.length)), p95: +s[Math.floor(s.length * 0.95)].toFixed(1), janky: s.filter((x) => x > 20).length }); } }
    requestAnimationFrame((now) => { last = now; requestAnimationFrame(tick); });
  }));
  await pg.close();
  return r;
}
console.log("glass ON  (atmosphere drifting behind):", JSON.stringify(await fps(false)));
console.log("glass OFF (control)                   :", JSON.stringify(await fps(true)));
await b.close();
