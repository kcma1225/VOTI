import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Eye, EyeOff } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, qk } from '@/api/queries'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/form'
import { useTitle } from '@/components/layout/page'
import { BrandMark, Logo } from '@/components/brand/logo'
import { LanguageSwitcher } from '@/components/layout/language-switcher'
import { useT } from '@/i18n'

function Bars() {
  // decorative waveform built from deterministic heights
  const hs = Array.from({ length: 64 }, (_, i) => 0.25 + 0.75 * Math.abs(Math.sin(i * 0.37) * Math.cos(i * 0.11 + 1.3)))
  return (
    <div className="flex h-28 items-center gap-[3px]" aria-hidden>
      {hs.map((h, i) => (
        <span key={i} className="w-[3px] rounded-full bg-white/70" style={{ height: `${h * 100}%`, opacity: 0.35 + h * 0.65 }} />
      ))}
    </div>
  )
}

export default function Login() {
  const t = useT()
  useTitle(t('shell.login.title'))
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [sp] = useSearchParams()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (!username || !password) {
      setError(t('shell.login.missing'))
      return
    }
    setBusy(true)
    try {
      const u = await api.login(username.trim(), password)
      qc.setQueryData(qk.me, u)
      const next = sp.get('next')
      navigate(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replace: true })
    } catch (err) {
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <aside className="relative hidden overflow-hidden bg-[#0f5f57] text-white lg:flex lg:flex-col lg:justify-between lg:p-12 dark:bg-[#0a3d38]">
        <div
          className="pointer-events-none absolute inset-0 opacity-90"
          style={{
            background:
              'radial-gradient(1200px 500px at 0% 0%, rgba(255,255,255,0.16), transparent 60%), radial-gradient(800px 400px at 100% 100%, rgba(42,157,143,0.55), transparent 60%)',
          }}
        />
        <Logo variant="solid" className="pointer-events-none absolute -right-16 top-1/2 h-[118%] w-auto -translate-y-1/2 text-white opacity-[0.07]" title="" />
        <div className="relative flex items-center gap-3">
          <span className="grid size-11 place-items-center rounded-xl bg-white/12 ring-1 ring-inset ring-white/20 backdrop-blur">
            <Logo className="h-8 w-auto text-white" strokeWidth={14} />
          </span>
          <span className="min-w-0">
            <span className="block text-lg font-semibold leading-6 tracking-tight">{t('common.brand.name')}</span>
            <span className="block text-xs leading-4 text-white/65">{t('common.brand.tagline')}</span>
          </span>
        </div>
        <div className="relative max-w-lg">
          <Bars />
          <h2 className="mt-8 text-3xl font-semibold leading-tight tracking-tight text-balance">
            <span className="block text-balance">{t('shell.login.heroLine1')}</span>
            <span className="block text-balance">{t('shell.login.heroLine2')}</span>
          </h2>
          <p className="mt-4 text-[15px] leading-7 text-white/75">{t('shell.login.heroBody')}</p>
        </div>
        <p className="relative text-xs text-white/55">{t('shell.login.footer')}</p>
      </aside>

      <main className="relative flex items-center justify-center px-5 pb-10 pt-20 sm:px-8 lg:py-10">
        <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
          <LanguageSwitcher />
        </div>
        <div className="w-full max-w-[380px]">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <BrandMark size={44} className="rounded-xl" />
            <div className="min-w-0">
              <div className="text-lg font-semibold leading-6">{t('common.brand.name')}</div>
              <div className="text-xs text-fg-3">{t('common.brand.tagline')}</div>
            </div>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{t('shell.login.title')}</h1>
          <p className="mt-1.5 text-sm text-fg-3">{t('shell.login.subtitle')}</p>

          <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
            {error && (
              <div role="alert" className="flex items-start gap-2 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2.5 text-[13px] text-danger-fg">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                {error}
              </div>
            )}
            <Field label={t('shell.login.username')} htmlFor="username">
              <Input id="username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} className="h-11 sm:h-10" autoFocus />
            </Field>
            <Field label={t('shell.login.password')} htmlFor="password">
              <div className="relative">
                <Input
                  id="password"
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-11 pr-11 sm:h-10"
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  className="absolute right-1 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-md text-fg-3 hover:bg-surface-2 hover:text-fg"
                  aria-label={show ? t('shell.login.hidePassword') : t('shell.login.showPassword')}
                >
                  {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </Field>
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>
              {t('shell.login.submit')}
            </Button>
          </form>
          {import.meta.env.VITE_MOCK === '1' && (
            <p className="mt-6 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-5 text-fg-3">
              {t('shell.login.demoPrefix')}<code className="font-mono text-fg-2">brian</code>{t('shell.login.demoAdmin')}<code className="font-mono text-fg-2">yijun</code>{t('shell.login.demoAnnotator')}
            </p>
          )}
        </div>
      </main>
    </div>
  )
}
