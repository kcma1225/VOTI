"""Vox-Profile trait inference (age/sex, voice quality, emotion, emotion dimensions, speech flow, accents).

Upstream: https://github.com/tiantiaf0627/vox-profile-release (RAIL source licence; models are
non-commercial — see the upstream README "Responsible Use"). Upstream code is fetched at a pinned commit
during the image build and patched only to replace hard-coded `.cuda()` calls with the model's device.

Windowing (models were trained on 3–15 s, 16 kHz mono):
  * span < MIN_SEC (1 s)      → result None
  * 1 s ≤ span < 3 s          → run on the span as-is, `low_confidence: true`
  * 3 s ≤ span ≤ 15 s         → one chunk
  * span > 15 s               → ceil(d/15) equal chunks (each 7.5–15 s); outputs averaged weighted by duration
  * speech flow               → upstream recipe: 3 s windows (hop SPEECH_FLOW_HOP_SEC, default 1 s) over the span,
                                probabilities averaged over windows

Batching: Whisper wrappers always pad log-mels to 15 s, so batching is exact. WavLM wrappers normalise the
padded batch row; we pre-normalise each chunk over its own samples and bypass the processor, which makes the
batched result equal to the single-item result up to the conv receptive field at the padding boundary.

Age/sex only exists as `tiantiaf/wavlm-large-age-sex` (no Whisper variant is published), so it is always
taken from WavLM regardless of the requested backbone.
"""
from __future__ import annotations

import gc
import importlib
import json
import logging
import math
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

from . import labels as L

log = logging.getLogger("legacy_ml.voxprofile")

VOX_ROOT = os.environ.get("VOXPROFILE_ROOT", "/opt/vox-profile")
VOX_COMMIT = os.environ.get("VOXPROFILE_COMMIT", "unknown")[:7]
SR = 16000
MIN_SEC = 1.0
RELIABLE_SEC = 3.0
MAX_SEC = 15.0
FLOW_WIN_SEC = 3.0
FLOW_HOP_SEC = float(os.environ.get("SPEECH_FLOW_HOP_SEC", "1.0"))
SINGLE_BACKBONE = os.environ.get("VOXPROFILE_SINGLE_BACKBONE", "1") == "1"

# (module under src/model, wrapper class, HF repo, pinned revision)
W, V = "WhisperWrapper", "WavLMWrapper"
MODELS: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "whisper-large-v3": {
        "emotion": ("emotion.whisper_emotion", W, "tiantiaf/whisper-large-v3-msp-podcast-emotion",
                    "b92dab65151206a603810ec8b72eb528b9dd983c"),
        "emotion_dim": ("emotion.whisper_emotion_dim", W, "tiantiaf/whisper-large-v3-msp-podcast-emotion-dim",
                        "09fe2573a1e636bad9333c408d9bd523f888145a"),
        "voice_quality": ("voice_quality.whisper_voice_quality", W, "tiantiaf/whisper-large-v3-voice-quality",
                          "bbd7e5ab2ef1b7a8bd86eae08c85e1b591f4ff8d"),
        "speech_flow": ("fluency.whisper_fluency", W, "tiantiaf/whisper-large-v3-speech-flow",
                        "9d31bf264d07f305985f33ac31ec6a9fed4cf235"),
        "accent_broad": ("accent.whisper_accent", W, "tiantiaf/whisper-large-v3-broad-accent",
                         "26e72f7924671ec239e3535983d8a545fb9b63eb"),
        "accent_narrow": ("accent.whisper_accent", W, "tiantiaf/whisper-large-v3-narrow-accent",
                          "3a0e270a9520666b46ae2f45ab637af77084a812"),
    },
    "wavlm-large": {
        "emotion": ("emotion.wavlm_emotion", V, "tiantiaf/wavlm-large-categorical-emotion",
                    "621a2ceaaa2408e390e0fc9454e8c29490f689e2"),
        "emotion_dim": ("emotion.wavlm_emotion_dim", V, "tiantiaf/wavlm-large-msp-podcast-emotion-dim",
                        "348ce815faa5dd1fd27adb72d78d7d0733cadcd9"),
        "voice_quality": ("voice_quality.wavlm_voice_quality", V, "tiantiaf/wavlm-large-voice-quality",
                          "5c0df77121ce879d9cdb86040d376c0d1ac40174"),
        "speech_flow": ("fluency.wavlm_fluency", V, "tiantiaf/wavlm-large-speech-flow",
                        "aea5a480f3767faf640c52a86f463abaac660789"),
        "accent_broad": ("accent.wavlm_accent", V, "tiantiaf/wavlm-large-broader-accent",
                         "49394e54c52c6463e9367050ea2efe109aba8b6e"),
        "accent_narrow": ("accent.wavlm_accent", V, "tiantiaf/wavlm-large-narrow-accent",
                          "5829f58b2234899aac96f55333a04275db4a1982"),
    },
}
AGE_SEX = ("age_sex.wavlm_demographics", V, "tiantiaf/wavlm-large-age-sex", "a4ad8039d8e298eb51d0bad71efb01a646ac56d5")
BACKBONES = tuple(MODELS)
TASKS = ("age_sex", "emotion", "emotion_dim", "voice_quality", "speech_flow", "accent_broad", "accent_narrow")


