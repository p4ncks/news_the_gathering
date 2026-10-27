/* Continents select their outlets; individual outlets set the continent state.
   Apply commits the choices, keeping the results URL easy to share. */
(() => {
  // Language switching also works on the article reader, which has no filters.
  document.querySelectorAll("[data-language-select]").forEach((select) => {
    select.addEventListener("change", () => { select.form.requestSubmit(); });
    const button = select.form.querySelector("[data-language-apply]");
    if (button) button.hidden = true;
  });
  const form = document.querySelector(".filter-form");
  if (!form) return;

  const continents = [...form.querySelectorAll('input[name="continent"]')];
  const outlets = [...form.querySelectorAll('input[name="outlet"]')];
  const selectionCount = form.querySelector("[data-selection-count]");
  const counts = [...form.querySelectorAll("[data-group-count]")];
  const allButtons = [...form.querySelectorAll("[data-select-all]")];
  const emptyOutlets = form.querySelector("[data-empty-outlets]");
  const enhanced = document.createElement("input");
  enhanced.type = "hidden";
  enhanced.name = "enhanced";
  enhanced.value = "1";
  form.append(enhanced);
  form.querySelectorAll("[data-enhanced-only]").forEach((element) => { element.hidden = false; });

  const panel = document.querySelector(".filter-panel");
  const panelCount = document.querySelector("[data-panel-selection-count]");
  const smallScreen = window.matchMedia("(max-width: 760px)");
  if (panel) {
    panel.open = !smallScreen.matches;
    smallScreen.addEventListener("change", () => { panel.open = !smallScreen.matches; });
  }

  function synchronize({ preserveEmpty = false } = {}) {
    continents.forEach((continent) => {
      const members = outlets.filter((outlet) => outlet.dataset.continent === continent.value);
      const selected = members.filter((outlet) => outlet.checked).length;
      // A partial continent stays checked so GET submission includes it.
      continent.checked = selected > 0 || (preserveEmpty && continent.checked);
      continent.indeterminate = selected > 0 && selected < members.length;
      continent.setAttribute("aria-checked", continent.indeterminate ? "mixed" : String(continent.checked));
      members.forEach((outlet) => {
        outlet.closest(".outlet-option").hidden = !continent.checked;
      });
      const count = counts.find((item) => item.dataset.groupCount === continent.value);
      if (count) count.textContent = `${selected}/${members.length}`;
    });
    const selected = outlets.filter((outlet) => outlet.checked).length;
    const countText = (selected === 1 ? form.dataset.selectionSingular : form.dataset.selectionPlural)
      .replace("{count}", String(selected));
    if (selectionCount) selectionCount.textContent = countText;
    if (panelCount) panelCount.textContent = countText;
    if (emptyOutlets) emptyOutlets.hidden = continents.some((continent) => continent.checked);
    allButtons.forEach((button) => {
      const pressed = button.dataset.selectAll === "on"
        ? outlets.length > 0 && selected === outlets.length
        : selected === 0;
      button.setAttribute("aria-pressed", String(pressed));
    });
  }

  allButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.dataset.selectAll === "on";
      outlets.forEach((outlet) => { outlet.checked = checked; });
      synchronize();
    });
  });
  continents.forEach((continent) => {
    continent.addEventListener("change", () => {
      outlets.forEach((outlet) => {
        if (outlet.dataset.continent === continent.value) outlet.checked = continent.checked;
      });
      synchronize();
    });
  });
  outlets.forEach((outlet) => outlet.addEventListener("change", synchronize));
  form.addEventListener("submit", synchronize);
  // History navigation can restore checkbox values independently of the HTML.
  window.addEventListener("pageshow", () => synchronize({ preserveEmpty: true }));
  synchronize({ preserveEmpty: true });

  document.querySelectorAll(".country-flag").forEach((flag) => {
    flag.addEventListener("keydown", (event) => {
      if (event.key === "Escape") flag.dataset.dismissed = "true";
    });
    flag.addEventListener("mouseleave", () => { delete flag.dataset.dismissed; });
    flag.addEventListener("blur", () => { delete flag.dataset.dismissed; });
  });

  // Show a quiet reading cue only when the card actually has a scroll area.
  const readingPanes = [...document.querySelectorAll(".outlet-content")];
  function updateReadingCues() {
    readingPanes.forEach((pane) => {
      const scrollable = getComputedStyle(pane).overflowY === "auto"
        && pane.scrollHeight > pane.clientHeight + 1;
      pane.closest(".outlet-card").toggleAttribute("data-scrollable", scrollable);
    });
  }
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(updateReadingCues);
    readingPanes.forEach((pane) => observer.observe(pane));
  }
  readingPanes.forEach((pane) => pane.addEventListener("toggle", updateReadingCues, true));
  window.addEventListener("resize", updateReadingCues);
  updateReadingCues();
})();
