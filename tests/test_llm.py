from jove.llm import parse_reply


def test_parse_reply_shapes():
    assert parse_reply('{"answers": {"q0": {"o0": 1}}}') == {"q0": {"o0": 1}}
    assert parse_reply('{"q0": {"o0": 1}}') == {"q0": {"o0": 1}}  # wrapper dropped
    assert parse_reply('```json\n{"answers": {}}\n```') == {}
    assert parse_reply("") == {}
    assert parse_reply("not json") == {}
    assert parse_reply("[1, 2]") == {}
