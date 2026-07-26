// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Collapse } from "./ui.jsx";

/* Animating a panel shut requires its body to stay mounted, which is a trade:
   a zero-height box with overflow:hidden is still focusable and still read by
   screen readers. These tests pin the part that keeps that trade honest. */

const body = () => document.querySelector('[id$="-body"]');

describe("Collapse", () => {
  afterEach(cleanup);

  it("keeps the body mounted when closed, so it can animate rather than vanish", () => {
    render(
      <Collapse title="Keys">
        <button type="button">Add key</button>
      </Collapse>,
    );
    expect(screen.getByRole("button", { name: "Add key" })).toBeTruthy();
    expect(body().className).toContain("grid-rows-[0fr]");
  });

  it("marks the closed body inert, so its controls leave the tab order", () => {
    render(
      <Collapse title="Keys">
        <button type="button">Add key</button>
      </Collapse>,
    );
    expect(body().hasAttribute("inert")).toBe(true);
  });

  it("drops inert and opens the row once expanded", () => {
    render(
      <Collapse title="Keys">
        <button type="button">Add key</button>
      </Collapse>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Keys/ }));

    expect(body().hasAttribute("inert")).toBe(false);
    expect(body().className).toContain("grid-rows-[1fr]");
  });

  it("points aria-controls at the body in both states", () => {
    render(
      <Collapse title="Keys">
        <p>content</p>
      </Collapse>,
    );
    const toggle = screen.getByRole("button", { name: /Keys/ });

    /* Previously aria-controls was only set while open, which left the closed
       toggle referencing nothing for assistive tech. */
    expect(toggle.getAttribute("aria-controls")).toBe(body().id);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-controls")).toBe(body().id);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("honours defaultOpen", () => {
    render(
      <Collapse title="Keys" defaultOpen>
        <p>content</p>
      </Collapse>,
    );
    expect(body().hasAttribute("inert")).toBe(false);
    expect(body().className).toContain("grid-rows-[1fr]");
  });
});
