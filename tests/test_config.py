from typing import Any

import pytest

from Mirror.config import BOOT_ONLY, SECRETS, Config, Size, coerce


def test_coerce_types():
    assert coerce(bool, "Yes") is True and coerce(bool, "0") is False
    assert coerce(int, "") == 0 and coerce(float, "1.5") == 1.5
    assert coerce(Size, "1GB") == 1024**3
    assert coerce(list[int], "1, 2 3") == [1, 2, 3]
    assert coerce(list[str], "gofile,streamtape") == ["gofile", "streamtape"]
    assert coerce(dict[str, Any], '{"a": 1}') == {"a": 1}
    assert coerce(int, 5) == 5


def test_sections_partition_keys():
    keys = [k for section in Config.sections().values() for k in section]
    assert len(keys) == len(set(keys)) == len(Config.types())
    assert set(keys) >= BOOT_ONLY and set(keys) >= SECRETS


def test_env_apply_reset(monkeypatch):
    env = {"QUEUE_DOWNLOAD": "7", "AS_DOCUMENT": "true", "LEECH_SPLIT_SIZE": "1GB"}
    Config.load_env(env)
    assert Config.QUEUE_DOWNLOAD == 7 and Config.AS_DOCUMENT is True and Config.LEECH_SPLIT_SIZE == 1024**3
    applied = Config.apply({"QUEUE_DOWNLOAD": 2, "BOT_TOKEN": "x", "UNKNOWN": 1})
    assert applied == ["QUEUE_DOWNLOAD"] and Config.QUEUE_DOWNLOAD == 2 and Config.BOT_TOKEN == ""
    assert Config.reset("QUEUE_DOWNLOAD", env) == 7
    assert Config.reset("AS_DOCUMENT", {}) is False
    Config.load_env({})
    assert Config.QUEUE_DOWNLOAD == 4


def test_show_masks_secrets():
    Config.set("GOFILE_TOKEN", "abc")
    assert Config.show("GOFILE_TOKEN") == "•••"
    Config.set("GOFILE_TOKEN", "")
    assert Config.show("DDL_SITES") == '["gofile"]'
    with pytest.raises(KeyError):
        Config.set("NOPE", "1")


def test_env_example_lists_every_key():
    from pathlib import Path

    text = Path(__file__).parents[1].joinpath(".env.example").read_text()
    missing = [key for key in Config.types() if f"\n{key}=" not in text]
    assert not missing
