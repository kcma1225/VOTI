/* Deleting a video's downloaded / processed data (media detail page + batch action in the media lists). The
   video stays in its channel list as not downloaded. Admin only. */
import type { Shape } from '../shape'
import { fmtInt } from '@/lib/format'

export const zh = {
  action: '刪除影片',
  batch: '刪除',
  title: (n: number) => (n === 1 ? '刪除這部影片的下載與處理資料？' : `刪除 ${fmtInt(n)} 部影片的下載與處理資料？`),
  descOne: (title: string) => `「${title}」的音檔與所有處理結果會被刪除；影片仍留在頻道清單中，回到「未下載」，之後可以重新下載。`,
  descMany: (n: number) => `${fmtInt(n)} 部影片的音檔與所有處理結果會被刪除；影片仍留在頻道清單中，回到「未下載」，之後可以重新下載。`,
  unprocessed: (n: number) => `另有 ${fmtInt(n)} 部選取的影片尚未下載或處理，不受影響。`,
  nothing: '選取的影片都尚未下載或處理，沒有可刪除的資料。',
  warnHead: '以下資料會永久刪除，無法復原：',
  audio: (n: number, hours: string, size: string) => `${fmtInt(n)} 部已下載影片的音檔（${hours}，${size}）`,
  analyses: '下載的字幕軌、品質、VAD、語者分段、OCR、對齊與 Vox-Profile 等分析結果',
  segments: (n: number) => `${fmtInt(n)} 個片段`,
  reviewedPart: '，其中 ',
  reviewed: (n: number) => `${fmtInt(n)} 個已審核`,
  activeJobs: (n: number) => `${fmtInt(n)} 個進行中的工作會被取消`,
  metadataOnly: '這些影片只有處理紀錄或暫存檔，沒有音檔。',
  statVideos: '要清除的影片',
  statDownloaded: '已下載',
  statSegments: '片段',
  statStorage: '儲存空間',
  understand: '我了解刪除後無法復原',
  submit: '永久刪除',
  toastDone: (n: number) => (n === 1 ? '已刪除影片的處理資料，影片回到「未下載」' : `已刪除 ${fmtInt(n)} 部影片的處理資料，影片回到「未下載」`),
}

export const en: Shape<typeof zh> = {
  action: 'Delete video',
  batch: 'Delete',
  title: (n) => (n === 1 ? 'Delete this video’s downloaded data?' : `Delete the downloaded data of ${fmtInt(n)} videos?`),
  descOne: (title) => `The audio and every processing result of “${title}” will be deleted. The video stays in the channel list as not downloaded and can be downloaded again.`,
  descMany: (n) => `The audio and every processing result of ${fmtInt(n)} videos will be deleted. They stay in the channel list as not downloaded and can be downloaded again.`,
  unprocessed: (n) => `${fmtInt(n)} other selected ${n === 1 ? 'video was' : 'videos were'} never downloaded or processed and ${n === 1 ? 'is' : 'are'} not affected.`,
  nothing: 'None of the selected videos were downloaded or processed; there is nothing to delete.',
  warnHead: 'This permanently deletes and cannot be undone:',
  audio: (n, hours, size) => `Audio of ${fmtInt(n)} downloaded ${n === 1 ? 'video' : 'videos'} (${hours}, ${size})`,
  analyses: 'Downloaded caption tracks, quality, VAD, diarization, OCR, alignment and Vox-Profile results',
  segments: (n) => `${fmtInt(n)} ${n === 1 ? 'segment' : 'segments'}`,
  reviewedPart: ', ',
  reviewed: (n) => `${fmtInt(n)} reviewed`,
  activeJobs: (n) => `${fmtInt(n)} running ${n === 1 ? 'job' : 'jobs'} will be cancelled`,
  metadataOnly: 'These videos only have job records or temporary files, no audio.',
  statVideos: 'Videos to clear',
  statDownloaded: 'Downloaded',
  statSegments: 'Segments',
  statStorage: 'Storage',
  understand: 'I understand this cannot be undone',
  submit: 'Delete permanently',
  toastDone: (n) => (n === 1 ? 'Processed data deleted; the video is not downloaded again' : `Processed data of ${fmtInt(n)} videos deleted; they are not downloaded again`),
}
