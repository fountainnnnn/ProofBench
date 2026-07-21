import { chromium } from "@playwright/test";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:5199/app/benchmark", { waitUntil: "networkidle" });
await page.waitForTimeout(800);

const profile = (label) =>
  page.evaluate(
    (lbl) =>
      new Promise((resolve) => {
        const deltas = [];
        let last = performance.now();
        const start = last;
        let long = 0;
        const tick = (t) => {
          const d = t - last;
          deltas.push(d);
          if (d > 20) long += 1;
          last = t;
          if (t - start < 3000) requestAnimationFrame(tick);
          else {
            deltas.sort((a, b) => a - b);
            resolve({
              label: lbl,
              fps: Math.round(1000 / (deltas.reduce((s, x) => s + x, 0) / deltas.length)),
              p95_frame_ms: +deltas[Math.floor(deltas.length * 0.95)].toFixed(2),
              janky_frames_over_20ms: long,
            });
          }
        };
        requestAnimationFrame(tick);
      }),
    label,
  );

const on = await profile("atmosphere ON");
await page.evaluate(() => {
  document.querySelector(".pb-atmosphere").style.display = "none";
});
const off = await profile("atmosphere OFF");
console.log(JSON.stringify({ on, off }, null, 2));
await browser.close();
