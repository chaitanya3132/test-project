from investor import news


def test_material_headline_alerts_even_when_neutral():
    v = news.classify("Acme Corp announces Q3 earnings date", threshold=0.4)
    assert v is not None and v["material"]


def test_strong_sentiment_alerts_without_keywords():
    v = news.classify("Acme shares are an absolutely terrible disaster", threshold=0.4)
    assert v is not None and v["tone"] == "negative"


def test_bland_headline_is_ignored():
    assert news.classify("Acme opens new office in Denver", threshold=0.4) is None


def test_tone_labels():
    pos = news.classify("Analysts upgrade Acme, praise excellent growth", threshold=0.4)
    assert pos["tone"] == "positive" and pos["material"]
