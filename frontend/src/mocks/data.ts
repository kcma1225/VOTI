/* Deterministic fake corpus for VOTI_MOCK. Everything is synthetic. */
import type * as T from '@/api/types'
import { genOcr, type MockOcr } from './ocr'

// ───────────── seeded randomness ─────────────
function mulberry32(a: number) {
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
export function rngFor(seed: number) {
  const r = mulberry32(seed)
  const api = {
    next: r,
    range: (a: number, b: number) => a + (b - a) * r(),
    int: (a: number, b: number) => Math.floor(a + (b - a + 1) * r()),
    pick: <X,>(xs: readonly X[]) => xs[Math.floor(r() * xs.length)],
    chance: (p: number) => r() < p,
    normal: (mu: number, sd: number) => {
      const u = Math.max(1e-9, r())
      const v = r()
      return mu + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
    },
    uuid: () => {
      const h = () => Math.floor(r() * 0x10000).toString(16).padStart(4, '0')
      return `${h()}${h()}-${h()}-4${h().slice(1)}-a${h().slice(1)}-${h()}${h()}${h()}`
    },
    ytid: () => {
      const c = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
      return Array.from({ length: 11 }, () => c[Math.floor(r() * c.length)]).join('')
    },
  }
  return api
}
const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x))
const round = (x: number, d = 2) => Math.round(x * 10 ** d) / 10 ** d

export const NOW = Date.now()
const DAY = 86400_000
const iso = (ms: number) => new Date(ms).toISOString()

// ───────────── content pools ─────────────
const PHRASES = [
  '今仔日欲來佮大家分享一个古早味的料理。',
  '阮阿嬤較早攏佇菜市仔賣菜，一工做甲暗暝。',
  '這个所在以早是一片稻仔田，這馬攏起厝矣。',
  '你若是有閒，歡迎來阮兜坐。',
  '台語是咱的母語，愛常常講才袂袂記得。',
  '這款物件愛用文火慢慢仔炕，才會入味。',
  '伊講話真趣味，逐家攏笑甲欲死。',
  '這擺的節目阮請著一位真特別的來賓。',
  '咱來看這幾工的天氣，北部會較寒。',
  '囡仔時代上愛食的就是阿母煮的鹹粥。',
  '透早五點就愛起床，去海邊仔掠魚。',
  '這間廟有三百外冬的歷史矣。',
  '我感覺這个問題愛較深入來探討。',
  '多謝逐家的收看，咱下禮拜閣再相見。',
  '歡迎收看今仔日的節目，我是主持人。',
  '這碗麵的湯頭是用大骨熬八點鐘。',
  '彼陣的生活真艱苦，毋過人情味真厚。',
  '學台語上重要的是敢講，毋通驚講毋著。',
  '這首歌是阮阿公上愛唱的。',
  '布袋戲的戲偶攏是師傅一刀一刀刻出來的。',
  '車頭邊仔彼間冰店開幾若十冬矣。',
  '這馬的少年人較少咧講台語矣。',
  '咱共這个菜頭切做細塊，囥落去鼎仔內。',
  '頭家，這粒西瓜敢有甜？',
  '伊細漢的時陣蹛佇鹿港。',
  '下晡欲落雨，出門愛記得紮雨傘。',
  '這部電影講的是一个漁村家庭的故事。',
  '逐家好，閣來到咱的講古時間。',
  '古早古早，佇一个山跤下有一个少年家。',
  '這个字的音讀做食，意思就是吃飯。',
  '鹽愛較少囥一屑仔，若無會傷鹹。',
  '阮老爸逐工攏騎鐵馬去田裡巡水。',
  '這个政策對農民的影響真大。',
  '你敢知影這條路以早叫做啥物名？',
  '欲過年矣，厝內大大細細攏咧無閒。',
  '彼个老師傅講，做粿上驚的就是米漿傷稀。',
]
const TOPICS = ['颱風動態', '稻米收成', '地方選舉', '海洋保育', '老街再生', '捷運通車', '廟會文化', '移工生活', '偏鄉醫療', '漁村轉型', '夜市美食', '鐵道旅行']
const DISHES = ['菜頭粿', '鹹粥', '肉粽', '碗粿', '滷肉飯', '麻油雞', '芋粿', '紅龜粿', '米糕', '虱目魚湯', '筍乾', '菜脯蛋', '油飯', '鹹湯圓']
const LESSONS = ['數字的講法', '親屬稱呼', '問路', '買物件', '天氣', '身體部位', '時間', '食飯', '看病', '坐車', '動物', '顏色']
const STORIES = ['虎姑婆', '白賊七', '林投姐', '蛇郎君', '鴨母王', '賣香屁', '傻女婿', '金魚的故事', '好鼻師', '嘉義的石猴']

// ───────────── channels ─────────────
interface ChannelDef {
  title: string
  handle: string
  subs: number
  size: number
  shorts: number
  manual: number
  auto: number
  cc: number
  durMin: [number, number]
  snr: [number, number]
  sr: number[]
  upsampled: number
  readyShare: number
  years: [number, number]
  hosts: { sex: 'male' | 'female'; age: number; name?: string }[]
  guests: number
  desc: string
  list_status?: T.ListStatus
  title_fn: (r: ReturnType<typeof rngFor>, i: number) => string
}

const CHANNEL_DEFS: ChannelDef[] = [
  {
    title: '公視台語台',
    handle: '@ptstaigi',
    subs: 412000,
    size: 412,
    shorts: 0.22,
    manual: 0.82,
    auto: 0.6,
    cc: 0.02,
    durMin: [4, 48],
    snr: [24, 36],
    sr: [48000, 48000, 44100],
    upsampled: 0.02,
    readyShare: 0.19,
    years: [2019, 2026],
    hosts: [
      { sex: 'female', age: 34, name: '新聞主播（林）' },
      { sex: 'male', age: 45, name: '新聞主播（陳）' },
      { sex: 'female', age: 29 },
      { sex: 'male', age: 58, name: '文化節目主持' },
    ],
    guests: 18,
    desc: '台灣第一個以台語為主要語言的電視頻道，提供新聞、文化、兒童與戲劇節目。',
    title_fn: (r, i) =>
      r.pick([
        () => `【台語新聞】${r.pick(TOPICS)}最新消息｜${2026 - (i % 6)}年${(i % 12) + 1}月${(i % 27) + 1}日`,
        () => `逐工一句台語：${r.pick(['袂記得', '足好食', '無要緊', '真拍損', '慢慢仔來', '有影無'])}`,
        () => `《台灣好所在》第 ${i + 1} 集｜${r.pick(['鹿港', '旗津', '北埔', '大溪', '東港', '安平'])}的${r.pick(['老街', '漁港', '廟埕', '古厝'])}`,
        () => `《講台語過生活》${r.pick(TOPICS)}專題：從${r.pick(['田庄', '漁村', '市場', '學校'])}看見台灣社會的轉變與在地人的真實心聲`,
        () => `公視台語新聞深度報導　${r.pick(TOPICS)}`,
      ])(),
  },
  {
    title: '阿嬤的灶跤',
    handle: '@amaskitchen',
    subs: 186000,
    size: 268,
    shorts: 0.38,
    manual: 0.55,
    auto: 0.78,
    cc: 0.0,
    durMin: [6, 28],
    snr: [12, 24],
    sr: [44100, 48000],
    upsampled: 0.05,
    readyShare: 0.16,
    years: [2020, 2026],
    hosts: [
      { sex: 'female', age: 76, name: '阿嬤' },
      { sex: 'male', age: 27, name: '孫仔' },
    ],
    guests: 8,
    desc: '阿嬤用台語教你煮古早味。灶跤的聲音、阿嬤的叮嚀，攏佇遮。',
    title_fn: (r) =>
      r.pick([
        () => `阿嬤教你做${r.pick(DISHES)}｜古早味做法`,
        () => `【阿嬤的灶跤】過年一定愛食的${r.pick(DISHES)}，阿嬤親手教你從頭到尾的完整步驟（附詳細配方與失敗原因分析）`,
        () => `${r.pick(DISHES)}按呢做上好食！#Shorts`,
        () => `阿嬤講古早：${r.pick(['戰後的灶跤', '做粿的年代', '菜市仔的記持', '阿公的漁船'])}`,
      ])(),
  },
  {
    title: '台語好好學',
    handle: '@learntaigi',
    subs: 64000,
    size: 176,
    shorts: 0.3,
    manual: 0.93,
    auto: 0.4,
    cc: 0.34,
    durMin: [3, 18],
    snr: [26, 38],
    sr: [48000],
    upsampled: 0,
    readyShare: 0.2,
    years: [2018, 2025],
    hosts: [
      { sex: 'female', age: 41, name: '李老師' },
      { sex: 'male', age: 38, name: '王老師' },
    ],
    guests: 6,
    desc: '每週更新台語教學影片，附台羅拼音與華語對照字幕。',
    title_fn: (r, i) =>
      r.pick([
        () => `第 ${i + 1} 課：${r.pick(LESSONS)}`,
        () => `台語日常會話｜${r.pick(LESSONS)}（附台羅拼音）`,
        () => `一分鐘學台語：${r.pick(LESSONS)} #Shorts`,
      ])(),
  },
  {
    title: '鄉土劇場精華',
    handle: '@drama-clips',
    subs: 951000,
    size: 94,
    shorts: 0.25,
    manual: 0.18,
    auto: 0.7,
    cc: 0,
    durMin: [8, 42],
    snr: [6, 16],
    sr: [44100, 48000],
    upsampled: 0.32,
    readyShare: 0.22,
    years: [2016, 2023],
    hosts: [
      { sex: 'male', age: 62 },
      { sex: 'female', age: 55 },
      { sex: 'male', age: 31 },
      { sex: 'female', age: 26 },
      { sex: 'male', age: 47 },
      { sex: 'female', age: 68 },
    ],
    guests: 10,
    desc: '經典鄉土劇精華片段。',
    title_fn: (r, i) => `《${r.pick(['天地有情', '家和萬事興', '春風吹過庄頭', '海口人生'])}》第 ${i + 12} 集精華　${r.pick(['婆媳大和解', '真相大白', '老爸的祕密', '一場誤會'])}`,
  },
  {
    title: '海口人Podcast',
    handle: '@haikhau-pod',
    subs: 23800,
    size: 57,
    shorts: 0,
    manual: 0.12,
    auto: 0.62,
    cc: 0.1,
    durMin: [38, 92],
    snr: [20, 30],
    sr: [44100],
    upsampled: 0.04,
    readyShare: 0.21,
    years: [2021, 2026],
    hosts: [
      { sex: 'male', age: 36, name: '阿海' },
      { sex: 'female', age: 33, name: '小芸' },
      { sex: 'male', age: 52 },
    ],
    guests: 14,
    desc: '兩個海口囡仔用台語聊生活、聊故鄉、聊時事。',
    title_fn: (r, i) => `EP${(57 - i).toString().padStart(2, '0')}｜${r.pick(['轉去海口', '漁村少年', '阿爸的船', '討海人的心聲', '台語敢有未來'])}（feat. ${r.pick(['在地漁民', '國小老師', '文史工作者', '返鄉青年'])}）`,
  },
  {
    title: '府城講古',
    handle: '@hu-siann',
    subs: 3100,
    size: 23,
    shorts: 0.05,
    manual: 0.3,
    auto: 0.2,
    cc: 0.6,
    durMin: [10, 35],
    snr: [14, 22],
    sr: [44100, 22050],
    upsampled: 0.45,
    readyShare: 0.22,
    years: [2015, 2019],
    hosts: [{ sex: 'male', age: 81, name: '講古仙' }],
    guests: 0,
    desc: '府城老師傅用台語講民間故事。',
    list_status: 'listing',
    title_fn: (r) => `講古：${r.pick(STORIES)}（${r.pick(['上', '下', '全'])}）`,
  },
]

