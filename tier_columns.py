"""档位列名、价格解析与表头绑定。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

FREE_COLUMN = "无偿支持"
_PRICE_AT_END = re.compile(r"（￥([0-9]+(?:\.[0-9]+)?)）\s*$")
_LV_AT_START = re.compile(r"^Lv\.?\s*([0-9]+)", re.IGNORECASE)
_LV_PREFIX = re.compile(r"^Lv\.?\s*[0-9]+[\s·\-—:：]*", re.IGNORECASE)


@dataclass(frozen=True)
class Tier:
    """页面上的一个支持档位。免费档的 price 字段为空。"""

    price_text: str | None
    price_value: Decimal | None
    title: str
    count: int
    free: bool = False


def strip_lv_prefix(title: str) -> str:
    """去掉标题开头的 LV1 / LV 1 / LV1 · 等编号前缀。"""
    text = (title or "").strip()
    text = _LV_PREFIX.sub("", text, count=1).strip()
    return text


def format_tier_name(index: int, tier: Tier) -> str:
    """生成 Lv.{index} {标题}（￥{价格}） 或 Lv.{index}（￥{价格}）。"""
    if tier.free:
        return FREE_COLUMN
    if tier.price_text is None:
        raise ValueError("付费档位缺少价格文本")
    title = strip_lv_prefix(tier.title)
    if title:
        return f"Lv.{index} {title}（￥{tier.price_text}）"
    return f"Lv.{index}（￥{tier.price_text}）"


def parse_price(column: str) -> Decimal | None:
    """只接受列名末尾的全角括号价格，避免把标题里的 ￥ 当成价格。"""
    match = _PRICE_AT_END.search(column or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def parse_lv_no(column: str) -> int | None:
    match = _LV_AT_START.match(column or "")
    return int(match.group(1)) if match else None


def paid_tiers(tiers: Iterable[Tier]) -> list[Tier]:
    return [t for t in tiers if not t.free and t.price_value is not None]


def canonical_header(tiers: Sequence[Tier]) -> list[str]:
    """新文件的表头：固定三列 + 无偿支持（若存在）+ 按页面顺序的付费列。"""
    header = ["timestamp", "总人数", "总金额"]
    if any(t.free for t in tiers):
        header.append(FREE_COLUMN)
    paid = paid_tiers(tiers)
    for i, tier in enumerate(paid, 1):
        header.append(format_tier_name(i, tier))
    return header


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    k = 2
    while f"{name} #{k}" in used:
        k += 1
    return f"{name} #{k}"


def resolve_columns(header: Sequence[str], tiers: Sequence[Tier]) -> tuple[list[tuple[Tier, str]], list[str], bool]:
    """按价格把本次档位绑定到已有列。

    同价第 k 次出现绑定到表头中同价第 k 个列；只有价格不存在（或同价列不足）
    时才生成新列。返回 (档位->列, 新表头, 是否需要重写)。
    """
    new_header = list(header)
    by_price: dict[Decimal, list[str]] = {}
    for col in header:
        price = parse_price(col)
        if price is not None:
            by_price.setdefault(price, []).append(col)

    used_count: dict[Decimal, int] = {}
    mapping: list[tuple[Tier, str]] = []
    paid_index = 0
    used_names = set(new_header)

    for tier in tiers:
        if tier.free:
            col = FREE_COLUMN
            if col not in used_names:
                new_header.append(col)
                used_names.add(col)
            mapping.append((tier, col))
            continue

        if tier.price_value is None:
            continue
        paid_index += 1
        candidates = by_price.get(tier.price_value, [])
        k = used_count.get(tier.price_value, 0)
        if k < len(candidates):
            col = candidates[k]
        else:
            col = _unique_name(format_tier_name(paid_index, tier), used_names)
            new_header.append(col)
            used_names.add(col)
            by_price.setdefault(tier.price_value, []).append(col)
        used_count[tier.price_value] = k + 1
        mapping.append((tier, col))

    return mapping, new_header, new_header != list(header)
