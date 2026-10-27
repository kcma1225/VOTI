import pytest
from twcorpus.youtube import normalize_video_url, search_candidates, YouTubeError


def test_video_id_normalization_and_restrictions():
    expected = ("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert normalize_video_url("https://youtu.be/dQw4w9WgXcQ?t=6") == expected
    assert normalize_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc") == expected
    for url in ("https://example.com/watch?v=dQw4w9WgXcQ", "file:///tmp/test", "https://youtube.com/playlist?list=x"):
        with pytest.raises(ValueError):
            normalize_video_url(url)
    with pytest.raises(YouTubeError) as error:
        search_candidates("", "test")
    assert error.value.code == "missing_api_key"
