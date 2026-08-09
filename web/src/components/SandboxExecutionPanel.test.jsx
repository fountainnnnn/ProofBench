// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import SandboxExecutionPanel from "./SandboxExecutionPanel.jsx";

afterEach(cleanup);

const logs = {
  tesseract: [
    { phase: "building", line: "$ python -m pip install pytesseract" },
    { phase: "running", line: "ran images/inv_001.png" },
  ],
  easyocr: [
    { phase: "validating", line: "validation: ok" },
  ],
};

describe("sandbox execution panel", () => {
  it("shows concurrent sandboxes in the split terminal grid", () => {
    const { container } = render(
      <SandboxExecutionPanel
        open
        onClose={vi.fn()}
        sandboxLogs={logs}
        phaseState={{ phase: "RUNNING" }}
        running
      />
    );

    expect(screen.getByRole("complementary", { name: "Sandbox execution" })).toBeTruthy();
    expect(screen.getByRole("article", { name: "tesseract sandbox" })).toBeTruthy();
    expect(screen.getByRole("article", { name: "easyocr sandbox" })).toBeTruthy();
    expect(container.querySelector(".pb-sandbox-grid--split")).toBeTruthy();
    expect(screen.getByText("$ python -m pip install pytesseract")).toBeTruthy();
  });

  it("keeps source revisions available in a Files view", () => {
    render(
      <SandboxExecutionPanel
        open
        onClose={vi.fn()}
        sandboxLogs={{ tesseract: logs.tesseract }}
        sandboxFiles={{
          tesseract: [
            { path: "adapter.py", revision: 1, content: "def extract(path):\n    return {}" },
            { path: "adapter.py", revision: 2, content: "def extract(path):\n    return {'fixed': True}" },
          ],
        }}
        phaseState={{ phase: "DONE" }}
        running={false}
      />
    );

    const terminal = screen.getByRole("article", { name: "tesseract sandbox" });
    fireEvent.click(within(terminal).getByRole("tab", { name: /Files 2/ }));
    expect(within(terminal).getByText(/return \{\}/)).toBeTruthy();
    fireEvent.click(within(terminal).getByRole("button", { name: /adapter\.py · v2/ }));
    expect(within(terminal).getByText(/'fixed': True/)).toBeTruthy();
    expect(screen.getByText("Saved with this run")).toBeTruthy();
  });

  it("closes from the button and Escape", () => {
    const onClose = vi.fn();
    render(
      <SandboxExecutionPanel
        open
        onClose={onClose}
        sandboxLogs={{ tesseract: logs.tesseract }}
        running
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Close sandbox execution" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("cards only exist for candidates that get a sandbox", () => {
  it("shows no card for a candidate merely being assessed", () => {
    render(
      <SandboxExecutionPanel
        open
        onClose={() => {}}
        sandboxLogs={{}}
        sandboxFiles={{}}
        phaseState={{ phase: "ADAPTER_GEN", candidates: { "math-aids": "batching" } }}
        running
      />
    );
    // A tool assessment names every candidate while reading documentation.
    // Inventing a sandbox card for one left it on "Waiting for sandbox
    // output..." for the rest of the run, and after it too.
    expect(screen.queryByText("math-aids")).toBeNull();
    expect(screen.getByText(/execution stream will appear/i)).toBeTruthy();
  });

  it("shows a card once the run is actually provisioning", () => {
    render(
      <SandboxExecutionPanel
        open
        onClose={() => {}}
        sandboxLogs={{}}
        sandboxFiles={{}}
        phaseState={{ phase: "PROVISIONING", candidates: { tesseract: "provisioning" } }}
        running
      />
    );
    expect(screen.getByText("tesseract")).toBeTruthy();
  });
});
