// @vitest-environment jsdom
/**
 * What the thread may promise before a benchmark has been run.
 *
 * The failure this covers: a session that was still searching the web for
 * candidates showed a Results table of empty skeleton rows and opened the
 * sandbox execution panel, so the console advertised a ranking and a live
 * execution stream for work nobody had started. Both came from reading
 * "something is running" as "a benchmark is running" — a chat turn sets the
 * same flag, and the session's phase during one is not a run phase at all.
 */

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatThread from "./ChatThread.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

afterEach(cleanup);

const BASE = {
  messages: [{ role: "user", text: "RAG platforms for SharePoint" }],
  trace: [],
  sandboxLogs: {},
  results: null,
  report: null,
  spec: null,
};

function thread({ phase, ...props }) {
  return render(
    <ChatThread {...BASE} {...props} phaseState={phase === undefined ? null : { phase }} />
  );
}

describe("results placeholder before a run", () => {
  it("stays away while the session is still deciding what to run", () => {
    // Discovery: the agent is working, but no benchmark has been confirmed.
    thread({ running: true, phase: "" });
    expect(screen.queryByText("Results")).toBeNull();
  });

  it("stays away during intake and spec confirmation", () => {
    for (const phase of ["INTAKE", "SPEC_CONFIRM"]) {
      cleanup();
      thread({ running: true, phase });
      expect(screen.queryByText("Results")).toBeNull();
    }
  });

  it("appears once the confirmed run is actually under way", () => {
    thread({ running: true, phase: "RUNNING" });
    expect(screen.getByText("Results")).toBeTruthy();
  });

  it("appears for a finished run even though nothing is running", () => {
    thread({ running: false, phase: "DONE" });
    expect(screen.getByText("Results")).toBeTruthy();
  });
});
