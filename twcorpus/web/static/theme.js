// Loaded in <head> so the stored theme applies before first paint (no flash of the wrong palette).
(function () {
  var root = document.documentElement;
  var stored = null;
  try { stored = localStorage.getItem("theme"); } catch (e) { /* storage blocked: follow system */ }
  if (stored === "light" || stored === "dark") root.setAttribute("data-theme", stored);

  function current() {
    var set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function sync(button) {
    var dark = current() === "dark";
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.setAttribute("title", dark ? "切換為淺色" : "切換為深色");
    button.querySelector("[data-theme-label]").textContent = dark ? "深色" : "淺色";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll("[data-theme-toggle]");
    buttons.forEach(function (button) {
      sync(button);
      button.addEventListener("click", function () {
        var next = current() === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try { localStorage.setItem("theme", next); } catch (e) { /* ignore */ }
        buttons.forEach(sync);
      });
    });
    // Ask before destructive-looking or long-running submits.
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
      });
    });
    // Disable submit buttons while a slow request (upload, export) is in flight.
    document.querySelectorAll("form[data-busy]").forEach(function (form) {
      form.addEventListener("submit", function () {
        form.querySelectorAll("button[type=submit]").forEach(function (b) {
          setTimeout(function () { b.disabled = true; }, 0);
        });
        form.classList.add("is-busy");
      });
    });
    // Import dialogs on the source list (<dialog> keeps focus trapped and closes on Esc).
    document.querySelectorAll("[data-dialog-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        var dialog = document.getElementById(button.getAttribute("data-dialog-open"));
        if (dialog && dialog.showModal) dialog.showModal();
      });
    });
    document.querySelectorAll("dialog").forEach(function (dialog) {
      dialog.querySelectorAll("[data-dialog-close]").forEach(function (b) {
        b.addEventListener("click", function () { dialog.close(); });
      });
      dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
      if (dialog.hasAttribute("data-open-on-load") && dialog.showModal) dialog.showModal();
    });
    // Candidate list: select all / none.
    document.querySelectorAll("[data-check-all]").forEach(function (box) {
      box.addEventListener("change", function () {
        var form = box.closest("form");
        form.querySelectorAll("input[name=url]").forEach(function (c) { c.checked = box.checked; });
      });
    });
  });
})();
