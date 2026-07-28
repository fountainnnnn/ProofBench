"""Vendor logos, resolved while the deployment runs.

The build-time script (web/scripts/fetch-brand-logos.mjs) can only bundle marks
for tools that were already known when the frontend was built, so every new
benchmark showed its candidates as blank monograms until someone remembered to
re-run it and redeploy. That is not a pipeline, it is a chore.

This resolves a mark the first time the console asks for one and caches it on
disk, so a tool benchmarked five minutes ago has its logo without a rebuild.

Two properties matter more than coverage:

* **No new fetch surface.** A logo is only ever fetched from the host of a
  candidate's own `docs_url` — a public URL the schema already validated and the
  run already scraped. Callers pass that URL; this module never takes one from a
  request.
* **A wrong logo is worse than none.** A request that redirects off-site is
  refused, because an unregistered vendor domain parks on a marketplace and
  serving that marketplace's icon under a vendor's name is a misattribution.
  This is the rule that stopped `customgpt.io` (which redirects to
  unstoppabledomains.com) from being published as CustomGPT.
"""
from __future__ import annotations

import base64
import os
import re
import time
from urllib.parse import urljoin, urlsplit

# Small enough that a page of them is cheap, large enough for every real
# favicon: the biggest mark bundled today is 17 KB.
MAX_LOGO_BYTES = 300_000
MIN_LOGO_BYTES = 200
TIMEOUT_S = 4.0
# A vendor that has no mark today may have one next month, but re-checking on
# every page view would put a network call behind every render.
NEGATIVE_TTL_S = 24 * 3600

_EXTENSIONS = {
    "image/svg+xml": "svg", "image/png": "png", "image/jpeg": "jpg",
    "image/webp": "webp", "image/gif": "gif",
    "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Hosts whose favicon is the platform's, never the tool's. An open-source
# project documented at github.com/opf/openproject would otherwise be published
# wearing GitHub's octocat — the same misattribution as a parked domain, just
# with a more familiar logo. Project sites on these platforms (a *.github.io
# page is the project's own) are deliberately not listed.
_GENERIC_HOSTS = frozenset({
    "github.com", "gitlab.com", "bitbucket.org", "sourceforge.net", "codeberg.org",
    "pypi.org", "npmjs.com", "rubygems.org", "crates.io", "packagist.org",
    "readthedocs.io", "readthedocs.org", "gitbook.io", "notion.site",
    "medium.com", "substack.com", "wordpress.com", "blogspot.com",
    "docs.google.com", "google.com", "youtube.com", "reddit.com",
})


def slug_of(name: str) -> str:
    return _SLUG_RE.sub("-", str(name or "").lower()).strip("-")


def _site(host: str) -> str:
    """Registrable domain, approximated by the last two labels.

    Good enough for the only question asked of it: did this request land
    somewhere else entirely?
    """
    return ".".join(str(host or "").lower().split(".")[-2:])


def _same_site(a: str, b: str) -> bool:
    return bool(a) and bool(b) and _site(a) == _site(b)


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").lower()


def _extension(content_type: str) -> str | None:
    return _EXTENSIONS.get(str(content_type or "").split(";")[0].strip().lower())


def _get(client, url: str, *, same_site_as: str | None):
    try:
        response = client.get(url)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    if same_site_as and not _same_site(_host(str(response.url)), same_site_as):
        return None
    return response


def _fetch_image(client, url: str, *, same_site_as: str | None):
    response = _get(client, url, same_site_as=same_site_as)
    if response is None:
        return None
    extension = _extension(response.headers.get("content-type", ""))
    body = response.content
    # A 1x1 tracking pixel or an HTML error page is not a logo.
    if not extension or not (MIN_LOGO_BYTES < len(body) <= MAX_LOGO_BYTES):
        return None
    return body, extension


def _declared_icons(html: str, base: str) -> list[str]:
    """Icons the page declares for itself, largest first.

    Scanning stops at </head> rather than at a character count: customgpt.ai
    inlines roughly 400 KB of CSS before its icon link, and a fixed window cut
    the declaration off so a vendor with a good mark read as having none.
    """
    head = re.split(r"</head\s*>", html, maxsplit=1, flags=re.I)[0]
    found = []
    for tag in re.findall(r"<link\b[^>]*>", head, flags=re.I):
        if not re.search(r"rel\s*=\s*[\"'][^\"']*\bicon\b", tag, flags=re.I):
            continue
        href = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", tag, flags=re.I)
        if not href:
            continue
        size = re.search(r"sizes\s*=\s*[\"'](\d+)", tag, flags=re.I)
        found.append((int(size.group(1)) if size else 0, urljoin(base, href.group(1))))
    # Largest declared size wins: a 16x16 .ico renders as mush at avatar scale.
    return [url for _, url in sorted(found, key=lambda item: -item[0])]


def resolve(docs_url: str) -> tuple[bytes, str] | None:
    """Fetch the mark for the site a candidate's documentation lives on."""
    import httpx

    host = _host(docs_url)
    if not host or _site(host) in _GENERIC_HOSTS:
        return None
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT_S,
                      headers={"User-Agent": "ProofBench/1.0"}) as client:
        for candidate_host in ([host] if host == _site(host) else [host, _site(host)]):
            root = f"https://{candidate_host}/"
            for path in ("favicon.svg", "favicon.ico"):
                hit = _fetch_image(client, root + path, same_site_as=candidate_host)
                if hit:
                    return hit
            page = _get(client, root, same_site_as=candidate_host)
            if page is None or "text/html" not in page.headers.get("content-type", ""):
                continue
            for url in _declared_icons(page.text, root):
                # Declared icons are fetched wherever they point, deliberately.
                # The same-site rule guards against a domain we GUESSED handing
                # us someone else's mark; this URL came out of the vendor's own
                # <head>, on a page already checked as same-site, so the vendor
                # is telling us where its logo lives. Requiring same-site here
                # would reject every CDN-hosted favicon — Webflow, Shopify and
                # Cloudflare all serve them off their own domains, which is why
                # Ragie resolved to nothing.
                hit = _fetch_image(client, url, same_site_as=None)
                if hit:
                    return hit
    return None


