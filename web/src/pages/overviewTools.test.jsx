// @vitest-environment jsdom
//
// The Tools evaluated card must keep every evidence group reachable. The two
// groups ("Measured by execution", "Rated from documentation") live in one
// scrollable region rather than each clipping itself, so the second group is
// never cut out of the card. A header control opens the whole leaderboard in a
// side sheet that behaves like the trace panel: backdrop close, Escape close,
// focus trap, and focus restore to the control that opened it.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  listSessions: vi.fn(),
  getResults: vi.fn(),
  fetchBrandLogos: vi.fn(),
}));
vi.mock("../api.js", () => api);

// Keep the dashboard offline: no bundled-mark lookups, no runtime icon fetch.
vi.mock("../brandIcons.js", () => ({
  brandAssetFor: () => null,
  runtimeBrandAssetFor: () => null,
  ensureBrandAssets: () => Promise.resolve(false),
}));

import Overview from "./Overview.jsx";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  api.fetchBrandLogos.mockResolvedValue({});
});

// One extraction run (exact_accuracy -> "Measured by execution") and one
// assessment run (rating -> "Rated from documentation"), so both groups exist.
const EXTRACTION = {
  tesseract: { exact_accuracy: 0.93, field_f1: 0.95, cer: 0.04, n_docs: 15, display_name: "Tesseract" },
  easyocr: { exact_accuracy: 0.81, field_f1: 0.86, cer: 0.09, n_docs: 15, display_name: "EasyOCR" },
};
const ASSESSMENT = {
  ace_quiz: { rating: 81, implementable: true, display_name: "Ace Quiz" },
  varsity: { rating: 40, implementable: true, display_name: "Varsity Tutors" },
};

function session(id, runId, title) {
  return {
    id,
    title,
    provenance: "measured",
    latest_run_id: runId,
    created_at: "2026-07-20T00:00:00Z",
    mode: "real",
  };
}

async function renderOverview() {
  api.listSessions.mockResolvedValue([
    session("s-extract", "run-extract", "Invoice extraction"),
    session("s-assess", "run-assess", "Quiz tool assessment"),
  ]);
  api.getResults.mockImplementation((runId) =>
    Promise.resolve({ metrics: runId === "run-extract" ? EXTRACTION : ASSESSMENT }),
  );
  render(<MemoryRouter><Overview /></MemoryRouter>);
  await screen.findByRole("region", { name: /all evidence groups/i });
}

describe("Tools evaluated: one scrollable region, no per-group clipping", () => {
  it("holds every evidence group in a single scroll region", async () => {
    await renderOverview();
    const region = screen.getByRole("region", { name: /all evidence groups/i });

    // Both groups live inside the one region, so neither is clipped out.
    expect(within(region).getByText("Measured by execution")).toBeTruthy();
    expect(within(region).getByText("Rated from documentation")).toBeTruthy();
    const groupHeaders = within(region).getAllByText(
      /Measured by execution|Rated from documentation/,
    );
    expect(groupHeaders[0].textContent).toBe("Measured by execution");

    // It is keyboard focusable so the list can be scrolled without a pointer,
    // and it scrolls rather than fitting-and-clipping.
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.className).toContain("overflow-y-auto");
  });

  it("drops the obsolete hidden-tools footer count", async () => {
    await renderOverview();
    expect(screen.queryByText(/below the fold/i)).toBeNull();
    expect(screen.queryByText(/more below/i)).toBeNull();
  });
});

describe("Tools evaluated: the expand sheet", () => {
  it("opens a right-side dialog holding the complete leaderboard", async () => {
    await renderOverview();
    expect(screen.queryByRole("dialog")).toBeNull();

    const expand = screen.getByRole("button", { name: /open the full leaderboard/i });
    expand.focus();
    fireEvent.click(expand);

    const dialog = await screen.findByRole("dialog", { name: "Tools evaluated" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(expand.getAttribute("aria-expanded")).toBe("true");
    // The same two groups are present in the expanded view.
    expect(within(dialog).getByText("Measured by execution")).toBeTruthy();
    expect(within(dialog).getByText("Rated from documentation")).toBeTruthy();
    // Focus is moved into the dialog, onto its close control.
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: /close tools evaluated/i }));
  });

  it("closes on Escape and restores focus to the control that opened it", async () => {
    await renderOverview();
    const expand = screen.getByRole("button", { name: /open the full leaderboard/i });
    expand.focus();
    fireEvent.click(expand);
    await screen.findByRole("dialog");

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(expand);
  });

  it("closes when the backdrop behind it is pressed", async () => {
    await renderOverview();
    const expand = screen.getByRole("button", { name: /open the full leaderboard/i });
    expand.focus();
    fireEvent.click(expand);
    const dialog = await screen.findByRole("dialog");

    // The dimming backdrop is the panel's immediate previous sibling.
    fireEvent.mouseDown(dialog.previousElementSibling);

    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
