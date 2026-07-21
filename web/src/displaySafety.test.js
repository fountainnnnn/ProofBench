import { describe, expect, it } from "vitest";
import { safeVisibleText, sanitizeForDisplay } from "./displaySafety.js";

describe("safeVisibleText", () => {
  it("redacts Windows and UNC server paths", () => {
    const visible = safeVisibleText("failed at C:\\srv\\secret\\invoice.png and \\\\host\\share\\file.csv");
    expect(visible).not.toContain("invoice.png");
    expect(visible).not.toContain("host");
    expect(visible).toContain("[server path]");
  });

  it("redacts common POSIX server paths without changing ordinary URLs", () => {
    const visible = safeVisibleText("/srv/proofbench/data/a.csv https://docs.example.com/api https://docs.example.com/app/guide");
    expect(visible).not.toContain("/srv/proofbench");
    expect(visible).toContain("https://docs.example.com/api");
    expect(visible).toContain("https://docs.example.com/app/guide");
  });

  it("redacts bearer, named, and key-shaped credentials", () => {
    const visible = safeVisibleText("Authorization: Bearer abc.def API_KEY=super-secret sk-abcdefghijklmnop");
    expect(visible).not.toContain("abc.def");
    expect(visible).not.toContain("super-secret");
    expect(visible).not.toContain("abcdefghijklmnop");
    expect(visible).toContain("[REDACTED]");
  });

  it("redacts quoted JSON credentials and file URLs", () => {
    const visible = safeVisibleText('{"api_key":"secret-value","path":"file:///srv/private/report.json"}');
    expect(visible).not.toContain("secret-value");
    expect(visible).not.toContain("/srv/private");
    expect(visible).toContain("[REDACTED]");
    expect(visible).toContain("[server path]");
  });

  it("recursively sanitizes sensitive keys in objects and arrays", () => {
    const sanitized = sanitizeForDisplay({
      token: "secret-token",
      nested: [{ password: "secret-password", note: "/srv/private/file.csv" }],
    });
    expect(sanitized.token).toBe("[REDACTED]");
    expect(sanitized.nested[0].password).toBe("[REDACTED]");
    expect(sanitized.nested[0].note).toBe("[server path]");
  });
});
