#!/usr/bin/env node
/* Visual acceptance + horizontal-overflow check against the mock build.
 *
 *   npm run build:mock && npm run preview:mock &      # serves dist-mock on :4173
 *   npm run shots                                      # screenshots + overflow check
 *   npm run check:overflow                             # overflow check only (no files written)
 *
 * Live site: BASE_URL=http://127.0.0.1:8080 VOTI_USER=admin VOTI_PASS=… npm run shots (logs in via the API).
 * Env: BASE_URL (default http://127.0.0.1:4173), OUT (default ../screenshots), ONLY=regex on screen name,
 *      VIEWPORTS=comma list of names, THEMES=light,dark, CHROMIUM=/path/to/chrome,
 *      LANG=zh-TW,en (UI language(s): written to localStorage 'voti-lang' before load; part of the file name)
 */
import { chromium } from 'playwright'
import { mkdirSync, existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:4173'
const OUT = resolve(process.env.OUT ?? join(here, '..', '..', 'screenshots'))
const CHECK_ONLY = process.argv.includes('--check-only')
const ONLY = process.env.ONLY ? new RegExp(process.env.ONLY) : null

const ALL_VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  laptop: { width: 1024, height: 768 },
  tablet: { width: 768, height: 1024 },
  phone: { width: 390, height: 844 },
}
const viewports = (process.env.VIEWPORTS ?? Object.keys(ALL_VIEWPORTS).join(',')).split(',').map((n) => [n, ALL_VIEWPORTS[n]])
const themes = (process.env.THEMES ?? 'light,dark').split(',')
// NB: the shell's own $LANG (e.g. en_US.UTF-8) is ignored — only our two UI language codes count.
const langs = (process.env.LANG ?? '').split(',').filter((l) => l === 'zh-TW' || l === 'en')
if (!langs.length) langs.push('zh-TW', 'en')
// accessible names used by screen actions, per UI language (must match src/i18n/messages/shell.ts)
const A11Y = {
  'zh-TW': { jobs: '工作佇列', menu: '開啟選單' },
  en: { jobs: 'Job queue', menu: 'Open menu' },
}

// playwright@1.63.0 ↔ chromium build 1243 (uses ~/.cache/ms-playwright/chromium_headless_shell-1243 by default).
function findChromium() {
  if (process.env.CHROMIUM) return process.env.CHROMIUM
  const p = join(homedir(), '.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell')
  return existsSync(p) ? p : undefined
}

const USER = process.env.VOTI_USER
const PASS = process.env.VOTI_PASS

async function login(ctx) {
  if (!USER) return
  const r = await ctx.request.post(`${BASE}/api/auth/login`, { data: { username: USER, password: PASS }, headers: { Origin: BASE } })
  if (!r.ok()) throw new Error(`login failed: ${r.status()}`)
}

