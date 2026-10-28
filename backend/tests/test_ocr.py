"""Hard-subtitle OCR logic (voti.pipeline.hardsub + ocr.extract) on synthetic frames rendered with PIL.

The OCR engine is stubbed: a scene description says what text is where at time t; the stub returns those boxes
for the region it is given (with optional recognition noise), while glyph colours are measured on the rendered
pixels exactly as with the real engine. Distractors: a static channel title, a scrolling ticker, slide text at
random positions, an English translation line under the Chinese subtitle.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import pytest

from voti.pipeline import hardsub as H

W, HGT = 640, 360


@dataclass
class Item:
    text: str
    box: tuple          # x0, y0, x1, y1
    fill: tuple
    stroke: tuple | None = None


SUBS = [(0.5, 2.6, "大家好歡迎收看"), (2.9, 5.0, "今天我們來聊聊字幕"), (5.3, 7.1, "這是第三句話喔"),
        (7.4, 9.8, "硬字幕要用光學辨識"), (10.1, 12.0, "然後跟語音配對"), (12.3, 14.6, "沒有語音的字幕不要"),
        (14.9, 16.8, "謝謝大家收看")]
TICKER = "快訊：颱風明天登陸請注意安全　股市今日收盤上漲　"


def _sub_box(text: str, cy: float = 318, ch: float = 24) -> tuple:
    w = len(text) * ch
    return (W / 2 - w / 2, cy - ch / 2, W / 2 + w / 2, cy + ch / 2)


CLIP = [(3.0, 4.4, "影片裡的人說話"), (4.6, 6.0, "另一個人回答他"), (6.2, 7.6, "第三句片中字幕"), (7.8, 9.0, "最後一句")]


def scene(t: float, english: bool = False, second_track: bool = False, moving_bg: bool = False) -> list[Item]:
    items = [Item("頻道名稱", (16, 12, 16 + 4 * 20, 32), (255, 220, 0))]                    # static title
    off = int(t * 4) % len(TICKER)                                                           # scrolling ticker
    items.append(Item((TICKER * 2)[off:off + 14], (0, 228, 14 * 18, 246), (240, 240, 240)))
    k = int(t // 5)                                                                          # slide text
    rnd = random.Random(k)
    x, y = rnd.randint(40, 300), rnd.randint(60, 180)
    items.append(Item(f"圖表標籤{k}號", (x, y, x + 6 * 16, y + 16), (90, 200, 255)))
    for a, b, txt in SUBS:
        if a <= t < b:
            items.append(Item(txt, _sub_box(txt), (255, 255, 255), (0, 0, 0)))
            if english:
                en = "Hello and welcome"[: len(txt) + 4]
                items.append(Item(en, _sub_box(en, cy=346, ch=14), (255, 255, 120)))
    if second_track:                                                                         # clip captions
        for a, b, txt in CLIP:
            if a <= t < b:
                items.append(Item(txt, _sub_box(txt, cy=270, ch=18), (40, 40, 40), (255, 160, 90)))
    return items


def render(items: list[Item], shift: int = 0) -> np.ndarray:
    from PIL import Image, ImageDraw
    base = 60 + (shift % 5) * 9          # a moving / flickering background changes every frame
    img = Image.new("RGB", (W, HGT), (base, base + 10, base + 20))
    dr = ImageDraw.Draw(img)
    for gx in range(-80, W, 40):  # some background texture (moving when shift changes)
        dr.line([(gx + shift % 40, 0), (gx + shift % 40 + 60, HGT)], fill=(70, 85, 95), width=6)
    for it in items:
        x0, y0, x1, y1 = it.box
        n = max(1, len(it.text))
        cw = (x1 - x0) / n
        for i in range(n):  # one filled "glyph" block (with strokes) per character
            gx0, gx1 = x0 + i * cw + 0.12 * cw, x0 + (i + 1) * cw - 0.12 * cw
            if it.stroke is not None:
                dr.rectangle([gx0 - 2, y0 - 2, gx1 + 2, y1 + 2], fill=it.stroke)
            dr.rectangle([gx0, y0, gx1, y0 + 0.25 * (y1 - y0)], fill=it.fill)
            dr.rectangle([gx0, y0, gx0 + 0.25 * (gx1 - gx0), y1], fill=it.fill)
            dr.rectangle([gx0, y1 - 0.25 * (y1 - y0), gx1, y1], fill=it.fill)
    return np.asarray(img)


class Source:
    """In-memory VideoSource: frames rendered from the scene."""

    def __init__(self, duration: float = 17.0, **kw):
        self.size, self.duration, self.kw = (W, HGT), duration, kw

    def frames(self, fps: float, crop=None):  # noqa: ANN001
        k = 0
        while k / fps < self.duration:
            t = k / fps
            fr = render(scene(t, **self.kw), shift=k * 7 if self.kw.get("moving_bg") else 0)
            yield t, (fr[crop[0]:crop[1]] if crop else fr)
            k += 1


class StubEngine:
    """Returns the scene's text items inside the given region; `noise` = share of readings with one wrong char."""

    def __init__(self, noise: float = 0.0, **kw):
        self.noise, self.kw, self.calls = noise, kw, 0

    def __call__(self, rgb, t, dx=0.0, dy=0.0):  # noqa: ANN001
        self.calls += 1
        out = []
        for it in scene(t, **self.kw):
            x0, y0, x1, y1 = it.box
            cy = (y0 + y1) / 2
            if not (dy <= cy < dy + rgb.shape[0]):
                continue
            text, conf = it.text, 0.98
            r = random.Random(f"{t:.2f}{text}")
            if self.noise and r.random() < self.noise:
                i = r.randrange(len(text))
                text, conf = text[:i] + "口" + text[i + 1:], 0.9
            crop = rgb[max(0, int(y0 - dy) - 2):int(y1 - dy) + 3, max(0, int(x0) - 2):int(x1) + 3]
            fill, edge = H.glyph_colors(crop)
            out.append(H.Box(t, text, conf, x0 - 2, y0 - 2, x1 + 2, y1 + 2, fill, edge))
        return out


