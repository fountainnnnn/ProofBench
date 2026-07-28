/* Pure resolution rules for the brand-logo pipeline.
 *
 * Split out of fetch-brand-logos.mjs so the parts that decide WHICH domain
 * answers for a tool, and which icon a page declares, can be tested without a
 * network. Both rules exist because of real misattributions — see the comments
 * on each.
 */

export const keyOf = (name) => String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
export const slugOf = (name) =>
  String(name || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export const hostOf = (url) => {
  try {
    return new URL(String(url)).hostname;
  } catch {
    return null;
  }
};

/* Registrable domain, approximated by the last two labels. Good enough for the
   only question asked of it: did this request land somewhere else entirely? */
export const site = (host) => String(host || "").toLowerCase().split(".").slice(-2).join(".");

export const sameSite = (a, b) => Boolean(a) && Boolean(b) && site(a) === site(b);

/* Hosts whose favicon is the platform's, never the tool's. An open-source
   project documented at github.com/opf/openproject would otherwise be published
   wearing GitHub's octocat — the same misattribution as a parked domain, just
   with a more familiar logo. Project sites on these platforms (a *.github.io
   page is the project's own) are deliberately not listed. */
export const GENERIC_HOSTS = new Set([
  "github.com", "gitlab.com", "bitbucket.org", "sourceforge.net", "codeberg.org",
  "pypi.org", "npmjs.com", "rubygems.org", "crates.io", "packagist.org",
  "readthedocs.io", "readthedocs.org", "gitbook.io", "notion.site",
  "medium.com", "substack.com", "wordpress.com", "blogspot.com",
  "docs.google.com", "google.com", "youtube.com", "reddit.com",
]);

/* Candidate domains, most authoritative first.
 *
 * The docs URL is the run's own evidence for what a candidate IS — the page the
 * agent actually read to assess it — so when there is one it settles the
 * question and no guess is made. Guessing cost us a real misattribution:
 * "customgpt" was tried as customgpt.com, then customgpt.io, which redirects to
 * a domain marketplace, and the console shipped Unstoppable Domains' logo
 * labelled CustomGPT. TLD roulette only runs for a bare name with no docs URL
 * and no curated hint. */
export function candidateDomains(name, docsUrl, hints = {}) {
  const key = keyOf(name);
  if (hints[key]) return [hints[key]];
  const head = slugOf(name).split("-")[0];
  if (hints[head]) return [hints[head]];
  const documented = hostOf(docsUrl);
  if (documented && GENERIC_HOSTS.has(site(documented))) return [];
  if (documented) {
    // The docs host, then its bare domain: vendors often serve docs from
    // docs./learn./reference. subdomains that carry no favicon of their own.
    const bare = site(documented);
    return documented === bare ? [documented] : [documented, bare];
  }
  return [`${key}.com`, `${key}.io`, `${key}.ai`, `${key}.dev`, `${key}.org`];
}

/* Icon hrefs a page declares for itself, largest first — which is what a
   browser would show. Scanning stops at </head> rather than at a character
   count: customgpt.ai inlines ~400 KB of CSS before its icon link, and a fixed
   200 KB window cut the declaration off, so a vendor with a perfectly good mark
   read as having none. */
export function declaredIconHrefs(pageHtml, base) {
  const page = String(pageHtml || "");
  const headEnd = page.search(/<\/head\s*>/i);
  const head = page.slice(0, headEnd === -1 ? 600_000 : headEnd);
  return [...head.matchAll(/<link\b[^>]*>/gi)]
    .map((match) => match[0])
    .filter((tag) => /rel\s*=\s*["'][^"']*\bicon\b/i.test(tag))
    .map((tag) => ({
      href: (tag.match(/href\s*=\s*["']([^"']+)["']/i) || [])[1],
      // Largest declared size wins: a 16x16 .ico renders as mush at avatar scale.
      size: parseInt((tag.match(/sizes\s*=\s*["'](\d+)/i) || [])[1] || "0", 10),
    }))
    .filter((item) => item.href)
    .sort((a, b) => b.size - a.size)
    .map((item) => {
      try {
        return new URL(item.href, base).toString();
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}
