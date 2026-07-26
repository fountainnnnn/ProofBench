import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
await pg.goto("http://localhost:5199/app/overview", { waitUntil: "networkidle" });
await pg.waitForTimeout(1400);

const trigger = pg.getByRole("button", { name: /Local operator/ });
const out = {};
out.triggerFound = await trigger.count() > 0;

await trigger.click();
await pg.waitForTimeout(300);
out.menuOpen = await pg.getByRole("menu", { name: "Profile" }).isVisible();
out.focusOnFirstItem = await pg.evaluate(() => document.activeElement?.textContent?.trim());
await pg.screenshot({ path: "qa-profile-menu.png", clip: { x: 0, y: 560, width: 300, height: 340 } });

await pg.keyboard.press("ArrowDown");
out.afterArrowDown = await pg.evaluate(() => document.activeElement?.textContent?.trim());

await pg.keyboard.press("Escape");
await pg.waitForTimeout(250);
out.closedOnEscape = (await pg.getByRole("menu", { name: "Profile" }).count()) === 0;
out.focusReturned = await pg.evaluate(() => document.activeElement?.textContent?.includes("Local operator"));

await trigger.click();
await pg.waitForTimeout(250);
await pg.mouse.click(900, 400);
await pg.waitForTimeout(250);
out.closedOnOutsideClick = (await pg.getByRole("menu", { name: "Profile" }).count()) === 0;

await trigger.click();
await pg.waitForTimeout(250);
await pg.getByRole("menuitem", { name: "View profile" }).click();
await pg.waitForTimeout(900);
out.navigatedToProfile = pg.url().includes("/app/profile");
await pg.screenshot({ path: "qa-profile-page.png" });
console.log(JSON.stringify(out, null, 2));
await b.close();