def _import(module: str):
    if VOX_ROOT not in sys.path:
        sys.path.insert(0, VOX_ROOT)
    return importlib.import_module(f"src.model.{module}")


class _ConfigOnly:
    """Stand-in for WhisperModel/WavLMModel.from_pretrained inside the wrappers' __init__: builds the backbone
    from its config only. The task checkpoint contains every backbone weight (verified at load time: no missing
    keys), so downloading openai/whisper-large-v3 (3 GB) / microsoft/wavlm-large just to overwrite it is skipped."""

    def __init__(self, model_cls, config_cls):
        self.model_cls, self.config_cls = model_cls, config_cls

    def from_pretrained(self, name, **kw):
        from transformers.modeling_utils import no_init_weights
        kw.pop("ignore_mismatched_sizes", None)
        cfg = self.config_cls.from_pretrained(name, **kw)
        with no_init_weights():
            return self.model_cls(cfg)


def _load(spec: tuple[str, str, str, str], device: torch.device) -> torch.nn.Module:
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file
    from transformers import WavLMConfig, WavLMModel, WhisperConfig, WhisperModel

    module, cls_name, repo, rev = spec
    mod = _import(module)
    if hasattr(mod, "WhisperModel"):
        mod.WhisperModel = _ConfigOnly(WhisperModel, WhisperConfig)
    if hasattr(mod, "WavLMModel"):
        mod.WavLMModel = _ConfigOnly(WavLMModel, WavLMConfig)
    cfg_path = hf_hub_download(repo, "config.json", revision=rev)
    weights = hf_hub_download(repo, "model.safetensors", revision=rev)
    with open(cfg_path) as f:
        cfg = json.load(f)
    model = getattr(mod, cls_name)(**cfg)
    if cls_name == W:  # encoder-only use: drop the Whisper decoder (~0.9 B params) before anything is copied
        model.backbone_model.decoder = None
    model.to(device)
    sd = load_file(weights, device=str(device))  # straight to the target device: no second host copy
    missing, unexpected = model.load_state_dict(sd, strict=False)
    del sd
    if cls_name == W and "embed_positions" in missing:
        # The wrapper's own copy of the positional table is not in the checkpoint. Upstream gets it from
        # WhisperModel.from_pretrained(..., max_source_positions=750, ignore_mismatched_sizes=True), where the
        # mismatched table is re-initialised by WhisperPreTrainedModel._init_weights → sinusoids(750, d_model).
        from transformers.models.whisper.modeling_whisper import sinusoids
        with torch.no_grad():
            model.embed_positions.copy_(sinusoids(*model.embed_positions.shape).to(device))
        missing = [k for k in missing if k != "embed_positions"]
    if missing:
        raise RuntimeError(f"{repo}: checkpoint lacks {len(missing)} weights, e.g. {missing[:5]}")
    unexpected = [k for k in unexpected if not k.startswith("backbone_model.decoder.")]
    if unexpected:
        log.warning("%s: %d unexpected keys ignored, e.g. %s", repo, len(unexpected), unexpected[:5])
    if cls_name == V:  # we pre-normalise per chunk (see module doc)
        model.processor = lambda x, **_: {"input_values": x[None]}
    model.eval()  # also merges LoRA weights (loralib) — must happen after the weights are loaded
    for p in model.parameters():
        p.requires_grad_(False)
    _release_host_memory(device)
    return model


