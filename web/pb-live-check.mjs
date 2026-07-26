import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
await pg.goto("http://localhost:5199/app/benchmark", { waitUntil: "networkidle" });
await pg.waitForTimeout(1400);
const r = await pg.evaluate(() => {
  const cs = getComputedStyle(document.documentElement);
  const composer = document.querySelector("#benchmark-composer")?.closest(".pb-glass");
  const header = document.querySelector(".pb-page-header");
  return {
    accent: cs.getPropertyValue("--accent").trim(),
    paper: cs.getPropertyValue("--paper").trim(),
    glassEdge: cs.getPropertyValue("--glass-edge").trim(),
    composerGlass: composer ? getComputedStyle(composer).backdropFilter : "NOT FOUND",
    headerGlass: header ? getComputedStyle(header).backdropFilter : "NOT FOUND",
  };
});
console.log(JSON.stringify(r, null, 2));
await b.close();