// ───────────── DB shape ─────────────
export interface MockMedia extends T.MediaSummary {
  channel_idx: number
  description: string
  sample_rate: number
  q: T.MediaQuality | null
  speakers: string[] // global speaker ids used in this media
  like_count: number | null
}
export interface MockSpeaker extends T.Speaker {
  sex: 'male' | 'female'
  age: number
  emo: string
}
export interface DB {
  users: (T.User & { password: string })[]
  settings: T.Settings
  channels: T.ChannelDetail[]
  playlists: T.Playlist[]
  playlistItems: Map<string, string[]>
  media: MockMedia[]
  mediaById: Map<string, MockMedia>
  segments: Map<string, T.SegmentDetail[]> // by media id (generated lazily)
  segById: Map<string, T.SegmentDetail>
  speakers: MockSpeaker[]
  runs: T.ClusterRun[]
  repeated: Map<string, T.RepeatedGroup[]>
  regions: Map<string, { id: string; group_id: string; start: number; end: number; position: T.RepeatPosition }[]>
  jobs: (T.Job & { _dur?: number })[]
  datasets: T.Dataset[]
  exports: (T.DatasetExport & { _start?: number })[]
  me: string | null
  /** media with word timings (GET /media/{id}/words) */
  aligned: Set<string>
  /** per channel: the newest ready media — carries the Simplified→Traditional demo state */
  demo: Set<string>
  /** media whose text comes from burned-in subtitles (hard-subtitle OCR) */
  ocrMedia: Set<string>
  /** OCR results by media id */
  ocr: Map<string, MockOcr>
}

const AGE_GROUP = (a: number) => (a < 20 ? '<20' : a < 30 ? '20-29' : a < 40 ? '30-39' : a < 50 ? '40-49' : a < 60 ? '50-59' : '60+')
const EMOS = ['neutral', 'happy', 'sad', 'angry', 'surprise']

function makeSpeakers(r: ReturnType<typeof rngFor>, runId: string, chIds: string[]): MockSpeaker[] {
  const out: MockSpeaker[] = []
  let n = 1
  CHANNEL_DEFS.forEach((def, ci) => {
    const people = [
      ...def.hosts.map((h) => ({ ...h, host: true })),
      ...Array.from({ length: def.guests }, () => ({
        sex: (r.chance(0.5) ? 'male' : 'female') as 'male' | 'female',
        age: clamp(Math.round(r.normal(44, 16)), 12, 88),
        name: undefined as string | undefined,
        host: false,
      })),
    ]
    for (const p of people) {
      const emo = p.host ? 'neutral' : r.pick(['neutral', 'neutral', 'happy', 'sad', 'neutral', 'angry'])
      const pm = p.sex === 'male' ? clamp(r.range(0.82, 0.99), 0, 1) : clamp(r.range(0.01, 0.15), 0, 1)
      const emoDist: Record<string, number> = {}
      let rest = 1
      EMOS.forEach((e, k) => {
        const v = e === emo ? r.range(0.45, 0.7) : k === EMOS.length - 1 ? rest : r.range(0, rest * 0.4)
        const vv = Math.min(rest, v)
        emoDist[e] = round(vv, 3)
        rest -= vv
      })
      out.push({
        id: r.uuid(),
        run_id: runId,
        label: `cand_${String(n++).padStart(3, '0')}`,
        display_name: p.name ?? null,
        verified: !!p.name && r.chance(0.8),
        segment_count: 0,
        total_sec: 0,
        media_count: 0,
        channel_ids: [chIds[ci]],
        sex: p.sex,
        age: p.age,
        emo,
        profile: {
          sex: { male: round(pm, 3), female: round(1 - pm, 3) },
          age_mean: round(p.age + r.normal(0, 3), 1),
          age_group: { [AGE_GROUP(p.age)]: round(r.range(0.55, 0.85), 2) },
          emotion: emoDist,
          voice_quality: {
            breathy: round(r.range(0, 0.4), 2),
            creaky: round(p.age > 60 ? r.range(0.3, 0.7) : r.range(0, 0.3), 2),
            hoarse: round(p.age > 65 ? r.range(0.2, 0.6) : r.range(0, 0.25), 2),
            resonant: round(r.range(0.2, 0.8), 2),
            nasal: round(r.range(0, 0.3), 2),
          },
        },
      })
    }
  })
  return out
}

function speakersOfChannel(db: DB, cid: string) {
  return db.speakers.filter((s) => s.channel_ids[0] === cid)
}

function quality(r: ReturnType<typeof rngFor>, def: ChannelDef, sr: number, ups: boolean): T.MediaQuality {
  const snr = r.range(def.snr[0], def.snr[1])
  const nyq = sr / 2
  const bw = ups ? r.pick([3900, 5400, 7800, 8000, 10900]) : clamp(r.range(nyq * 0.72, nyq * 0.97), 3000, nyq)
  const ovrl = clamp(1.6 + snr * 0.055 + r.normal(0, 0.18), 1.2, 4.1)
  const spectrum: { hz: number; db: number }[] = []
  for (let hz = 0; hz <= nyq; hz += nyq / 96) {
    const base = -18 - 22 * Math.log10(1 + hz / 900) + r.normal(0, 1.2)
    const roll = hz > bw ? -Math.min(55, (hz - bw) / 45) : 0
    spectrum.push({ hz: Math.round(hz), db: round(Math.max(-96, base + roll - (hz > bw ? 20 : 0)), 1) })
  }
  const speechRatio = clamp(r.range(0.55, 0.92) - (def.snr[0] < 10 ? 0.15 : 0), 0.2, 0.97)
  return {
    sample_rate: sr,
    channels: r.pick([1, 2, 2]),
    codec: r.pick(['opus', 'opus', 'opus', 'aac', 'aac', 'mp3']),
    bitrate: r.pick([128000, 160000, 96000, 64000]),
    container: r.pick(['webm', 'm4a']),
    real_bandwidth_hz: Math.round(bw),
    upsampled_suspect: ups,
    lufs_integrated: round(r.range(-26, -13), 1),
    true_peak_db: round(r.range(-6, 0.3), 1),
    clipping_ratio: round(r.chance(0.12) ? r.range(0.001, 0.012) : r.range(0, 0.0008), 5),
    speech_sec: null,
    speech_ratio: round(speechRatio, 3),
    vad_agreement: round(r.range(0.82, 0.97), 3),
    snr_mean: round(snr, 1),
    snr_p10: round(snr - r.range(6, 12), 1),
    c50_mean: round(r.range(def.snr[0] > 20 ? 30 : 12, def.snr[0] > 20 ? 55 : 35), 1),
    dnsmos_sig: round(clamp(ovrl + r.range(0.3, 0.6), 1, 5), 2),
    dnsmos_bak: round(clamp(ovrl + r.range(0.4, 0.9), 1, 5), 2),
    dnsmos_ovrl: round(ovrl, 2),
    dnsmos_p808: round(clamp(ovrl + r.range(0.2, 0.5), 1, 5), 2),
    computed_at: iso(NOW - r.range(1, 40) * DAY),
    details: {
      spectrum,
      model_versions: { brouhaha: 'brouhaha-0.9.0', dnsmos: 'dnsmos-p835-v1', silero: 'silero-vad-v5' },
    },
  }
}

