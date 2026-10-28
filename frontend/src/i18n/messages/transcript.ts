/* Media page transcript: full-text search + filters. */
import type { Shape } from '../shape'
import { fmtInt } from '@/lib/format'

export const zh = {
  search: '搜尋逐字稿…',
  speaker: '語者',
  allSpeakers: '全部語者',
  noSpeaker: '未分語者',
  review: '審核',
  allReview: '全部審核狀態',
  flag: '標記',
  allFlags: '全部標記',
  matches: (n: number, total: number) => `符合 ${fmtInt(n)} / ${fmtInt(total)} 句`,
  clear: '清除篩選',
  noMatch: '沒有符合的句子',
  noMatchDesc: '換個關鍵字或放寬篩選條件。',
}

export const en: Shape<typeof zh> = {
  search: 'Search the transcript…',
  speaker: 'Speaker',
  allSpeakers: 'All speakers',
  noSpeaker: 'No speaker',
  review: 'Review',
  allReview: 'Any review status',
  flag: 'Flag',
  allFlags: 'Any flag',
  matches: (n, total) => `${fmtInt(n)} of ${fmtInt(total)} sentences`,
  clear: 'Clear filters',
  noMatch: 'No matching sentences',
  noMatchDesc: 'Try another keyword or loosen the filters.',
}
