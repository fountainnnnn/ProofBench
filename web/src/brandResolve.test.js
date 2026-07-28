// Stamping the wrong company's logo on a tool is worse than showing no logo.
//
// Both rules here were written after a real failure. "customgpt" was resolved
// by guessing TLDs: customgpt.io redirects to a domain marketplace, and the
// console shipped Unstoppable Domains' mark labelled CustomGPT. Then the fix
// for that still missed customgpt.ai, because the page declares its icon after
// ~400 KB of inlined CSS and the scan stopped at 200 KB.
import { describe, expect, it } from "vitest";
import { candidateDomains, declaredIconHrefs, sameSite, site } from "../scripts/brandResolve.mjs";

const HINTS = { azure: "azure.microsoft.com", tesseract: "tesseract-ocr.github.io" };

describe("which domain answers for a tool", () => {
  it("uses the docs URL the run was actually assessed from", () => {
    expect(candidateDomains("customgpt", "https://customgpt.ai/integrations/sharepoint/", HINTS))
      .toEqual(["customgpt.ai"]);
  });

  it("never guesses a TLD once a docs URL is known", () => {
    const domains = candidateDomains("customgpt", "https://customgpt.ai/", HINTS);
    expect(domains).not.toContain("customgpt.io");
    expect(domains).not.toContain("customgpt.com");
  });

  it("falls back to the bare domain for docs served from a subdomain", () => {
    // reference.langchain.com carries no favicon of its own on some vendors.
    expect(candidateDomains("langchain_sharepoint", "https://reference.langchain.com/python/x", HINTS))
      .toEqual(["reference.langchain.com", "langchain.com"]);
  });

  it("prefers a curated hint over the docs host", () => {
    expect(candidateDomains("azure", "https://learn.microsoft.com/azure/search/", HINTS))
      .toEqual(["azure.microsoft.com"]);
  });

  it("matches a hint on the first segment of a compound name", () => {
    expect(candidateDomains("azure_ai_search_openai", null, HINTS)).toEqual(["azure.microsoft.com"]);
  });

  it("refuses a code host, whose favicon belongs to the platform", () => {
    // github.com/opf/openproject would otherwise wear GitHub's octocat.
    expect(candidateDomains("openproject", "https://github.com/opf/openproject", HINTS)).toEqual([]);
    expect(candidateDomains("some_lib", "https://pypi.org/project/some-lib/", HINTS)).toEqual([]);
  });

  it("still accepts a project site hosted on one of those platforms", () => {
    // A *.github.io page is the project's own, and serves its own mark.
    expect(candidateDomains("tesseract", "https://tesseract-ocr.github.io/docs", {}))
      .toEqual(["tesseract-ocr.github.io", "github.io"]);
  });

  it("only guesses when there is neither a hint nor a docs URL", () => {
    expect(candidateDomains("ragie", null, HINTS)).toEqual([
      "ragie.com", "ragie.io", "ragie.ai", "ragie.dev", "ragie.org",
    ]);
  });
});

describe("a request that lands somewhere else is refused", () => {
  it("rejects a parked domain redirecting to a marketplace", () => {
    // The exact failure: customgpt.io -> unstoppabledomains.com.
    expect(sameSite("unstoppabledomains.com", "customgpt.io")).toBe(false);
  });

  it("accepts a subdomain of the site that was asked", () => {
    expect(sameSite("www.ragie.ai", "ragie.ai")).toBe(true);
    expect(sameSite("cdn.assets.customgpt.ai", "customgpt.ai")).toBe(true);
  });

  it("treats a missing host as no match rather than a match", () => {
    expect(sameSite(null, "ragie.ai")).toBe(false);
    expect(sameSite("ragie.ai", null)).toBe(false);
  });

  it("reduces a host to its registrable domain", () => {
    expect(site("reference.langchain.com")).toBe("langchain.com");
  });
});

describe("the icon a page declares for itself", () => {
  const page = (head) => `<html><head>${head}</head><body>x</body></html>`;

  it("is found however much CSS precedes it", () => {
    const bulk = `<style>${"a{}".repeat(90_000)}</style>`; // ~270 KB, as WordPress emits
    const html = page(`${bulk}<link rel="icon" href="/favicon-192.png" sizes="192x192" />`);
    expect(html.indexOf("<link")).toBeGreaterThan(200_000);

    expect(declaredIconHrefs(html, "https://customgpt.ai/"))
      .toEqual(["https://customgpt.ai/favicon-192.png"]);
  });

  it("prefers the largest declared size", () => {
    const html = page(
      '<link rel="icon" href="/small.png" sizes="16x16" />' +
      '<link rel="icon" href="/big.png" sizes="192x192" />'
    );
    expect(declaredIconHrefs(html, "https://x.test/")[0]).toBe("https://x.test/big.png");
  });

  it("resolves relative and protocol-relative hrefs against the page", () => {
    const html = page('<link rel="shortcut icon" href="assets/icon.ico" />');
    expect(declaredIconHrefs(html, "https://x.test/")).toEqual(["https://x.test/assets/icon.ico"]);
  });

  it("ignores link tags that are not icons", () => {
    const html = page('<link rel="stylesheet" href="/app.css" /><link rel="canonical" href="/" />');
    expect(declaredIconHrefs(html, "https://x.test/")).toEqual([]);
  });

  it("does not scan the body, where a link tag is not a declaration", () => {
    const html = '<html><head></head><body><link rel="icon" href="/nope.png" /></body></html>';
    expect(declaredIconHrefs(html, "https://x.test/")).toEqual([]);
  });
});
