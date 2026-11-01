import pytest

from Mirror.core.flags import FLAGS, FlagError, TaskArgs, flags_help, parse_args


def test_links_and_greedy_name():
    args = parse_args("/leech https://a.b/c.mkv -n My Movie 2024.mkv -z")
    assert args.links == ["https://a.b/c.mkv"]
    assert args.name == "My Movie 2024.mkv"
    assert args.zip and args.zip_pass == ""


def test_optional_values_and_links_after_flags():
    args = parse_args("/mirror -z secret -e https://x.y/a.zip")
    assert args.zip_pass == "secret" and args.extract and args.extract_pass == ""
    assert args.link == "https://x.y/a.zip"


def test_typed_values():
    args = parse_args("/l magnet:?xt=urn:btih:abc -sd 1.5:30 -sp 1.9GB -ss -sv 30:3 -i 4 -up tg:-1001:7")
    assert args.seed and args.ratio == 1.5 and args.seed_time == 1800
    assert args.split == int(1.9 * 1024**3)
    assert args.shots == 10 and args.sample == "30:3" and args.multi == 4
    assert args.up == "tg:-1001:7"


def test_quoted_and_multiline():
    args = parse_args('/mirror "https://a/b c" -ff "-c:v libx265 -crf 28"\nhttps://two\nhttps://three')
    assert args.links == ["https://a/b c", "https://two", "https://three"]
    assert args.ffmpeg == "-c:v libx265 -crf 28"


def test_bool_flags():
    args = parse_args("/l x -s -b -doc -mg -ut -f -fd -fu -j -hl -es")
    assert all((args.select, args.bulk, args.doc, args.group, args.user_session, args.force, args.force_dl,
                args.force_up, args.join, args.hybrid, args.equal))


def test_errors():
    with pytest.raises(FlagError):
        parse_args("/m link -nope")
    with pytest.raises(FlagError):
        parse_args("/m link -up")
    with pytest.raises(FlagError):
        parse_args("/m link -sp huge")


def test_without_command_and_negative_numbers():
    args = parse_args("https://a -up -1002", has_command=False)
    assert args.up == "-1002"
    assert parse_args("").links == []


def test_every_flag_maps_to_a_field():
    fields = set(TaskArgs.__dataclass_fields__)
    for attr, _, _ in FLAGS.values():
        assert attr in fields
    assert "-ff" in flags_help()
