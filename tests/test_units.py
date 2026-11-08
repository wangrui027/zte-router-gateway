"""纯函数单测：协议解析、编码、超时判定。"""

from __future__ import annotations

from zte_router_gateway.protocol.xml import parse_instances, xml_field
from zte_router_gateway.util import form_encode, is_session_timeout, round_half_up

XML = """<root>
<OBJ_X>
<Instance>
<ParaName>MACAddress</ParaName><ParaValue>aa:bb</ParaValue>
<ParaName>Note</ParaName><ParaValue>a &amp; b &#65;</ParaValue>
</Instance>
</OBJ_X>
</root>"""


def test_form_encode_matches_encode_uri_component() -> None:
    encoded = form_encode({"a": "x y:z/1", "b": "~!*'()", "c": None, "d": ""})
    assert encoded == "a=x%20y%3Az%2F1&b=~!*'()&d="


def test_parse_instances() -> None:
    rows = parse_instances(XML, "OBJ_X")
    assert rows == [{"MACAddress": "aa:bb", "Note": "a & b A"}]


def test_xml_field() -> None:
    assert xml_field("<R><IF_ERRORSTR>SUCC</IF_ERRORSTR></R>", "IF_ERRORSTR") == "SUCC"
    assert xml_field("<R><X>a &lt; b</X></R>", "X") == "a < b"
    assert xml_field("<R></R>", "X") is None


def test_is_session_timeout() -> None:
    assert is_session_timeout('{"SessionTimeout":true}')
    assert is_session_timeout("session timeout")
    assert is_session_timeout("SESSION  TIMEOUT")
    assert not is_session_timeout('{"login_need_refresh":true}')


def test_round_half_up() -> None:
    assert round_half_up(2.5) == 3
    assert round_half_up(2.4) == 2
    assert round_half_up(-2.5) == -2
    assert round_half_up(0) == 0
