import { describe, expect, it } from "vitest";
import { repairLegacyReportTables, splitReportFindings } from "./reportMarkdown.js";

describe("legacy report Markdown tables", () => {
  it("joins a display-name pipe back into the candidate cell", () => {
    const markdown = [
      "| Rank | Tool | Suitability | Basis |",
      "|---:|---|---:|---|",
      "| 7 | Mindgrasp | The #1 AI Study Tool for Students | 31/100 | Documentation |",
      "",
      "### Mindgrasp | The #1 AI Study Tool for Students",
    ].join("\n");

    const repaired = repairLegacyReportTables(markdown);

    expect(repaired).toContain(
      "| 7 | Mindgrasp \\| The #1 AI Study Tool for Students | 31/100 | Documentation |",
    );
    expect(repaired).toContain("### Mindgrasp | The #1 AI Study Tool for Students");
  });

  it("leaves correctly shaped tables unchanged", () => {
    const markdown = [
      "| Rank | Tool | Score |",
      "|---:|---|---:|",
      "| 1 | Tesseract | 93 |",
    ].join("\n");

    expect(repairLegacyReportTables(markdown)).toBe(markdown);
  });
});

describe("report findings", () => {
  it("groups each candidate subsection and preserves the following section", () => {
    const markdown = [
      "# Report",
      "",
      "## Findings",
      "",
      "Read each candidate against the requirement.",
      "",
      "### Alpha",
      "",
      "Alpha detail.",
      "",
      "### Beta",
      "",
      "Beta detail.",
      "",
      "## Sources",
      "",
      "- source",
    ].join("\n");

    expect(splitReportFindings(markdown)).toEqual({
      before: "# Report\n\n## Findings",
      intro: "Read each candidate against the requirement.",
      findings: ["### Alpha\n\nAlpha detail.", "### Beta\n\nBeta detail."],
      after: "## Sources\n\n- source",
    });
  });

  it("leaves reports without candidate findings on the normal renderer", () => {
    expect(splitReportFindings("# Report\n\nNo findings yet.")).toBeNull();
  });
});
