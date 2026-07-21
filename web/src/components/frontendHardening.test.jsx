// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import AgentTraceCard from "./AgentTraceCard.jsx";
import ResultsCard from "./ResultsCard.jsx";
import SpecCard from "./SpecCard.jsx";
import ChatThread from "./ChatThread.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

afterEach(cleanup);

describe("API-driven presentation hardening", () => {
  it("recursively redacts secrets and server paths across spec, trace, results, report, and citations", () => {
    render(
      <>
        <SpecCard
          spec={{
            category: "API_KEY=category-secret",
            candidates: [{ name: "Authorization: Bearer candidate-secret" }],
            fields: ["/srv/private/labels.csv"],
            dataset: { path: "/srv/private/dataset" },
          }}
          onRun={vi.fn()}
          onStop={vi.fn()}
        />
        <AgentTraceCard
          trace={[{ tool: "Bearer trace-secret", args_summary: "access_token=trace-token", detail: "/srv/private/trace.json" }]}
          sandboxLogs={{ "API_KEY=sandbox-secret": [{ phase: "running", line: "password=log-secret" }] }}
          phaseState={{ phase: "running", candidates: { "Bearer phase-secret": "ok" } }}
        />
        <ResultsCard
          metrics={{ "Authorization: Bearer result-secret": { exact_accuracy: 0.8 } }}
          report={{
            markdown: "password=report-secret at /srv/private/report.md",
            citations: [{ title: "access_token=citation-secret", url: "https://example.com/?access_token=url-secret" }],
          }}
          phase="DONE"
        />
      </>
    );

    const visible = document.body.textContent;
    for (const secret of ["category-secret", "candidate-secret", "trace-secret", "trace-token", "sandbox-secret", "log-secret", "phase-secret", "result-secret", "report-secret", "citation-secret", "url-secret", "/srv/private"]) {
      expect(visible).not.toContain(secret);
    }
    expect(visible).toContain("[REDACTED]");
    expect(screen.getByRole("button", { name: /remove .* from benchmark/i })).toBeTruthy();
  });

  it("renders an honest terminal state when completed metrics are missing", () => {
    render(<ResultsCard metrics={null} report={null} phase="DONE" running={false} />);
    expect(screen.getByText("The run completed without any result metrics.")).toBeTruthy();
    expect(screen.queryByText("Preparing report")).toBeNull();
  });

  it("renders non-finite result sets as unavailable instead of loading forever", () => {
    render(<ResultsCard metrics={{ alpha: { exact_accuracy: Number.NaN } }} phase="DONE" />);
    expect(screen.getByText("Result metrics were returned, but no finite primary score is available.")).toBeTruthy();
  });

  it("blocks a report whose independent provenance mismatches the results", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <ChatThread
        messages={[]}
        trace={[]}
        sandboxLogs={{}}
        phaseState={{ phase: "DONE" }}
        results={{ alpha: { exact_accuracy: 0.9 } }}
        report={{ markdown: "blocked report body", provenance: { status: "measured", mode: "real", datasetKind: "upload" } }}
        resultsProvenance={{ status: "synthetic", mode: "demo", datasetKind: "synthetic" }}
        onRun={vi.fn()}
        onStop={vi.fn()}
        running={false}
        stopping={false}
      />
    );

    expect(screen.getByText("Report provenance does not match the result metrics. The report has been blocked.")).toBeTruthy();
    expect(screen.queryByText("blocked report body")).toBeNull();
  });
});
