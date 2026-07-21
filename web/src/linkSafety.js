export function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || "").trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    for (const key of url.searchParams.keys()) {
      const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
      if (/(?:authorization|apikey|accesstoken|refreshtoken|token|secret|password|credential|signature|auth)/.test(normalized)) return null;
    }
    return url.href;
  } catch {
    return null;
  }
}
