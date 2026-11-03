import { Languages } from 'lucide-react'
import { LANGS, msgs, useI18n, type Lang } from '@/i18n'
import { Segmented } from '@/components/ui/form'
import { Menu, MenuContent, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { Button } from '@/components/ui/button'

/** Inline segmented switch: 繁體中文 | English (login page, account settings). */
export function LanguageSwitcher({ className, size = 'sm' }: { className?: string; size?: 'sm' | 'md' }) {
  const { lang, setLang, t } = useI18n()
  return (
    <Segmented<Lang>
      value={lang}
      onChange={setLang}
      size={size}
      className={className}
      aria-label={t('common.language')}
      options={LANGS.map((l) => ({ value: l, label: <span lang={l === 'en' ? 'en' : 'zh-Hant-TW'}>{msgs().common.languageNames[l]}</span> }))}
    />
  )
}

/** Icon button + menu (sidebar footer, phone top bar). */
export function LanguageMenu({ collapsed }: { collapsed?: boolean }) {
  const { lang, setLang, t } = useI18n()
  return (
    <Menu>
      <Tooltip content={t('common.language')} side={collapsed ? 'right' : 'top'}>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={t('common.language')}>
            <Languages />
          </Button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align={collapsed ? 'start' : 'end'}>
        <MenuLabel>{t('common.language')}</MenuLabel>
        <MenuRadioGroup value={lang} onValueChange={(v) => setLang(v as Lang)}>
          {LANGS.map((l) => (
            <MenuRadioItem key={l} value={l}>
              <span lang={l === 'en' ? 'en' : 'zh-Hant-TW'}>{msgs().common.languageNames[l]}</span>
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
      </MenuContent>
    </Menu>
  )
}
