// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import Logo from "./Logo.jsx";

afterEach(cleanup);

describe("ProofBench logo", () => {
  it("renders the generated proof-frame mark and wordmark", () => {
    const { container } = render(<Logo size={32} />);

    const mark = screen.getByRole("img", { name: "ProofBench logo" });
    expect(mark.getAttribute("width")).toBe("32");
    expect(container.querySelectorAll("[data-logo-letter]").length).toBe(2);
    expect(screen.getByText("ProofBench")).toBeTruthy();
    expect(mark.querySelector("linearGradient")).toBeNull();
  });

  it("can render the compact mark without the wordmark", () => {
    render(<Logo withWordmark={false} />);

    expect(screen.getByRole("img", { name: "ProofBench logo" })).toBeTruthy();
    expect(screen.queryByText("ProofBench")).toBeNull();
  });
});
