"""Brouhaha (VAD + SNR + C50) over a whole file, block-streamed so a 2 h file needs only ~10 min of audio in RAM.

Upstream: https://github.com/marianne-m/brouhaha-vad (MIT), checkpoint models/best/checkpoints/best.ckpt,
model = pyannote PyanNet (SincNet + 3×BiLSTM) trained on 6 s chunks; pyannote Inference slides 6 s windows
with a 0.6 s step (10 %) and averages overlapping frames with a Hamming window. Frame step = 270 samples
(SincNet stride 10 × three max-pools of 3) = 0.016875 s.

Streaming: the file is cut into blocks of BLOCK samples, each read with MARGIN extra samples on both sides.
Block starts are multiples of lcm(chunk step 9600, frame step 270) = 86400 samples, so chunk and frame grids
coincide with a whole-file run; MARGIN (10.8 s) > window (6 s) means every kept frame sees exactly the same
set of overlapping windows as in a whole-file run.
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np
import torch

from . import audio

log = logging.getLogger("legacy_ml.brouhaha")

CKPT = os.environ.get("BROUHAHA_CKPT", "/opt/brouhaha/models/best/checkpoints/best.ckpt")
COMMIT = os.environ.get("BROUHAHA_COMMIT", "unknown")[:7]
FRAME = 270                  # samples per output frame
GRID = 86400                 # lcm(9600, 270)
BLOCK = GRID * int(os.environ.get("BROUHAHA_BLOCK_UNITS", "100"))   # 540 s
MARGIN = GRID * 2            # 10.8 s


class Brouhaha:
    def __init__(self, device: torch.device, batch_size: int):
        from brouhaha.inference import BrouhahaInference
        from pyannote.audio import Model

        t = time.time()
        self.device = device
        self.model = Model.from_pretrained(CKPT, map_location=torch.device("cpu"))
        self.model.eval().to(device)
        self.inference = BrouhahaInference(self.model, batch_size=batch_size, device=device)
        self.duration = float(self.inference.duration)
        self.step = float(self.inference.step)
        rf = self.model.receptive_field
        self.rf_start, self.rf_duration, self.rf_step = float(rf.start), float(rf.duration), float(rf.step)
        if round(self.rf_step * audio.SR) != FRAME or round(self.step * audio.SR) * 9 != 86400:
            raise RuntimeError(f"unexpected brouhaha geometry step={self.step} frame={self.rf_step}")
        import pyannote.audio
        self.name = f"brouhaha@{COMMIT} (pyannote.audio {pyannote.audio.__version__})"
        log.info("brouhaha loaded on %s in %.1fs (window %.1fs step %.2fs hop %.6fs)", device, time.time() - t,
                 self.duration, self.step, self.rf_step)

    @torch.inference_mode()
    def _run_block(self, y: np.ndarray) -> np.ndarray:
        w = torch.from_numpy(y)[None]
        out = self.inference({"waveform": w, "sample_rate": audio.SR})
        return np.asarray(out.data, dtype=np.float32)  # (frames, 3): vad, snr, c50

    def run(self, la: audio.LocalAudio) -> dict:
        t = time.time()
        n = int(round(la.frames * audio.SR / la.sample_rate))
        n_frames = max(1, -(-n // FRAME))
        out = np.zeros((n_frames, 3), dtype=np.float32)
        filled = np.zeros(n_frames, dtype=bool)
        cores = [(s, min(s + BLOCK, n)) for s in range(0, n, BLOCK)] or [(0, 0)]
        ranges = [(max(0, a - MARGIN), min(n, b + MARGIN)) for a, b in cores]
        for (ca, cb), (ra, rb), y in zip(cores, ranges, audio.iter_blocks(la, ranges)):
            if len(y) < audio.SR // 10:  # < 0.1 s: nothing meaningful
                continue
            res = self._run_block(y)
            g0 = ra // FRAME                       # global index of the block's first frame
            lo, hi = ca // FRAME, min(n_frames, -(-cb // FRAME))
            i0, i1 = lo - g0, min(hi - g0, len(res))
            if i1 > i0:
                out[lo:lo + (i1 - i0)] = res[i0:i1]
                filled[lo:lo + (i1 - i0)] = True
        if not filled.all():  # trailing frames (rounding at EOF): repeat last value
            last = np.flatnonzero(filled)
            if len(last):
                out[last[-1] + 1:] = out[last[-1]]
        return {
            "hop_sec": FRAME / audio.SR,
            # frame i spans [t0 + i*hop, t0 + i*hop + frame_duration); its center is i*hop + center_offset_sec
            "center_offset_sec": self.rf_start + self.rf_duration / 2,
            "frame_duration_sec": self.rf_duration,
            "n_frames": int(n_frames),
            "duration_sec": n / audio.SR,
            "vad": out[:, 0], "snr": out[:, 1], "c50": out[:, 2],
            "model": self.name,
            "elapsed_sec": round(time.time() - t, 3),
        }