export function buildDB(): DB {
  const r = rngFor(20261027)
  const runId = r.uuid()
  const channels: T.ChannelDetail[] = []
  const media: MockMedia[] = []
  const playlists: T.Playlist[] = []
  const playlistItems = new Map<string, string[]>()
  const chIds = CHANNEL_DEFS.map(() => r.uuid())
  const speakers = makeSpeakers(r, runId, chIds)
  const db: DB = {
    users: [
      { id: r.uuid(), username: 'brian', display_name: '陳柏宇', role: 'admin', is_active: true, created_at: iso(NOW - 120 * DAY), last_login_at: iso(NOW - 3600_000), password: 'voti' },
      { id: r.uuid(), username: 'yijun', display_name: '林怡君', role: 'annotator', is_active: true, created_at: iso(NOW - 90 * DAY), last_login_at: iso(NOW - 2 * DAY), password: 'voti' },
      { id: r.uuid(), username: 'chihming', display_name: '黃志明', role: 'annotator', is_active: true, created_at: iso(NOW - 60 * DAY), last_login_at: iso(NOW - 6 * 3600_000), password: 'voti' },
      { id: r.uuid(), username: 'yating', display_name: '張雅婷', role: 'annotator', is_active: false, created_at: iso(NOW - 200 * DAY), last_login_at: iso(NOW - 80 * DAY), password: 'voti' },
    ],
    settings: {
      youtube: { rate_limit_kbps: 0, sleep_between_downloads_sec: 5, sleep_between_requests_sec: 2, max_concurrent_downloads: 1, cookies_enabled: false, auto_refresh_hours: 12, verify_captions: true },
      pipeline: { voxprofile_backbone: 'whisper-large-v3', diarization_enabled: true, segment_min_sec: 1.0, segment_max_sec: 20.0, edge_pad_sec: 0.1 },
      quality_thresholds: { low_speech_ratio: 0.5, low_snr_db: 10, clipping_ratio: 0.001 },
      text: { convert_simplified: true, opencc_config: 's2twp' },
      validation: { enabled: true, lid_min_prob: 0.7, lid_min_speech_sec: 1.0, align_min_score: 0.4, align_min_units: 4 },
      ocr: { enabled: true, auto_when_no_captions: true, fps: 5, discovery_step_sec: 2, max_discovery_frames: 900, model: 'server', cpu_model: 'mobile', keep_crops: true, min_event_conf: 0.85 },
      cookies: { present: true, updated_at: iso(NOW - 9 * DAY) },
    },
    channels,
    playlists,
    playlistItems,
    media,
    mediaById: new Map(),
    segments: new Map(),
    segById: new Map(),
    speakers,
    runs: [],
    repeated: new Map(),
    regions: new Map(),
    jobs: [],
    datasets: [],
    exports: [],
    me: null,
    aligned: new Set(),
    demo: new Set(),
    ocrMedia: new Set(),
    ocr: new Map(),
  }

  CHANNEL_DEFS.forEach((def, ci) => {
    const cid = chIds[ci]
    const chSpeakers = speakers.filter((s) => s.channel_ids[0] === cid)
    const mlist: MockMedia[] = []
    const startY = new Date(`${def.years[0]}-01-15`).getTime()
    const endY = Math.min(NOW - 2 * DAY, new Date(`${def.years[1]}-12-20`).getTime())
    for (let i = 0; i < def.size; i++) {
      const isShort = r.chance(def.shorts)
      const pub = endY - (i / def.size) * (endY - startY) - r.range(0, 3) * DAY
      const detailed = def.list_status === 'listing' ? r.chance(0.4) : r.chance(0.9)
      const manual = detailed ? r.chance(def.manual) : null
      const auto = detailed ? r.chance(def.auto) || (!manual && r.chance(0.3)) : null
      const sr = r.pick(def.sr)
      const ups = r.chance(def.upsampled)
      const dur = isShort ? r.range(14, 59) : r.range(def.durMin[0], def.durMin[1]) * 60
      let title = def.title_fn(r, i)
      if (isShort && !title.includes('#Shorts')) title = `${title.replace(/｜.*$/, '')} #Shorts`
      const m: MockMedia = {
        id: r.uuid(),
        platform: 'youtube',
        platform_media_id: r.ytid(),
        url: '',
        kind: isShort ? 'short' : 'video',
        title,
        thumbnail_url: null,
        duration_sec: Math.round(dur),
        published_at: iso(pub),
        view_count: Math.round(def.subs * r.range(0.02, 1.4) * (isShort ? 2.5 : 1)),
        like_count: null,
        license: r.chance(def.cc) ? 'creative_commons' : 'youtube',
        has_manual_captions: manual,
        has_auto_captions: auto,
        manual_caption_langs: manual ? (r.chance(0.8) ? ['zh-TW'] : r.chance(0.5) ? ['zh-TW', 'nan'] : ['nan']) : [],
        auto_caption_langs: auto ? (r.chance(0.85) ? ['zh'] : ['zh', 'en']) : [],
        status: detailed ? 'detailed' : 'listed',
        stage: null,
        failure_code: null,
        channel: { id: cid, title: def.title },
        quality: null,
        segment_count: 0,
        channel_idx: ci,
        description: `${def.desc}\n\n本集內容：${title}\n\n#台語 #${def.title}`,
        sample_rate: sr,
        q: null,
        speakers: [],
      }
      m.url = isShort ? `https://www.youtube.com/shorts/${m.platform_media_id}` : `https://www.youtube.com/watch?v=${m.platform_media_id}`
      m.like_count = Math.round((m.view_count ?? 0) * r.range(0.01, 0.05))
      // processing state: the most recent captioned videos are the processed ones
      const usable = manual === true
      const roll = r.next()
      if (usable && roll < def.readyShare * 2.6) {
        m.status = 'ready'
      } else if (usable && roll < def.readyShare * 2.6 + 0.03) {
        m.status = r.pick(['queued', 'queued', 'downloading', 'processing', 'processing', 'downloaded'] as const)
        if (m.status === 'processing') m.stage = r.pick(['media.quality', 'media.diarize', 'media.segment', 'media.embed', 'media.profile'])
        if (m.status === 'downloading') m.stage = 'media.download'
      } else if (usable && roll < def.readyShare * 2.6 + 0.045) {
        m.status = 'failed'
        m.failure_code = r.pick(['download_forbidden', 'legacy_ml_timeout', 'no_usable_track'])
        m.stage = m.failure_code === 'legacy_ml_timeout' ? 'media.quality' : 'media.download'
      }
      if (m.status === 'ready' || m.status === 'processing') {
        m.q = quality(r, def, sr, ups)
        m.q.speech_sec = round((m.duration_sec ?? 0) * (m.q.speech_ratio ?? 0.7), 1)
        if (m.status === 'ready')
          m.quality = { snr_mean: m.q.snr_mean, dnsmos_ovrl: m.q.dnsmos_ovrl, real_bandwidth_hz: m.q.real_bandwidth_hz, speech_ratio: m.q.speech_ratio, sample_rate: m.q.sample_rate, upsampled_suspect: m.q.upsampled_suspect }
        // speakers in this media: 1–2 hosts + 0–2 guests
        const hosts = chSpeakers.slice(0, def.hosts.length)
        const guests = chSpeakers.slice(def.hosts.length)
        const sp = new Set<string>()
        sp.add(r.pick(hosts).id)
        if (hosts.length > 1 && r.chance(0.6)) sp.add(r.pick(hosts).id)
        if (guests.length && !isShort) for (let g = 0; g < r.int(0, 2); g++) sp.add(r.pick(guests).id)
        m.speakers = [...sp]
      }
      mlist.push(m)
    }
    media.push(...mlist)

    const counts = {
      listed: mlist.length,
      detailed: mlist.filter((m) => m.has_manual_captions !== null).length,
      with_manual_captions: mlist.filter((m) => m.has_manual_captions).length,
      downloaded: mlist.filter((m) => ['downloaded', 'processing', 'ready'].includes(m.status)).length,
      ready: mlist.filter((m) => m.status === 'ready').length,
      failed: mlist.filter((m) => m.status === 'failed').length,
    }
    const sum = (xs: MockMedia[], f: (m: MockMedia) => number) => xs.reduce((a, m) => a + f(m), 0)
    const ready = mlist.filter((m) => m.status === 'ready')
    channels.push({
      id: cid,
      platform: 'youtube',
      title: def.title,
      handle: def.handle,
      url: `https://www.youtube.com/${def.handle}`,
      thumbnail_url: null,
      subscriber_count: def.subs,
      video_count: def.list_status === 'listing' ? def.size + 41 : def.size,
      list_status: def.list_status ?? 'listed',
      last_listed_at: def.list_status === 'listing' ? null : iso(NOW - (ci + 1) * 0.7 * DAY),
      next_refresh_at: def.list_status === 'listing' ? null : iso(NOW + (ci * 2.3 + 1.5) * 3600_000),

      detail_progress: { detailed: counts.detailed, total: counts.listed, active: false },
      counts,
      hours: {
        listed: round(sum(mlist, (m) => m.duration_sec ?? 0) / 3600, 1),
        ready: round(sum(ready, (m) => m.duration_sec ?? 0) / 3600, 1),
        speech: round(sum(ready, (m) => m.q?.speech_sec ?? 0) / 3600, 1),
      },
      description: def.desc,
      banner_url: null,
      country: 'TW',
      tags: ['台語', 'Taiwanese', def.title],
      notes: ci === 0 ? '主播語料品質高，但語者集中在 4 位主播；需要多收外景受訪者。' : ci === 1 ? '阿嬤 76 歲，年長女性語者的重要來源。灶跤背景音偏多。' : '',
      metadata: {},
      speakers: { candidates: chSpeakers.length, total_sec: 0 },
      quality: {
        snr_mean: ready.length ? round(sum(ready, (m) => m.q!.snr_mean!) / ready.length, 1) : null,
        dnsmos_ovrl: ready.length ? round(sum(ready, (m) => m.q!.dnsmos_ovrl!) / ready.length, 2) : null,
        speech_ratio: ready.length ? round(sum(ready, (m) => m.q!.speech_ratio!) / ready.length, 2) : null,
      },
      kind_counts: { video: mlist.filter((m) => m.kind === 'video').length, short: mlist.filter((m) => m.kind === 'short').length },
    })

    // playlists
    const plNames =
      ci === 0 ? ['台語新聞 2026', '台灣好所在', '逐工一句台語', '講台語過生活'] : ci === 1 ? ['過年料理', '日常小菜', '阿嬤講古早'] : ci === 2 ? ['初級課程', '日常會話', '一分鐘學台語'] : ci === 4 ? ['全部集數'] : []
    plNames.forEach((name) => {
      const items = mlist.filter(() => r.chance(0.25)).map((m) => m.id)
      const pl: T.Playlist = {
        id: r.uuid(),
        platform: 'youtube',
        title: name,
        description: `${def.title}播放清單：${name}`,
        url: `https://www.youtube.com/playlist?list=PL${r.ytid()}${r.ytid()}`,
        channel_id: cid,
        list_status: 'listed',
        last_listed_at: iso(NOW - r.range(1, 20) * DAY),
        media_count: items.length,
      }
      playlists.push(pl)
      playlistItems.set(pl.id, items)
    })
    channels[ci].playlist_count = plNames.length

    // repeated regions (intro / outro) groups
    const groups: T.RepeatedGroup[] = []
    const readyM = mlist.filter((m) => m.status === 'ready' && m.kind === 'video')
    if (readyM.length > 3) {
      const defs: [T.RepeatPosition, number, number][] = [
        ['intro', 0, r.range(5, 11)],
        ['outro', -1, r.range(8, 16)],
      ]
      if (ci === 0 || ci === 4) defs.push(['middle', 0.5, r.range(3, 7)])
      defs.forEach(([pos, at, len], gi) => {
        const ex = readyM.filter(() => r.chance(gi === 2 ? 0.25 : 0.8))
        const group_id = r.uuid()
        ex.forEach((m) => {
          const d = m.duration_sec ?? 600
          const s = at === 0 ? 0.4 : at === -1 ? d - len - 1 : d * at
          const list = db.regions.get(m.id) ?? []
          list.push({ id: r.uuid(), group_id, start: round(s, 2), end: round(s + len, 2), position: pos })
          db.regions.set(m.id, list)
        })
        groups.push({
          group_id,
          position: pos,
          duration: round(len, 1),
          match_count: ex.length,
          excluded: pos !== 'middle',
          examples: ex.slice(0, 6).map((m) => {
            const rg = db.regions.get(m.id)!.find((x) => x.group_id === group_id)!
            return { media_id: m.id, start: rg.start, end: rg.end, media_title: m.title }
          }),
        })
      })
    }
    db.repeated.set(cid, groups)
  })

  media.forEach((m) => db.mediaById.set(m.id, m))
  // hard-subtitle OCR demo: a video without CC whose burned-in subtitles were OCRed (third channel, oldest ready video)
  const ocrDemo = media.filter((m) => m.channel_idx === 2 && m.status === 'ready' && m.kind === 'video' && !m.title.includes('Shorts') && (m.duration_sec ?? 0) > 300).at(-1)
  if (ocrDemo) {
    Object.assign(ocrDemo, { has_manual_captions: false, has_auto_captions: false, manual_caption_langs: [], auto_caption_langs: [] })
    db.ocrMedia.add(ocrDemo.id)
  }
  // eager segment generation for all ready media (keeps speaker / diversity totals consistent)
  for (const m of media) if (m.status === 'ready') genSegments(db, m)
  // speaker totals
  const spMap = new Map(speakers.map((s) => [s.id, s]))
  const spMedia = new Map<string, Set<string>>()
  for (const segs of db.segments.values())
    for (const s of segs) {
      if (!s.speaker) continue
      const sp = spMap.get(s.speaker.id)!
      sp.segment_count++
      sp.total_sec += s.duration
      if (!spMedia.has(sp.id)) spMedia.set(sp.id, new Set())
      spMedia.get(sp.id)!.add(s.media_id)
    }
  speakers.forEach((s) => {
    s.total_sec = round(s.total_sec, 1)
    s.media_count = spMedia.get(s.id)?.size ?? 0
  })
  db.speakers = speakers.filter((s) => s.segment_count > 0).sort((a, b) => b.total_sec - a.total_sec)
  channels.forEach((c) => {
    const sps = speakersOfChannel(db, c.id)
    c.speakers = { candidates: sps.length, total_sec: round(sps.reduce((a, s) => a + s.total_sec, 0), 1) }
  })

  db.runs = [
    { id: runId, model_id: 'speechbrain/spkrec-ecapa-voxceleb', algorithm: 'agglomerative', threshold: 0.72, scope: {}, params: { linkage: 'average', min_cluster_sec: 10 }, stats: { speakers: db.speakers.length, segments: [...db.segments.values()].reduce((a, x) => a + x.length, 0), singletons: 7 }, is_current: true, created_at: iso(NOW - 2 * DAY) },
    { id: r.uuid(), model_id: 'speechbrain/spkrec-ecapa-voxceleb', algorithm: 'agglomerative', threshold: 0.65, scope: {}, params: { linkage: 'average' }, stats: { speakers: db.speakers.length - 9, segments: 11842, singletons: 3 }, is_current: false, created_at: iso(NOW - 9 * DAY) },
    { id: r.uuid(), model_id: 'speechbrain/spkrec-ecapa-voxceleb', algorithm: 'agglomerative', threshold: 0.8, scope: { channel_ids: [chIds[0]] }, params: { linkage: 'average' }, stats: { speakers: 31, segments: 6120, singletons: 12 }, is_current: false, created_at: iso(NOW - 16 * DAY) },
  ]

  // demo state: newest ready media per channel (s2t conversion, word alignment), "new" videos from auto refresh
  channels.forEach((c, ci) => {
    const mine = media.filter((m) => m.channel?.id === c.id).sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))
    const demo = mine.find((m) => m.status === 'ready')
    if (demo) db.demo.add(demo.id)
    if (ci === 0 || ci === 1) {
      const fresh = mine.filter((m) => m.status === 'listed' || m.status === 'detailed').slice(0, ci === 0 ? 3 : 2)
      fresh.forEach((m) => (m.is_new = true))
      c.counts.new = fresh.length
    }
    // every media status (incl. members-only) on the first page of the first channel: status-badge screenshots
    if (ci === 0) {
      const pool = mine.filter((m) => (m.status === 'listed' || m.status === 'detailed') && !m.is_new && (m.has_manual_captions === true || m.has_auto_captions !== true)).slice(2, 11)
      const demo: [T.MediaStatus, string | null, string | null][] = [
        ['skipped', 'members_only', null], ['blocked', 'bot_check', 'media.download'], ['skipped', 'no_usable_captions', null], ['queued', null, null],
        ['downloading', null, 'media.download'], ['downloaded', null, null], ['processing', null, 'media.diarize'], ['failed', 'download_forbidden', 'media.download'],
        ['skipped', 'empty_captions', null],
      ]
      pool.forEach((m, k) => {
        const [st, code, stage] = demo[k]
        Object.assign(m, { status: st, failure_code: code, stage })
        // YouTube listed an uploader track whose content turned out empty
        if (code === 'empty_captions')
          Object.assign(m, {
            has_manual_captions: true,
            manual_caption_langs: [],
            captions_empty: true,
            caption_check: { checked_at: iso(NOW - DAY), source: 'detail', tracks: { 'zh-TW': { status: 'empty', cues: 2, speech_cues: 0, chars: 0, format: 'vtt' } }, usable_langs: [] },
          })
      })
    }
  })
  channels[0].detail_progress = { detailed: channels[0].counts.detailed, total: channels[0].counts.listed, active: true }
  for (const m of media) {
    if (m.status !== 'ready') continue
    const taiwaneseOnly = m.manual_caption_langs.length > 0 && m.manual_caption_langs.every((l) => l === 'nan')
    if (!taiwaneseOnly || db.demo.has(m.id)) db.aligned.add(m.id)
    if (db.demo.has(m.id)) for (const sg of db.segments.get(m.id) ?? []) if (sg.id.charCodeAt(1) % 3 === 0) sg.flags = [...sg.flags, 's2t_converted']
  }
  addPendingChannel(db)

  buildJobs(db, r)
  buildDatasets(db, r)
  return db
}

