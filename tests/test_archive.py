import shutil
import zipfile

import pytest

from Mirror.modules.archive import _extract_plain, create_zip, extract_all

SEVEN = shutil.which("7z")


async def test_zip_and_extract_roundtrip(tmp_path):
    src = tmp_path / "folder"
    (src / "in").mkdir(parents=True)
    (src / "in" / "a.txt").write_text("hello")
    out = tmp_path / "z" / "folder.zip"
    made = await create_zip([src], out)
    assert made == [out] and zipfile.is_zipfile(out)
    shutil.rmtree(src)
    result = await extract_all(out)
    assert (result / "folder" / "in" / "a.txt").read_text() == "hello" and not out.exists()


def test_unsafe_zip_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        _extract_plain(bad, tmp_path / "out")


@pytest.mark.skipif(not SEVEN, reason="7z not installed")
async def test_password_split_with_7z(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 300_000)
    parts = await create_zip([src], tmp_path / "o" / "big.zip", "pw", split=100_000)
    assert len(parts) >= 2
    src.unlink()
    await extract_all(parts[0], "pw")
    assert any(p.name == "big.bin" for p in (tmp_path / "o").rglob("*"))


async def test_missing_7z_for_password(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    (tmp_path / "f").write_text("x")
    with pytest.raises(RuntimeError):
        await create_zip([tmp_path / "f"], tmp_path / "f.zip", password="x")


def test_volume_grouping(tmp_path):
    from Mirror.modules.archive import _volumes

    for name in ("a.part1.rar", "a.part2.rar", "a.part10.rar", "b.rar", "b.r00", "b.r01", "b2.r00", "c.7z.001",
                 "c.7z.002"):
        (tmp_path / name).write_text("x")
    names = lambda first: sorted(p.name for p in _volumes(tmp_path / first))  # noqa: E731
    assert names("a.part1.rar") == ["a.part1.rar", "a.part10.rar", "a.part2.rar"]
    assert names("b.rar") == ["b.r00", "b.r01", "b.rar"]
    assert names("c.7z.001") == ["c.7z.001", "c.7z.002"]
