"""/api/quality/summary: per-media quality distributions."""
from __future__ import annotations

import uuid

import numpy as np
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Media, MediaQuality, User
from . import schemas as S

router = APIRouter(prefix="/quality", tags=["quality"])

# metric → (column, bin edges). Values outside the range land in the first/last bin.
HISTOGRAMS = {
    "snr": (MediaQuality.snr_mean, np.arange(-10, 55, 5.0)),
    "dnsmos_ovrl": (MediaQuality.dnsmos_ovrl, np.arange(1.0, 5.01, 0.25)),
    "c50": (MediaQuality.c50_mean, np.arange(-10, 65, 5.0)),
    "bandwidth_hz": (MediaQuality.real_bandwidth_hz, np.arange(0, 25000, 1000.0)),
    "speech_ratio": (MediaQuality.speech_ratio, np.arange(0, 1.01, 0.05)),
}


def histogram(values: list[float], edges: np.ndarray) -> list[S.HistBin]:
    vals = np.clip(np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float),
                   edges[0], edges[-1])
    counts, _ = np.histogram(vals, bins=edges)
    return [S.HistBin(bin=round(float(e), 4), count=int(c)) for e, c in zip(edges[:-1], counts)]


@router.get("/summary", response_model=S.QualitySummary)
def summary(channel_id: uuid.UUID | None = None, _: User = Depends(current_user), s: Session = Depends(get_session)):
    q = select(MediaQuality)
    if channel_id:
        q = q.join(Media, Media.id == MediaQuality.media_id).where(Media.channel_id == channel_id)
    rows = s.scalars(q).all()
    hist = {name: histogram([getattr(r, col.key) for r in rows], edges) for name, (col, edges) in HISTOGRAMS.items()}
    rates: dict[str, int] = {}
    codecs: dict[str, int] = {}
    for r in rows:
        if r.sample_rate:
            rates[str(r.sample_rate)] = rates.get(str(r.sample_rate), 0) + 1
        if r.codec:
            codecs[r.codec] = codecs.get(r.codec, 0) + 1
    return S.QualitySummary(media_count=len(rows), histograms=hist, sample_rates=rates, codecs=codecs,
                            upsampled_suspect=sum(1 for r in rows if r.upsampled_suspect))