/** A channel added a moment ago whose first listing is still waiting in the queue. */
function addPendingChannel(db: DB) {
  const r = rngFor(424242)
  db.channels.push({
    id: r.uuid(), platform: 'youtube', title: '客家鄉親講古', handle: '@hakka-koo', url: 'https://www.youtube.com/@hakka-koo',
    thumbnail_url: null, subscriber_count: null, video_count: null, list_status: 'queued', last_listed_at: null,
    counts: { listed: 0, detailed: 0, with_manual_captions: 0, downloaded: 0, ready: 0, failed: 0 }, hours: { listed: 0, ready: 0, speech: 0 },
    description: '', banner_url: null, country: null, tags: [], notes: '', metadata: {}, speakers: { candidates: 0, total_sec: 0 },
    quality: { snr_mean: null, dnsmos_ovrl: null }, kind_counts: { video: 0, short: 0 }, playlist_count: 0,
    next_refresh_at: null, detail_progress: { detailed: 0, total: 0, active: false },
  })
  db.repeated.set(db.channels[db.channels.length - 1].id, [])
}

/** Character timings for a segment (absolute media seconds); punctuation is untimed. */
export function wordsFor(s: Pick<T.SegmentSummary, 'id' | 'text' | 'start_sec' | 'end_sec'>): T.WordUnit[] {
  const chars = Array.from(s.text)
  const PUNCT = /[\s，。、？！：；「」『』（）《》〈〉…—,.?!:;"'()]/
  const timed = chars.filter((c) => !PUNCT.test(c)).length || 1
  const r = rngFor(s.id.charCodeAt(0) * 97 + s.id.charCodeAt(4))
  const t0 = s.start_sec + 0.12
  const span = Math.max(0.2, s.end_sec - 0.15 - t0)
  const w = Array.from({ length: timed }, () => r.range(0.7, 1.3))
  const tot = w.reduce((a, b) => a + b, 0)
  let t = t0
  let k = 0
  return chars.map((c) => {
    if (PUNCT.test(c)) return { t: c, s: null, e: null }
    const d = (w[k++] / tot) * span
    const u = { t: c, s: round(t, 3), e: round(t + d * 0.92, 3) }
    t += d
    return u
  })
}

export function genSegments(db: DB, m: MockMedia): T.SegmentDetail[] {
  const have = db.segments.get(m.id)
  if (have) return have
  const idx = db.media.indexOf(m)
  const r = rngFor(9000 + idx * 7919)
  const segs: T.SegmentDetail[] = []
  const dur = m.duration_sec ?? 60
  const q = m.q!
  const groups = db.repeated.get(m.channel!.id) ?? []
  const reps = (db.regions.get(m.id) ?? []).map((rg) => ({ ...rg, excluded: groups.find((g) => g.group_id === rg.group_id)?.excluded ?? true }))
  const localMap = new Map<string, string>()
  m.speakers.forEach((sid, k) => localMap.set(sid, `SPEAKER_${String(k).padStart(2, '0')}`))
  const spLookup = new Map(db.speakers.map((s) => [s.id, s]))
  let t = r.range(0.5, 3)
  const maxSegs = 150
  // long media: spread the capped number of segments over the whole duration
  const gapMean = Math.max(0.8, (dur - maxSegs * 5.2) / maxSegs)
  const manual = !!m.has_manual_captions
  const ocr = db.ocrMedia.has(m.id)
  while (t < dur - 1.5 && segs.length < maxSegs) {
    const len = clamp(r.normal(5.2, 2.6), 0.8, 19.5)
    const start = t
    const end = Math.min(dur - 0.2, start + len)
    const sid = m.speakers.length ? (r.chance(0.7) ? m.speakers[0] : r.pick(m.speakers)) : null
    const sp = sid ? spLookup.get(sid) : null
    const snr = clamp(r.normal(q.snr_mean ?? 20, 4.5), -2, 45)
    const speechRatio = clamp(r.normal(q.speech_ratio ?? 0.75, 0.12), 0.15, 1)
    const ovrl = clamp(1.5 + snr * 0.055 + r.normal(0, 0.22), 1.1, 4.3)
    const repOverlap = reps.reduce((a, rp) => {
      const o = Math.max(0, Math.min(end, rp.end) - Math.max(start, rp.start))
      return rp.excluded ? a + o / (end - start) : a
    }, 0)
    const state: T.SpeakerState = r.chance(0.05) ? 'overlap' : r.chance(0.04) ? 'multiple' : r.chance(0.7) ? 'single' : 'unknown'
    const flags: string[] = []
    if (snr < db.settings.quality_thresholds.low_snr_db) flags.push('low_snr')
    if (speechRatio < db.settings.quality_thresholds.low_speech_ratio) flags.push('low_speech')
    if (r.chance(0.015)) flags.push('clipped')
    if (repOverlap > 0) flags.push('repeated')
    if (state === 'overlap') flags.push('overlap')
    if (end - start < 1) flags.push('too_short')
    const roll = r.next()
    const review: T.ReviewStatus = segs.length < 3 && idx % 3 === 0 ? 'unchecked' : roll < 0.2 ? 'passed' : roll < 0.235 ? 'needs_fix' : roll < 0.26 ? 'excluded' : 'unchecked'
    const original = Array.from({ length: len > 9 ? 2 : 1 }, () => r.pick(PHRASES)).join('')
    const corrected = review === 'passed' && r.chance(0.25) ? original.replace('。', '，') + '' : null
    const age = sp ? clamp(sp.age + r.normal(0, 6), 8, 95) : null
    const emo = sp ? (r.chance(0.65) ? sp.emo : r.pick(EMOS)) : null
    const seg: T.SegmentDetail = {
      id: r.uuid(),
      media_id: m.id,
      media_title: m.title,
      channel_title: m.channel!.title,
      start_sec: round(start, 2),
      end_sec: round(end, 2),
      duration: round(end - start, 2),
      text: corrected ?? original,
      original_text: original,
      corrected_text: corrected,
      text_source: ocr ? 'ocr_caption' : corrected ? 'human_revision' : manual ? 'manual_caption' : 'auto_caption',
      local_speaker: sid ? localMap.get(sid)! : null,
      speaker: sp && state !== 'multiple' && state !== 'overlap' ? { id: sp.id, label: sp.label, display_name: sp.display_name } : null,
      speaker_state: state,
      speech_ratio: round(speechRatio, 3),
      snr: round(snr, 1),
      c50: round(clamp(r.normal(q.c50_mean ?? 30, 6), -5, 60), 1),
      dnsmos_ovrl: round(ovrl, 2),
      dnsmos_p808: round(clamp(ovrl + r.range(0.15, 0.5), 1, 5), 2),
      flags,
      review_status: review,
      revision: r.chance(0.05) ? 2 : 1,
      track_id: null,
      cue_ids: [r.uuid()],
      dnsmos_sig: round(clamp(ovrl + r.range(0.3, 0.6), 1, 5), 2),
      dnsmos_bak: round(clamp(ovrl + r.range(0.4, 1), 1, 5), 2),
      clipping_ratio: round(flags.includes('clipped') ? r.range(0.002, 0.02) : r.range(0, 0.0005), 5),
      lufs: round(r.range(-28, -16), 1),
      repeated_overlap: round(clamp(repOverlap, 0, 1), 2),
      review_note: review === 'needs_fix' ? r.pick(['字幕與發音不一致', '結尾被切掉一個字', '背景有音樂']) : '',
      reviewed_by: review !== 'unchecked' ? { id: db.users[1].id, display_name: db.users[1].display_name } : null,
      reviewed_at: review !== 'unchecked' ? iso(NOW - r.range(0.1, 20) * DAY) : null,
      profile:
        sp && state !== 'multiple' && state !== 'overlap' && end - start >= 1
          ? {
              backbone: 'whisper-large-v3',
              age: round(age!, 1),
              age_group: AGE_GROUP(age!),
              sex: sp.sex,
              sex_prob: round(sp.sex === 'male' ? (sp.profile.sex?.male ?? 0.9) : (sp.profile.sex?.female ?? 0.9), 3),
              voice_quality: sp.profile.voice_quality ?? {},
              emotion: Object.fromEntries(EMOS.map((e) => [e, e === emo ? round(r.range(0.5, 0.8), 2) : round(r.range(0, 0.15), 2)])),
              emotion_dim: { arousal: round(r.range(0.2, 0.8), 2), valence: round(r.range(0.3, 0.8), 2), dominance: round(r.range(0.3, 0.7), 2) },
              speech_flow: { fluent: round(r.range(0.6, 0.95), 2), disfluent: round(r.range(0.05, 0.3), 2), fast: round(r.range(0, 0.4), 2), slow: round(r.range(0, 0.4), 2) },
              accent_broad: { 'East Asian': round(r.range(0.55, 0.9), 2), 'Southeast Asian': round(r.range(0.05, 0.2), 2), Other: round(r.range(0.01, 0.1), 2) },
              accent_narrow: { Chinese: round(r.range(0.4, 0.8), 2), Taiwanese: round(r.range(0.05, 0.3), 2), Japanese: round(r.range(0.01, 0.1), 2) },
              model_versions: { voxprofile: 'vox-profile-2025.06', backbone: 'whisper-large-v3' },
            }
          : null,
      history:
        review !== 'unchecked'
          ? [
              {
                id: r.uuid(),
                at: iso(NOW - r.range(0.1, 20) * DAY),
                user: { id: db.users[1].id, display_name: db.users[1].display_name },
                action: 'review',
                before: { review_status: 'unchecked' },
                after: { review_status: review },
              },
            ]
          : [],
    }
    segs.push(seg)
    db.segById.set(seg.id, seg)
    t = end + clamp(r.normal(gapMean, gapMean * 0.5 + 0.5), 0.05, gapMean * 3 + 6)
  }
  applyValidity(segs, idx)
  if (ocr) {
    // OCR text needs a usable alignment score: without one the segment stays unknown (validation.note ocr_unverified)
    segs.forEach((s, i) => {
      if (i % 11 === 5 && s.validity === 'valid' && s.validity_source === 'auto')
        Object.assign(s, { validity: 'unknown', align_score: null, validation: { ...s.validation, note: 'ocr_unverified' } })
    })
    db.ocr.set(m.id, genOcr(m, segs, idx))
  }
  m.segment_count = segs.length
  db.segments.set(m.id, segs)
  return segs
}

/** Caption ↔ audio validation demo states: English interview clips with Chinese captions (language_mismatch),
 * a caption that does not match its audio (text_mismatch), one manual invalid and one manual valid decision. */
function applyValidity(segs: T.SegmentDetail[], mediaIdx: number) {
  const r = rngFor(77_000 + mediaIdx * 131)
  const th = { lid_min_prob: 0.7, align_min_score: 0.4, align_min_units: 4, lid_min_speech_sec: 1 }
  segs.forEach((s, i) => {
    const zh = round(r.range(0.93, 0.999), 3)
    const lid: [string, number][] = [['zh', zh], ['ko', round((1 - zh) * 0.7, 3)], ['ja', round((1 - zh) * 0.2, 3)]]
    let v: Pick<T.SegmentDetail, 'validity' | 'invalid_reason' | 'validity_source' | 'lid_lang' | 'lid_prob' | 'align_score'> = {
      validity: 'valid', invalid_reason: null, validity_source: 'auto', lid_lang: 'zh', lid_prob: zh, align_score: round(r.range(0.6, 0.93), 3),
    }
    let top = lid
    const english = i === 3 || i === 4 || (i >= 9 && i % 13 === 9)
    if (english) {
      const en = i === 3 ? 0.92 : round(r.range(0.9, 0.999), 3)
      top = [['en', en], ['cy', round((1 - en) * 0.6, 3)], ['zh', round((1 - en) * 0.3, 3)]]
      v = { validity: 'invalid', invalid_reason: 'language_mismatch', validity_source: 'auto', lid_lang: 'en', lid_prob: en, align_score: round(r.range(0.13, 0.33), 3) }
    } else if (i === 7 || (i > 20 && i % 29 === 7)) {
      v = { ...v, validity: 'invalid', invalid_reason: 'text_mismatch', align_score: round(r.range(0.12, 0.36), 3) }
    } else if (i === 11) {
      v = { ...v, validity: 'invalid', invalid_reason: 'manual', validity_source: 'manual' }
    } else if (i === 12) {
      // the check said English, a reviewer disagreed
      top = [['en', 0.74], ['zh', 0.24], ['ko', 0.01]]
      v = { validity: 'valid', invalid_reason: null, validity_source: 'manual', lid_lang: 'en', lid_prob: 0.74, align_score: 0.52 }
    } else if (s.duration < 1) {
      v = { ...v, validity: 'unknown', lid_lang: null, lid_prob: null }
      top = []
    }
    Object.assign(s, v, {
      validation: { lid: top.length ? top : null, expected: 'zh', align_units: Math.max(4, s.text.length - 2), thresholds: th, at: iso(NOW - DAY), version: 1 },
    })
  })
}

export function summary(s: T.SegmentDetail): T.SegmentSummary {
  return {
    id: s.id, media_id: s.media_id, media_title: s.media_title, channel_title: s.channel_title,
    start_sec: s.start_sec, end_sec: s.end_sec, duration: s.duration, text: s.text, original_text: s.original_text,
    corrected_text: s.corrected_text, text_source: s.text_source, local_speaker: s.local_speaker, speaker: s.speaker,
    speaker_state: s.speaker_state, speech_ratio: s.speech_ratio, snr: s.snr, c50: s.c50, dnsmos_ovrl: s.dnsmos_ovrl,
    dnsmos_p808: s.dnsmos_p808, flags: s.flags, review_status: s.review_status, revision: s.revision,
    profile: s.profile ? { age: s.profile.age, sex: s.profile.sex, emotion_label: topKey(s.profile.emotion) } : null,
    validity: s.validity, invalid_reason: s.invalid_reason, validity_source: s.validity_source, lid_lang: s.lid_lang, lid_prob: s.lid_prob,
    align_score: s.align_score,
  }
}
export function topKey(d: Record<string, number> | undefined | null) {
  if (!d) return null
  let best: string | null = null
  let v = -1
  for (const [k, x] of Object.entries(d)) if (x > v) [best, v] = [k, x]
  return best
}

const STEP_DUR: Record<string, number> = {
  'media.download': 26_000, 'media.normalize': 9_000, 'media.quality': 30_000, 'media.diarize': 22_000,
  'media.segment': 6_000, 'media.embed': 14_000, 'media.profile': 25_000, 'media.fingerprint': 8_000,
  'media.ocr': 14_000, 'channel.list': 20_000, 'media.detail': 4_000, 'speakers.cluster': 12_000, 'dataset.export': 9_000, 'channel.dedup': 15_000,
}
const QUEUE: Record<string, T.QueueName> = {
  'source.resolve': 'scan', 'channel.list': 'scan', 'playlist.list': 'scan', 'media.detail': 'detail', 'media.download': 'download',
  'media.normalize': 'audio', 'media.segment': 'audio', 'media.quality': 'ml', 'media.diarize': 'ml', 'media.embed': 'ml',
  'media.profile': 'ml', 'media.fingerprint': 'ml', 'channel.dedup': 'ml', 'speakers.cluster': 'ml', 'dataset.export': 'export',
  'media.convert_text': 'audio', 'media.align': 'ml', 'media.validate': 'ml', 'media.ocr': 'ml',
}
export const PIPE: T.JobKind[] = ['media.download', 'media.normalize', 'media.quality', 'media.diarize', 'media.segment', 'media.embed', 'media.profile', 'media.fingerprint']

export function newJob(db: DB, kind: T.JobKind, o: Partial<T.Job> & { _dur?: number } = {}, seed = db.jobs.length + 1) {
  const r = rngFor(seed * 31 + 7)
  const m = o.media_id ? db.mediaById.get(o.media_id) : null
  const ch = o.channel_id ? db.channels.find((c) => c.id === o.channel_id) : null
  const job: T.Job & { _dur?: number } = {
    id: r.uuid(),
    kind,
    queue: QUEUE[kind] ?? 'scan',
    status: 'queued',
    progress: 0,
    phase: '',
    message: '',
    error_code: null,
    error_message: null,
    attempts: 1,
    media_id: null,
    channel_id: null,
    media_title: m?.title ?? null,
    channel_title: ch?.title ?? null,
    created_at: iso(Date.now()),
    started_at: null,
    finished_at: null,
    eta_sec: null,
    _dur: STEP_DUR[kind] ?? 10_000,
    ...o,
  }
  db.jobs.unshift(job)
  return job
}

/* Phase codes as the real workers report them (Job.phase), with the zh-TW progress message (Job.message). */
type PhaseMsg = (frac: number) => string
const PHASES: Record<string, [string, PhaseMsg][]> = {
  'media.download': [['download', () => '取得影片資訊'], ['audio', (f) => `下載音訊 ${Math.round(f * 100)}%`], ['store', () => '上傳音訊與字幕']],
  'media.normalize': [['normalize', () => ''], ['spectrum', () => '']],
  'media.quality': [['brouhaha', () => ''], ['silero', () => ''], ['dnsmos', () => ''], ['save', () => '']],
  'media.diarize': [['diarize', () => ''], ['diarize:segmentation', () => ''], ['save', () => '']],
  'media.segment': [['plan', () => ''], ['dnsmos', () => ''], ['save', () => '']],
  'media.embed': [['ecapa', () => ''], ['save', () => '']],
  'media.profile': [['voxprofile', (f) => `${Math.round(f * 48)}/48`]],
  'media.fingerprint': [['fingerprint', () => ''], ['match', () => ''], ['save', () => '']],
  'channel.list': [['listing', (f) => `videos: ${Math.round(f * 160)} 支`]],
  'speakers.cluster': [['load', () => ''], ['cluster', () => ''], ['save', () => '']],
  'dataset.export': [['select', () => '篩選音段'], ['audio', (f) => `${Math.round(f * 7348)}/7348`], ['manifest', () => '寫入清單']],
  'media.convert_text': [['convert_text', () => 'OpenCC s2twp']],
  'media.align': [['align', () => '']],
  'media.validate': [['load_model', () => ''], ['lid', (f) => `${Math.round(f * 60)}/60`]],
  'media.ocr': [['load_model', () => 'PP-OCRv5 server'], ['discover', (f) => `${Math.round(f * 300)}/300`], ['track', (f) => `${Math.round(f * 100)}%`], ['crops', () => '']],
}

/** Advance the simulated queue to wall-clock now. */
export function tick(db: DB) {
  const now = Date.now()
  // demo retry: keep the countdown running in the mock
  for (const j of db.jobs as (T.Job & { _retryLoop?: boolean })[])
    if (j._retryLoop && j.run_after && new Date(j.run_after).getTime() < now + 5000) j.run_after = iso(now + 95_000)
  for (const j of db.jobs) {
    if (j.status !== 'running' || !j.started_at) continue
    const dur = j._dur ?? 10_000
    const el = now - new Date(j.started_at).getTime()
    if (j.error_code === 'pending_fail' && el > dur * 0.6) {
      j.status = 'failed'
      j.error_code = 'legacy_ml_timeout'
      j.error_message = 'legacy-ml 服務在 120 秒內沒有回應（Brouhaha）。'
      j.finished_at = iso(now)
      const m = j.media_id ? db.mediaById.get(j.media_id) : null
      if (m) {
        m.status = 'failed'
        m.failure_code = 'legacy_ml_timeout'
      }
      continue
    }
    if (el >= dur) {
      j.status = 'completed'
      j.progress = 1
      j.finished_at = iso(now)
      j.eta_sec = null
      onComplete(db, j)
    } else {
      j.progress = round(el / dur, 3)
      j.eta_sec = Math.round((dur - el) / 1000)
      const ph = PHASES[j.kind]
      if (ph) {
        const f = el / dur
        const [code, msg] = ph[Math.min(ph.length - 1, Math.floor(f * ph.length))]
        j.phase = code
        j.message = msg(f)
      }
    }
  }
  // start queued jobs when queue slots are free (fetch:1, audio:2, ml:1, export:1)
  const slots: Record<string, number> = { fetch: 2, audio: 2, ml: 2, export: 1 }
  for (const q of Object.keys(slots)) {
    const running = db.jobs.filter((j) => j.queue === q && j.status === 'running').length
    let free = slots[q] - running
    const waiting = db.jobs
      .filter((j) => j.queue === q && j.status === 'queued' && !(j as { _hold?: boolean })._hold && !(j.run_after && new Date(j.run_after).getTime() > now))
      .reverse()
    for (const j of waiting) {
      if (free <= 0) break
      j.status = 'running'
      j.started_at = iso(now)
      j.progress = 0
      free--
      const m = j.media_id ? db.mediaById.get(j.media_id) : null
      if (m) {
        m.status = j.kind === 'media.download' ? 'downloading' : 'processing'
        m.stage = j.kind
      }
    }
  }
  // exports follow their job
  for (const e of db.exports) {
    if (e.status === 'completed' || e.status === 'failed' || !e._start) continue
    const el = now - e._start
    if (el > 9000) {
      e.status = 'completed'
      e.progress = 1
      e.finished_at = iso(now)
      e.files = ['manifest.jsonl', 'manifest.csv', 'README.md', 'dataset.tar']
      e.size_bytes = Math.round(e.total_sec * 32000 * 1.02)
    } else if (el > 1500) {
      e.status = 'running'
      e.progress = round(el / 9000, 2)
    }
  }
}

function onComplete(db: DB, j: T.Job) {
  const m = j.media_id ? db.mediaById.get(j.media_id) : null
  if (m && j.kind === 'media.align') db.aligned.add(m.id)
  if (m && j.kind === 'media.ocr') {
    // a re-run re-segments afterwards; the mock just restores the media and finishes the result
    m.status = db.segments.get(m.id)?.length ? 'ready' : m.status === 'processing' ? 'ready' : m.status
    m.stage = null
    const o = db.ocr.get(m.id)
    if (o) o.summary = { ...o.summary, status: o.events.length ? 'done' : 'no_subtitles', at: iso(Date.now()), requested: 'rerun' }
  }
  if (m && j.kind.startsWith('media.') && PIPE.includes(j.kind)) {
    const i = PIPE.indexOf(j.kind)
    if (i >= 0 && i < PIPE.length - 1) {
      m.status = 'processing'
      m.stage = PIPE[i + 1]
      newJob(db, PIPE[i + 1], { media_id: m.id, status: 'queued' })
    } else if (i === PIPE.length - 1) {
      m.status = 'ready'
      m.stage = null
      if (!m.q) {
        const def = CHANNEL_DEFS[m.channel_idx]
        m.q = quality(rngFor(db.media.indexOf(m)), def, m.sample_rate, false)
        m.q.speech_sec = round((m.duration_sec ?? 0) * (m.q.speech_ratio ?? 0.7), 1)
      }
      m.quality = { snr_mean: m.q.snr_mean, dnsmos_ovrl: m.q.dnsmos_ovrl, real_bandwidth_hz: m.q.real_bandwidth_hz, speech_ratio: m.q.speech_ratio, sample_rate: m.q.sample_rate, upsampled_suspect: m.q.upsampled_suspect }
      if (!m.speakers.length) m.speakers = [speakersOfChannel(db, m.channel!.id)[0]?.id].filter(Boolean) as string[]
      genSegments(db, m)
    }
  }
  if (j.kind === 'speakers.cluster') {
    db.runs.forEach((r) => (r.is_current = false))
    const r0 = rngFor(db.runs.length * 13)
    db.runs.unshift({
      id: r0.uuid(), model_id: 'speechbrain/spkrec-ecapa-voxceleb', algorithm: 'agglomerative',
      threshold: (j as T.Job & { _threshold?: number })._threshold ?? 0.72, scope: {}, params: { linkage: 'average' },
      stats: { speakers: db.speakers.length, segments: [...db.segments.values()].reduce((a, x) => a + x.length, 0), singletons: 5 },
      is_current: true, created_at: iso(Date.now()),
    })
    db.speakers.forEach((s) => (s.run_id = db.runs[0].id))
  }
  if (j.kind === 'channel.list' && j.channel_id) {
    const c = db.channels.find((c) => c.id === j.channel_id)
    if (c) {
      c.list_status = 'listed'
      c.last_listed_at = iso(Date.now())
      c.next_refresh_at = iso(Date.now() + (db.settings.youtube.auto_refresh_hours || 12) * 3600_000)
    }
  }
}

function buildJobs(db: DB, r: ReturnType<typeof rngFor>) {
  const now = NOW
  // the OCR demo video's OCR job (between diarize and segment)
  for (const id of db.ocrMedia) {
    const t = now - 3 * DAY + 3600_000
    newJob(db, 'media.ocr', { media_id: id, status: 'completed', progress: 1, phase: 'done', created_at: iso(t - 30 * 60_000), started_at: iso(t - 26 * 60_000), finished_at: iso(t), payload: { ocr_auto: true } }, 4242)
  }
  // historic completed/failed jobs (oldest first so unshift keeps newest on top)
  const ready = db.media.filter((m) => m.status === 'ready').slice(0, 36)
  ready.forEach((m, k) => {
    for (const kind of PIPE) {
      const t = now - (k + 1) * 3.1 * 3600_000 - PIPE.indexOf(kind) * 240_000
      newJob(db, kind, { media_id: m.id, status: 'completed', progress: 1, created_at: iso(t - 60_000), started_at: iso(t - 50_000), finished_at: iso(t) }, 500 + k * 10 + PIPE.indexOf(kind))
    }
  })
  // failed media
  db.media
    .filter((m) => m.status === 'failed')
    .forEach((m, k) => {
      const t = now - (k + 1) * 47 * 60_000
      const msg: Record<string, [T.JobKind, string]> = {
        download_forbidden: ['media.download', 'YouTube 回應 403：此影片需要登入才能觀看（請上傳 cookies.txt）。'],
        legacy_ml_timeout: ['media.quality', 'legacy-ml 服務在 120 秒內沒有回應（Brouhaha）。'],
        no_usable_track: ['media.download', '找不到符合條件的字幕軌（需要 zh-TW 原生字幕）。'],
      }
      const [kind, text] = msg[m.failure_code ?? 'download_forbidden']
      newJob(db, kind, { media_id: m.id, status: 'failed', attempts: 3, error_code: m.failure_code, error_message: text, created_at: iso(t - 600_000), started_at: iso(t - 300_000), finished_at: iso(t) }, 900 + k)
    })
  newJob(db, 'speakers.cluster', { status: 'completed', progress: 1, created_at: iso(now - 2 * DAY), started_at: iso(now - 2 * DAY), finished_at: iso(now - 2 * DAY + 90_000) }, 990)
  // queued & running (relative to real wall clock so progress moves while you watch)
  const wall = Date.now()
  const active = db.media.filter((m) => ['queued', 'downloading', 'processing', 'downloaded'].includes(m.status))
  active.forEach((m, k) => {
    if (m.status === 'queued') newJob(db, 'media.download', { media_id: m.id, status: 'queued', created_at: iso(wall - k * 30_000) }, 1000 + k)
    else if (m.status === 'downloaded') newJob(db, 'media.normalize', { media_id: m.id, status: 'queued', created_at: iso(wall - k * 30_000) }, 1000 + k)
    else {
      const kind = (m.stage ?? 'media.download') as T.JobKind
      const dur = STEP_DUR[kind] ?? 20_000
      newJob(db, kind, {
        media_id: m.id, status: 'running', created_at: iso(wall - dur), started_at: iso(wall - dur * r.range(0.1, 0.75)),
        _dur: dur, error_code: k === 2 ? 'pending_fail' : null,
      }, 1000 + k)
    }
  })
  // first listing of 府城講古 in progress (slow, so it stays mid-way for a while)
  const hu = db.channels.find((c) => c.list_status === 'listing')
  if (hu) newJob(db, 'channel.list', { channel_id: hu.id, status: 'running', started_at: iso(wall - 1_500_000), created_at: iso(wall - 1_510_000), _dur: 3_600_000 }, 1100)
  // a channel added a moment ago, waiting for the fetch queue
  const pending = db.channels.find((c) => c.list_status === 'queued')
  if (pending) Object.assign(newJob(db, 'channel.list', { channel_id: pending.id, status: 'queued', created_at: iso(wall - 20_000) }, 1101), { _hold: true })
  // job recovery states (see ARCHITECTURE "Job recovery")
  const pick = (i: number) => db.media.filter((m) => m.status === 'ready')[i]
  const m1 = pick(3)
  const m2 = pick(7)
  const m3 = pick(11)
  const m4 = pick(15)
  if (m1)
    Object.assign(
      newJob(db, 'media.quality', {
        media_id: m1.id, status: 'queued', phase: 'retry_wait', attempts: 1, max_attempts: 4, created_at: iso(wall - 40_000),
        run_after: iso(wall + 95_000), error_code: 'legacy_ml_unavailable', error_message: 'legacy-ml 服務沒有回應（連線被拒絕）。',
        message: '暫時性錯誤（legacy_ml_unavailable），2 分鐘後自動重試（第 2/4 次）',
      }, 1102),
      { _hold: true, _retryLoop: true },
    )
  if (m2)
    Object.assign(
      newJob(db, 'media.diarize', {
        media_id: m2.id, status: 'queued', phase: 'recovered', attempts: 1, created_at: iso(wall - 60_000),
        message: '已自動恢復：worker 沒有回應心跳，工作已重新排隊（已執行 1 次）',
      }, 1103),
      { _hold: true },
    )
  if (m3)
    Object.assign(
      newJob(db, 'media.download', {
        media_id: m3.id, status: 'queued', phase: 'requeued', attempts: 1, created_at: iso(wall - 80_000),
        message: 'worker 停止中：工作已放回佇列，重新啟動後自動繼續',
      }, 1104),
      { _hold: true },
    )
  if (m4)
    newJob(db, 'media.embed', {
      media_id: m4.id, status: 'interrupted', attempts: 4, error_code: 'interrupted', created_at: iso(wall - 3_600_000), started_at: iso(wall - 3_000_000), finished_at: iso(wall - 2_400_000),
      error_message: '工作中斷（worker 沒有回應心跳），已達自動重試上限 4 次；請確認原因後手動重試',
    }, 1105)
  tick(db)
}

function buildDatasets(db: DB, r: ReturnType<typeof rngFor>) {
  const base: T.DatasetFilters = {
    channel_ids: [], media_ids: [], speaker_ids: [], text_sources: ['manual_caption', 'human_revision'], review_status: ['passed', 'unchecked'],
    exclude_repeated: true, max_repeated_overlap: 0.2, speaker_states: ['single', 'unknown'], min_duration: 1.0, max_duration: 20.0,
    min_snr: 10, min_c50: null, min_dnsmos_ovrl: 2.8, min_dnsmos_p808: null, min_speech_ratio: 0.6, min_bandwidth_hz: 7000,
    exclude_upsampled: false, exclude_flags: ['clipped'], licenses: [], sex: [], age_groups: [], max_sec_per_speaker: null,
  }
  const mk = (name: string, description: string, filters: T.DatasetFilters, ago: number): T.Dataset => ({
    id: r.uuid(), name, description, filters, created_at: iso(NOW - ago * DAY), updated_at: iso(NOW - (ago / 2) * DAY),
    created_by: { id: db.users[0].id, display_name: db.users[0].display_name },
  })
  const d1 = mk('台語 ASR 訓練集 v1', '原生字幕 + 人工校正，SNR ≥ 10 dB，排除片頭片尾。', base, 14)
  const d2 = mk('長者語音子集', '60 歲以上語者，僅已審查通過音段，每位語者最多 30 分鐘。', { ...base, review_status: ['passed'], age_groups: ['60+'], max_sec_per_speaker: 1800 }, 6)
  const d3 = mk('高品質朗讀（實驗）', 'DNSMOS ≥ 3.2、頻寬 ≥ 12 kHz，用於 TTS 前測。', { ...base, min_dnsmos_ovrl: 3.2, min_bandwidth_hz: 12000, min_snr: 20, exclude_upsampled: true }, 2)
  db.datasets.push(d3, d2, d1)
  const ex = (d: T.Dataset, status: T.ExportStatus, ago: number, fmt: 'jsonl' | 'csv', segs: number, sec: number): T.DatasetExport => ({
    id: r.uuid(), dataset_id: d.id, status, format: fmt, include_audio: true, segment_count: segs, total_sec: sec, stats: {},
    progress: status === 'completed' ? 1 : 0, size_bytes: status === 'completed' ? Math.round(sec * 32000 * 1.02) : null,
    files: status === 'completed' ? ['manifest.jsonl', 'manifest.csv', 'README.md', 'dataset.tar'] : [],
    created_at: iso(NOW - ago * DAY), finished_at: status === 'completed' ? iso(NOW - ago * DAY + 600_000) : null,
  })
  db.exports.push(
    ex(d1, 'completed', 13, 'jsonl', 6120, 30211),
    ex(d1, 'completed', 3, 'jsonl', 7348, 36420),
    ex(d2, 'failed', 5, 'csv', 0, 0),
    ex(d2, 'completed', 4.8, 'csv', 1180, 5650),
  )
  db.datasets.forEach((d) => (d.last_export = db.exports.filter((e) => e.dataset_id === d.id).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null))
}
