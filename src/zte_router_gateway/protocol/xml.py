"""中兴固件返回的「伪 XML」解析。

实际响应形如：

    <OBJ_LAN_INFO_ID>
      <Instance>
        <ParaName>MACAddress</ParaName><ParaValue>aa:bb:...</ParaValue>
        ...
      </Instance>
    </OBJ_LAN_INFO_ID>

用正则解析（固件不保证严格合法 XML），实体交给 html.unescape 处理。
"""

from __future__ import annotations

import html
import re

_INSTANCE_RE = re.compile(r"<Instance>([\s\S]*?)</Instance>")
_PAIR_RE = re.compile(
    r"<ParaName>([\s\S]*?)</ParaName>\s*<ParaValue>([\s\S]*?)</ParaValue>"
)


def unescape_xml(value: str) -> str:
    return html.unescape(value)


def xml_field(xml: str, tag: str) -> str | None:
    """取单个标签的文本，如 <IF_ERRORSTR>SUCC</IF_ERRORSTR>。"""
    match = re.search(rf"<{re.escape(tag)}>([\s\S]*?)</{re.escape(tag)}>", xml)
    return unescape_xml(match.group(1).strip()) if match else None


def parse_instances(xml: str, obj_name: str) -> list[dict[str, str]]:
    """把一个 OBJ_xxx 块里的 Instance 解析成字典列表。"""
    block = re.search(
        rf"<{re.escape(obj_name)}>([\s\S]*?)</{re.escape(obj_name)}>", xml
    )
    if not block:
        return []

    rows: list[dict[str, str]] = []
    for instance in _INSTANCE_RE.finditer(block.group(1)):
        row: dict[str, str] = {}
        for pair in _PAIR_RE.finditer(instance.group(1)):
            name = unescape_xml(pair.group(1).strip())
            value = unescape_xml(pair.group(2).strip())
            row[name] = value
        if row:
            rows.append(row)
    return rows


def parse_all_instances(xml: str) -> list[dict[str, str]]:
    """不关心外层 OBJ 名字，直接扫出所有 Instance（不同固件的对象名不一样时用）。"""
    rows: list[dict[str, str]] = []
    for instance in _INSTANCE_RE.finditer(xml):
        row: dict[str, str] = {}
        for pair in _PAIR_RE.finditer(instance.group(1)):
            name = unescape_xml(pair.group(1).strip())
            value = unescape_xml(pair.group(2).strip())
            row[name] = value
        if row:
            rows.append(row)
    return rows
