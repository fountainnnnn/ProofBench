import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    // Playwright specs are driven by `npx playwright test`; collected under
    // vitest they abort at import ("did not expect test.use() here") and
    // paint the unit suite red without a single failing assertion.
    exclude: ["e2e/**", "node_modules/**"],
  },
  server: {
    port: 5173,
    // Mirror production security headers so local browser QA exercises the
    // same CSP boundary as the Nginx image.
    //
    // One deliberate relaxation, dev only: `script-src` adds 'unsafe-inline'
    // because Vite injects the React Fast Refresh preamble as an inline script.
    // Without it the dev server serves a blank page ("can't detect preamble").
    // A production build contains no preamble, so nginx.conf keeps the strict
    // policy with no script exception. Application code must therefore never
    // rely on inline script: anything that has to run pre-paint ships as an
    // external file (see /theme-init.js).
    headers: {
      "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    },
    // Dev convenience: same-origin /api calls hit the local backend, matching
    // how Nginx proxies /api in the Compose deployment. VITE_API_BASE_URL in
    // api.js still wins when set.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
