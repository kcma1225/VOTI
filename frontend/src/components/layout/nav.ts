import { t } from '@/i18n'
import {
  AudioWaveform,
  BarChart3,
  Database,
  Gauge,
  LayoutDashboard,
  ListChecks,
  PlusCircle,
  Settings,
  Tv,
  Users,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  adminOnly?: boolean
  end?: boolean
}
export interface NavGroup {
  label: string | null
  items: NavItem[]
}

/** Built on call so labels follow the active language. */
export const navGroups = (): NavGroup[] => [
  { label: null, items: [{ to: '/', label: t('shell.nav.overview'), icon: LayoutDashboard, end: true }] },
  {
    label: t('shell.nav.groups.collect'),
    items: [
      { to: '/sources/new', label: t('shell.nav.addSource'), icon: PlusCircle },
      { to: '/channels', label: t('shell.nav.channels'), icon: Tv },
    ],
  },
  {
    label: t('shell.nav.groups.process'),
    items: [
      { to: '/review', label: t('shell.nav.review'), icon: AudioWaveform },
      { to: '/speakers', label: t('shell.nav.speakers'), icon: Users },
      { to: '/jobs', label: t('shell.nav.jobs'), icon: ListChecks },
    ],
  },
  {
    label: t('shell.nav.groups.analyze'),
    items: [
      { to: '/diversity', label: t('shell.nav.diversity'), icon: BarChart3 },
      { to: '/quality', label: t('shell.nav.quality'), icon: Gauge },
    ],
  },
  { label: t('shell.nav.groups.output'), items: [{ to: '/datasets', label: t('shell.nav.datasets'), icon: Database }] },
  { label: t('shell.nav.groups.system'), items: [{ to: '/settings', label: t('shell.nav.settings'), icon: Settings }] },
]

/** Phone bottom bar (the rest lives in the "more" drawer). */
export const phoneTabs = (): NavItem[] => [
  { to: '/', label: t('shell.nav.overview'), icon: LayoutDashboard, end: true },
  { to: '/channels', label: t('shell.nav.channels'), icon: Tv },
  { to: '/review', label: t('shell.nav.reviewShort'), icon: AudioWaveform },
  { to: '/datasets', label: t('shell.nav.datasets'), icon: Database },
]
