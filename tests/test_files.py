from pathlib import Path

import pytest

from Mirror.core.files import (
    apply_names,
    excluded,
    final_name,
    is_archive,
    is_later_part,
    join_parts,
    link_tree,
    single_root,
    split_file,
    split_sizes,
    substitute,
)


def test_name_rules():
    assert substitute("Movie.2024.WEB.mkv", r"\.WEB/|2024/2025") == "Movie.2025.mkv"
    assert final_name("a.mkv", "[P] ", " [S]") == "[P] a [S].mkv"
    assert final_name("x www.site.com y.mp4", remname=r"www\.site\.com " ) == "x y.mp4"
    assert substitute("a(b", "a(/z") == "zb"


def test_apply_names(tmp_path):
    root = tmp_path / "dir"
    root.mkdir()
    (root / "one.txt").write_text("1")
    single = tmp_path / "solo.bin"
    single.write_text("2")
    apply_names(root, prefix="p_")
    assert (root / "p_one.txt").exists()
    assert apply_names(single, suffix="_s").name == "solo_s.bin"


def test_archive_detection():
    assert all(is_archive(Path(n)) for n in ("a.zip", "a.part1.rar", "a.7z.001", "a.tar.gz", "A.RAR"))
    assert not is_archive(Path("movie.mkv"))
    assert is_later_part(Path("a.part2.rar")) and is_later_part(Path("a.7z.002")) and is_later_part(Path("x.r01"))
    assert not is_later_part(Path("a.part1.rar")) and not is_later_part(Path("movie.mkv"))
    assert excluded(Path("x.NFO"), ["nfo", ".txt"]) and not excluded(Path("x.mkv"), ["nfo"])
    assert not excluded(Path("x.nfo"), [])


def test_split_sizes():
    assert split_sizes(10, 4, False) == [4, 4, 2]
    assert split_sizes(10, 4, True) == [4, 3, 3]
    assert split_sizes(3, 4, True) == [3]
    with pytest.raises(ValueError):
        split_sizes(1, 0, False)


def test_split_and_join_roundtrip(tmp_path):
    source = tmp_path / "data.bin"
    payload = bytes(range(256)) * 41
    source.write_bytes(payload)
    parts = split_file(source, 4096, equal=True)
    assert [p.name for p in parts] == ["data.bin.001", "data.bin.002", "data.bin.003"]
    assert max(p.stat().st_size for p in parts) - min(p.stat().st_size for p in parts) <= 1
    source.unlink()
    assert join_parts(tmp_path) == [source]
    assert source.read_bytes() == payload and not list(tmp_path.glob("*.00*"))
    with pytest.raises(FileNotFoundError):
        split_file(tmp_path / "missing", 2)


def test_link_tree_and_single_root(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")
    copy = link_tree(src, tmp_path / "out")
    assert (copy / "sub" / "f.txt").read_text() == "x"
    assert single_root(tmp_path / "out") == copy and single_root(tmp_path) == tmp_path
