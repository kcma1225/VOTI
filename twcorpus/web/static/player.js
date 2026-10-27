// Source page transcript: click a word or cue time to seek; highlight the current cue and word while playing.
(function () {
  "use strict";

  function num(node, name) {
    var v = node.getAttribute(name);
    return v === null || v === "" ? NaN : parseFloat(v);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var audio = document.querySelector("[data-player-audio]");
    var transcript = document.querySelector("[data-transcript]");
    if (!transcript) return;
    var follow = document.querySelector("[data-player-follow]");
    var cues = Array.prototype.slice.call(transcript.querySelectorAll(".cue")).map(function (node) {
      return {
        node: node, start: num(node, "data-start"), end: num(node, "data-end"),
        words: Array.prototype.slice.call(node.querySelectorAll(".w[data-start]")).map(function (w) {
          return { node: w, start: num(w, "data-start"), end: num(w, "data-end") };
        })
      };
    });
    var currentCue = null, currentWord = null;

    function seek(seconds) {
      if (!audio || !isFinite(seconds)) return;
      audio.currentTime = Math.max(0, seconds);
      var playing = audio.play();
      if (playing && playing.catch) playing.catch(function () { /* autoplay refused: position is still set */ });
    }

    transcript.addEventListener("click", function (e) {
      var target = e.target.closest(".w[data-start], [data-seek]");
      if (!target) return;
      seek(num(target, target.hasAttribute("data-seek") ? "data-seek" : "data-start"));
    });
    // Words are spans so the sentence reads naturally; keyboard users reach them as buttons.
    transcript.querySelectorAll(".w[data-start]").forEach(function (w) {
      w.setAttribute("role", "button");
      w.setAttribute("tabindex", audio ? "0" : "-1");
    });
    transcript.addEventListener("keydown", function (e) {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches(".w[data-start]")) {
        e.preventDefault();
        seek(num(e.target, "data-start"));
      }
    });
    if (!audio) return;

    function mark(previous, next, cls) {
      if (previous === next) return next;
      if (previous) { previous.classList.remove(cls); previous.removeAttribute("aria-current"); }
      if (next) { next.classList.add(cls); next.setAttribute("aria-current", "true"); }
      return next;
    }

    function within(node) {
      // Scroll only the transcript box, never the page under the reader.
      var box = transcript.getBoundingClientRect(), r = node.getBoundingClientRect();
      if (r.top < box.top + 8 || r.bottom > box.bottom - 8) {
        transcript.scrollTop += r.top - box.top - box.height / 3;
      }
    }

    function update() {
      var t = audio.currentTime, cue = null, word = null;
      for (var i = 0; i < cues.length; i++) {
        if (cues[i].start <= t && t < cues[i].end) { cue = cues[i]; break; }
      }
      if (cue) {
        for (var j = 0; j < cue.words.length; j++) {
          var w = cue.words[j];
          if (w.start <= t && t < w.end) { word = w; break; }
        }
      }
      var changed = (cue ? cue.node : null) !== currentCue;
      currentCue = mark(currentCue, cue ? cue.node : null, "playing");
      currentWord = mark(currentWord, word ? word.node : null, "playing");
      if (changed && currentCue && (!follow || follow.checked) && !audio.paused) within(currentCue);
    }

    audio.addEventListener("timeupdate", update);
    audio.addEventListener("seeked", update);
    // timeupdate fires ~4×/s; a frame loop keeps word highlighting in step while playing.
    var frame = null;
    function loop() { update(); frame = window.requestAnimationFrame(loop); }
    audio.addEventListener("play", function () { if (frame === null) frame = window.requestAnimationFrame(loop); });
    audio.addEventListener("pause", function () { if (frame !== null) window.cancelAnimationFrame(frame); frame = null; });
    audio.addEventListener("ended", function () { if (frame !== null) window.cancelAnimationFrame(frame); frame = null; });
  });
})();
