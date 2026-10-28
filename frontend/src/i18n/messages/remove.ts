/* Deleting videos (media detail page + batch delete in the media lists). Admin only. */
import type { Shape } from '../shape'
import { fmtInt } from '@/lib/format'

export const zh = {
  action: '刪除影片',
  batch: '刪除',
  title: (n: number) => (n === 1 ? '刪除這部影片？' : `刪除 ${fmtInt(n)} 部影片？`),
  descOne: (title: string) => `「${title}」會從系統中完全移除。頻道保留；之後重新抓取頻道清單時，這部影片會以「未下載」再出現。`,
  descMany: (n: number) => `選取的 ${fmtInt(n)} 部影片會從系統中完全移除。頻道保留；之後重新抓取頻道清單時，這些影片會以「未下載」再出現。`,
  warnHead: '以下資料會永久刪除，無法復原：',
  audio: (n: number, hours: string, size: string) => `${fmtInt(n)} 部已下載影片的音檔（${hours}，${size}）`,
  analyses: '字幕、品質、VAD、語者分段、OCR 與 Vox-Profile 等分析結果',
  segments: (n: number) => `${fmtInt(n)} 個片段`,
  reviewedPart: '，其中 ',
  reviewed: (n: number) => `${fmtInt(n)} 個已審核`,
  activeJobs: (n: number) => `${fmtInt(n)} 個進行中的工作會被取消`,
  metadataOnly: '這些影片尚未下載，只會刪除影片資訊。',
  statVideos: '影片',
  statDownloaded: '已下載',
  statSegments: '片段',
  statStorage: '儲存空間',
  understand: '我了解刪除後無法復原',
  submit: '永久刪除',
  toastDone: (n: number) => (n === 1 ? '影片已刪除' : `已刪除 ${fmtInt(n)} 部影片`),
}

export const en: Shape<typeof zh> = {
  action: 'Delete video',
  batch: 'Delete',
  title: (n) => (n === 1 ? 'Delete this video?' : `Delete ${fmtInt(n)} videos?`),
  descOne: (title) => `“${title}” will be removed from the system completely. The channel stays; the next channel re-list shows the video again as not downloaded.`,
  descMany: (n) => `The ${fmtInt(n)} selected videos will be removed from the system completely. The channel stays; the next channel re-list shows them again as not downloaded.`,
  warnHead: 'This permanently deletes and cannot be undone:',
  audio: (n, hours, size) => `Audio of ${fmtInt(n)} downloaded ${n === 1 ? 'video' : 'videos'} (${hours}, ${size})`,
  analyses: 'Captions, quality, VAD, diarization, OCR and Vox-Profile results',
  segments: (n) => `${fmtInt(n)} ${n === 1 ? 'segment' : 'segments'}`,
  reviewedPart: ', ',
  reviewed: (n) => `${fmtInt(n)} reviewed`,
  activeJobs: (n) => `${fmtInt(n)} running ${n === 1 ? 'job' : 'jobs'} will be cancelled`,
  metadataOnly: 'These videos were never downloaded; only their metadata is deleted.',
  statVideos: 'Videos',
  statDownloaded: 'Downloaded',
  statSegments: 'Segments',
  statStorage: 'Storage',
  understand: 'I understand this cannot be undone',
  submit: 'Delete permanently',
  toastDone: (n) => (n === 1 ? 'Video deleted' : `${fmtInt(n)} videos deleted`),
}
