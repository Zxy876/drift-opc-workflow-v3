from __future__ import annotations

from typing import Any


_DIRECT_RESOURCE_ALIASES = {
    # 食物
    "porkchop": "pork",
    "raw_porkchop": "pork",
    "cooked_porkchop": "pork",

    # 中文→英文映射（LLM 设计文本用中文，MC 物品是英文）
    "珍珠": "ender_pearl",
    "末影珍珠": "ender_pearl",
    "宝石": "emerald",
    "绿宝石": "emerald",
    "钻石": "diamond",
    "金锭": "gold_ingot",
    "铁锭": "iron_ingot",
    "钥匙": "tripwire_hook",
    "符文": "nether_star",
    "下界之星": "nether_star",
    "荧石粉": "glowstone_dust",
    "海晶碎片": "prismarine_shard",
    "书": "book",
    "书本": "book",
    "弓箭": "bow",
    "弓": "bow",
    "盔甲": "iron_chestplate",
    "木头": "wood",
    "原木": "wood",
    "火把": "torch",

    # MC ID 变体（确保内部键也能归一化）
    "ender_pearl": "ender_pearl",
    "prismarine_shard": "prismarine_shard",
    "glowstone_dust": "glowstone_dust",
    "nether_star": "nether_star",
    "tripwire_hook": "tripwire_hook",
    "gold_ingot": "gold_ingot",
    "iron_ingot": "iron_ingot",
    "iron_chestplate": "iron_chestplate",
}


def _strip_collect_prefix(token: str) -> str:
    if token.startswith("collect_"):
        return token[len("collect_") :]
    if token.startswith("collect:"):
        return token[len("collect:") :]
    return token


def _strip_namespace_or_suffix(token: str) -> str:
    if ":" not in token:
        return token

    parts = [segment.strip("_") for segment in token.split(":") if segment.strip("_")]
    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0]

    namespace = parts[0]
    path = parts[1]

    if namespace in {"minecraft", "mc"}:
        return path

    if len(parts) == 2:
        if path.isdigit():
            return namespace
        return f"{namespace}:{path}"

    if path.isdigit():
        return namespace

    if parts[-1].isdigit():
        return f"{namespace}:{path}"

    return f"{namespace}:{path}"


def normalize_inventory_resource_token(raw_value: Any) -> str:
    token = str(raw_value or "").strip().lower()
    if not token:
        return ""

    token = token.replace("-", "_").replace(" ", "_")
    token = _strip_collect_prefix(token)
    token = _strip_namespace_or_suffix(token)
    token = token.strip("_")
    if not token:
        return ""

    aliased = _DIRECT_RESOURCE_ALIASES.get(token)
    if aliased:
        return aliased

    if ":" not in token and (token.endswith("_log") or token.endswith("_wood") or token.endswith("_stem")):
        return "wood"

    return token