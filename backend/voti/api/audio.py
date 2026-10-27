"""/api/audio: segment clips cut on demand from normalized.wav (S3 Range read), full media audio with Range."""
from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_session
from ..models import Media, Segment, User
from .errors import ApiError
from .views import get_or_404

router = APIRouter(prefix="/audio", tags=["audio"])

HEADER_PROBE = 64 * 1024
MAX_HEADER = 4 * 1024 * 1024
MAX_CLIP_SEC = 180.0


@dataclass
class WavInfo:
    audio_format: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    block_align: int
    data_offset: int
    data_size: int

    @property
    def duration(self) -> float:
        return self.data_size / (self.block_align * self.sample_rate)


def parse_wav_header(buf: bytes, total_size: int | None = None) -> WavInfo | None:
    """Walk RIFF chunks in `buf` (the first bytes of the file). None when `buf` is too short to reach `data`."""
    if len(buf) < 12 or buf[:4] not in (b"RIFF", b"RF64") or buf[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt = 12, None
    while pos + 8 <= len(buf):
        cid, size = buf[pos:pos + 4], struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt ":
            if body + 16 > len(buf):
                return None
            af, ch, sr, _br, ba, bps = struct.unpack("<HHIIHH", buf[body:body + 16])
            if af == 0xFFFE and size >= 40 and body + 26 <= len(buf):  # WAVE_FORMAT_EXTENSIBLE
                af = struct.unpack("<H", buf[body + 24:body + 26])[0]
            fmt = (af, ch, sr, bps, ba)
        elif cid == b"data":
            if fmt is None:
                raise ValueError("data chunk before fmt chunk")
            if size in (0, 0xFFFFFFFF) and total_size:  # streamed / RF64 writers
                size = total_size - body
            if total_size:
                size = min(size, total_size - body)
            af, ch, sr, bps, ba = fmt
            return WavInfo(af, ch, sr, bps, ba, body, size)
        pos = body + size + (size & 1)
    return None


def read_wav_info(key: str) -> WavInfo:
    h = storage.head(key)
    if h is None:
        raise ApiError(404, "audio_missing", "找不到正規化音訊（影片可能尚未處理）")
    total = int(h["ContentLength"])
    probe = HEADER_PROBE
    while True:
        buf = storage.open_range(key, f"bytes=0-{min(probe, total) - 1}")["Body"].read()
        try:
            info = parse_wav_header(buf, total)
        except ValueError as e:
            raise ApiError(500, "bad_audio", f"音訊檔格式錯誤：{e}") from None
        if info is not None:
            break
        if probe >= min(total, MAX_HEADER):
            raise ApiError(500, "bad_audio", "音訊檔找不到 data 區塊")
        probe *= 4
    if info.audio_format != 1 or info.bits_per_sample != 16:
        raise ApiError(500, "bad_audio", "正規化音訊必須是 PCM16")
    return info


def wav_header(sample_rate: int, channels: int, bits: int, data_bytes: int) -> bytes:
    block = channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, sample_rate * block, block, bits) +
            b"data" + struct.pack("<I", data_bytes))


def clip_range(info: WavInfo, start: float, end: float) -> tuple[int, int]:
    """Byte range [a, b) inside the file for [start, end) seconds, aligned to whole frames."""
    n_frames = info.data_size // info.block_align
    f0 = max(0, min(n_frames, int(round(start * info.sample_rate))))
    f1 = max(f0, min(n_frames, int(round(end * info.sample_rate))))
    return info.data_offset + f0 * info.block_align, info.data_offset + f1 * info.block_align


def cut_clip(media_id: uuid.UUID, start: float, end: float, head_only: bool = False) -> tuple[bytes, int]:
    key = storage.media_key(media_id, "normalized.wav")
    info = read_wav_info(key)
    a, b = clip_range(info, start, end)
    header = wav_header(info.sample_rate, info.channels, info.bits_per_sample, b - a)
    if head_only or b <= a:
        return header, len(header) + (b - a)
    pcm = storage.open_range(key, f"bytes={a}-{b - 1}")["Body"].read()
    return header + pcm, len(header) + len(pcm)


@router.head("/segments/{segment_id}", include_in_schema=False)
@router.get("/segments/{segment_id}", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
def segment_audio(segment_id: uuid.UUID, request: Request, pad: float = Query(0.3, ge=0, le=5),
                  rev: int | None = Query(None, description="segment revision; cache-busting only"),
                  _: User = Depends(current_user), s: Session = Depends(get_session)):
    g = get_or_404(s, Segment, segment_id, "音段")
    start, end = max(0.0, g.start_sec - pad), g.end_sec + pad
    if end - start > MAX_CLIP_SEC:
        raise ApiError(400, "clip_too_long", "音段太長")
    head = request.method == "HEAD"
    data, length = cut_clip(g.media_id, start, end, head_only=head)
    # URLs carrying the current revision may be cached (a boundary edit bumps the revision → new URL);
    # anything else must be revalidated so an edited segment is never served stale.
    cache = "private, max-age=86400" if rev is not None and rev == g.revision else "private, no-cache"
    headers = {"Content-Length": str(length), "Cache-Control": cache,
               "ETag": f'"{g.id}-r{g.revision}-p{pad:g}-{g.start_sec:g}-{g.end_sec:g}"',
               "X-Segment-Revision": str(g.revision),
               "Content-Disposition": f'inline; filename="{g.id}_r{g.revision}.wav"',
               "X-Clip-Start": f"{start:.3f}", "X-Clip-End": f"{end:.3f}"}
    if head:
        return Response(status_code=200, headers=headers, media_type="audio/wav")
    return Response(content=data, headers=headers, media_type="audio/wav")


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@router.head("/media/{media_id}", include_in_schema=False)
@router.get("/media/{media_id}", response_class=Response,
            responses={200: {"content": {"audio/wav": {}}}, 206: {"content": {"audio/wav": {}}}})
def media_audio(media_id: uuid.UUID, request: Request, _: User = Depends(current_user),
                s: Session = Depends(get_session)):
    get_or_404(s, Media, media_id, "影片")
    key = storage.media_key(media_id, "normalized.wav")
    h = storage.head(key)
    if h is None:
        raise ApiError(404, "audio_missing", "找不到正規化音訊（影片可能尚未處理）")
    total = int(h["ContentLength"])
    rng = request.headers.get("range")
    if rng:
        m = _RANGE_RE.match(rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            raise ApiError(416, "invalid_range", "請求範圍無效", headers={"Content-Range": f"bytes */{total}"})
        if m.group(1) and int(m.group(1)) >= total:
            raise ApiError(416, "invalid_range", "請求範圍無效", headers={"Content-Range": f"bytes */{total}"})
    base = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    if request.method == "HEAD":
        return Response(status_code=200, headers={**base, "Content-Length": str(total)}, media_type="audio/wav")
    try:
        obj = storage.open_range(key, rng.strip() if rng else None)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("InvalidRange", "416"):
            raise ApiError(416, "invalid_range", "請求範圍無效", headers={"Content-Range": f"bytes */{total}"}) from None
        raise
    headers = {**base, "Content-Length": str(obj["ContentLength"])}
    status = 200
    if rng and obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
        status = 206
    body = obj["Body"]

    def chunks():  # noqa: ANN202
        try:
            yield from body.iter_chunks(chunk_size=256 * 1024)
        finally:
            body.close()
    return StreamingResponse(chunks(), status_code=status, headers=headers, media_type="audio/wav")
