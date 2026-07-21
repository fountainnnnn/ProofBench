import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";

const axe = readFileSync("node_modules/axe-core/axe.min.js", "utf8");
const routes = [
  ["/", "landing"],
  ["/app/benchmark", "benchmark"],
  ["/app/benchmark?session=a5a1aec63424&dataset=cb82723b4af0", "benchmark-completed"],
  ["/app/runs", "runs"],
  ["/app/datasets", "datasets"],
  ["/app/settings", "settings"],
];

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const [w, h, label] of [[390, 844, "mobile"], [1440, 900, "desktop"]]) {
    const page = await browser.newPage({
      viewport: { width: w, height: h },
      colorScheme: scheme,
      bypassCSP: true,
    });
    for (const [route, name] of routes) {
      await page.goto(`http://127.0.0.1:5174${route}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(800);
      await page.addScriptTag({ content: axe });
      const res = await page.evaluate(async () => {
        const r = await window.axe.run(document, { resultTypes: ["violations"] });
        return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, ex: v.nodes[0]?.html?.slice(0, 120) }));
      });
      console.log(`${scheme}/${label}/${name}: ${res.length ? JSON.stringify(res, null, 1) : "clean"}`);
      await page.screenshot({ path: `pb-shot-${scheme}-${label}-${name}.png`, fullPage: label === "desktop" });
    }
    await page.close();
  }
}
await browser.close();
