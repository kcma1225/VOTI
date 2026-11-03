"""voti.pipeline.legacy_client against a mocked legacy-ml (httpx.MockTransport); no DB, no network."""
from __future__ import annotations

import base64
import json

import httpx
import numpy as np
import pytest

from voti.pipeline import legacy_client as lc
from voti.queue import JobError


def b64(a) -> str:  # noqa: ANN001
    return base64.b64encode(np.asarray(a, dtype="<f4").tobytes()).decode()


@pytest.fixture
def mock_http(monkeypatch):  # noqa: ANN001
    """Route legacy_client's httpx.Client through a handler; returns the list of seen requests."""
    seen: list[httpx.Request] = []
    state = {"handler": None}
    real_client = httpx.Client

    def factory(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        kw.pop("transport", None)

        def h(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return state["handler"](req)
        return real_client(*a, transport=httpx.MockTransport(h), **kw)

    monkeypatch.setattr(lc.httpx, "Client", factory)
    monkeypatch.setattr(lc, "RETRY_BACKOFF_SEC", 0.0)

    def set_handler(fn):  # noqa: ANN001, ANN202
        state["handler"] = fn
    set_handler.seen = seen
    return set_handler


def test_brouhaha_decodes_base64_arrays(mock_http):  # noqa: ANN001
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/brouhaha"
        assert json.loads(req.content) == {"key": "media/x/normalized.wav"}
        return httpx.Response(200, json={"hop_sec": 0.016875, "model": "brouhaha@9132cbe", "n_frames": 3,
                                         "duration_sec": 0.05, "center_offset_sec": 0.0,
                                         "vad_b64": b64([0.1, 0.9, 0.5]), "snr_b64": b64([3, 20, 11]),
                                         "c50_b64": b64([40, 41, 42])})
    mock_http(h)
    out = lc.brouhaha("media/x/normalized.wav")
    assert out["hop_sec"] == pytest.approx(0.016875)
    assert out["model"] == "brouhaha@9132cbe"
    for k in ("vad", "snr", "c50"):
        assert out[k].dtype == np.float32 and out[k].shape == (3,)
    np.testing.assert_allclose(out["snr"], [3, 20, 11])


def test_brouhaha_accepts_plain_lists(mock_http):  # noqa: ANN001
    mock_http(lambda req: httpx.Response(200, json={"hop_sec": 0.02, "vad": [0.5], "snr": [1.0], "c50": [2.0]}))
    out = lc.brouhaha("k")
    assert out["vad"].tolist() == [0.5] and out["model"] == "brouhaha"


def test_voxprofile_request_shape(mock_http):  # noqa: ANN001
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.url.path == "/voxprofile"
        assert body == {"key": "k", "backbone": "wavlm-large", "spans": [[0.0, 4.0], [5.0, 5.5]]}
        return httpx.Response(200, json={"results": [{"age": 30.0}, None], "backbone": "wavlm-large",
                                         "model_versions": {"emotion": "x@1"}})
    mock_http(h)
    out = lc.voxprofile("k", [(0, 4), (5, 5.5)], "wavlm-large")
    assert out["results"] == [{"age": 30.0}, None]
    assert out["model_versions"] == {"emotion": "x@1"}


def test_voxprofile_rejects_unknown_backbone():
    with pytest.raises(ValueError):
        lc.voxprofile("k", [(0, 1)], "hubert")


def test_voxprofile_result_count_mismatch(mock_http):  # noqa: ANN001
    mock_http(lambda req: httpx.Response(200, json={"results": [], "model_versions": {}}))
    with pytest.raises(JobError) as e:
        lc.voxprofile("k", [(0, 4)], "whisper-large-v3")
    assert e.value.code == "legacy_ml_error"


def test_unreachable_is_blocked_after_retries(mock_http):  # noqa: ANN001
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)
    mock_http(h)
    with pytest.raises(JobError) as e:
        lc.brouhaha("k")
    assert e.value.code == "legacy_ml_unavailable" and e.value.blocked
    assert len(mock_http.seen) == lc.CONNECT_RETRIES


def test_retry_then_success(mock_http):  # noqa: ANN001
    calls = {"n": 0}

    def h(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json={"ok": True, "device": "cuda", "loaded": []})
    mock_http(h)
    assert lc.health()["device"] == "cuda"
    assert calls["n"] == 2


def test_503_is_blocked(mock_http):  # noqa: ANN001
    mock_http(lambda req: httpx.Response(503, json={"detail": "model download failed"}))
    with pytest.raises(JobError) as e:
        lc.voxprofile("k", [(0, 4)], "whisper-large-v3")
    assert e.value.blocked and e.value.code == "legacy_ml_unavailable"


def test_404_not_retryable(mock_http):  # noqa: ANN001
    mock_http(lambda req: httpx.Response(404, json={"detail": "s3://voti/k not found"}))
    with pytest.raises(JobError) as e:
        lc.brouhaha("k")
    assert e.value.code == "legacy_ml_not_found" and not e.value.retryable and not e.value.blocked
    assert "not found" in e.value.message


def test_500_is_failed_not_blocked(mock_http):  # noqa: ANN001
    mock_http(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(JobError) as e:
        lc.brouhaha("k")
    assert e.value.code == "legacy_ml_error" and not e.value.blocked


def test_read_timeout_is_failed(mock_http):  # noqa: ANN001
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)
    mock_http(h)
    with pytest.raises(JobError) as e:
        lc.brouhaha("k")
    assert e.value.code == "legacy_ml_timeout" and not e.value.blocked
