#!/usr/bin/env node
/* Brand-logo pipeline.
 *
 * Resolves every tool this deployment has benchmarked to its real logo and
 * bundles it locally, so the console can show actual brand marks without the
 * browser ever calling out to an icon service at runtime (the app's CSP is
 * `img-src 'self'`, and a verification product should not leak the list of
 * tools you are evaluating to a third party on every page view).
 *
 * Usage:
 *   node scripts/fetch-brand-logos.mjs                 # names from the API
 *   node scripts/fetch-brand-logos.mjs tesseract mindee  # explicit names
 *   node scripts/fetch-brand-logos.mjs customgpt=https://customgpt.ai/  # name=docs URL
 *
 * Writes:
 *   public/brand/<slug>.<ext>   the downloaded mark
 *   src/brandManifest.js        generated slug -> asset map
 *
 * Re-run it whenever new tools appear; it is idempotent and skips what it
 * already has.
 */
import { writeFile, mkdir, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  candidateDomains, declaredIconHrefs, hostOf, sameSite, site, slugOf,
} from "./brandResolve.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..");
const BRAND_DIR = path.join(WEB, "public", "brand");
const MANIFEST = path.join(WEB, "src", "brandManifest.js");
const API = process.env.PROOFBENCH_API || "http://127.0.0.1:8000";
/* Short, because most misses are a DNS failure on a guessed domain and the
   whole point is that this finishes in seconds, not minutes. */
const TIMEOUT_MS = 3500;

/* Where a tool's mark actually lives. Only needed where the name does not map
 * to <name>.com — the guesses below handle the common case. Open-source tools
 * point at their project home rather than a generic code-host icon. */
const DOMAIN_HINTS = {
  aws: "aws.amazon.com",
  amazonses: "aws.amazon.com",
  gcp: "cloud.google.com",
  googlecloud: "cloud.google.com",
  azure: "azure.microsoft.com",
  microsoftazure: "azure.microsoft.com",
  microsoftdocumentintelligence: "azure.microsoft.com",
  azureaidocumentintelligence: "azure.microsoft.com",
  digitalocean: "digitalocean.com",
  openaivision: "openai.com",
  openai: "openai.com",
  tesseract: "tesseract-ocr.github.io",
  paddleocr: "paddlepaddle.org.cn",
  easyocr: "jaided.ai",
  mindeeinvoiceocr: "mindee.com",
  mindee: "mindee.com",
  /* Deliberately absent: generic adapter names like `invoice_data_extraction_api`
     or `arya_invoice_extraction`. Guessing a vendor for them would stamp a real
     company's mark on something that may not be theirs — a misattribution, not
     an identification. Unknown names keep the neutral monogram. */
  sendgrid: "sendgrid.com",
  mailgun: "mailgun.com",
  postmark: "postmarkapp.com",
  resend: "resend.com",
  heroku: "heroku.com",
  vercel: "vercel.com",
  netlify: "netlify.com",
  stripe: "stripe.com",
  cloudflare: "cloudflare.com",
  supabase: "supabase.com",
  anthropic: "anthropic.com",
  daytona: "daytona.io",
  deepseek: "deepseek.com",
  openrouter: "openrouter.ai",
  nosana: "nosana.io",
  oxylabs: "oxylabs.io",
  doubleword: "doubleword.ai",
  kimi: "moonshot.cn",
  moonshot: "moonshot.cn",
};

const candidates = (name, docsUrl) => candidateDomains(name, docsUrl, DOMAIN_HINTS);

async function get(url, { sameSiteAs = null } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { redirect: "follow", signal: controller.signal });
    if (!res.ok) return null;
    /* A request that ends up on a different registrable domain is answering for
       somebody else. An unregistered vendor domain parks on a marketplace, and
       serving that marketplace's icon under the vendor's name is a
       misattribution, not an identification — so it is refused outright. */
    if (sameSiteAs && !sameSite(hostOf(res.url), sameSiteAs)) return null;
    const type = res.headers.get("content-type") || "";
    if (!/image|icon/i.test(type)) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    // A 1x1 tracking pixel or an empty body is not a logo.
    return buf.length > 200 ? { buf, type } : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const extFor = (type) =>
  /svg/i.test(type) ? "svg" : /png/i.test(type) ? "png" : /x-icon|vnd\.microsoft|ico/i.test(type) ? "ico" : "png";

/* Try the site's own favicon first — it is the canonical mark and comes from
 * the vendor. Fall back to DuckDuckGo's icon service, which resolves sites that
 * do not serve /favicon.ico at the root. */
/* The icon the page declares for itself, which is what a browser would show.
   Plenty of vendors serve nothing at /favicon.ico — customgpt.ai 404s there and
   points at a WordPress upload path instead — so without this they resolve to
   nothing and fall back to a monogram despite having a perfectly good mark. */
