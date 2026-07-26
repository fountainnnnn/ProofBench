import { chromium } from "@playwright/test";
const b = await chromium.launch();
const pg = await b.newPage();
// fetch exactly what the browser would, with no cache
const res = await pg.goto("http://localhost:5199/logo.svg", { waitUntil: "load" });
const body = await res.text();
const fills = body.match(/(fill|stroke)="[^"]*"/g) || [];
console.log("served fills:", fills.slice(0, 2).join(" "));
console.log("contains blue hue 268:", /268/.test(body));
await b.close();
