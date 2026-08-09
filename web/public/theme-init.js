/* Pre-paint theme pin. Loaded as an external file rather than inlined so it
   satisfies the deployment's `default-src 'self'` CSP, which forbids inline
   script. A blocking script in <head> still runs before first paint, so a
   pinned theme never flashes the wrong canvas. No stored value means light:
   the console defaults to its light face rather than following the machine.
   Only the explicit "system" choice defers to the prefers-color-scheme block
   in index.css, by leaving the attribute off. */
(function () {
  try {
    var t = localStorage.getItem("pb-theme");
    if (t !== "system") {
      document.documentElement.dataset.theme = t === "dark" ? "dark" : "light";
    }
  } catch (e) {
    /* private mode: the default light face still applies for this page load */
    document.documentElement.dataset.theme = "light";
  }
})();
