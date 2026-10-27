// Applies the saved theme before first paint (external file so the CSP needs no 'unsafe-inline').
(function () {
  try {
    var t = localStorage.getItem('voti-theme') || 'system'
    var dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    var d = document.documentElement
    d.classList.toggle('dark', dark)
    d.dataset.theme = dark ? 'dark' : 'light'
  } catch (e) { /* storage blocked: fall back to light */ }
  // UI language before first paint (same rule as src/i18n/lang.ts): saved choice, else any zh* → zh-TW, else en.
  try {
    var l = null
    try { l = localStorage.getItem('voti-lang') } catch (e) { /* ignore */ }
    if (l !== 'zh-TW' && l !== 'en') {
      var prefs = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language || '']
      l = String(prefs[0] || '').toLowerCase().indexOf('zh') === 0 ? 'zh-TW' : 'en'
    }
    document.documentElement.lang = l === 'en' ? 'en' : 'zh-Hant-TW'
    if (l === 'en') document.title = 'Voice of the Island'
    else document.title = '島嶼之聲'
  } catch (e) { /* ignore */ }
})()
