// Applies the saved theme before first paint (external file so the CSP needs no 'unsafe-inline').
(function () {
  try {
    var t = localStorage.getItem('voti-theme') || 'system'
    var dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    var d = document.documentElement
    d.classList.toggle('dark', dark)
    d.dataset.theme = dark ? 'dark' : 'light'
  } catch (e) { /* storage blocked: fall back to light */ }
})()
