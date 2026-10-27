import numpy as np
import pytest

from twcorpus.speakers import cluster, top_k


def test_similarity_and_candidate_clusters():
    vectors = {"a": np.array([1, 0, 0]), "b": np.array([0.99, .01, 0]),
               "c": np.array([0, 1, 0])}
    assert top_k(vectors, "a", 2)[0][0] == "b"
    labels = cluster(vectors, distance_threshold=0.2)
    assert labels["a"] == labels["b"]
    assert labels["c"] != labels["a"]
    assert len(cluster({"one": vectors["a"]})) == 1
    with pytest.raises(ValueError):
        cluster({"bad": np.zeros(3)})
