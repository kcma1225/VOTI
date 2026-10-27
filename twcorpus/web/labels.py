"""Code values stored in the DB and their Traditional Chinese labels. Codes stay the source of truth."""
from __future__ import annotations

LANGUAGES = {"zh": "華語", "nan": "台語", "hak": "客語", "en": "英語", "mixed": "混語", "unknown": "未知"}
SUBTITLE_KINDS = {"local_provided": "本地提供", "uploader_provided": "上傳者提供", "auto_generated": "自動產生",
                  "translated": "翻譯字幕", "unknown": "未知"}
DOWNLOAD_KINDS = {"uploader_provided": "上傳者提供", "auto_generated": "自動產生"}
LICENSES = {"unknown": "未知", "creative_commons": "創用 CC", "permission_recorded": "已記錄授權", "other": "其他"}
TAIWAN_BASIS = {"unknown": "未知", "taiwan_source": "台灣來源", "speaker_self_report": "語者自述",
                "human_judgment": "人工判斷"}
SPEAKER_COUNT = {"unknown": "未知", "single": "單人", "multiple": "多人"}
REVIEW_STATUS = {"unchecked": "未檢查", "passed": "通過", "needs_fix": "需修正", "excluded": "排除"}
REVIEW_LEVEL = {"none": "未校對", "sampled": "抽查", "fully_corrected": "逐段校對"}
SPEAKER_STATE = {"unknown": "未知", "single": "單一語者", "multiple": "多語者", "overlap": "重疊"}
MANUAL_LABELS = {"overlapping_speech": "語音重疊", "music": "背景音樂", "subtitle_mismatch": "字幕不符",
                 "truncated": "截斷", "translation": "翻譯", "noise": "噪音"}
QUALITY_FLAGS = {"short_segment": "過短", "long_segment": "過長", "long_cue_not_split": "長句未拆",
                 "low_volume_or_silence": "低音量／靜音", "possible_clipping": "可能爆音", "empty_text": "空字幕",
                 "duplicate_cue": "重複 cue", "overlap": "cue 重疊", "beyond_audio": "超出音訊",
                 "invalid_timestamp": "時間碼無效", "negative_start": "負起點", "non_positive_duration": "非正時長",
                 "possible_rolling_caption": "疑似滾動字幕"}
SOURCE_STATUS = {"candidate": "候選", "inspected": "已檢查", "acquired": "已取得", "normalized": "已正規化",
                 "segmented": "已切片", "failed": "失敗", "blocked": "受阻"}
JOB_STATUS = {"queued": "排隊中", "running": "執行中", "completed": "完成", "failed": "失敗", "blocked": "受阻",
              "cancelled": "已取消", "interrupted": "中斷"}
JOB_KIND = {"youtube_inspect": "YouTube 檢查", "youtube_acquire": "YouTube 取得", "embedding": "語者向量",
            "local_import": "本地匯入", "segment": "字幕切片", "alignment": "字詞對齊",
            "youtube_subtitles": "YouTube 字幕", "playback": "轉 MP3", "reprocess": "重新處理"}
JOB_PHASE = {"imported": "等待 worker", "inspecting": "檢查中繼資料", "downloading": "下載音訊與字幕", "subtitles": "下載字幕", "playback": "轉為 MP3 播放檔",
             "storing": "保存與計算指紋", "normalizing": "轉為 16 kHz 音訊", "segmenting": "依字幕切片",
             "embedding": "計算語者向量", "model_download": "下載對齊模型", "model_loading": "載入對齊模型",
             "aligning": "逐句對齊", "completed": "完成"}
EMBEDDING_STATUS = {"pending": "待計算", "ready": "已計算", "failed": "失敗"}
PLATFORMS = {"local": "本地", "youtube": "YouTube"}
# Content domain of a source (what the speech is about / where it comes from). Filter axis for corpus users.
DOMAINS = {"news": "新聞時事", "education": "教育講座", "science": "科普科技", "entertainment": "娛樂綜藝",
           "lifestyle": "生活美食", "travel": "旅遊在地", "sports": "運動", "gaming": "遊戲", "music": "音樂",
           "vlog": "人物日常", "interview": "訪談", "other": "其他", "unknown": "未分類"}
# YouTube's own category names -> our domain codes (used only while the domain is still unclassified).
YOUTUBE_DOMAINS = {"News & Politics": "news", "Education": "education", "Science & Technology": "science",
                   "Entertainment": "entertainment", "Comedy": "entertainment", "Film & Animation": "entertainment",
                   "Howto & Style": "lifestyle", "Travel & Events": "travel", "Sports": "sports", "Gaming": "gaming",
                   "Music": "music", "People & Blogs": "vlog", "Pets & Animals": "lifestyle",
                   "Nonprofits & Activism": "news"}

# Badge tone per code; the label text is always shown too, so meaning never depends on color alone.
TONES = {
    "passed": "ok", "completed": "ok", "segmented": "ok", "ready": "ok", "acquired": "ok", "normalized": "ok",
    "needs_fix": "warn", "queued": "info", "running": "info", "inspected": "info", "pending": "muted",
    "unchecked": "muted", "candidate": "muted", "cancelled": "muted", "interrupted": "warn",
    "excluded": "bad", "failed": "bad", "blocked": "warn",
}

NAV = [("overview", "/", "總覽"), ("sources", "/sources", "匯入與來源"),
       ("segments", "/segments", "音段"), ("clusters", "/clusters", "聲音相似群"), ("export", "/export", "統計與匯出")]


def pick(value: str | None, options: dict, field: str, *, allow_empty: bool = False) -> str | None:
    """Accept only known codes from form input."""
    if allow_empty and not value:
        return None
    if value not in options:
        raise ValueError(f"{field} 的值無效")
    return value
