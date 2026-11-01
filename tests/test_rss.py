import pytest

from Mirror.modules.rss import entry_link, parse_feed_spec, parse_filter, passes


def test_filters():
    assert parse_filter("1080p, 720p; x265") == [["1080p", "720p"], ["x265"]]
    assert passes("Show S01E01 1080p x265", "1080p,720p;x265", "")
    assert not passes("Show 480p x265", "1080p,720p;x265", "")
    assert not passes("Show 1080p CAM x265", "1080p;x265", "cam,ts")
    assert passes("anything", "", "")


def test_feed_spec():
    spec = parse_feed_spec('news https://f.xml -c /leech -inf "1080p;x265" -f "-z -up rc"')
    assert spec["cmd"] == "/leech" and spec["inf"] == "1080p;x265" and spec["flags"] == "-z -up rc"
    with pytest.raises(ValueError):
        parse_feed_spec("onlyname")
    with pytest.raises(ValueError):
        parse_feed_spec("n url -bad x")


def test_entry_link_prefers_torrent():
    entry = {"link": "https://page", "links": [{"type": "application/x-bittorrent", "href": "https://t.torrent"}]}
    assert entry_link(entry) == "https://t.torrent"
    assert entry_link({"link": "https://page", "links": []}) == "https://page"
