// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  brandAssetFor, ensureBrandAssets, normalizeBrandName, resetRuntimeBrandAssets,
  runtimeBrandAssetFor,
} from "./brandIcons.js";

describe("brand icons", () => {
  it("normalizes candidate separators without changing identity", () => {
    expect(normalizeBrandName(" Microsoft_Azure ")).toBe("microsoftazure");
    expect(normalizeBrandName("Google Cloud")).toBe("googlecloud");
  });

  it.each([
    ["aws", "/brand/aws.svg"],
    ["Amazon Web Services", "/brand/aws.svg"],
    ["GCP", "/brand/google-cloud.svg"],
    ["google_cloud", "/brand/google-cloud.svg"],
    ["microsoft_azure", "/brand/microsoft-azure.svg"],
    ["microsoft_document_intelligence", "/brand/microsoft-azure.svg"],
    ["Digital Ocean", "/brand/digital-ocean.svg"],
    ["elementor", "/brand/elementor.svg"],
  ])("maps %s to its bundled service mark", (name, asset) => {
    expect(brandAssetFor(name)).toBe(asset);
  });

  it("keeps custom adapters on the neutral fallback", () => {
    /* A name that identifies no particular vendor must never borrow one's mark:
       stamping a real company's logo on an unknown adapter is misattribution,
       not identification. These stay on the monogram by design. */
    expect(brandAssetFor("arya_invoice_extraction")).toBeNull();
    expect(brandAssetFor("invoice_data_extraction_api")).toBeNull();
  });

  it("resolves a named vendor's tool to that vendor's mark", () => {
    /* mindee_invoice_ocr names Mindee outright, so it carries Mindee's logo —
       fetched by scripts/fetch-brand-logos.mjs into the generated manifest. */
    expect(brandAssetFor("mindee_invoice_ocr")).toBe("/brand/mindee-invoice-ocr.png");
  });

  it.each([
    ["tesseract", "/brand/tesseract.png"],
    ["affinda", "/brand/affinda.ico"],
    ["arya_ai", "/brand/arya-ai.png"],
    ["easyocr", "/brand/easyocr.svg"],
    ["mindee", "/brand/mindee.png"],
    ["veryfi", "/brand/veryfi.png"],
  ])("bundles the measured OCR tool %s", (name, asset) => {
    expect(brandAssetFor(name)).toBe(asset);
  });
});

/* The bundled manifest is frozen when the frontend is built, so a tool
   benchmarked since the last deploy has no mark in it. Those are resolved by
   the backend at request time. */
describe("marks for tools newer than this build", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeBrandAssets();
  });

  it("asks only for names the bundle does not already cover", async () => {
    const fetchLogos = vi.fn().mockResolvedValue({});
    await ensureBrandAssets(["aws", "jira_n8n_integration"], fetchLogos);

    expect(fetchLogos).toHaveBeenCalledWith(["jira_n8n_integration"]);
  });

  it("serves a resolved mark from then on", async () => {
    const fetchLogos = vi.fn().mockResolvedValue({ jira_n8n_integration: "data:image/png;base64,AA" });
    const added = await ensureBrandAssets(["jira_n8n_integration"], fetchLogos);

    expect(added).toBe(true);
    expect(runtimeBrandAssetFor("jira_n8n_integration")).toBe("data:image/png;base64,AA");
  });

  it("reuses a stored mark after a full module-memory reload", async () => {
    const firstFetch = vi.fn().mockResolvedValue({
      jira_n8n_integration: "data:image/png;base64,AA",
    });
    await ensureBrandAssets(["jira_n8n_integration"], firstFetch);

    resetRuntimeBrandAssets();
    const reloadFetch = vi.fn();
    await ensureBrandAssets(["jira_n8n_integration"], reloadFetch);

    expect(reloadFetch).not.toHaveBeenCalled();
    expect(runtimeBrandAssetFor("jira_n8n_integration")).toBe("data:image/png;base64,AA");
  });

  it("asks about a vendor with no mark once, not once per render", async () => {
    const fetchLogos = vi.fn().mockResolvedValue({});
    await ensureBrandAssets(["obscure_tool"], fetchLogos);
    const added = await ensureBrandAssets(["obscure_tool"], fetchLogos);

    expect(fetchLogos).toHaveBeenCalledTimes(1);
    expect(added).toBe(false);
  });

  it("reuses a stored negative result after a full reload", async () => {
    await ensureBrandAssets(["obscure_tool"], vi.fn().mockResolvedValue({}));

    resetRuntimeBrandAssets();
    const reloadFetch = vi.fn();
    await ensureBrandAssets(["obscure_tool"], reloadFetch);

    expect(reloadFetch).not.toHaveBeenCalled();
    expect(runtimeBrandAssetFor("obscure_tool")).toBeNull();
  });

  it("reports nothing new when every name was already bundled", async () => {
    const fetchLogos = vi.fn();
    expect(await ensureBrandAssets(["aws", "gcp"], fetchLogos)).toBe(false);
    expect(fetchLogos).not.toHaveBeenCalled();
  });

  it("survives a failed request, because a logo is decoration", async () => {
    const fetchLogos = vi.fn().mockRejectedValue(new Error("offline"));
    await expect(ensureBrandAssets(["jira_n8n_integration"], fetchLogos)).resolves.toBe(false);
    expect(runtimeBrandAssetFor("jira_n8n_integration")).toBeNull();
  });

  it("discards corrupt persistent data and falls back to the backend", async () => {
    localStorage.setItem("proofbench.brandAssets.v1", "{not json");
    resetRuntimeBrandAssets();
    const fetchLogos = vi.fn().mockResolvedValue({
      jira_n8n_integration: "data:image/png;base64,AA",
    });

    await expect(ensureBrandAssets(["jira_n8n_integration"], fetchLogos)).resolves.toBe(true);
    expect(fetchLogos).toHaveBeenCalledTimes(1);
  });

  it("discards v1 false-negative entries created by the unbatched client", async () => {
    localStorage.setItem("proofbench.brandAssets.v1", JSON.stringify({
      version: 1,
      items: [["late_tool", { asset: null, cachedAt: Date.now() }]],
    }));
    resetRuntimeBrandAssets();
    const fetchLogos = vi.fn().mockResolvedValue({
      late_tool: "data:image/png;base64,AA",
    });

    await expect(ensureBrandAssets(["late_tool"], fetchLogos)).resolves.toBe(true);
    expect(fetchLogos).toHaveBeenCalledWith(["late_tool"]);
    expect(runtimeBrandAssetFor("late_tool")).toBe("data:image/png;base64,AA");
  });
});
