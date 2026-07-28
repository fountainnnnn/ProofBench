// @vitest-environment jsdom
//
// The direction confirmation card. A vague opening request used to send the
// agent searching on whichever reading it picked, and the mismatch surfaced only
// once a shortlist arrived. The card asks first — so what it shows has to be the
// prompt that will actually run, and correcting an assumption has to reach the
// message that gets sent.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import DirectionCard from "./DirectionCard.jsx";

const DIRECTION = {
  improved_prompt:
    "Find a self-hosted retrieval platform that answers staff questions over "
    + "internal documents on Python infrastructure.",
  assumptions: [
    { assumption: "Internal staff are the users", basis: "\"our internal docs\"" },
    { assumption: "This replaces an existing search", basis: "\"instead of\"" },
  ],
  unknowns: ["document volume", "team size"],
};

afterEach(cleanup);

function setup(overrides = {}) {
  const onSend = vi.fn();
  const onDismiss = vi.fn();
  render(
    <DirectionCard
      direction={{ ...DIRECTION, ...overrides }}
      onSend={onSend}
      onDismiss={onDismiss}
    />,
  );
  return { onSend, onDismiss };
}

it("shows the prompt that will actually drive the search, verbatim", () => {
  setup();
  // Not paraphrased and not truncated: a user cannot correct wording they were
  // never shown.
  expect(screen.getByText(DIRECTION.improved_prompt)).toBeTruthy();
  expect(screen.getByText("Here's what I understood")).toBeTruthy();
});

it("renders nothing at all without a prompt to confirm", () => {
  const { container } = render(
    <DirectionCard direction={{ improved_prompt: "" }} onSend={vi.fn()} onDismiss={vi.fn()} />,
  );
  expect(container.firstChild).toBeNull();
});

it("accepts every assumption by default and sends them as confirmed", () => {
  const { onSend } = setup();

  fireEvent.click(screen.getByRole("button", { name: /Search with this/ }));

  const [text] = onSend.mock.calls[0];
  expect(text).toContain(`Proceed with this direction: ${DIRECTION.improved_prompt}`);
  expect(text).toContain(
    "Confirmed assumptions: Internal staff are the users; This replaces an existing search",
  );
  expect(text).not.toContain("Not true:");
});

it("moves a switched-off assumption into the correction line", () => {
  const { onSend } = setup();

  fireEvent.click(screen.getByRole("button", { name: /This replaces an existing search/ }));
  fireEvent.click(screen.getByRole("button", { name: /Search with this/ }));

  const [text] = onSend.mock.calls[0];
  expect(text).toContain("Confirmed assumptions: Internal staff are the users");
  expect(text).toContain("Not true: This replaces an existing search");
  // The corrected one must not also count as confirmed.
  expect(text.split("Confirmed assumptions:")[1].split("\n")[0])
    .not.toContain("This replaces an existing search");
});

it("reports each chip's state to assistive technology", () => {
  setup();
  const chip = screen.getByRole("button", { name: /Internal staff are the users/ });
  expect(chip.getAttribute("aria-pressed")).toBe("true");

  fireEvent.click(chip);
  expect(chip.getAttribute("aria-pressed")).toBe("false");
});

it("shows what was left open without assuming either way", () => {
  setup();
  expect(screen.getByText("Not assumed either way")).toBeTruthy();
  expect(screen.getByText("document volume")).toBeTruthy();
  expect(screen.getByText("team size")).toBeTruthy();
});

it("omits the assumption and unknown sections when there are none", () => {
  setup({ assumptions: [], unknowns: [] });
  expect(screen.queryByText("Not assumed either way")).toBeNull();
  expect(screen.queryByText(/Switch off anything/)).toBeNull();
  // The prompt and its actions still stand on their own.
  expect(screen.getByRole("button", { name: /Search with this/ })).toBeTruthy();
});

it("hands the turn back to the composer when the user would rather rephrase", () => {
  const { onDismiss, onSend } = setup();

  fireEvent.click(screen.getByRole("button", { name: /I'll rephrase/ }));

  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(onSend).not.toHaveBeenCalled();
});
