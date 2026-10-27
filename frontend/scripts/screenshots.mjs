#!/usr/bin/env node
/* Visual acceptance + horizontal-overflow check against the mock build.
 *
 *   npm run build:mock && npm run preview:mock &      # serves dist-mock on :4173
 *   npm run shots                                      # screenshots + overflow check
 *   npm run check:overflow                             # overflow check only (no files written)
 *
 * Live site: BASE_URL=http://127.0.0.1:8080 VOTI_USER=admin VOTI_PASS=… npm run shots (logs in via the API).
 * Env: BASE_URL (default http://127.0.0.1:4173), OUT (default ../screenshots), ONLY=regex on screen name,
 *      VIEWPORTS=comma list of names, THEMES=light,dark, CHROMIUM=/path/to/chrome
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
    const media = (await j(`/api/media?channel_id=${ch[0]?.id}&status=ready&limit=5`)).items ?? []
    const failed = (await j('/api/media?status=failed,blocked&limit=1')).items ?? []
    const sp = (await j('/api/speakers?limit=3')).items ?? []
    const ds = await j('/api/datasets')
    return { ch: ch[0]?.id, ch2: ch[1]?.id, media: media[0]?.id, failed: failed[0]?.id, speaker: (sp[1] ?? sp[0])?.id, dataset: (ds.items ?? ds)[0]?.id }
  })
}

function screens(ids) {
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
    { name: '14-media-failed', path: `/media/${ids.failed}` },
    { name: '15-review', path: '/review' },
    { name: '16-speakers', path: '/speakers' },
    { name: '17-speaker-detail', path: `/speakers/${ids.speaker}` },
    { name: '18-diversity', path: '/diversity' },
    { name: '19-quality', path: '/quality' },
    { name: '20-datasets', path: '/datasets' },
    { name: '21-dataset-new', path: '/datasets/new' },
    { name: '22-dataset-detail', path: `/datasets/${ids.dataset}` },
    { name: '23-jobs', path: '/jobs' },
    { name: '24-jobs-drawer', path: '/', act: async (p, vp) => { await p.locator(vp === 'phone' ? 'header [aria-label="工作佇列"]' : 'aside [aria-label="工作佇列"]').first().click() } },
    { name: '25-settings-users', path: '/settings/users' },
    { name: '26-settings-youtube', path: '/settings/youtube' },
    { name: '27-settings-pipeline', path: '/settings/pipeline' },
    { name: '28-settings-account', path: '/settings/account' },
    { name: '29-not-found', path: '/nope' },
    { name: '30-phone-menu', path: '/', phoneOnly: true, act: async (p) => { await p.locator('header [aria-label="開啟選單"]').click() } },
  ]
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
  for (const theme of themes) {
    for (const [vpName, vp] of viewports) {
      const ctx = await browser.newContext({ locale: 'zh-TW', timezoneId: 'Asia/Taipei', viewport: vp, deviceScaleFactor: vpName === 'phone' ? 2 : 1, colorScheme: theme, reducedMotion: 'reduce', hasTouch: vpName === 'phone', isMobile: vpName === 'phone' })
      await ctx.addInitScript((t) => { try { localStorage.setItem('voti-theme', t); localStorage.removeItem('voti-mock-logged-out') } catch {} }, theme)
      await login(ctx)
      const page = await ctx.newPage()
      for (const s of screens(ids)) {
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
            failures.push(`${s.name} @ ${vpName}/${theme}: action failed – ${e.message.split('\n')[0]}`)
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
        if (m.sw > m.iw) failures.push(`${s.name} @ ${vpName}/${theme}: scrollWidth ${m.sw} > innerWidth ${m.iw}\n      ${m.offenders.join('\n      ')}`)
        if (!CHECK_ONLY) {
          const file = join(OUT, `${s.name}__${vpName}-${vp.width}x${vp.height}__${theme}.png`)
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
        count++
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
console.log(`${count} screen/viewport/theme combinations checked${CHECK_ONLY ? '' : ` · screenshots in ${OUT}`}`)
if (failures.length) {
  console.error(`\n✗ ${failures.length} problem(s):\n  ` + failures.join('\n  '))
  process.exit(1)
}
console.log('✓ no horizontal overflow (document.documentElement.scrollWidth <= innerWidth on every screen)')
