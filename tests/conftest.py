import pytest

from Mirror.config import Config
from Mirror.core.flags import TaskArgs, parse_args
from Mirror.core.prefs import USER_OPTIONS, Prefs
from Mirror.core.task import Task


def make_prefs(**values: object) -> Prefs:
    base = {key: getattr(Config, cfg) for key, (_, _, cfg) in USER_OPTIONS.items()}
    return Prefs(1, {**base, **values})


@pytest.fixture
def task_factory(tmp_path):
    def build(text: str = "/mirror https://x.y/f.bin", mode: str = "mirror", engine: str = "direct",
              **prefs: object) -> Task:
        args = parse_args(text) if text else TaskArgs()
        return Task(id="t1", user_id=1, chat_id=-100, message_id=5, mode=mode, engine=engine, args=args,
                    dir=tmp_path / "t1", prefs=make_prefs(**prefs))
    return build
