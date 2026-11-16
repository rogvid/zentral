// Applied before first paint so the page never flashes the wrong theme.
// Kept as a separate same-origin file because the CSP forbids inline scripts.
(function () {
  try {
    var choice = localStorage.getItem("zentral.theme");
    if (choice === "light" || choice === "dark") {
      document.documentElement.dataset.theme = choice;
    }
  } catch (e) {
    /* storage unavailable: follow the system preference */
  }
})();