async function discover(page) {
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  return page.evaluate(async () => {
    const j = (u) => fetch(u).then((r) => r.json())
    const ch = (await j('/api/channels')).items
    const listed = ch.filter((c) => c.list_status === 'listed')
    const media = (await j(`/api/media?channel_id=${listed[0]?.id}&status=ready&limit=5`)).items ?? []
    const failed = (await j('/api/media?status=failed,blocked&limit=1')).items ?? []
    const sp = (await j('/api/speakers?limit=3')).items ?? []
    const ds = await j('/api/datasets')
    const seg = (await j('/api/segments?review_status=unchecked&limit=1')).items ?? []
    // a processed video without word timings (sentence-level highlight fallback)
    let unaligned
    for (const m of (await j('/api/media?status=ready&limit=60')).items ?? []) {
      const w = await fetch(`/api/media/${m.id}/words`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      const words = w && typeof w.words === 'object' ? w.words : w
      if (words && !Object.keys(words).length) {
        unaligned = m.id
        break
      }
    }
    // caption validation: an English-speech segment with Chinese captions (language mismatch)
    const inv = (await j('/api/segments?validity=invalid&status=all&limit=40')).items ?? []
    const invalidSeg = (inv.find((s) => s.invalid_reason === 'language_mismatch') ?? inv[0])?.id
    // uploader captions that turned out empty
    const emptyCap = ((await j('/api/media?captions=empty&include_auto=true&limit=1')).items ?? [])[0]?.id
    // hard-subtitle OCR: a segment whose text was read from burned-in subtitles (and its media)
    const ocrSegs = (await j('/api/segments?text_source=ocr_caption&limit=20')).items ?? []
    const ocrSeg = (ocrSegs[2] ?? ocrSegs[0])?.id
    const ocrMedia = ocrSegs[0]?.media_id
    return {
      invalidSeg, emptyCap, ocrSeg, ocrMedia,
      ch: listed[0]?.id, ch2: listed[1]?.id, media: media[0]?.id, failed: failed[0]?.id, speaker: (sp[1] ?? sp[0])?.id, dataset: (ds.items ?? ds)[0]?.id,
      queued: ch.find((c) => c.list_status === 'queued' || c.list_status === 'new')?.id, listing: ch.find((c) => c.list_status === 'listing' && !c.last_listed_at)?.id,
      segment: seg[0]?.id, unaligned,
    }
  })
}

function screens(ids, lang) {
  const a = A11Y[lang]
  return [
    { name: '01-login', path: '/login' },
    { name: '02-overview', path: '/' },
    { name: '03-add-source', path: '/sources/new', act: async (p) => { await p.fill('input[name="url"]', 'https://www.youtube.com/@amaskitchen/videos') } },
    { name: '04-channels', path: '/channels' },
    { name: '05-channel-videos', path: `/channels/${ids.ch}` },
    { name: '06-channel-videos-selected', path: `/channels/${ids.ch}`, act: async (p) => { await p.locator('[data-shot="select-all-page"]:visible').first().click() } },
    { name: '07-channel-download-dialog', path: `/channels/${ids.ch}`, act: async (p) => { await p.locator('[data-shot="select-all-page"]:visible').first().click(); await p.locator('[data-shot="select-all-matching"]').first().click(); await p.locator('[data-shot="open-download"]').first().click() } },
    { name: '08-channel-shorts', path: `/channels/${ids.ch}/shorts` },
    { name: '09-channel-playlists', path: `/channels/${ids.ch}/playlists` },
    { name: '10-channel-repeated', path: `/channels/${ids.ch}/repeated` },
    { name: '11-channel-speakers', path: `/channels/${ids.ch}/speakers` },
    { name: '12-channel-quality', path: `/channels/${ids.ch}/quality` },
    { name: '13-media-detail', path: `/media/${ids.media}` },
    { name: '13b-media-karaoke', path: `/media/${ids.media}`, act: playAndFreeze(0.45) },
    { name: '13c-media-inline-edit', path: `/media/${ids.media}`, act: async (p) => { const row = p.locator('[data-seg-row]').nth(1); await row.scrollIntoViewIfNeeded(); await row.focus(); await p.keyboard.press('e'); await p.waitForTimeout(200); await centerOn(p, '[data-seg-row]:nth-child(2)') } },
    { name: '13d-media-unaligned', path: `/media/${ids.unaligned}`, act: playAndFreeze(0.5) },
    { name: '14-media-failed', path: `/media/${ids.failed}` },
    { name: '15-review', path: '/review' },
    { name: '15b-review-filtered', path: '/review?status=all&flags=low_snr&sort=snr' },
    { name: '15c-review-detail', path: `/review/${ids.segment}` },
    { name: '15d-review-karaoke', path: `/review/${ids.segment}`, act: async (p) => { await p.waitForFunction(() => window.__votiSegAudio?.freeze(0.5), null, { timeout: 8000 }); await p.waitForTimeout(250) } },
    { name: '16-speakers', path: '/speakers' },
    { name: '17-speaker-detail', path: `/speakers/${ids.speaker}` },
    { name: '18-diversity', path: '/diversity' },
    { name: '19-quality', path: '/quality' },
    { name: '20-datasets', path: '/datasets' },
    { name: '21-dataset-new', path: '/datasets/new' },
    { name: '22-dataset-detail', path: `/datasets/${ids.dataset}` },
    { name: '23-jobs', path: '/jobs' },
    { name: '24-jobs-drawer', path: '/', act: async (p, vp) => { await p.locator(vp === 'phone' ? `header [aria-label="${a.jobs}"]` : `aside [aria-label="${a.jobs}"]`).first().click() } },
    { name: '25-settings-users', path: '/settings/users' },
    { name: '26-settings-youtube', path: '/settings/youtube' },
    { name: '27-settings-pipeline', path: '/settings/pipeline' },
    { name: '28-settings-account', path: '/settings/account' },
    { name: '29-not-found', path: '/nope' },
    { name: '30-phone-menu', path: '/', phoneOnly: true, act: async (p) => { await p.locator(`header [aria-label="${a.menu}"]`).click() } },
    { name: '31-channel-queued', path: `/channels/${ids.queued}` },
    { name: '32-channel-listing', path: `/channels/${ids.listing}` },
    // caption validation (invalid segments)
    { name: '40-validate-media-header', path: `/media/${ids.media}`, act: async (p) => { await p.waitForSelector('[data-invalid]'); await p.evaluate(() => window.scrollTo(0, 0)) } },
    { name: '41-validate-media-rows', path: `/media/${ids.media}`, act: async (p) => { await p.waitForSelector('[data-invalid]'); await centerOn(p, '[data-invalid]') } },
    { name: '42-validate-media-only-invalid', path: `/media/${ids.media}`, act: async (p) => { await p.locator('[data-shot="only-invalid"]').first().click(); await p.waitForTimeout(300); await centerOn(p, '[data-shot="only-invalid"]') } },
    { name: '43-validate-review-detail', path: `/review/${ids.invalidSeg}?status=all` },
    { name: '44-validate-review-list', path: '/review?status=all&validity=invalid' },
    { name: '45-validate-dataset-preview', path: '/datasets/new' },
    { name: '46-validate-settings', path: '/settings/pipeline', act: async (p) => { await centerOn(p, '[data-shot="validation-card"]') } },
    // speakers on the media page, speaker colours everywhere, click-a-character-to-seek, status badges
    { name: '50-media-speakers', path: `/media/${ids.media}`, act: async (p) => { await p.waitForSelector('[data-speaker-card]'); await centerOn(p, '[data-shot="media-speakers"]') } },
    { name: '51-media-speaker-detail', path: `/media/${ids.media}`, act: async (p) => { await p.locator('[data-speaker-card]').first().click(); await p.waitForSelector('[data-speaker-dialog]'); await p.waitForTimeout(400) } },
    { name: '52-media-karaoke-speaker2', path: `/media/${ids.media}`, act: playAndFreeze(0.5, '[data-seg-row][data-speaker="1"]') },
    { name: '53-media-seek-char', path: `/media/${ids.media}`, act: async (p) => { const row = p.locator('[data-seg-row]').nth(2); await row.scrollIntoViewIfNeeded(); await row.locator('[data-seek]').nth(5).click(); await p.waitForTimeout(900); if (!(await p.evaluate(() => window.__votiPlayer?.freeze(0.3)))) throw new Error('player hook unavailable'); await centerOn(p, '[data-seg-row].hl-row-active') } },
    { name: '54-review-list-playing', path: '/review?status=all', act: async (p) => { const li = p.locator('li[id^="rv-"]').nth(1); await li.locator('[data-seek]').nth(3).click(); await p.waitForSelector('li.hl-row-active [data-k]', { timeout: 8000 }); await p.waitForTimeout(600); if (!(await p.evaluate(() => window.__votiPlayer?.freeze(0.4)))) throw new Error('player hook unavailable'); await centerOn(p, 'li.hl-row-active') } },
    { name: '55-channel-status-badges', path: `/channels/${ids.ch}`, act: async (p) => { await p.waitForSelector('[data-status="members_only"]', { state: 'attached' }); await p.evaluate(() => [...document.querySelectorAll('[data-status="members_only"]')].find((e) => e.offsetParent)?.scrollIntoView({ block: 'center' })); await p.waitForTimeout(150) } },
    { name: '56-media-empty-captions', path: `/media/${ids.emptyCap}` },
    { name: '57-channel-filter-empty-captions', path: `/channels/${ids.ch}?cap=empty` },
    // Shift+click range selection: anchor on row 2, Shift held while hovering row 9 (preview + floating hint)
    { name: '58-channel-shift-range-preview', path: `/channels/${ids.ch}`, act: async (p) => {
      const boxes = p.locator('[data-preview], tbody td button[role="checkbox"]:visible, li button[role="checkbox"]:visible')
      await p.locator('button[role="checkbox"]:visible').nth(2).click()
      const target = p.locator('button[role="checkbox"]:visible').nth(9)
      await target.scrollIntoViewIfNeeded()
      const b = await target.boundingBox()
      await p.mouse.move(b.x + 200, b.y + b.height / 2)
      await p.keyboard.down('Shift')
      await p.mouse.move(b.x + 220, b.y + b.height / 2 + 2)
      await p.waitForSelector('[data-shot="range-hint"]', { timeout: 4000 })
      void boxes
    } },
    // hard-subtitle OCR
    { name: '60-ocr-media-header', path: `/media/${ids.ocrMedia}`, act: async (p) => { await p.waitForSelector('[data-shot="ocr-header-badge"]'); await p.evaluate(() => window.scrollTo(0, 0)) } },
    { name: '61-ocr-media-panel', path: `/media/${ids.ocrMedia}`, act: async (p) => { await p.waitForSelector('[data-ocr-event]'); await scrollTop(p, '[data-shot="media-ocr"]') } },
    { name: '62-ocr-media-events', path: `/media/${ids.ocrMedia}`, act: async (p) => { await p.waitForSelector('[data-ocr-event]'); await waitImages(p); await centerOn(p, '[data-ocr-event]:nth-child(6)') } },
    { name: '63-ocr-media-reason-filter', path: `/media/${ids.ocrMedia}`, act: async (p) => { await p.locator('[data-ocr-reason]').first().click(); await p.waitForTimeout(300); await waitImages(p); await scrollTop(p, '[data-ocr-reason]') } },
    { name: '64-ocr-media-playing', path: `/media/${ids.ocrMedia}`, act: async (p) => { const row = p.locator('[data-ocr-event]').nth(3); await row.scrollIntoViewIfNeeded(); await row.click(); await p.waitForTimeout(1200); if (!(await p.evaluate(() => window.__votiPlayer?.freeze(0.45)))) throw new Error('player hook unavailable'); await waitImages(p); await centerOn(p, '[data-ocr-event].hl-row-active') } },
    { name: '65-ocr-rerun-confirm', path: `/media/${ids.ocrMedia}`, act: async (p) => { await p.locator('[data-shot="ocr-run"]').first().click(); await p.waitForSelector('[role="dialog"]') } },
    { name: '66-ocr-start-card', path: `/media/${ids.media}`, act: async (p) => { await p.waitForSelector('[data-shot="media-ocr-start"]'); await centerOn(p, '[data-shot="media-ocr-start"]') } },
    { name: '67-ocr-review-detail', path: `/review/${ids.ocrSeg}?status=all&source=ocr_caption`, act: async (p, vp) => { await p.waitForSelector('[data-shot="review-ocr-crops"] img'); await waitImages(p); if (vp === 'phone') await centerOn(p, '[data-shot="review-ocr-crops"]') } },
    { name: '68-ocr-review-list', path: '/review?status=all&source=ocr_caption' },
    { name: '69-ocr-download-dialog', path: `/channels/${ids.ch}`, act: async (p) => { await p.locator('[data-shot="select-all-page"]:visible').first().click(); await p.locator('[data-shot="open-download"]').first().click(); await p.waitForSelector('[data-shot="dl-ocr"]'); await p.evaluate(() => document.querySelector('[data-shot="dl-ocr"]')?.scrollIntoView({ block: 'end' })) } },
    { name: '70-ocr-settings', path: '/settings/pipeline', act: async (p) => { await scrollTop(p, '[data-shot="ocr-settings-card"]') } },
    { name: '71-ocr-dataset-off', path: '/datasets/new', act: async (p) => { await p.waitForTimeout(600); await centerOn(p, '[data-shot="dataset-ocr-source"]') } },
    { name: '72-ocr-dataset-on', path: '/datasets/new', act: async (p) => { await p.locator('[data-shot="dataset-ocr-source"] button[role="switch"]').click(); await p.waitForTimeout(900); await centerOn(p, '[data-shot="dataset-ocr-source"]') } },
    // YouTube Data API keys (Settings → YouTube) + two-stage caption info
    { name: '75-ytapi-keys', path: '/settings/youtube', act: async (p) => { await p.waitForSelector('[data-shot="ytapi-card"] [data-key-status]'); await scrollTop(p, '[data-shot="ytapi-card"]') } },
    { name: '76-ytapi-backfill-how', path: '/settings/youtube', act: async (p) => { await p.locator('[data-shot="ytapi-card"] details summary').click(); await p.waitForTimeout(150); await centerOn(p, '[data-shot="ytapi-card"] details') } },
    { name: '77-ytapi-remove-confirm', path: '/settings/youtube', act: async (p) => { await p.locator('[data-shot="ytapi-card"] [data-key-status] button').nth(1).click(); await p.waitForSelector('[role="dialog"]') } },
    { name: '78-ytapi-dl-langs-unknown', path: `/channels/${ids.ch}`, act: async (p) => { await p.locator('[data-shot="select-all-page"]:visible').first().click(); await p.locator('[data-shot="select-all-matching"]').first().click(); await p.locator('[data-shot="open-download"]').first().click(); await p.waitForSelector('[data-langs-unknown]'); await p.evaluate(() => document.querySelector('[data-langs-unknown]')?.scrollIntoView({ block: 'center' })) } },
    { name: '33-add-video-new-channel', path: '/sources/new', act: async (p) => { await p.fill('input[name="url"]', 'https://youtu.be/dQw4w9WgXcQ'); await p.locator('form button[type="submit"]').click(); await p.waitForTimeout(1600) } },
  ]
}

async function scrollTop(p, sel) {
  await p.evaluate((s) => { const el = document.querySelector(s); if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 16) }, sel)
  await p.waitForTimeout(150)
}