async function declaredIcon(domain) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`https://${domain}/`, { redirect: "follow", signal: controller.signal });
    if (!res.ok || !sameSite(hostOf(res.url), domain)) return null;
    if (!/text\/html/i.test(res.headers.get("content-type") || "")) return null;
    for (const href of declaredIconHrefs(await res.text(), `https://${domain}/`)) {
      /* Fetched wherever it points, deliberately. The same-site rule guards
         against a domain we GUESSED handing us someone else's mark; this URL
         came out of the vendor's own <head>, on a page already checked as
         same-site. Requiring same-site here rejects every CDN-hosted favicon —
         Webflow, Shopify and Cloudflare all serve them off their own domains. */
      const hit = await get(href);
      if (hit) return { ...hit, source: href };
    }
    return null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function fetchLogo(domain) {
  for (const url of [`https://${domain}/favicon.svg`, `https://${domain}/favicon.ico`]) {
    const hit = await get(url, { sameSiteAs: domain });
    if (hit) return { ...hit, source: url };
  }
  const declared = await declaredIcon(domain);
  if (declared) return declared;
  /* The icon service is exempt from the same-site check by definition — it is
     always a third-party host answering for the domain we asked about. */
  const proxied = await get(`https://icons.duckduckgo.com/ip3/${domain}.ico`);
  return proxied ? { ...proxied, source: `duckduckgo:${domain}` } : null;
}

const json = async (url) => {
  const res = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) }).catch(() => null);
  return res && res.ok ? res.json().catch(() => null) : null;
};

/* Every benchmarked tool, paired with the docs URL its run was assessed from.
   The session spec is read rather than only the metrics keys, because the spec
   is where docs_url lives — and that URL is the difference between identifying
   a vendor and guessing at one. */
async function toolsFromApi() {
  const sessions = await json(`${API}/api/sessions`);
  const list = Array.isArray(sessions) ? sessions : sessions?.sessions || [];
  const found = new Map();
  for (const session of list) {
    const detail = await json(`${API}/api/sessions/${session.id}`);
    for (const candidate of detail?.spec?.candidates || []) {
      if (candidate?.name && !found.has(candidate.name)) {
        found.set(candidate.name, candidate.docs_url || null);
      }
    }
    if (!session.latest_run_id) continue;
    const results = await json(`${API}/api/runs/${session.latest_run_id}/results`);
    // Metrics keys are the backstop: a legacy run may have no spec on file, and
    // a name with no docs URL still deserves its curated hint or a monogram.
    for (const key of Object.keys(results?.metrics || {})) {
      if (!found.has(key)) found.set(key, null);
    }
  }
  return [...found].map(([name, docsUrl]) => ({ name, docsUrl }));
}

const argv = process.argv.slice(2);
const tools = argv.length
  ? argv.map((arg) => {
      const at = arg.indexOf("=");
      return at === -1
        ? { name: arg, docsUrl: null }
        : { name: arg.slice(0, at), docsUrl: arg.slice(at + 1) };
    })
  : await toolsFromApi();
if (!tools.length) {
  console.error("No tool names. Pass them as arguments, or start the API so they can be read from it.");
  process.exit(1);
}

await mkdir(BRAND_DIR, { recursive: true });
const existing = new Set((await readdir(BRAND_DIR).catch(() => [])).map((f) => f.replace(/\.[^.]+$/, "")));

/* Every tool is resolved concurrently — these are independent network lookups
   and running them in series made a dozen names take minutes. */
const results = await Promise.all(
  tools.map(async ({ name, docsUrl }) => {
    const slug = slugOf(name);
    if (!slug) return null;
    if (existing.has(slug)) return { name, slug, kept: true };
    for (const domain of candidates(name, docsUrl)) {
      const logo = await fetchLogo(domain);
      if (!logo) continue;
      const ext = extFor(logo.type);
      await writeFile(path.join(BRAND_DIR, `${slug}.${ext}`), logo.buf);
      return { name, slug, ext, domain, source: logo.source };
    }
    return { name, missed: true };
  }),
);
const found = results.filter((r) => r && !r.missed);
const missed = results.filter((r) => r && r.missed).map((r) => r.name);

/* The manifest is generated, never hand-edited: it simply lists what is on disk
 * so a rerun of this script is the only thing that changes it. */
const files = (await readdir(BRAND_DIR)).filter((f) => /\.(svg|png|ico)$/i.test(f));
const entries = files
  .map((f) => [f.replace(/\.[^.]+$/, ""), `/brand/${f}`])
  .sort(([a], [b]) => a.localeCompare(b));

await writeFile(
  MANIFEST,
  `/* GENERATED by scripts/fetch-brand-logos.mjs — do not edit by hand.
   Every logo bundled under public/brand, keyed by normalised tool name. Re-run
   the script to add tools; it downloads each vendor's own mark so the console
   never calls an icon service at runtime. */
export const BRAND_MANIFEST = new Map([
${entries.map(([slug, url]) => `  [${JSON.stringify(slug.replace(/-/g, ""))}, ${JSON.stringify(url)}],`).join("\n")}
]);
`,
  "utf8",
);

console.log(`resolved ${found.length}/${tools.length}`);
for (const f of found) console.log(`  ${f.kept ? "kept " : "saved"} ${f.slug}${f.domain ? `  <- ${f.domain}` : ""}`);
if (missed.length) console.log(`no logo found (monogram stays): ${missed.join(", ")}`);
console.log(`manifest: ${path.relative(WEB, MANIFEST)} (${entries.length} marks)`);
