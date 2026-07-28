// @vitest-environment jsdom
/**
 * A run that could only execute some of its candidates.
 *
 * The failure this covers: six candidates, two reachable, and a table where the
 * other four were seven columns of "Unavailable" — nothing to compare, and the
 * two that ran squeezed off the top by stack traces. The comparison now runs on
 * documentation evidence, which every candidate has, and the sandbox columns
 * fold away behind one control that states how many actually ran.
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ResultsCard from "./ResultsCard.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

afterEach(cleanup);

const MIXED = {
  tesseract: {
    exact_accuracy: 0.667, field_f1: 0.792, cer: 0.255, mean_latency_s: 0.74,
    failure_rate: 0, setup_complexity: 2, n_docs: 15, status: "ok",
    research_score: 55, documentation_quality: 60, integration_feasibility: 52,
    auth_clarity: 70, implementable: true,
  },
  veryfi: {
    exact_accuracy: 0.0, field_f1: 0.0, cer: 1.0, mean_latency_s: 0,
    failure_rate: 0, setup_complexity: 1, n_docs: 15, status: "ok",
    research_score: 91, documentation_quality: 92, integration_feasibility: 90,
    auth_clarity: 90, implementable: true,
  },
  affinda: {
    exact_accuracy: null, field_f1: null, cer: null, mean_latency_s: null,
    n_docs: 15, status: "no_result",
    error_summary: "adapter validation failed: AFFINDA_API_KEY not set",
    research_score: 88, documentation_quality: 90, integration_feasibility: 86,
    auth_clarity: 84, setup_complexity: 3, implementable: true,
  },
  easyocr: {
    exact_accuracy: null, field_f1: null, cer: null, n_docs: 15,
    status: "no_result", error_summary: "sandbox command failed with exit code 1",
  },
};

const show = (metrics = MIXED) =>
  render(<ResultsCard metrics={metrics} report={null} runId="run-1" phase="DONE" running={false} />);

// Scoped to the table: the verdict above it names the winner too.
const body = () => within(document.querySelector("tbody"));
const rowFor = (name) => body().getByText(name).closest("tr");
const sortHeader = (label) => screen.queryByRole("button", { name: new RegExp(`Sort by ${label}`) });

describe("a run that could not execute every candidate", () => {
  it("compares the unreachable ones instead of leaving them blank", () => {
    show();
    const affinda = rowFor("affinda");
    expect(within(affinda).getByText("88/100")).toBeTruthy();
    expect(within(affinda).getByText("Documentation")).toBeTruthy();
  });

  it("says on every row which kind of evidence its numbers came from", () => {
    show();
    expect(within(rowFor("tesseract")).getByText("Measured")).toBeTruthy();
    expect(within(rowFor("veryfi")).getByText("Measured")).toBeTruthy();
    expect(within(rowFor("affinda")).getByText("Documentation")).toBeTruthy();
  });

  it("ranks the measured candidates above the documented one", () => {
    const { container } = show();
    const names = [...container.querySelectorAll("tbody tr")]
      .map((row) => row.querySelector("[data-primary]").textContent);
    expect(names).toEqual(["tesseract", "veryfi", "affinda"]);
  });

  it("folds the sandbox columns away and states how many ran", () => {
    show();
    expect(screen.getByText(/2 of 3 ran/)).toBeTruthy();
    expect(sortHeader("CER")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Show sandbox measurements/ }));
    expect(sortHeader("CER")).toBeTruthy();
    // Still withheld for the candidate that never ran — revealing the column
    // does not invent a measurement to put in it.
    expect(within(rowFor("affinda")).getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("keeps the sandbox columns open when every ranked candidate ran", () => {
    show({ tesseract: MIXED.tesseract, veryfi: MIXED.veryfi });
    expect(sortHeader("CER")).toBeTruthy();
    expect(screen.getByText(/2 of 2 ran/)).toBeTruthy();
  });

  it("still withholds a candidate with no evidence of any kind", () => {
    show();
    expect(body().queryByText("easyocr")).toBeNull();
    expect(screen.getByText("Not compared · 1")).toBeTruthy();
  });

  it("names the winner on the evidence the winner actually has", () => {
    show({ affinda: MIXED.affinda, easyocr: MIXED.easyocr });
    expect(screen.getByText(/Docs score 88\/100/)).toBeTruthy();
    expect(screen.getAllByText("Documentation").length).toBeGreaterThan(0);
  });

  it("counts measured and documented candidates separately in the verdict", () => {
    show();
    expect(screen.getByText(/2 measured, 1 scored from documentation/)).toBeTruthy();
  });
});
