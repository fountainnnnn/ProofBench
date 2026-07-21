import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "./linkSafety.js";

describe("safeHttpUrl", () => {
  it("allows only absolute HTTP and HTTPS links", () => {
    expect(safeHttpUrl("https://docs.example.com/guide")).toBe("https://docs.example.com/guide");
    expect(safeHttpUrl("http://example.com")).toBe("http://example.com/");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,unsafe")).toBeNull();
    expect(safeHttpUrl("/relative/path")).toBeNull();
    expect(safeHttpUrl("https://example.com/report?token=secret")).toBeNull();
    expect(safeHttpUrl("https://example.com/report?access_token=secret")).toBeNull();
    expect(safeHttpUrl("https://example.com/report?next_access_token_hint=secret")).toBeNull();
    expect(safeHttpUrl("https://example.com/report?API-KEY=secret")).toBeNull();
    expect(safeHttpUrl("https://user:password@example.com/report")).toBeNull();
  });
});
