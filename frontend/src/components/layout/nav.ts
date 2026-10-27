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

export const navGroups: NavGroup[] = [
  { label: null, items: [{ to: '/', label: '總覽', icon: LayoutDashboard, end: true }] },
  {
    label: '收集',
    items: [
      { to: '/sources/new', label: '新增來源', icon: PlusCircle },
      { to: '/channels', label: '頻道', icon: Tv },
    ],
  },
  {
    label: '處理',
    items: [
      { to: '/review', label: '音段審查', icon: AudioWaveform },
      { to: '/speakers', label: '語者', icon: Users },
      { to: '/jobs', label: '工作佇列', icon: ListChecks },
    ],
  },
  {
    label: '分析',
    items: [
      { to: '/diversity', label: '多樣性', icon: BarChart3 },
      { to: '/quality', label: '音質總覽', icon: Gauge },
    ],
  },
  { label: '輸出', items: [{ to: '/datasets', label: '資料集', icon: Database }] },
  { label: '系統', items: [{ to: '/settings', label: '設定', icon: Settings }] },
]

/** Phone bottom bar (the rest lives in the「更多」drawer). */
export const phoneTabs: NavItem[] = [
  { to: '/', label: '總覽', icon: LayoutDashboard, end: true },
  { to: '/channels', label: '頻道', icon: Tv },
  { to: '/review', label: '審查', icon: AudioWaveform },
  { to: '/datasets', label: '資料集', icon: Database },
]