CFG = {"discovery_step_sec": 0.5, "fps": 5.0}
ALL_SPEECH = [(0.0, 17.0)]


def _run(regions=ALL_SPEECH, noise=0.0, **kw):  # noqa: ANN001, ANN202
    from voti.pipeline.ocr import extract
    return extract(Source(**kw), StubEngine(noise, **kw), regions, CFG)


# ───────────────────────────── text helpers ─────────────────────────────

def test_vote_text_character_consensus():
    readings = ["今天我們來聊聊字幕"] * 3 + ["今天我們來聊聊字慕", "今天我門來聊聊字幕", "今天我們來聊字幕"]
    text, agree = H.vote_text(readings)
    assert text == "今天我們來聊聊字幕"
    assert agree == pytest.approx(0.5)
    # no majority string: per-character voting still recovers the line
    text, _ = H.vote_text(["硬字慕要用光學辨識", "硬字幕要用光學辯識", "硬字幕要用先學辨識", "硬子幕要用光學辨識"])
    assert text == "硬字幕要用光學辨識"


def test_clean_text_brackets_and_notes():
    assert H.clean_text("古人到底怎麼證明『我是我」") == "古人到底怎麼證明「我是我」"
    assert H.clean_text("搞『父傳子』") == "搞「父傳子」"
    assert H.clean_text("他說「我『愛』你」") == "他說「我『愛』你」"
    assert H.clean_text("只能透過理論推論(無法100%吻合證實)怎麼推論（所以）") == "只能透過理論推論怎麼推論"


def test_scroll_detection():
    assert H.is_scroll("快訊颱風明天登陸請注意", "颱風明天登陸請注意安全")
    assert not H.is_scroll("今天我們來聊聊", "硬字幕要用光學辨識")


def test_glyph_colours_flip_insensitive():
    white = render([Item("字", (100, 100, 140, 140), (255, 255, 255), (0, 0, 0))])[95:146, 95:146]
    f, e = H.glyph_colors(white)
    assert H.pair_dist(f, e, (255, 255, 255), (0, 0, 0)) < 60


# ───────────────────────────── discovery / tracking / voting ─────────────────────────────

