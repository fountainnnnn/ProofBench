import { chromium } from "@playwright/test";

const base = "http://localhost:5199";
const shots = [
  { path: "/", name: "landing" },
  { path: "/app/overview", name: "overview" },
  { path: "/app/benchmark", name: "benchmark" },
  { path: "/app/settings", name: "settings" },
  { path: "/app/runs", name: "runs" },
  { path: "/app/datasets", name: "datasets" },
];

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: scheme,
  });
  const page = await ctx.newPage();
  for (const s of shots) {
    await page.goto(base + s.path, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: `pb-theme-${s.name}-${scheme}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log("done");