def _release_host_memory(device: torch.device) -> None:
    """Building a 1.5 B-param backbone on the host leaves freed-but-unreturned malloc arenas; give them back."""
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


class VoxProfile:
    def __init__(self, device: torch.device, batch_size: int):
        self.device = device
        self.batch_size = batch_size
        self.models: dict[tuple[str, str], torch.nn.Module] = {}

    # ── model management ──
    def _spec(self, backbone: str, task: str):
        return AGE_SEX if task == "age_sex" else MODELS[backbone][task]

    def loaded(self) -> list[str]:
        return sorted({self._spec(b, t)[2] for b, t in self.models})

    def ensure(self, backbone: str) -> None:
        if SINGLE_BACKBONE:
            for (b, t) in list(self.models):
                if b != backbone and t != "age_sex":
                    del self.models[(b, t)]
            _release_host_memory(self.device)
        for task in TASKS:
            key = ("*", "age_sex") if task == "age_sex" else (backbone, task)
            if key not in self.models:
                t = time.time()
                self.models[key] = _load(self._spec(backbone, task), self.device)
                log.info("loaded %s on %s in %.1fs", self._spec(backbone, task)[2], self.device, time.time() - t)

    def versions(self, backbone: str) -> dict:
        out = {t: f"{self._spec(backbone, t)[2]}@{self._spec(backbone, t)[3][:7]}" for t in TASKS}
        out["code"] = f"vox-profile-release@{VOX_COMMIT}"
        return out

    # ── batched forward ──
    def _batches(self, items: list[np.ndarray]):
        order = sorted(range(len(items)), key=lambda i: len(items[i]))
        for k in range(0, len(order), self.batch_size):
            idx = order[k:k + self.batch_size]
            yield idx, [items[i] for i in idx]

    def _forward(self, model, cls_is_wavlm: bool, chunk: list[np.ndarray]):
        n = max(len(c) for c in chunk)
        x = torch.zeros(len(chunk), n)
        for i, c in enumerate(chunk):
            if cls_is_wavlm:
                c = (c - c.mean()) / np.sqrt(c.var() + 1e-7)
            x[i, :len(c)] = torch.from_numpy(np.asarray(c, dtype=np.float32))
        length = torch.tensor([len(c) for c in chunk])
        return model(x.to(self.device), length=length.to(self.device))

    @torch.inference_mode()
    def _run_task(self, backbone: str, task: str, items: list[np.ndarray]) -> np.ndarray:
        key = ("*", "age_sex") if task == "age_sex" else (backbone, task)
        model = self.models[key]
        is_wavlm = self._spec(backbone, task)[1] == V
        out: list[np.ndarray | None] = [None] * len(items)
        for idx, chunk in self._batches(items):
            r = self._forward(model, is_wavlm, chunk)
            if task == "age_sex":
                age, sex = r[0], r[1]
                vec = torch.cat([age.reshape(-1, 1) * 100.0, F.softmax(sex, dim=1)], dim=1)
            elif task == "emotion":
                vec = F.softmax(r[0], dim=1)
            elif task == "emotion_dim":
                vec = torch.cat([r[0].reshape(-1, 1), r[1].reshape(-1, 1), r[2].reshape(-1, 1)], dim=1)
            elif task == "voice_quality":
                vec = torch.sigmoid(r[0] if isinstance(r, tuple) else r)
            elif task == "speech_flow":
                vec = torch.cat([F.softmax(r[0], dim=1), torch.sigmoid(r[1])], dim=1)
            else:  # accents
                vec = F.softmax(r[0] if isinstance(r, tuple) else r, dim=1)
            vec = vec.float().cpu().numpy()
            for j, i in enumerate(idx):
                out[i] = vec[j]
        return np.stack(out) if out else np.zeros((0, 0), np.float32)

    # ── public ──
    def run(self, spans_audio: list[np.ndarray], backbone: str) -> list[dict | None]:
        self.ensure(backbone)
        chunks, chunk_owner, chunk_w = [], [], []
        flows, flow_owner = [], []
        for si, y in enumerate(spans_audio):
            d = len(y) / SR
            if d < MIN_SEC:
                continue
            n = max(1, math.ceil(d / MAX_SEC - 1e-9))
            bounds = np.linspace(0, len(y), n + 1).round().astype(int)
            for a, b in zip(bounds[:-1], bounds[1:]):
                chunks.append(y[a:b]); chunk_owner.append(si); chunk_w.append(b - a)
            win, hop = int(FLOW_WIN_SEC * SR), int(FLOW_HOP_SEC * SR)
            if len(y) <= win:
                starts = [0]
            else:
                starts = list(range(0, len(y) - win + 1, hop))
                if starts[-1] != len(y) - win:
                    starts.append(len(y) - win)
            for s in starts:
                flows.append(y[s:s + win]); flow_owner.append(si)

        per_task = {t: self._run_task(backbone, t, flows if t == "speech_flow" else chunks) for t in TASKS}

        chunk_owner, flow_owner, chunk_w = np.array(chunk_owner), np.array(flow_owner), np.array(chunk_w, float)
        results: list[dict | None] = []
        for si, y in enumerate(spans_audio):
            d = len(y) / SR
            if d < MIN_SEC:
                results.append(None)
                continue
            m = chunk_owner == si
            w = chunk_w[m] / chunk_w[m].sum()
            avg = {t: (per_task[t][m] * w[:, None]).sum(0) for t in TASKS if t != "speech_flow"}
            fm = flow_owner == si
            flow = per_task["speech_flow"][fm]
            results.append(self._format(avg, flow, d, int(m.sum())))
        return results

    @staticmethod
    def _cat(vec: np.ndarray, names: list[str]) -> dict:
        probs = {n: round(float(p), 5) for n, p in zip(names, vec)}
        return {"label": names[int(np.argmax(vec))], "probs": probs}

    def _format(self, avg: dict, flow: np.ndarray, d: float, n_chunks: int) -> dict:
        age = float(avg["age_sex"][0])
        sexp = avg["age_sex"][1:3]
        si = int(np.argmax(sexp))
        vq = avg["voice_quality"]
        fl = flow[:, :2].mean(0)
        dis = flow[:, 2:]
        disfluent_windows = flow[:, 1] > 0.5
        return {
            "duration_sec": round(d, 3),
            "n_chunks": n_chunks,
            "low_confidence": d < RELIABLE_SEC,
            "age": round(age, 2),
            "age_group": L.age_group(age),
            "sex": L.SEX[si].lower(),
            "sex_prob": round(float(sexp[si]), 5),
            "sex_probs": {s.lower(): round(float(p), 5) for s, p in zip(L.SEX, sexp)},
            "voice_quality": {
                "label": L.VOICE_QUALITY[int(np.argmax(vq))],
                "labels": [n for n, p in zip(L.VOICE_QUALITY, vq) if p > 0.5],   # multi-label, upstream threshold 0.5
                "probs": {n: round(float(p), 5) for n, p in zip(L.VOICE_QUALITY, vq)},
            },
            "emotion": self._cat(avg["emotion"], L.EMOTION),
            "emotion_dim": {k: round(float(v), 5) for k, v in zip(("arousal", "valence", "dominance"), avg["emotion_dim"])},
            "speech_flow": {
                **self._cat(fl, L.FLUENCY),
                "n_windows": int(len(flow)),
                "disfluent_window_ratio": round(float(disfluent_windows.mean()), 4),
                # mean sigmoid over windows; "detected" uses upstream's 0.7 threshold on disfluent windows
                "disfluency_types": {n: round(float(p), 5) for n, p in zip(L.DISFLUENCY_TYPES, dis.mean(0))},
                "disfluency_detected": [n for k, n in enumerate(L.DISFLUENCY_TYPES)
                                        if (dis[disfluent_windows, k] > 0.7).any()],
            },
            "accent_broad": self._cat(avg["accent_broad"], L.ACCENT_BROAD),
            "accent_narrow": self._cat(avg["accent_narrow"], L.ACCENT_NARROW),
        }