def test_discovery_rejects_static_scrolling_and_scattered_text():
    ex = _run()
    acc = [s for s in ex["styles"] if s.accepted]
    assert len(ex["groups"]) == 1
    assert all(abs(s.cy - 318) < 10 for s in acc), [s.dict() for s in acc]
    by_reason = {tuple(s.reasons) for s in ex["styles"] if not s.accepted}
    assert any("static" in r or "unchanging" in r for r in by_reason)      # the title
    texts = [e.text for e in ex["events"]]
    assert texts == [t for _, _, t in SUBS]
    for e, (a, b, _) in zip(ex["events"], SUBS):
        assert abs(e.start - a) <= 0.21 and abs(e.end - b) <= 0.21


def test_voting_repairs_recognition_noise():
    ex = _run(noise=0.3, moving_bg=True)   # moving background: every frame is read again
    assert [e.text for e in ex["events"]] == [t for _, _, t in SUBS]
    assert min(e.agreement for e in ex["events"]) < 1.0


def test_translation_line_is_dropped():
    ex = _run(english=True)
    assert [e.text for e in ex["events"]] == [t for _, _, t in SUBS]
    eng = [s for s in ex["styles"] if abs(s.cy - 346) < 6]
    assert eng and not any(s.accepted for s in eng)


def test_unchanged_frames_are_not_re_read():
    from voti.pipeline.ocr import extract
    eng = StubEngine()
    ex = extract(Source(), eng, ALL_SPEECH, CFG)
    st = ex["stats"]
    # the band changes only with subtitles / ticker: most tracking frames reuse the previous reading
    assert st["reused_frames"] + st["ocr_frames"] == st["tracking_frames"]
    assert st["ocr_frames"] < st["tracking_frames"]


# ───────────────────────────── pairing ─────────────────────────────

def test_pairing_with_speech():
    speech = [(0.4, 9.9), (14.8, 17.0)]   # subtitles 5 and 6 (10.1–14.6 s) have no speech
    ex = _run(regions=speech)
    st = {e.text: p for e, p in zip(ex["events"], ex["pairs"])}
    assert st["然後跟語音配對"]["status"] == "rejected"
    assert st["然後跟語音配對"]["reason"] in ("no_speech", "little_speech")
    assert st["沒有語音的字幕不要"]["status"] == "rejected"
    assert st["大家好歡迎收看"]["status"] == "paired"
    p = st["大家好歡迎收看"]
    assert p["start"] >= 0.4 - 1e-6 and p["end"] <= 2.9 + 1e-6


def test_pair_events_rules():
    ev = [H.Event(0.0, 2.0, "大家好歡迎", [], 0, (0, 0, 1, 1), 0.99, 10, 1.0),
          H.Event(3.0, 20.0, "這是很久的標題卡", [], 0, (0, 0, 1, 1), 0.99, 85, 1.0),
          H.Event(21.0, 22.0, "低信心文字內容", [], 0, (0, 0, 1, 1), 0.6, 5, 1.0),
          H.Event(23.0, 23.2, "這一句話的字數實在是太多太多了吧", [], 0, (0, 0, 1, 1), 0.99, 2, 1.0)]
    pairs = H.pair_events(ev, [(0.1, 30.0)])
    assert [p["reason"] for p in pairs] == [None, "static_text", "low_ocr_confidence", "rate_mismatch"]


def test_concurrent_tracks_are_rejected():
    ex = _run(second_track=True)
    assert len(ex["groups"]) == 2
    clip = [p for e, p in zip(ex["events"], ex["pairs"]) if e.text in {t for _, _, t in CLIP}]
    assert len(clip) == len(CLIP) and all(p["reason"] == "concurrent_tracks" for p in clip)
    host_overlapping = [p for e, p in zip(ex["events"], ex["pairs"]) if e.text in ("今天我們來聊聊字幕", "這是第三句話喔")]
    assert all(p["reason"] == "concurrent_tracks" for p in host_overlapping)
    assert any(p["status"] == "paired" for p in ex["pairs"])


def test_centred_track_wins_over_side_track():
    """Live chat beside the picture overlaps the subtitles all the time: only the side events are rejected."""
    ev = [H.Event(0.0, 2.0, "這是字幕第一句", [], 0, (300, 540, 900, 620), 0.99, 10, 1.0),
          H.Event(0.5, 3.0, "@someone 哈哈哈", [], 0, (920, 400, 1200, 430), 0.99, 10, 1.0),
          H.Event(5.0, 6.0, "主持人與片中片", [], 0, (300, 540, 900, 620), 0.99, 10, 1.0),
          H.Event(5.2, 6.0, "片中片的字幕喔", [], 0, (300, 300, 900, 360), 0.99, 10, 1.0)]
    ev[0].track, ev[1].track, ev[2].track, ev[3].track = 0, 1, 0, 2
    assert H.concurrent(ev, {0}) == {1, 3}       # centred track 0 beats side tracks 1 and 2
    assert H.concurrent(ev, {0, 2}) == {1, 2, 3}  # two centred tracks at once: both rejected
    assert H.concurrent(ev) == {0, 1, 2, 3}


