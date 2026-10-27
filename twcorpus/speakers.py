"""Real SpeechBrain speaker embeddings and small-corpus candidate grouping."""
from __future__ import annotations

import wave
from functools import lru_cache
from pathlib import Path

import numpy as np


MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"


class ModelUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(model_id: str, device: str, cache_dir: str):
    try:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
    except Exception as exc:
        raise ModelUnavailable(f"無法載入 SpeechBrain/PyTorch：{exc}") from exc
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ModelUnavailable("CUDA GPU 不可用；請設定 SPEAKER_DEVICE=cpu")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ModelUnavailable("Apple MPS 不可用；請設定 SPEAKER_DEVICE=cpu")
    try:
        from speechbrain.utils.fetching import LocalStrategy
        # Real copies, not symlinks into a per-machine HF cache, so the same data dir works on host and in Docker.
        target = Path(cache_dir)
        if target.is_dir():
            for item in target.iterdir():
                if item.is_symlink() and not item.exists():
                    item.unlink()
        return EncoderClassifier.from_hparams(source=model_id, savedir=cache_dir, run_opts={"device": device},
                                              local_strategy=LocalStrategy.COPY)
    except Exception as exc:
        raise ModelUnavailable(f"模型下載或載入受阻：{str(exc)[:350]}") from exc


def extract_embedding(wav_path: Path, cache_dir: Path, model_id: str = MODEL_ID,
                      device: str = "cpu") -> np.ndarray:
    """Do not return dummy vectors when model is missing or audio is unsuitable."""
    with wave.open(str(wav_path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("模型要求 16 kHz 單聲道 16-bit PCM")
        if wav.getnframes() / 16000 < 1.0:
            raise ValueError("人聲片段過短（至少 1 秒）")
        data = wav.readframes(wav.getnframes())
    samples = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
    if np.sqrt(np.mean(samples * samples)) < 0.0025:
        raise ValueError("音段近乎靜音，不擷取語者向量")
    try:
        import torch
        model = _load_model(model_id, device, str(cache_dir))
        with torch.inference_mode():
            tensor = torch.tensor(samples, dtype=torch.float32, device=device).unsqueeze(0)
            vector = model.encode_batch(tensor).squeeze().detach().cpu().numpy().astype("float32")
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"向量擷取失敗：{str(exc)[:350]}") from exc
    if vector.ndim != 1 or not np.isfinite(vector).all() or np.linalg.norm(vector) <= 0:
        raise ModelUnavailable("模型輸出無效向量")
    return vector


def unit(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if norm <= 0 or not np.isfinite(norm) or not np.isfinite(array).all():
        raise ValueError("向量無效或零長度")
    return array / norm


def top_k(vectors: dict[str, np.ndarray], query_id: str, k: int = 5) -> list[tuple[str, float]]:
    query = unit(vectors[query_id])
    matches = [(identifier, float(np.dot(query, unit(v)))) for identifier, v in vectors.items() if identifier != query_id]
    return sorted(matches, key=lambda item: (-item[1], item[0]))[:max(0, k)]


def cluster(vectors: dict[str, np.ndarray], distance_threshold: float = 0.35) -> dict[str, str]:
    """Average-linkage agglomerative clustering on cosine distances; output candidate labels."""
    if not 0 < distance_threshold < 2:
        raise ValueError("cosine 距離閾值須介於 0 與 2")
    if not vectors:
        return {}
    ids = sorted(vectors)
    normalized = np.vstack([unit(vectors[i]) for i in ids])
    if len(ids) == 1:
        return {ids[0]: "candidate_001"}
    from sklearn.cluster import AgglomerativeClustering
    labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                    distance_threshold=distance_threshold).fit_predict(normalized)
    label_names: dict[int, str] = {}
    result = {}
    for identifier, label in zip(ids, labels):
        if int(label) not in label_names:
            label_names[int(label)] = f"candidate_{len(label_names) + 1:03d}"
        result[identifier] = label_names[int(label)]
    return result
