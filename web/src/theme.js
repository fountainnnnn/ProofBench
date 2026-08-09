/* Theme pinning shared by the sidebar toggle and Settings. The stored value is
   "dark" | "light" | "system"; index.html applies the pin pre-paint so first
   render never flashes.

   Absent means light, not system: the console's default face is the light one,
   and a first-time visitor on a dark-set machine should not be shown a theme
   nobody chose. "system" is therefore stored explicitly when it is picked —
   were it stored as absence, choosing it would silently revert to light on the
   next load. */

export const THEME_CHOICES = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function storedTheme() {
  try {
    const t = localStorage.getItem("pb-theme");
    return t === "dark" || t === "light" || t === "system" ? t : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(value) {
  const root = document.documentElement;
  if (value === "dark" || value === "light") {
    root.dataset.theme = value;
  } else {
    delete root.dataset.theme;
  }
  try {
    localStorage.setItem("pb-theme", value === "dark" || value === "light" ? value : "system");
  } catch {
    /* private mode: the choice applies for this page load only */
  }
  window.dispatchEvent(new CustomEvent("pb-theme-change", { detail: { theme: value } }));
}
