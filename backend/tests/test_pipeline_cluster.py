"""Speaker clustering with human overrides, and Vox-Profile aggregation."""
from __future__ import annotations

import _pipeline_support as sup  # noqa: F401
import numpy as np

from voti.pipeline import cluster


def _blobs(seed=0, n_per=20, k=3, spread=0.15):
    g = np.random.default_rng(seed)
    centers = g.standard_normal((k, 192))
    X, y = [], []
    for c in range(k):
        X.append(centers[c] + spread * np.linalg.norm(centers[c]) / np.sqrt(192) * g.standard_normal((n_per, 192)))
        y += [c] * n_per
    return np.vstack(X).astype(np.float32), np.array(y)


def _same_partition(a, b):
    return all((a[i] == a[j]) == (b[i] == b[j]) for i in range(len(a)) for j in range(len(a)))


def test_clusters_recover_speakers():
    X, y = _blobs()
    labels = cluster.cluster_vectors(X, [None] * len(X), threshold=0.5)
    assert _same_partition(labels, y)


def test_override_forces_same_cluster():
    X, y = _blobs()
    names = [None] * len(X)
    names[0] = "王小明"   # speaker 0
    names[25] = "王小明"  # speaker 1 sample, human says same person
    labels = cluster.cluster_vectors(X, names, threshold=0.5)
    assert labels[0] == labels[25]


def test_different_names_are_never_merged():
    X, y = _blobs()
    names = [None] * len(X)
    names[0], names[1] = "甲", "乙"  # same acoustic speaker, two different names
    labels = cluster.cluster_vectors(X, names, threshold=0.5)
    assert labels[0] != labels[1]
    # every unnamed member of speaker 0 joined one of the two named clusters
    assert set(labels[2:20]) <= {labels[0], labels[1]}
    assert len(set(labels[20:40])) == 1 and labels[20] not in (labels[0], labels[1])


def test_large_input_uses_micro_clusters(monkeypatch):
    monkeypatch.setattr(cluster, "DIRECT_MAX", 50)
    monkeypatch.setattr(cluster, "MICRO_CLUSTERS", 20)
    X, y = _blobs(n_per=40)
    labels = cluster.cluster_vectors(X, [None] * len(X), threshold=0.5)
    assert _same_partition(labels, y)


def test_aggregate_profiles_duration_weighted():
    class VP:
        def __init__(self, sex, sex_prob, age, age_group, emotion, vq):
            self.sex, self.sex_prob, self.age, self.age_group = sex, sex_prob, age, age_group
            self.emotion, self.voice_quality = emotion, vq
    items = [(3.0, VP("female", 0.9, 30.0, "adult", {"label": "neutral", "probs": {"neutral": 0.8, "happy": 0.2}},
                     {"labels": ["clear"], "probs": {"clear": 0.9, "breathy": 0.1}})),
             (1.0, VP("male", 0.6, 50.0, "senior", {"label": "happy"}, {"clear": 0.5, "breathy": 0.5}))]
    p = cluster.aggregate_profiles(items)
    assert p["age_mean"] == 35.0
    assert p["sex"]["female"] > p["sex"]["male"]
    assert abs(sum(p["sex"].values()) - 1) < 1e-3
    assert p["age_group"] == {"adult": 0.75, "senior": 0.25}
    assert abs(p["emotion"]["neutral"] - 0.6) < 1e-3 and abs(p["emotion"]["happy"] - 0.4) < 1e-3
    assert abs(p["voice_quality"]["clear"] - 0.8) < 1e-3
    assert cluster.aggregate_profiles([]) == {}
