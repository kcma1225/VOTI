// Custom drop-downs. Each <select> stays in the page (hidden) so forms, labels and the server see no change;
// without JavaScript the native control simply remains. The list is appended to the nearest <dialog> or <body>
// so label clicks never reach it and a dialog's overflow never clips it.
(function () {
  "use strict";
  var open = null; // {close: fn}

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function labelFor(select) {
    var named = select.id && document.querySelector('label[for="' + select.id + '"]');
    var wrap = select.closest("label");
    var source = named || wrap;
    if (!source) return select.getAttribute("aria-label") || "";
    var clone = source.cloneNode(true);
    clone.querySelectorAll("select, .cs, .help").forEach(function (n) { n.remove(); });
    return clone.textContent.trim();
  }

  function enhance(select) {
    if (select.multiple || select.size > 1 || select.dataset.cs) return;
    select.dataset.cs = "1";
    var uid = "cs" + Math.random().toString(36).slice(2, 8);
    var root = el("span", "cs");
    var trigger = el("button", "cs-trigger");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    var name = labelFor(select);
    if (name) trigger.setAttribute("aria-label", name);
    var current = el("span", "cs-value");
    trigger.appendChild(current);
    trigger.appendChild(el("span", "cs-caret"));
    root.appendChild(trigger);
    select.parentNode.insertBefore(root, select); // before the select: a wrapping <label> then targets the button
    select.classList.add("cs-native");
    select.tabIndex = -1;

    var list = null, items = [], active = -1, typed = "", typedAt = 0;

    function sync() {
      var chosen = select.options[select.selectedIndex];
      current.textContent = chosen ? chosen.textContent : "";
      current.classList.toggle("is-empty", !!chosen && chosen.value === "");
      trigger.disabled = select.disabled;
    }

    function mark(index, scroll) {
      if (!items.length) return;
      active = Math.max(0, Math.min(items.length - 1, index));
      items.forEach(function (item, i) { item.classList.toggle("is-active", i === active); });
      list.setAttribute("aria-activedescendant", items[active].id);
      if (scroll !== false) items[active].scrollIntoView({block: "nearest"});
    }

    function choose(index) {
      var option = select.options[index];
      if (!option || option.disabled) return;
      var changed = select.selectedIndex !== index;
      select.selectedIndex = index;
      sync();
      close(true);
      if (changed) select.dispatchEvent(new Event("change", {bubbles: true}));
    }

    function place() {
      var box = trigger.getBoundingClientRect();
      var room = window.innerHeight - box.bottom - 8;
      var above = room < 180 && box.top > room;
      list.style.minWidth = box.width + "px";
      list.style.left = Math.max(8, Math.min(box.left, window.innerWidth - list.offsetWidth - 8)) + "px";
      list.style.maxHeight = Math.max(120, Math.min(320, (above ? box.top : room) - 8)) + "px";
      list.style.top = above ? Math.max(8, box.top - list.offsetHeight - 4) + "px" : box.bottom + 4 + "px";
    }

    function close(refocus) {
      if (!list) return;
      list.remove();
      list = null;
      items = [];
      open = null;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("mousedown", outside, true);
      window.removeEventListener("resize", dismiss);
      document.removeEventListener("scroll", dismiss, true);
      if (refocus) trigger.focus();
    }
    function dismiss(event) { if (!event || !list || !list.contains(event.target)) close(false); }
    function outside(event) { if (list && !list.contains(event.target) && !trigger.contains(event.target)) close(false); }

    function show() {
      if (open) open.close(false);
      list = el("ul", "cs-list");
      list.setAttribute("role", "listbox");
      list.tabIndex = -1;
      Array.prototype.forEach.call(select.options, function (option, i) {
        var item = el("li", "cs-option" + (option.value === "" ? " is-empty" : ""), option.textContent);
        item.id = uid + "-" + i;
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", i === select.selectedIndex ? "true" : "false");
        if (option.disabled) item.setAttribute("aria-disabled", "true");
        item.addEventListener("mousedown", function (e) { e.preventDefault(); });
        item.addEventListener("click", function () { choose(i); });
        item.addEventListener("mousemove", function () { if (active !== i) mark(i, false); });
        list.appendChild(item);
        items.push(item);
      });
      (select.closest("dialog") || document.body).appendChild(list);
      trigger.setAttribute("aria-expanded", "true");
      place();
      mark(Math.max(0, select.selectedIndex));
      list.addEventListener("keydown", onKey);
      list.focus({preventScroll: true});
      open = {close: close};
      document.addEventListener("mousedown", outside, true);
      window.addEventListener("resize", dismiss);
      document.addEventListener("scroll", dismiss, true);
    }

    function onKey(event) {
      var key = event.key;
      if (key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
      else if (key === "ArrowDown") { event.preventDefault(); mark(active + 1); }
      else if (key === "ArrowUp") { event.preventDefault(); mark(active - 1); }
      else if (key === "Home") { event.preventDefault(); mark(0); }
      else if (key === "End") { event.preventDefault(); mark(items.length - 1); }
      else if (key === "Enter" || key === " ") { event.preventDefault(); choose(active); }
      else if (key === "Tab") { close(false); }
      else if (key.length === 1 && !event.ctrlKey && !event.metaKey) {
        var now = Date.now();
        typed = now - typedAt > 700 ? key.toLowerCase() : typed + key.toLowerCase();
        typedAt = now;
        for (var i = 0; i < select.options.length; i++) {
          var at = (active + 1 + i) % select.options.length;
          if (select.options[at].textContent.trim().toLowerCase().indexOf(typed) === 0) { mark(at); break; }
        }
      }
    }

    trigger.addEventListener("click", function () { if (list) close(true); else show(); });
    trigger.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); }
    });
    select.addEventListener("change", sync);
    if (select.form) select.form.addEventListener("reset", function () { setTimeout(sync, 0); });
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select").forEach(enhance);
  });
})();
