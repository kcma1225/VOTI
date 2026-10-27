import type { JobKind, JobStatus, MediaStatus, ReviewStatus, SpeakerState } from '@/api/types'

export type Tone = 'neutral' | 'primary' | 'info' | 'success' | 'warning' | 'danger'

export const mediaStatus: Record<MediaStatus, { label: string; tone: Tone }> = {
  listed: { label: '已列出', tone: 'neutral' },
  detailed: { label: '已取得資訊', tone: 'neutral' },
  queued: { label: '排隊中', tone: 'info' },
  downloading: { label: '下載中', tone: 'primary' },
  downloaded: { label: '已下載', tone: 'info' },
  processing: { label: '處理中', tone: 'primary' },
  ready: { label: '可用', tone: 'success' },
  failed: { label: '失敗', tone: 'danger' },
  blocked: { label: '受阻', tone: 'warning' },
  skipped: { label: '已略過', tone: 'neutral' },
}

export const jobStatus: Record<JobStatus, { label: string; tone: Tone }> = {
  queued: { label: '排隊中', tone: 'neutral' },
  running: { label: '執行中', tone: 'primary' },
  completed: { label: '完成', tone: 'success' },
  failed: { label: '失敗', tone: 'danger' },
  blocked: { label: '受阻', tone: 'warning' },
  cancelled: { label: '已取消', tone: 'neutral' },
  interrupted: { label: '中斷', tone: 'warning' },
}

export const jobKind: Record<JobKind, string> = {
  'source.resolve': '解析來源',
  'channel.list': '列出頻道影片',
  'playlist.list': '列出播放清單',
  'media.detail': '取得影片資訊',
  'media.download': '下載音訊與字幕',
  'media.normalize': '音訊正規化',
  'media.quality': '音質分析',
  'media.diarize': '語者分段',
  'media.segment': '切分音段',
  'media.embed': '語者嵌入',
  'media.profile': '語者特徵',
  'media.fingerprint': '音訊指紋',
  'channel.dedup': '重複片段偵測',
  'speakers.cluster': '語者分群',
  'dataset.export': '匯出資料集',
}

export const pipelineSteps: { kind: JobKind; short: string }[] = [
  { kind: 'media.download', short: '下載' },
  { kind: 'media.normalize', short: '正規化' },
  { kind: 'media.quality', short: '音質' },
  { kind: 'media.diarize', short: '分段' },
  { kind: 'media.segment', short: '切分' },
  { kind: 'media.embed', short: '嵌入' },
  { kind: 'media.profile', short: '特徵' },
  { kind: 'media.fingerprint', short: '指紋' },
]

export const queueLabel: Record<string, string> = { fetch: '擷取', audio: '音訊', ml: '模型', export: '匯出' }

export const reviewStatus: Record<ReviewStatus, { label: string; tone: Tone }> = {
  unchecked: { label: '未審查', tone: 'neutral' },
  passed: { label: '通過', tone: 'success' },
  needs_fix: { label: '需修正', tone: 'warning' },
  excluded: { label: '排除', tone: 'danger' },
}

export const speakerState: Record<SpeakerState, string> = {
  unknown: '未確認',
  single: '單一語者',
  multiple: '多位語者',
  overlap: '語音重疊',
}

export const textSource: Record<string, string> = {
  manual_caption: '原生字幕',
  auto_caption: '自動字幕',
  human_revision: '人工校正',
  none: '無文字',
}

export const flagLabel: Record<string, string> = {
  low_speech: '語音比例低',
  low_snr: '訊噪比低',
  clipped: '削波失真',
  repeated: '重複片段',
  overlap: '語音重疊',
  boundary_adjusted: '已調邊界',
  too_short: '過短',
  too_long: '過長',
  upsampled: '疑似升頻',
  music: '背景音樂',
}

export const sexLabel: Record<string, string> = { male: '男性', female: '女性', unknown: '未知' }

export const emotionLabel: Record<string, string> = {
  neutral: '中性', happy: '開心', sad: '悲傷', angry: '生氣', surprise: '驚訝',
  fear: '恐懼', disgust: '厭惡', contempt: '輕蔑', other: '其他',
}

export const voiceQualityLabel: Record<string, string> = {
  breathy: '氣聲', creaky: '嘎裂聲', hoarse: '沙啞', nasal: '鼻音', resonant: '共鳴', tense: '緊繃', smooth: '平滑', shrill: '尖細',
}
export const speechFlowLabel: Record<string, string> = { fluent: '流暢', disfluent: '不流暢', slow: '慢速', fast: '快速', normal: '正常語速' }

export const ageGroupOrder = ['<20', '20-29', '30-39', '40-49', '50-59', '60+']
export const ageGroupLabel = (g: string) => (g === 'unknown' ? '未知' : g === '<20' ? '20 歲以下' : g === '60+' ? '60 歲以上' : `${g} 歲`)

export const langLabel: Record<string, string> = {
  'zh-TW': '中文（台灣）', 'zh-Hant': '繁體中文', zh: '中文', 'zh-Hans': '簡體中文', 'zh-CN': '簡體中文',
  nan: '台語', 'nan-TW': '台語', hak: '客語', en: '英文', ja: '日文',
}
export const lang = (code: string) => langLabel[code] ?? code

export const repeatPosition: Record<string, string> = { intro: '片頭', outro: '片尾', middle: '片中' }
export const mediaKind: Record<string, string> = { video: '影片', short: 'Shorts', live: '直播', upload: '上傳' }
export const licenseLabel: Record<string, string> = { youtube: '標準授權', creative_commons: 'CC 授權' }
