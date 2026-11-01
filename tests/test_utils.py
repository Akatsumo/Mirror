import pytest

from Mirror.core.utils import (
    dir_size,
    files_in,
    human_size,
    human_time,
    parse_opts,
    parse_size,
    progress_bar,
    run,
    safe_name,
)


@pytest.mark.parametrize(("value", "text"), [(0, "0 B"), (1023, "1023 B"), (1024, "1.00 KiB"),
                                             (5 * 1024**3, "5.00 GiB")])
def test_human_size(value, text):
    assert human_size(value) == text


def test_human_size_negative():
    with pytest.raises(ValueError):
        human_size(-1)


def test_human_time_and_bar():
    assert human_time(3725) == "1h 2m 5s"
    assert human_time(-3) == "0s"
    assert progress_bar(5, 10, 4) == "██░░"
    assert progress_bar(1, 0, 3) == "░░░"


@pytest.mark.parametrize(("text", "size"), [("2GB", 2 * 1024**3), ("1.5g", int(1.5 * 1024**3)), ("700 MiB",
                                            700 * 1024**2), ("123", 123), ("", 0), (42, 42)])
def test_parse_size(text, size):
    assert parse_size(text) == size


def test_parse_size_invalid():
    with pytest.raises(ValueError):
        parse_size("lots")


def test_parse_opts():
    assert parse_opts("format:best|playlistend:5|embed:true") == {"format": "best", "playlistend": 5, "embed": True}
    assert parse_opts('{"a": [1]}') == {"a": [1]}
    assert parse_opts("") == {}


def test_safe_name():
    assert safe_name('a/b:c*?.mkv') == "a_b_c__.mkv"
    long = safe_name("x" * 300 + ".mkv")
    assert long.endswith(".mkv") and len(long.encode()) <= 240


def test_dir_helpers(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "1.txt").write_bytes(b"12")
    (tmp_path / "2.txt").write_bytes(b"345")
    assert dir_size(tmp_path) == 5
    assert [p.name for p in files_in(tmp_path)] == ["2.txt", "1.txt"]


async def test_run_streams_lines():
    seen = []
    code, out = await run("sh", "-c", "printf 'a\\rb\\nc'", on_line=seen.append)
    assert code == 0 and seen == ["a", "b"] and out.endswith("c")
