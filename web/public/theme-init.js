/* Pre-paint theme pin. Loaded as an external file rather than inlined so it
   satisfies the deployment's `default-src 'self'` CSP, which forbids inline
   script. A blocking script in <head> still runs before first paint, so a
   pinned theme never flashes the wrong canvas. No stored value means follow
   the system, via the prefers-color-scheme block in index.css. */
(function () {
  try {
    var t = localStorage.getItem("pb-theme");
    if (t === "dark" || t === "light") {
      document.documentElement.dataset.theme = t;
    }
  } catch (e) {
    /* private mode: fall through to the system preference */
  }
})();
