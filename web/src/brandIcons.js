import { BRAND_MANIFEST } from "./brandManifest.js";

/* Hand-kept aliases: names that do not match their asset's own slug, mostly
   because one vendor mark serves several tool names. Everything else comes from
   BRAND_MANIFEST, which scripts/fetch-brand-logos.mjs generates from whatever
   is bundled in public/brand — so adding a tool is a script run, not an edit. */
const BRAND_ASSETS = new Map([
  ["aws", "/brand/aws.svg"],
  ["amazonaws", "/brand/aws.svg"],
  ["amazonwebservices", "/brand/aws.svg"],
  ["gcp", "/brand/google-cloud.svg"],
  ["googlecloud", "/brand/google-cloud.svg"],
  ["googlecloudplatform", "/brand/google-cloud.svg"],
  ["azure", "/brand/microsoft-azure.svg"],
  ["microsoftazure", "/brand/microsoft-azure.svg"],
  ["microsoftdocumentintelligence", "/brand/microsoft-azure.svg"],
  ["azureaidocumentintelligence", "/brand/microsoft-azure.svg"],
  ["digitalocean", "/brand/digital-ocean.svg"],
  ["elementor", "/brand/elementor.svg"],
]);

export function normalizeBrandName(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function brandAssetFor(name) {
  const key = normalizeBrandName(name);
  return BRAND_ASSETS.get(key) || BRAND_MANIFEST.get(key) || null;
}

/* Marks resolved by the backend for tools benchmarked since this frontend was
   built. The backend keeps the original bytes on disk; this small browser
   cache avoids downloading the same data URI again after every full reload.
   Only validated image data and normalized names are stored. */
const STORAGE_KEY = "proofbench.brandAssets.v1";
const STORAGE_VERSION = 1;
const MAX_STORED_ASSETS = 96;
const MAX_STORED_CHARS = 3_500_000;
const MAX_ASSET_CHARS = 420_000;
const NEGATIVE_TTL_MS = 24 * 60 * 60 * 1000;
const BRAND_KEY_RE = /^[a-z0-9]{1,160}$/;
const IMAGE_DATA_RE = /^data:image\/(?:svg\+xml|png|jpeg|webp|gif|x-icon|vnd\.microsoft\.icon);base64,[a-z0-9+/=\s]+$/i;
const runtimeAssets = new Map();
const runtimeCachedAt = new Map();
let runtimeHydrated = false;

function browserStorage() {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function validAsset(asset) {
  return typeof asset === "string"
    && asset.length <= MAX_ASSET_CHARS
    && IMAGE_DATA_RE.test(asset);
}

function hydrateRuntimeAssets() {
  if (runtimeHydrated) return;
  runtimeHydrated = true;
  const storage = browserStorage();
  if (!storage) return;

  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return;
    if (raw.length > MAX_STORED_CHARS) {
      storage.removeItem(STORAGE_KEY);
      return;
    }
    const parsed = JSON.parse(raw);
    if (parsed?.version !== STORAGE_VERSION || !Array.isArray(parsed?.items)) {
      storage.removeItem(STORAGE_KEY);
      return;
    }
    const now = Date.now();
    for (const [key, record] of parsed.items.slice(-MAX_STORED_ASSETS)) {
      const cachedAt = Number(record?.cachedAt);
      const asset = record?.asset;
      if (!BRAND_KEY_RE.test(key) || !Number.isFinite(cachedAt)) continue;
      if (asset === null) {
        if (now - cachedAt >= NEGATIVE_TTL_MS) continue;
      } else if (!validAsset(asset)) {
        continue;
      }
      runtimeAssets.set(key, asset);
      runtimeCachedAt.set(key, cachedAt);
    }
  } catch {
    try {
      storage.removeItem(STORAGE_KEY);
    } catch {
      // Storage can be disabled or full. The in-memory cache still works.
    }
  }
}

function persistRuntimeAssets() {
  const storage = browserStorage();
  if (!storage) return;

  let items = [...runtimeAssets.entries()]
    .slice(-MAX_STORED_ASSETS)
    .map(([key, asset]) => [key, {
      asset,
      cachedAt: runtimeCachedAt.get(key) || Date.now(),
    }]);
  try {
    let encoded = JSON.stringify({ version: STORAGE_VERSION, items });
    while (encoded.length > MAX_STORED_CHARS && items.length > 0) {
      items = items.slice(1);
      encoded = JSON.stringify({ version: STORAGE_VERSION, items });
    }
    storage.setItem(STORAGE_KEY, encoded);
  } catch {
    // Quota and privacy-mode failures are non-fatal. The backend disk cache
    // still prevents another request to the vendor's site.
  }
}

export function runtimeBrandAssetFor(name) {
  hydrateRuntimeAssets();
  return runtimeAssets.get(normalizeBrandName(name)) || null;
}

/** Resolve any names the bundle does not cover. Returns true if anything new
 *  arrived, so the caller knows whether a re-render is worth it. */
export async function ensureBrandAssets(names, fetchLogos) {
  hydrateRuntimeAssets();
  const missing = [...new Set(names || [])].filter(
    (name) => name && !brandAssetFor(name) && !runtimeAssets.has(normalizeBrandName(name)),
  );
  if (missing.length === 0) return false;
  let logos = {};
  try {
    logos = (await fetchLogos(missing)) || {};
  } catch {
    // A logo is decoration. Failing to fetch one must never disturb the page,
    // and the monogram it falls back to is a perfectly good answer.
    return false;
  }
  let added = false;
  const cachedAt = Date.now();
  for (const name of missing) {
    const key = normalizeBrandName(name);
    if (!BRAND_KEY_RE.test(key)) continue;
    const returned = logos[name];
    const asset = validAsset(returned) ? returned : null;
    runtimeAssets.delete(key);
    runtimeCachedAt.delete(key);
    runtimeAssets.set(key, asset);
    runtimeCachedAt.set(key, cachedAt);
    added = added || Boolean(asset);
  }
  persistRuntimeAssets();
  return added;
}

/** Test seam: clearing memory simulates a full browser reload. */
export function resetRuntimeBrandAssets({ clearStored = false } = {}) {
  runtimeAssets.clear();
  runtimeCachedAt.clear();
  runtimeHydrated = false;
  if (clearStored) {
    try {
      browserStorage()?.removeItem(STORAGE_KEY);
    } catch {
      // Test cleanup and privacy-mode storage failures are both harmless.
    }
  }
}
