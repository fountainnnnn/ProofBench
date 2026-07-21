import { chromium } from "@playwright/test";

const routes = [
  ["/", "landing"],
  ["/app/benchmark", "benchmark"],
  ["/app/benchmark?session=a5a1aec63424&dataset=cb82723b4af0", "benchmark-completed"],
  ["/app/runs", "runs"],
  ["/app/datasets", "datasets"],
  ["/app/settings", "settings"],
];

const browser = await chromium.launch();
for (const [w, h, label] of [[390, 844, "mobile"], [1920, 1080, "desktop"]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  for (const [route, name] of routes) {
    await page.goto(`http://127.0.0.1:5174${route}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    const info = await page.evaluate(() => {
      const de = document.documentElement;
      const h1s = [...document.querySelectorAll("h1")].map((n) => n.textContent.trim());
      let widest = null;
      if (de.scrollWidth > de.clientWidth) {
        for (const el of document.querySelectorAll("*")) {
          const r = el.getBoundingClientRect();
          if (r.right > de.clientWidth + 1) {
            widest = `${el.tagName}.${String(el.className).slice(0, 80)} right=${Math.round(r.right)}`;
            break;
          }
        }
      }
      return { scrollW: de.scrollWidth, clientW: de.clientWidth, h1s, widest };
    });
    const overflow = info.scrollW > info.clientW ? `OVERFLOW ${info.scrollW}>${info.clientW} :: ${info.widest}` : "ok";
    console.log(`${label.padEnd(8)} ${name.padEnd(20)} ${overflow.padEnd(30)} h1=${JSON.stringify(info.h1s)}`);
  }
  await page.close();
}
await browser.close();
