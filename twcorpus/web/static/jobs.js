// Background-job side panel and completion toasts. Polls /api/jobs; all job text is set with textContent.
(function () {
  "use strict";
  var ACTIVE = { queued: 1, running: 1 };
  var DONE_TONE = { completed: "ok", failed: "bad", blocked: "warn", cancelled: "muted", interrupted: "warn" };
  var FAST = 1500, SLOW = 8000;
  var panel, list, empty, workerLine, counts, toggles, toasts;
  var timer = null, lastBody = "", lastData = null, opener = null;

  function store(kind) {
    try { return window[kind]; } catch (e) { return null; }
  }
  function load(key, fallback) {
    var s = store("sessionStorage");
    try { var v = s && s.getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; }
  }
  function save(key, value) {
    var s = store("sessionStorage");
    try { if (s) s.setItem(key, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function span(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + " 秒";
    var m = Math.floor(seconds / 60), s = seconds % 60;
    if (m < 60) return m + " 分" + (s && m < 10 ? " " + s + " 秒" : "");
    var h = Math.floor(m / 60);
    return h + " 小時" + (m % 60 ? " " + (m % 60) + " 分" : "");
  }
  // Server timestamps are UTC ISO strings; show them at a fixed UTC+8 regardless of the browser's zone.
  function stamp(iso) {
    var t = Date.parse(iso || "");
    if (isNaN(t)) return "";
    return new Date(t + 8 * 3600 * 1000).toISOString().slice(0, 19).replace("T", " ");
  }
  function clockAt(secondsFromNow) {
    var d = new Date(Date.now() + secondsFromNow * 1000);
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  // ---------- panel ----------
  function isOpen() { return panel && !panel.hidden; }
  function setOpen(open, focus) {
    if (!panel) return;
    panel.hidden = !open;
    document.documentElement.classList.toggle("jobs-open", open);
    toggles.forEach(function (b) { b.setAttribute("aria-expanded", open ? "true" : "false"); });
    save("jobs-panel-open", open);
    if (open && focus) panel.querySelector("[data-jobs-close]").focus();
    if (!open && opener && focus) opener.focus();
    schedule(0);
  }

  // ---------- rendering ----------
  function jobItem(job) {
    var li = el("li", "job tone-" + job.tone);
    li.setAttribute("data-job", job.id);
    var top = el("div", "job-top");
    top.appendChild(el("strong", null, job.kind_label));
    top.appendChild(el("span", "badge " + job.tone, job.cancel_requested && ACTIVE[job.status] ? "取消中" : job.status_label));
    li.appendChild(top);
    if (job.source_id) {
      var a = el("a", "job-source", job.source_title || "來源 " + job.source_id.slice(0, 8));
      a.href = "/sources/" + job.source_id;
      li.appendChild(a);
    }
    if (job.status === "running") {
      var bar = el("progress", "bar");
      bar.max = 100;
      if (job.percent !== null) bar.value = job.percent; // no value = indeterminate
      bar.setAttribute("aria-label", job.kind_label + " 進度");
      li.appendChild(bar);
      var meta = el("div", "job-meta");
      meta.appendChild(el("span", null, job.phase_label + (job.percent !== null ? " · " + Math.floor(job.percent) + "%" : "")));
      var timing = [];
      if (job.elapsed_sec !== null) timing.push("已執行 " + span(job.elapsed_sec));
      if (job.eta_sec !== null) {
        timing.push(job.eta_sec < 5 ? "即將完成" : "預計剩餘約 " + span(job.eta_sec) + "（" + clockAt(job.eta_sec) + " 前後）");
      } else {
        timing.push("估算完成時間中…");
      }
      meta.appendChild(el("span", null, timing.join(" · ")));
      li.appendChild(meta);
    } else if (job.status === "queued") {
      li.appendChild(el("div", "job-meta", "排隊中；前一項完成後自動開始"));
    } else {
      li.appendChild(el("div", "job-meta", stamp(job.updated_at) + " (UTC+8)"));
    }
    if (job.error_message && job.status !== "running") li.appendChild(el("p", "job-error", job.error_message));
    var actions = el("div", "job-actions");
    if (ACTIVE[job.status] && !job.cancel_requested) actions.appendChild(actionButton(job, "cancel", "取消"));
    if (job.status === "failed" || job.status === "blocked" || job.status === "cancelled" || job.status === "interrupted") {
      actions.appendChild(actionButton(job, "retry", "重試"));
    }
    if (actions.childNodes.length) li.appendChild(actions);
    return li;
  }

  function actionButton(job, action, label) {
    var b = el("button", "btn small" + (action === "cancel" ? " danger" : ""), label);
    b.type = "button";
    b.setAttribute("data-job-action", action);
    b.setAttribute("data-job-id", job.id);
    return b;
  }

  function render(data) {
    var body = JSON.stringify(data.jobs);
    if (body !== lastBody) {
      var focused = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-job-action]");
      var refocus = focused ? [focused.getAttribute("data-job-id"), focused.getAttribute("data-job-action")] : null;
      list.replaceChildren.apply(list, data.jobs.map(jobItem));
      empty.hidden = data.jobs.length > 0;
      lastBody = body;
      if (refocus) {
        var again = list.querySelector('[data-job-id="' + refocus[0] + '"][data-job-action="' + refocus[1] + '"]');
        (again || panel.querySelector("[data-jobs-close]")).focus();
      }
    }
    counts.forEach(function (c) {
      c.hidden = !data.active;
      c.textContent = data.active;
    });
    toggles.forEach(function (b) { b.classList.toggle("busy", data.active > 0); });
    var age = data.worker_age_sec;
    workerLine.classList.remove("warn");
    if (age === null || age === undefined) {
      workerLine.textContent = "未偵測到背景工作程序；工作會等它啟動後執行";
      workerLine.classList.add("warn");
    } else if (age > 60) {
      workerLine.textContent = "背景工作程序 " + span(age) + "無回應";
      workerLine.classList.add("warn");
    } else {
      workerLine.textContent = "背景工作程序運作中 · 進行中 " + data.active + " 項";
    }
  }

  // ---------- toasts on finished jobs ----------
  function onThisSource(job) {
    var base = "/sources/" + job.source_id;
    return !!job.source_id && (location.pathname === base || location.pathname.indexOf(base + "/") === 0);
  }
  function notify(data) {
    var seen = load("jobs-seen", null);
    var next = {};
    data.jobs.forEach(function (job) {
      next[job.id] = job.status;
      if (!seen) return; // first load in this tab: remember, do not announce history
      var was = seen[job.id];
      if (was && ACTIVE[was] && !ACTIVE[job.status] && DONE_TONE[job.status]) toast(job);
    });
    save("jobs-seen", next);
  }

  function toast(job) {
    var tone = DONE_TONE[job.status];
    var box = el("div", "toast " + tone);
    var text = el("div", "toast-text");
    text.appendChild(el("strong", null, job.kind_label + " " + job.status_label));
    if (job.source_title) text.appendChild(el("span", null, job.source_title));
    if (job.error_message) text.appendChild(el("span", "muted", job.error_message.slice(0, 160)));
    box.appendChild(text);
    var actions = el("div", "toast-actions");
    if (job.status === "completed" && (onThisSource(job) || job.kind === "youtube_inspect" && location.pathname === "/sources")) {
      var reload = el("button", "btn small primary", "重新整理");
      reload.type = "button";
      reload.addEventListener("click", function () { location.reload(); });
      actions.appendChild(reload);
    } else if (job.source_id && !onThisSource(job)) {
      var go = el("a", "btn small", "查看來源");
      go.href = "/sources/" + job.source_id;
      actions.appendChild(go);
    }
    var close = el("button", "btn small ghost", "✕");
    close.type = "button";
    close.setAttribute("aria-label", "關閉通知");
    close.addEventListener("click", function () { box.remove(); });
    actions.appendChild(close);
    box.appendChild(actions);
    toasts.appendChild(box);
    while (toasts.children.length > 4) toasts.firstChild.remove();
    setTimeout(function () { box.remove(); }, tone === "ok" ? 10000 : 20000);
  }

  // ---------- polling ----------
  function schedule(delay) {
    clearTimeout(timer);
    if (document.hidden) return;
    if (delay === undefined) delay = isOpen() || (lastData && lastData.active) ? FAST : SLOW;
    timer = setTimeout(poll, delay);
  }

  function poll() {
    fetch("/api/jobs", { headers: { Accept: "application/json" }, cache: "no-store", credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) { lastData = data; render(data); notify(data); })
      .catch(function () { workerLine.textContent = "暫時無法讀取工作狀態，稍後重試"; workerLine.classList.add("warn"); })
      .then(function () { schedule(); });
  }

  function act(button) {
    var id = button.getAttribute("data-job-id"), action = button.getAttribute("data-job-action");
    if (!/^[0-9a-f]+$/.test(id) || (action !== "retry" && action !== "cancel")) return;
    button.disabled = true;
    fetch("/api/jobs/" + id + "/" + action, { method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json" } })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "操作失敗");
      })
      .catch(function (e) {
        button.disabled = false;
        toast({ kind_label: action === "retry" ? "重試" : "取消", status_label: "未完成", status: "failed",
                error_message: e.message || String(e) });
      })
      .then(function () { schedule(0); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    panel = document.getElementById("jobs-panel");
    if (!panel) return;
    list = panel.querySelector("[data-jobs-list]");
    empty = panel.querySelector("[data-jobs-empty]");
    workerLine = panel.querySelector("[data-jobs-worker]");
    toasts = document.querySelector("[data-toasts]");
    counts = Array.prototype.slice.call(document.querySelectorAll("[data-jobs-count]"));
    toggles = Array.prototype.slice.call(document.querySelectorAll("[data-jobs-toggle]"));

    toggles.forEach(function (b) {
      b.addEventListener("click", function () { opener = b; setOpen(!isOpen(), true); });
    });
    panel.querySelector("[data-jobs-close]").addEventListener("click", function () { setOpen(false, true); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) setOpen(false, true);
    });
    list.addEventListener("click", function (e) {
      var button = e.target.closest("[data-job-action]");
      if (button) act(button);
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) schedule(0); });

    // ?jobs=open (set after enqueueing) or the panel was left open on the previous page.
    var params = new URLSearchParams(location.search);
    // On phones the panel is a bottom sheet over the page, so it is not reopened from storage there.
    var phone = window.matchMedia && window.matchMedia("(max-width: 560px)").matches;
    var open = params.get("jobs") === "open" || (!phone && load("jobs-panel-open", false));
    if (params.has("jobs")) {
      params.delete("jobs");
      var query = params.toString();
      history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
    }
    setOpen(open, false);
  });
})();