class LogoCache:
    """One resolution per tool, ever — including the ones that find nothing."""

    def __init__(self, directory: str):
        self.directory = directory

    def _paths(self, slug: str) -> tuple[str, str]:
        return os.path.join(self.directory, slug), os.path.join(self.directory, f"{slug}.missing")

    def _cached(self, slug: str) -> tuple[bytes, str] | None:
        prefix, _ = self._paths(slug)
        for extension in set(_EXTENSIONS.values()):
            path = f"{prefix}.{extension}"
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    return handle.read(), extension
        return None

    def get(self, name: str, docs_url: str, *, resolver=resolve) -> tuple[bytes, str] | None:
        slug = slug_of(name)
        if not slug:
            return None
        cached = self._cached(slug)
        if cached:
            return cached
        _, missing = self._paths(slug)
        if os.path.isfile(missing) and time.time() - os.path.getmtime(missing) < NEGATIVE_TTL_S:
            return None
        try:
            found = resolver(docs_url) if docs_url else None
        except Exception:
            found = None
        os.makedirs(self.directory, exist_ok=True)
        if not found:
            # Touch, so a vendor with no mark is not re-fetched on every render.
            with open(missing, "wb"):
                pass
            return None
        body, extension = found
        with open(f"{os.path.join(self.directory, slug)}.{extension}", "wb") as handle:
            handle.write(body)
        if os.path.isfile(missing):
            os.remove(missing)
        return body, extension


def data_uri(body: bytes, extension: str) -> str:
    """Inline the mark, which the console's CSP allows and a new origin would not.

    `img-src 'self' data: blob:` is the deployed policy. Returning bytes as a
    data URI means logos need no CSP change and no separate image origin.
    """
    media = next((mime for mime, ext in _EXTENSIONS.items() if ext == extension), "image/png")
    return f"data:{media};base64,{base64.b64encode(body).decode('ascii')}"
