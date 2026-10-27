// Live filtering: a GET form marked data-live refetches its own URL when a field changes and swaps in the
// regions marked data-live-region, so there is no "apply" button and the search box keeps focus while typing.
// Without JavaScript the form still submits normally (a <noscript> button is rendered next to it).
(function () {
  "use strict";

  function init(form) {
    var timer = null, controller = null, composing = false;

    function target() {
      var params = new URLSearchParams();
      new FormData(form).forEach(function (value, key) {
        if (typeof value === "string" && value !== "") params.append(key, value);
      });
      var query = params.toString();
      return form.getAttribute("action") + (query ? "?" + query : "");
    }

    // Other forms on the page (the review editor) keep the current filters so a save returns to the same list.
    function carry() {
      document.querySelectorAll("[data-live-carry]").forEach(function (box) {
        box.textContent = "";
        new FormData(form).forEach(function (value, key) {
          if (key === "segment" || typeof value !== "string" || value === "") return;
          var input = document.createElement("input");
          input.type = "hidden";
          input.name = key;
          input.value = value;
          box.appendChild(input);
        });
      });
    }

    function refresh() {
      clearTimeout(timer);
      if (controller) controller.abort();
      controller = new AbortController();
      var url = target();
      form.classList.add("is-loading");
      fetch(url, {signal: controller.signal, credentials: "same-origin", headers: {"X-Live": "1"}})
        .then(function (response) {
          if (!response.ok) throw new Error(String(response.status));
          return response.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          document.querySelectorAll("[data-live-region]").forEach(function (region) {
            var fresh = doc.querySelector('[data-live-region="' + region.getAttribute("data-live-region") + '"]');
            if (fresh) region.innerHTML = fresh.innerHTML;
          });
          try { history.replaceState(null, "", url); } catch (e) { /* ignore */ }
          carry();
          form.classList.remove("is-loading");
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return;
          location.href = url; // anything unexpected: fall back to a normal page load
        });
    }

    function later() {
      clearTimeout(timer);
      timer = setTimeout(refresh, 250);
    }

    form.addEventListener("input", function (event) {
      var field = event.target;
      if (!(field instanceof HTMLInputElement) || composing || event.isComposing) return;
      if (field.type === "text" || field.type === "search") later();
    });
    // Chinese input: wait until the composition is committed instead of searching on every pinyin/zhuyin letter.
    form.addEventListener("compositionstart", function () { composing = true; });
    form.addEventListener("compositionend", function () { composing = false; later(); });
    form.addEventListener("change", function (event) {
      var field = event.target;
      if (field instanceof HTMLInputElement && (field.type === "text" || field.type === "search")) return;
      refresh();
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); refresh(); });
    form.addEventListener("click", function (event) {
      var chip = event.target.closest("[data-live-set]");
      if (!chip || !form.contains(chip)) return;
      event.preventDefault();
      var pair = chip.getAttribute("data-live-set").split("=");
      if (form.elements[pair[0]]) form.elements[pair[0]].value = pair.slice(1).join("=");
      refresh();
    });
    carry();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-live]").forEach(init);
  });
})();
