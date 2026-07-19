// A stable slug for a panel label, so MissionControl can stamp each panel wrapper
// with a scroll-target id and the CommandPalette can jump to it. The palette
// discovers the actual panels from the DOM at open time (elements carrying
// `data-section-label`), so this stays in sync even as panels are added/renamed.

export function sectionId(label: string): string {
  return "sec-" + label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

/** Smooth-scroll a panel into view and re-trigger the `.nav-flash` highlight ring.
 * Shared by the command palette and the keyboard layer (g-then-key quick-nav). */
export function flashSection(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("nav-flash");
  // reflow so the animation re-triggers even if recently flashed
  void el.offsetWidth;
  el.classList.add("nav-flash");
  window.setTimeout(() => el.classList.remove("nav-flash"), 1300);
}