/** wait until every <img> in the viewport has loaded (OCR crops) */
async function waitImages(p) {
  await p.waitForFunction(() => [...document.images].every((i) => i.complete), null, { timeout: 8000 }).catch(() => {})
}

async function centerOn(p, sel) {
  await p.evaluate((s) => document.querySelector(s)?.scrollIntoView({ block: 'center' }), sel)
  await p.waitForTimeout(150)
}

/** Play a transcript sentence from its start (its play button; a click on a character would seek), let it play
 * briefly, then freeze it mid-sentence (mock player hook). */
function playAndFreeze(frac, sel = '[data-seg-row]') {
  return async (p) => {
    const row = p.locator(sel).first()
    await row.scrollIntoViewIfNeeded()
    await row.locator('button').first().click()
    await p.waitForTimeout(900)
    const ok = await p.evaluate((f) => window.__votiPlayer?.freeze(f), frac)
    if (!ok) throw new Error('player hook unavailable')
    await centerOn(p, sel === '[data-seg-row]' ? '[data-seg-row]' : '[data-seg-row].hl-row-active')
  }
}

const browser = await chromium.launch({ executablePath: findChromium() })
if (!CHECK_ONLY) mkdirSync(OUT, { recursive: true })
const failures = []
let count = 0
try {
  const probeCtx = await browser.newContext()
  await login(probeCtx)
  const probe = await probeCtx.newPage()
  const ids = await discover(probe)
  await probeCtx.close()
  for (const lang of langs) {
  for (const theme of themes) {
    for (const [vpName, vp] of viewports) {
      const ctx = await browser.newContext({ locale: lang === 'en' ? 'en-US' : 'zh-TW', timezoneId: 'Asia/Taipei', viewport: vp, deviceScaleFactor: vpName === 'phone' ? 2 : 1, colorScheme: theme, reducedMotion: 'reduce', hasTouch: vpName === 'phone', isMobile: vpName === 'phone' })
      await ctx.addInitScript(([t, l]) => { try { localStorage.setItem('voti-theme', t); localStorage.setItem('voti-lang', l); localStorage.removeItem('voti-mock-logged-out') } catch {} }, [theme, lang])
      await login(ctx)
      const page = await ctx.newPage()
      for (const s of screens(ids, lang)) {
        if (ONLY && !ONLY.test(s.name)) continue
        if (s.path.includes('undefined')) continue // no data of that kind on this site
        if (USER && s.name === '01-login') continue // already logged in
        if (s.phoneOnly && vpName !== 'phone') continue
        await page.goto(`${BASE}${s.path}`, { waitUntil: 'networkidle' })
        // job-completion toasts are timing dependent; keep them only on the overview shot
        if (s.name !== '02-overview') await page.addStyleTag({ content: '[data-sonner-toaster]{display:none!important}' })
        await page.waitForTimeout(500)
        if (s.act) {
          try {
            await s.act(page, vpName)
            await page.waitForTimeout(700)
          } catch (e) {
            failures.push(`${s.name} @ ${lang}/${vpName}/${theme}: action failed – ${e.message.split('\n')[0]}`)
          }
        }
        const m = await page.evaluate(() => {
          const d = document.documentElement
          const offenders = []
          if (d.scrollWidth > innerWidth) {
            for (const el of document.querySelectorAll('body *')) {
              const r = el.getBoundingClientRect()
              if (r.right > innerWidth + 1 && r.width > 0 && getComputedStyle(el).position !== 'fixed') {
                offenders.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} → ${Math.round(r.right)}`)
                if (offenders.length > 4) break
              }
            }
          }
          return { sw: d.scrollWidth, iw: innerWidth, offenders }
        })
        if (m.sw > m.iw) failures.push(`${s.name} @ ${lang}/${vpName}/${theme}: scrollWidth ${m.sw} > innerWidth ${m.iw}\n      ${m.offenders.join('\n      ')}`)
        if (!CHECK_ONLY) {
          const file = join(OUT, `${s.name}__${lang}__${vpName}-${vp.width}x${vp.height}__${theme}.png`)
          if (s.act) await page.screenshot({ path: file })
          else {
            // grow the viewport to the document height so sticky/fixed chrome (sidebar, tab bar) lands where a reader would see it
            const h = await page.evaluate(() => document.documentElement.scrollHeight)
            await page.setViewportSize({ width: vp.width, height: Math.max(vp.height, Math.min(h, 12000)) })
            await page.waitForTimeout(250)
            await page.screenshot({ path: file })
            await page.setViewportSize(vp)
          }
        }
        await page.keyboard.up('Shift').catch(() => {})
        count++
      }
      await ctx.close()
    }
  }
  }
} finally {
  await browser.close()
}
console.log(`${count} screen/language/viewport/theme combinations checked${CHECK_ONLY ? '' : ` · screenshots in ${OUT}`}`)
if (failures.length) {
  console.error(`\n✗ ${failures.length} problem(s):\n  ` + failures.join('\n  '))
  process.exit(1)
}
console.log('✓ no horizontal overflow (document.documentElement.scrollWidth <= innerWidth on every screen)')
