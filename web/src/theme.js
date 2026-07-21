/* Theme pinning shared by the sidebar toggle and Settings. The stored value is
   "dark" | "light" | absent (follow the system); index.html applies the pin
   pre-paint so first render never flashes. */

export const THEME_CHOICES = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function storedTheme() {
  try {
    const t = localStorage.getItem("pb-theme");
    return t === "dark" || t === "light" ? t : "system";
  } catch {
    return "system";
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
    if (value === "dark" || value === "light") {
      localStorage.setItem("pb-theme", value);
    } else {
      localStorage.removeItem("pb-theme");
    }
  } catch {
    /* private mode: the choice applies for this page load only */
  }
  window.dispatchEvent(new CustomEvent("pb-theme-change", { detail: { theme: value } }));
}
