// @vitest-environment jsdom
//
// The direction confirmation card. A vague opening request used to send the
// agent searching on whichever reading it picked, and the mismatch surfaced only
// once a shortlist arrived. The card asks a plain yes or no first — so what it
// shows has to be the prompt that will actually run, and a "no" has to reach the
// message that gets sent in its place.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import DirectionCard from "./DirectionCard.jsx";

const DIRECTION = {
  improved_prompt:
    "Find a self-hosted retrieval platform that answers staff questions over "
    + "internal documents on Python infrastructure.",
};

afterEach(cleanup);

function setup(overrides = {}) {
  const onSend = vi.fn();
  render(<DirectionCard direction={{ ...DIRECTION, ...overrides }} onSend={onSend} />);
  return { onSend };
}

it("shows the prompt that will actually drive the search, verbatim", () => {
  setup();
  // Not paraphrased and not truncated: a user cannot correct wording they were
  // never shown.
  expect(screen.getByText(DIRECTION.improved_prompt)).toBeTruthy();
  expect(screen.getByText("Is this what you mean?")).toBeTruthy();
});

it("renders nothing at all without a prompt to confirm", () => {
  const { container } = render(
    <DirectionCard direction={{ improved_prompt: "" }} onSend={vi.fn()} />,
  );
  expect(container.firstChild).toBeNull();
});

it("sends the prompt on as it stands when the user answers yes", () => {
  const { onSend } = setup();

  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect(onSend).toHaveBeenCalledTimes(1);
  expect(onSend.mock.calls[0][0]).toBe(
    `Proceed with this direction: ${DIRECTION.improved_prompt}`,
  );
});

it("does not open the correction field until the user answers no", () => {
  setup();
  expect(screen.queryByRole("textbox")).toBeNull();

  const no = screen.getByRole("button", { name: "No" });
  expect(no.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(no);

  expect(screen.getByRole("textbox")).toBeTruthy();
  expect(no.getAttribute("aria-expanded")).toBe("true");
  expect(document.getElementById(no.getAttribute("aria-controls"))).toBeTruthy();
});

it("focuses the correction field the moment it appears", () => {
  setup();

  fireEvent.click(screen.getByRole("button", { name: "No" }));

  expect(document.activeElement).toBe(screen.getByRole("textbox"));
});

it("labels the correction field so assistive technology can name it", () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  const field = screen.getByLabelText("What should it be instead?");
  expect(field.tagName).toBe("TEXTAREA");
});

it("cannot send an empty or whitespace correction", () => {
  const { onSend } = setup();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  const send = screen.getByRole("button", { name: "Send correction" });
  expect(send.disabled).toBe(true);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
  expect(send.disabled).toBe(true);
  fireEvent.click(send);
  expect(onSend).not.toHaveBeenCalled();
});

it("sends the correction and keeps the rejected prompt for context", () => {
  const { onSend } = setup();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Compare hosted OCR APIs, not self-hosted search." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send correction" }));

  expect(onSend).toHaveBeenCalledTimes(1);
  const [text] = onSend.mock.calls[0];
  expect(text).toContain("Compare hosted OCR APIs, not self-hosted search.");
  // The prompt being corrected travels with the message so the agent knows what
  // it is replacing.
  expect(text).toContain(DIRECTION.improved_prompt);
});

it("redacts credentials before a correction becomes a chat message", () => {
  const { onSend } = setup();
  fireEvent.click(screen.getByRole("button", { name: "No" }));
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Use api_key=secret-value for this comparison." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send correction" }));

  expect(onSend.mock.calls[0][0]).toContain("api_key=[REDACTED]");
  expect(onSend.mock.calls[0][0]).not.toContain("secret-value");
});

it("still lets the user accept the prompt after opening the correction field", () => {
  const { onSend } = setup();

  fireEvent.click(screen.getByRole("button", { name: "No" }));
  // Yes stays available for a user who reconsiders mid-correction.
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect(onSend).toHaveBeenCalledTimes(1);
  expect(onSend.mock.calls[0][0]).toBe(
    `Proceed with this direction: ${DIRECTION.improved_prompt}`,
  );
});