def test_chat_rows_rejected():
    """@handles mark a style as chat; non-centred styles stacked in the same column are a chat panel."""
    def st(i, cy, h, x0, centered=0.0, reasons=None):
        s = H.Style(id=i, cy=cy, h=h, fill=(255, 255, 255), edge=(0, 0, 0))
        s.stats = {"centered_share": centered, "x0_rel": x0 / 1280, "x1_rel": (x0 + 250) / 1280}
        s.accepted, s.reasons = not reasons, reasons or []
        return s
    styles = [st(0, 582, 80, 300, centered=1.0), st(1, 392, 22, 1035, reasons=["chat"]), st(2, 444, 21, 1035),
              st(3, 539, 20, 1036), st(4, 578, 17, 40)]
    H._reject_panels(styles, 1280, H.DISCOVERY_DEFAULTS)
    assert [s.accepted for s in styles] == [True, False, False, False, True]
    assert styles[2].reasons == ["chat_panel"]
    assert H._HANDLE.search("@Sophieeeeeee上吹還在看") and not H._HANDLE.search("email me @ home")


# ───────────────────────────── segmentation / validation rules ─────────────────────────────

def test_segment_uses_only_paired_ocr_cues():
    from voti.pipeline import segment as S

    class C:  # Cue row stand-in
        def __init__(self, i, a, b, text, ok):
            self.id, self.start_sec, self.end_sec, self.text = f"c{i}", a, b, text
            self.flags = ["ocr", "ocr_paired" if ok else "ocr_rejected"]
    rows = [C(0, 0.0, 1.0, "第一句", True), C(1, 1.1, 2.0, "旁邊影片的字幕", False), C(2, 2.1, 3.0, "第二句", True),
            C(3, 3.1, 4.0, "第三句", True)]
    cues = S.ocr_cues(rows)
    assert [c.id for c in cues] == ["c0", "c2", "c3"]
    assert S.OCR_GAP in cues[1].flags and S.OCR_GAP not in cues[2].flags
    vad = S.Vad(np.ones(400, np.float32), 0.01, 4.0)
    plans = S.group_cues(cues, vad, S.Config(min_sec=3.0))
    assert plans[0].cue_ids == ["c0"]          # never merged across the rejected event
    assert plans[1].cue_ids == ["c2", "c3"]


def test_choose_track_prefers_captions_over_ocr():
    from voti.models import Track
    from voti.pipeline.segment import choose_track
    ocr = Track(id=None, language="zh-TW", kind="ocr", format="json")
    auto = Track(id=None, language="zh-TW", kind="auto", format="vtt")
    assert choose_track([ocr]) is ocr
    assert choose_track([ocr, auto]) is auto


def test_validate_ocr_requires_alignment():
    from voti.pipeline.validate import decide
    cfg = {}
    v = decide(text="大家好", text_source="ocr_caption", language="zh-TW", lid=[("zh", 0.99)], align_score=None,
               align_units=0, cfg=cfg)
    assert v.validity == "unknown" and v.details.get("note") == "ocr_unverified"
    v = decide(text="大家好歡迎收看", text_source="ocr_caption", language="zh-TW", lid=[("zh", 0.99)], align_score=0.8,
               align_units=7, cfg=cfg)
    assert v.validity == "valid"
    v = decide(text="大家好歡迎收看", text_source="ocr_caption", language="zh-TW", lid=[("zh", 0.99)], align_score=0.2,
               align_units=7, cfg=cfg)
    assert v.validity == "invalid" and v.reason == "text_mismatch"
    # captions keep the old behaviour (language ID alone → valid)
    v = decide(text="大家好", text_source="manual_caption", language="zh-TW", lid=[("zh", 0.99)], align_score=None,
               align_units=0, cfg=cfg)
    assert v.validity == "valid"
