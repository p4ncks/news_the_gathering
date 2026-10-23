/* Continents select their outlets; individual outlets set the continent state.
   Apply commits the choices, keeping the results URL easy to share. */
(() => {
  const form = document.querySelector(".filter-form");
  if (!form) return;

  const continents = [...form.querySelectorAll('input[name="continent"]')];
  const outlets = [...form.querySelectorAll('input[name="outlet"]')];
  const selectionCount = form.querySelector("[data-selection-count]");
  const note = form.querySelector("[data-filter-note]");
  const counts = [...form.querySelectorAll("[data-group-count]")];
  const allButtons = [...form.querySelectorAll("[data-select-all]")];
  const emptyOutlets = form.querySelector("[data-empty-outlets]");

  function synchronize() {
    continents.forEach((continent) => {
      const members = outlets.filter((outlet) => outlet.dataset.continent === continent.value);
      const selected = members.filter((outlet) => outlet.checked).length;
      // A partial continent stays checked so GET submission includes it.
      continent.checked = selected > 0;
      continent.indeterminate = selected > 0 && selected < members.length;
      continent.setAttribute("aria-checked", continent.indeterminate ? "mixed" : String(continent.checked));
      members.forEach((outlet) => {
        outlet.closest(".outlet-option").hidden = !continent.checked;
      });
      const count = counts.find((item) => item.dataset.groupCount === continent.value);
      if (count) count.textContent = `${selected}/${members.length}`;
    });
    const selected = outlets.filter((outlet) => outlet.checked).length;
    if (selectionCount) selectionCount.textContent = `${selected} outlet${selected === 1 ? "" : "s"} selected`;
    if (emptyOutlets) emptyOutlets.hidden = selected > 0;
    allButtons.forEach((button) => {
      const pressed = button.dataset.selectAll === "on"
        ? outlets.length > 0 && selected === outlets.length
        : selected === 0;
      button.setAttribute("aria-pressed", String(pressed));
    });
  }

  function markSelectionChanged() {
    if (note) note.textContent = "Your selection has changed. Apply it to update the desk.";
  }

  allButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.dataset.selectAll === "on";
      outlets.forEach((outlet) => { outlet.checked = checked; });
      synchronize();
      markSelectionChanged();
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
  form.addEventListener("change", markSelectionChanged);
  form.addEventListener("submit", synchronize);
  // History navigation can restore checkbox values independently of the HTML.
  window.addEventListener("pageshow", synchronize);
  synchronize();
})();
