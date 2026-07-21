import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const assets = join(process.cwd(), "dist", "assets");
const scripts = readdirSync(assets)
  .filter((name) => name.endsWith(".js"))
  .map((name) => ({ name, path: join(assets, name) }));

if (scripts.length < 3) {
  throw new Error(`Expected route code splitting, but found ${scripts.length} production script(s).`);
}

const maximumBytes = 500_000;
for (const script of scripts) {
  const bytes = statSync(script.path).size;
  if (bytes > maximumBytes) {
    throw new Error(`${script.name} is ${bytes} bytes, above the ${maximumBytes}-byte budget.`);
  }
  const source = readFileSync(script.path, "utf8");
  for (const forbidden of ["vitest", "testing-library", "playwright"]) {
    if (source.includes(forbidden)) {
      throw new Error(`${script.name} contains test-only dependency marker ${forbidden}.`);
    }
  }
}

console.log(`Bundle check passed for ${scripts.length} script(s).`);
