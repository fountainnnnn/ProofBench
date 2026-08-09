"""Which scraping provider answers first, and who covers when it does not.

Three providers reach the same public documentation, and measurement — not
marketing — put them in this order:

    provider    25 search results   docs page      cost
    scrape.do   2.9s (median)       fastest 4/5    $29/mo, 1 credit per page
    oxylabs     14.2s               fastest 1/5    $49/mo, JS render surcharge
    brightdata  49s                 wins on ragie  free at this volume

Extracted text was equivalent across all three on every documentation page
tested, so this order is about latency and cost, not quality. It is a default,
not a law: `PROOFBENCH_SCRAPER_ORDER` overrides it, which is how the Settings
page changes it per deployment without a redeploy.

Every provider stays in the chain even when it is not first. A search that
returns nothing ends an intake turn with no candidates, which is the worst
outcome the product has, so a second opinion is always worth one more call.
"""
from __future__ import annotations

# The three paid providers lead; the free self-hosted option trails, so a
# deployment with commercial keys is unaffected and one with none can still
# scrape. That option is a single provider by design — SearXNG finds pages and
# Crawl4AI reads them, and neither is a scraper alone — reachable only when
# PROOFBENCH_INSECURE_DEV=1 opens the local-service path (see engine.selfhosted).
DEFAULT_ORDER = ("scrapedo", "oxylabs", "brightdata", "selfhosted")
KNOWN = frozenset(DEFAULT_ORDER)

# Shown in Settings so the choice is a name a person recognises rather than a slug.
LABELS = {"scrapedo": "Scrape.do", "oxylabs": "Oxylabs", "brightdata": "Bright Data",
          "selfhosted": "SearXNG + Crawl4AI"}

# The self-hosted option costs nothing to run; the paid three bill per call.
FREE = frozenset({"selfhosted"})

# What each provider answers, and a one-line note for the Settings tooltip.
# `credentials` names the environment variables each provider authenticates
# with. The provider modules each read their own names, so this is the one place
# that states them together — anything describing a provider to a person (or to
# the integration agent) needs the real spelling, not a guess at it.
META = {
    "scrapedo": {"role": "search+read",
                 "credentials": ("SCRAPEDO_API_TOKEN",),
                 "hint": "Searches and fetches pages through Scrape.do. Paid, fastest measured."},
    "oxylabs": {"role": "search+read",
                "credentials": ("OXYLABS_USERNAME", "OXYLABS_PASSWORD"),
                "hint": "Searches and fetches pages through Oxylabs. Paid."},
    "brightdata": {"role": "search+read",
                   "credentials": ("BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE",
                                   "BRIGHTDATA_UNLOCKER_ZONE"),
                   "hint": "Searches and fetches pages through Bright Data. Paid."},
    "selfhosted": {"role": "search+read",
                   "credentials": (),
                   "hint": "Self-hosted and free: SearXNG finds candidate pages and Crawl4AI "
                           "reads them. Available whenever both services answer — the row "
                           "below reports which are up."},
}


def provider_meta(name: str, env: dict[str, str] | None = None) -> dict:
    """Label, role, free flag, and tooltip for one provider.

    A self-hosted provider also reports whether its services are actually up, so
    the UI can say "running" rather than leaving the operator to guess whether
    the thing it just called ready can answer.
    """
    info = META.get(name, {})
    meta = {
        "label": LABELS.get(name, name),
        "role": info.get("role", "search+read"),
        "free": name in FREE,
        "hint": info.get("hint", ""),
    }
    if name == "selfhosted":
        from engine import selfhosted

        meta["status"] = selfhosted.status(env)
    return meta

ORDER_ENV = "PROOFBENCH_SCRAPER_ORDER"


def parse_order(value: object) -> tuple[str, ...]:
    """Read a configured order, keeping only names this build knows.

    Unknown or duplicated names are dropped rather than rejected: a stored
    preference must never be able to stop a deployment from scraping at all.
    Providers the setting omits are appended, so disabling one in the UI demotes
    it to last rather than silently removing a fallback the operator still has
    credentials for.
    """
    if isinstance(value, str):
        names = [part.strip().lower() for part in value.replace(",", " ").split()]
    elif isinstance(value, (list, tuple)):
        names = [str(part).strip().lower() for part in value]
    else:
        names = []
    order = [name for index, name in enumerate(names)
             if name in KNOWN and name not in names[:index]]
    return tuple(order) + tuple(name for name in DEFAULT_ORDER if name not in order)


def order_from_env(env: dict[str, str] | None) -> tuple[str, ...]:
    return parse_order((env or {}).get(ORDER_ENV))


def _module(name: str):
    from engine import brightdata, scrapedo, selfhosted

    return {"scrapedo": scrapedo, "brightdata": brightdata,
            "selfhosted": selfhosted}.get(name)


def configured_providers(env: dict[str, str] | None, capability: str) -> list[str]:
    """The ordered providers that actually hold credentials for `capability`."""
    settings = dict(env or {})
    available = []
    for name in order_from_env(settings):
        if name == "oxylabs":
            if settings.get("OXYLABS_USERNAME") and settings.get("OXYLABS_PASSWORD"):
                available.append(name)
            continue
        module = _module(name)
        check = getattr(module, f"{capability}_configured", None) if module else None
        if check and check(settings):
            available.append(name)
    return available
