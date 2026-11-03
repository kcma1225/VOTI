"""End-to-end smoke test against a running legacy-ml service (real models, real S3).

    python tests/smoke.py --url http://127.0.0.1:8600 --wav speech16k.wav \
        --s3 http://127.0.0.1:59000 --bucket voti-test-legacy [--backbones whisper-large-v3,wavlm-large]

Uploads the wav, runs /brouhaha and /voxprofile (spans: whole-file 15 s windows plus a 0.5 s, a 2 s and a
40 s span to exercise null / low_confidence / multi-chunk paths), checks shapes, value ranges and that every
trait is present, and prints timings. Needs only the stdlib + boto3 + numpy (both are in the image).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request

import numpy as np

TRAITS = ("age", "age_group", "sex", "sex_prob", "voice_quality", "emotion", "emotion_dim", "speech_flow",
          "accent_broad", "accent_narrow")


def post(url: str, body: dict, timeout: float = 7200) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def dec(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype="<f4")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8600")
    ap.add_argument("--wav", required=True)
    ap.add_argument("--key", default="smoke/speech16k.wav")
    ap.add_argument("--s3", default=os.environ.get("S3_ENDPOINT", "http://127.0.0.1:59000"))
    ap.add_argument("--access", default=os.environ.get("S3_ACCESS_KEY", "voti"))
    ap.add_argument("--secret", default=os.environ.get("S3_SECRET_KEY", "voti-secret-change-me"))
    ap.add_argument("--bucket", default="voti-test-legacy")
    ap.add_argument("--backbones", default="whisper-large-v3,wavlm-large")
    ap.add_argument("--spans", default="", help="JSON list of [s,e] spans to use instead of the default set")
    ap.add_argument("--skip-upload", action="store_true")
    a = ap.parse_args()

    import boto3
    import soundfile as sf
    info = sf.info(a.wav)
    dur = info.frames / info.samplerate
    if not a.skip_upload:
        s3 = boto3.client("s3", endpoint_url=a.s3, aws_access_key_id=a.access, aws_secret_access_key=a.secret,
                          region_name="us-east-1")
        try:
            s3.head_bucket(Bucket=a.bucket)
        except Exception:  # noqa: BLE001
            s3.create_bucket(Bucket=a.bucket)
        s3.upload_file(a.wav, a.bucket, a.key)
    print("healthz", json.dumps(json.loads(urllib.request.urlopen(a.url + "/healthz").read())))

    t = time.time()
    b = post(a.url + "/brouhaha", {"key": a.key, "bucket": a.bucket})
    dt = time.time() - t
    vad, snr, c50 = dec(b["vad_b64"]), dec(b["snr_b64"]), dec(b["c50_b64"])
    assert len(vad) == len(snr) == len(c50) == b["n_frames"], "length mismatch"
    assert abs(len(vad) * b["hop_sec"] - dur) < 0.1, (len(vad) * b["hop_sec"], dur)
    assert 0 <= vad.min() and vad.max() <= 1 and np.isfinite(snr).all() and np.isfinite(c50).all()
    sp = vad > 0.5
    print(f"brouhaha {dur:.1f}s audio in {dt:.2f}s (server {b['elapsed_sec']}s) model={b['model']} hop={b['hop_sec']}"
          f" speech={sp.mean():.2f} snr(speech) median={np.median(snr[sp]) if sp.any() else float('nan'):.1f}dB"
          f" snr(non-speech) median={np.median(snr[~sp]) if (~sp).any() else float('nan'):.1f}dB"
          f" c50 median={np.median(c50):.1f}dB")

    if a.spans:
        spans = json.loads(a.spans)
    else:
        spans = [[s, min(dur, s + 15.0)] for s in np.arange(0, dur - 3, 15.0).tolist()]
        spans += [[1.0, 1.5], [1.0, 3.0], [0.0, min(dur, 40.0)]]
    for bb in a.backbones.split(","):
        for attempt in ("cold", "warm"):
            t = time.time()
            r = post(a.url + "/voxprofile", {"key": a.key, "bucket": a.bucket, "spans": spans, "backbone": bb})
            dt = time.time() - t
            print(f"voxprofile {bb} {attempt}: {len(spans)} spans "
                  f"({sum(e - s for s, e in spans):.1f}s audio) in {dt:.2f}s (server {r['elapsed_sec']}s)")
        assert len(r["results"]) == len(spans)
        for (s, e), res in zip(spans, r["results"]):
            if e - s < 1.0:
                assert res is None, "expected null for < 1 s"
                continue
            missing = [k for k in TRAITS if k not in res]
            assert not missing, missing
            assert res["low_confidence"] == (e - s < 3.0)
            assert abs(sum(res["emotion"]["probs"].values()) - 1) < 1e-3
            assert abs(sum(res["accent_narrow"]["probs"].values()) - 1) < 1e-3
            print(f"  [{s:6.1f},{e:6.1f}] age={res['age']:5.1f} ({res['age_group']}) sex={res['sex']}({res['sex_prob']:.2f})"
                  f" emo={res['emotion']['label']}({max(res['emotion']['probs'].values()):.2f})"
                  f" avd={[round(v, 2) for v in res['emotion_dim'].values()]}"
                  f" vq={res['voice_quality']['labels'] or res['voice_quality']['label']}"
                  f" flow={res['speech_flow']['label']}"
                  f" acc={res['accent_broad']['label']}/{res['accent_narrow']['label']}"
                  f" chunks={res['n_chunks']}{' LOW' if res['low_confidence'] else ''}")
        print("  model_versions", json.dumps(r["model_versions"]))
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
