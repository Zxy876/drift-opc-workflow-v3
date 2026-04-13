"""
Difficulty Amplifier — 视觉复杂度放大器
========================================
在 world_patch 生成后、推送到 MC 前，根据 difficulty 等级 (1-5)
自动放大视觉参数，使玩家一进入关卡就能感受到复杂度差异。

设计原则：
- 所有参数均为数据驱动（配置表），不硬编码具体事物
- 根据关卡已有内容动态决定增强什么
- D1 像便签，D5 像电影

使用方式：
    from app.core.runtime.difficulty_amplifier import amplify_world_patch
    amplified = amplify_world_patch(world_patch, difficulty=3)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ─── 难度等级配置表 ──────────────────────────────────────────────────────────
# 所有视觉参数均由此表驱动，修改此表即可调整视觉效果，无需改动逻辑代码。

DIFFICULTY_PROFILES: Dict[int, Dict[str, Any]] = {
    1: {
        "label":       "§7[简单]",
        "stars":       "§7★",
        "color":       "§7",
        "platform_size_bonus": 0,
        "particle_multiplier": 0.0,
        "particle_pool":       [],
        "sound_enabled":       False,
        "weather_override":    None,
        "time_override":       None,
        "build_decorations":   0,
        "npc_ambient_count":   0,
        "bossbar_enabled":     False,
        "bossbar_color":       "WHITE",
        "bossbar_style":       "SOLID",
        "trigger_zone_particles": False,
        "trigger_zone_beacon":    False,
        "beat_visual_level":   0,
        "actionbar_hints":     False,
        "title_fade_in":       10,
        "title_stay":          40,
        "title_fade_out":      10,
    },
    2: {
        "label":       "§a[普通]",
        "stars":       "§a★★",
        "color":       "§a",
        "platform_size_bonus": 2,
        "particle_multiplier": 1.0,
        "particle_pool":       ["END_ROD", "HAPPY_VILLAGER"],
        "sound_enabled":       True,
        "weather_override":    None,
        "time_override":       None,
        "build_decorations":   0,
        "npc_ambient_count":   0,
        "bossbar_enabled":     False,
        "bossbar_color":       "GREEN",
        "bossbar_style":       "SOLID",
        "trigger_zone_particles": False,
        "trigger_zone_beacon":    False,
        "beat_visual_level":   1,
        "actionbar_hints":     True,
        "title_fade_in":       10,
        "title_stay":          50,
        "title_fade_out":      15,
    },
    3: {
        "label":       "§e[困难]",
        "stars":       "§e★★★",
        "color":       "§e",
        "platform_size_bonus": 4,
        "particle_multiplier": 2.0,
        "particle_pool":       ["END_ROD", "CHERRY_LEAVES", "HAPPY_VILLAGER"],
        "sound_enabled":       True,
        "weather_override":    None,
        "time_override":       None,
        "build_decorations":   1,
        "npc_ambient_count":   1,
        "bossbar_enabled":     True,
        "bossbar_color":       "YELLOW",
        "bossbar_style":       "SEGMENTED_6",
        "trigger_zone_particles": True,
        "trigger_zone_beacon":    False,
        "beat_visual_level":   2,
        "actionbar_hints":     True,
        "title_fade_in":       15,
        "title_stay":          60,
        "title_fade_out":      20,
    },
    4: {
        "label":       "§6[史诗]",
        "stars":       "§6★★★★",
        "color":       "§6",
        "platform_size_bonus": 6,
        "particle_multiplier": 3.0,
        "particle_pool":       ["SOUL_FIRE_FLAME", "CHERRY_LEAVES", "END_ROD", "FLAME"],
        "sound_enabled":       True,
        "weather_override":    None,
        "time_override":       None,
        "build_decorations":   2,
        "npc_ambient_count":   2,
        "bossbar_enabled":     True,
        "bossbar_color":       "RED",
        "bossbar_style":       "SEGMENTED_10",
        "trigger_zone_particles": True,
        "trigger_zone_beacon":    True,
        "beat_visual_level":   3,
        "actionbar_hints":     True,
        "title_fade_in":       20,
        "title_stay":          80,
        "title_fade_out":      25,
    },
    5: {
        "label":       "§4§l[传说]",
        "stars":       "§4★★★★★",
        "color":       "§4",
        "platform_size_bonus": 10,
        "particle_multiplier": 5.0,
        "particle_pool":       ["DRAGON_BREATH", "SOUL_FIRE_FLAME", "END_ROD", "FLAME", "CHERRY_LEAVES"],
        "sound_enabled":       True,
        "weather_override":    "thunder",
        "time_override":       "night",
        "build_decorations":   3,
        "npc_ambient_count":   3,
        "bossbar_enabled":     True,
        "bossbar_color":       "PURPLE",
        "bossbar_style":       "SEGMENTED_20",
        "trigger_zone_particles": True,
        "trigger_zone_beacon":    True,
        "beat_visual_level":   4,
        "actionbar_hints":     True,
        "title_fade_in":       30,
        "title_stay":          100,
        "title_fade_out":      30,
    },
}

# 装饰建筑形状池（不硬编码材质，从关卡已有 build 推导或使用主题映射）
DECORATION_SHAPES: List[Dict[str, Any]] = [
    {"shape": "wall",     "size_ratio": 0.6, "offset_angle": 0},
    {"shape": "platform", "size_ratio": 0.4, "offset_angle": 90},
    {"shape": "wall",     "size_ratio": 0.5, "offset_angle": 180},
    {"shape": "platform", "size_ratio": 0.3, "offset_angle": 270},
]

# 主题→装饰材质映射（从关卡 theme 推导，而非硬编码）
THEME_DECORATION_MATERIALS: Dict[str, List[str]] = {
    "dawn":  ["SMOOTH_QUARTZ", "WHITE_CONCRETE", "BIRCH_PLANKS"],
    "noon":  ["OAK_PLANKS", "COBBLESTONE", "STONE_BRICKS"],
    "dusk":  ["PINK_STAINED_GLASS", "CHERRY_PLANKS", "TERRACOTTA"],
    "night": ["DEEPSLATE_BRICKS", "BLACKSTONE", "OBSIDIAN"],
}

# Beat 视觉等级对应的增强效果
BEAT_VISUAL_EFFECTS: Dict[int, Dict[str, Any]] = {
    0: {},  # D1: 无额外效果
    1: {    # D2: tell + 音效
        "sound": {"type": "ENTITY_EXPERIENCE_ORB_PICKUP", "volume": 0.6, "pitch": 1.2},
    },
    2: {    # D3: title 卡 + 音效 + 粒子爆发
        "sound": {"type": "ENTITY_PLAYER_LEVELUP", "volume": 0.7, "pitch": 1.0},
        "particle_burst": {"count": 60, "radius": 3.0},
    },
    3: {    # D4: 大 title + weather 变化 + 大粒子爆发
        "sound": {"type": "ENTITY_ENDER_DRAGON_GROWL", "volume": 0.5, "pitch": 1.0},
        "particle_burst": {"count": 150, "radius": 5.0},
        "weather_shift": True,
    },
    4: {    # D5: 全屏 cinematic + 大规模视觉
        "sound": {"type": "ENTITY_WITHER_SPAWN", "volume": 0.4, "pitch": 1.0},
        "particle_burst": {"count": 300, "radius": 8.0},
        "weather_shift": True,
        "cinematic_fade": True,
    },
}


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _get_profile(difficulty: int) -> Dict[str, Any]:
    """获取难度配置，越界时 clamp 到 1-5。"""
    return DIFFICULTY_PROFILES[_clamp(difficulty, 1, 5)]


def _infer_theme(mc: Dict[str, Any]) -> str:
    """从已有 mc patch 推断主题（用于选择装饰材质）。"""
    time_val = str(mc.get("time", "")).lower()
    weather_val = str(mc.get("weather", "")).lower()

    if "night" in time_val or "dark" in weather_val:
        return "night"
    if "sunset" in time_val or "dusk" in time_val or "dream" in weather_val:
        return "dusk"
    if "sunrise" in time_val or "dawn" in time_val:
        return "dawn"
    return "noon"


def _infer_primary_material(mc: Dict[str, Any]) -> Optional[str]:
    """从已有 build 推断主要材质。"""
    build = mc.get("build")
    if isinstance(build, dict):
        mat = build.get("material")
        if isinstance(mat, str) and mat.strip():
            return mat.strip().upper()
    return None


def _infer_primary_size(mc: Dict[str, Any]) -> int:
    """从已有 build 推断主要尺寸。"""
    build = mc.get("build")
    if isinstance(build, dict):
        size = build.get("size")
        if isinstance(size, (int, float)):
            return int(size)
    return 6


def _count_triggers(mc: Dict[str, Any]) -> int:
    """统计已有触发区数量。"""
    tzs = mc.get("trigger_zones")
    if isinstance(tzs, list):
        return len(tzs)
    return 0


def _count_spawns(mc: Dict[str, Any]) -> int:
    """统计已有实体数量。"""
    count = 0
    for key in ("spawn", "spawn_multi", "spawns"):
        val = mc.get(key)
        if isinstance(val, list):
            count += len(val)
        elif isinstance(val, dict):
            count += 1
    return count


def _count_beats(level_meta: Dict[str, Any]) -> int:
    """从 level 元数据推断 beat 数量。"""
    exp_spec = level_meta.get("experience_spec")
    if isinstance(exp_spec, dict):
        beats = exp_spec.get("beats")
        if isinstance(beats, list):
            return len(beats)
    return 0


def _decoration_material(theme: str, index: int) -> str:
    """根据主题和索引选择装饰材质。"""
    pool = THEME_DECORATION_MATERIALS.get(theme, THEME_DECORATION_MATERIALS["noon"])
    return pool[index % len(pool)]


# ─── 核心放大函数 ────────────────────────────────────────────────────────────

def amplify_world_patch(
    world_patch: Dict[str, Any],
    difficulty: int,
    *,
    level_meta: Optional[Dict[str, Any]] = None,
    level_title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    根据 difficulty (1-5) 放大 world_patch 的视觉参数。

    这是主入口函数。所有增强都是数据驱动的，根据关卡已有内容动态决定：
    - 不会覆盖已有内容，只会追加或增强
    - 不硬编码具体事物（材质、实体类型从关卡上下文推导）
    - difficulty=1 几乎不改动，difficulty=5 全面增强

    Args:
        world_patch: 原始 world_patch dict（会被就地修改并返回）
        difficulty:  难度等级 1-5
        level_meta:  关卡元数据（可选，用于推断 beat 数量等）
        level_title: 关卡标题（可选，用于 title 卡）

    Returns:
        修改后的 world_patch（同一个对象）
    """
    if not isinstance(world_patch, dict):
        return world_patch

    difficulty = _clamp(difficulty, 1, 5)
    profile = _get_profile(difficulty)
    mc = world_patch.setdefault("mc", {})
    if not isinstance(mc, dict):
        return world_patch

    meta = level_meta or {}
    title_text = level_title or ""

    # ── 1. 难度徽章（Title 增强）──────────────────────────────────────────
    _amplify_title(mc, profile, difficulty, title_text)

    # ── 2. 粒子密度缩放 ─────────────────────────────────────────────────
    _amplify_particles(mc, profile, difficulty)

    # ── 3. 平台/建筑尺寸增强 ─────────────────────────────────────────────
    _amplify_build_size(mc, profile)

    # ── 4. 装饰建筑追加 ──────────────────────────────────────────────────
    _amplify_decorations(mc, profile, difficulty)

    # ── 5. 天气/时间覆盖（仅高难度） ─────────────────────────────────────
    _amplify_atmosphere(mc, profile)

    # ── 6. BossBar 任务追踪 ──────────────────────────────────────────────
    _amplify_bossbar(mc, profile, meta)

    # ── 7. ActionBar 提示 ────────────────────────────────────────────────
    _amplify_actionbar(mc, profile)

    # ── 8. 触发区粒子可见化 ──────────────────────────────────────────────
    _amplify_trigger_zones(mc, profile, difficulty)

    # ── 9. 写入难度元信息（供 Java 端读取） ──────────────────────────────
    mc["_difficulty"] = {
        "level": difficulty,
        "label": profile["label"],
        "stars": profile["stars"],
    }

    return world_patch


def amplify_beat_patch(
    beat_patch: Dict[str, Any],
    difficulty: int,
    *,
    beat_index: int = 0,
    total_beats: int = 1,
    beat_id: str = "",
) -> Dict[str, Any]:
    """
    根据 difficulty 增强 beat 激活时的视觉效果。

    Args:
        beat_patch:   beat 触发时的 world_patch
        difficulty:   难度等级 1-5
        beat_index:   当前是第几个 beat（0-based）
        total_beats:  总 beat 数
        beat_id:      beat 标识符
    """
    if not isinstance(beat_patch, dict):
        return beat_patch

    difficulty = _clamp(difficulty, 1, 5)
    profile = _get_profile(difficulty)
    mc = beat_patch.setdefault("mc", {})
    if not isinstance(mc, dict):
        return beat_patch

    beat_level = profile.get("beat_visual_level", 0)
    effects = BEAT_VISUAL_EFFECTS.get(beat_level, {})
    if not effects:
        return beat_patch

    # 音效
    sound_cfg = effects.get("sound")
    if sound_cfg and "sound" not in mc:
        mc["sound"] = dict(sound_cfg)

    # 粒子爆发
    burst = effects.get("particle_burst")
    if burst:
        existing = mc.get("particle")
        if isinstance(existing, dict):
            existing["count"] = max(existing.get("count", 0), burst["count"])
            existing["radius"] = max(existing.get("radius", 0), burst["radius"])
        else:
            particle_pool = profile.get("particle_pool", ["END_ROD"])
            particle_type = particle_pool[beat_index % len(particle_pool)] if particle_pool else "END_ROD"
            mc["particle"] = {
                "type": particle_type,
                "count": burst["count"],
                "radius": burst["radius"],
            }

    # 阶段 title（D3+）
    if beat_level >= 2 and total_beats > 1:
        phase_num = beat_index + 1
        phase_label = f"第{phase_num}幕" if phase_num <= total_beats else "终幕"
        color = profile["color"]
        if "title" not in mc:
            mc["title"] = {
                "main": f"{color}§l{phase_label}",
                "sub": f"§7{beat_id}" if beat_id else "",
                "fade_in": 20,
                "stay": 60,
                "fade_out": 20,
            }

    # BossBar 阶段进度更新
    if profile.get("bossbar_enabled") and total_beats > 1:
        progress = (beat_index + 1) / total_beats
        mc["bossbar"] = {
            "title": f"{profile['color']}阶段 {beat_index + 1}/{total_beats}",
            "color": profile["bossbar_color"],
            "style": profile["bossbar_style"],
            "progress": round(progress, 2),
        }

    return beat_patch


# ─── 各维度放大实现 ───────────────────────────────────────────────────────────

def _amplify_title(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
    difficulty: int,
    level_title: str,
) -> None:
    """在 title 前面添加难度徽章和星级。"""
    label = profile["label"]
    stars = profile["stars"]

    existing_title = mc.get("title")
    if isinstance(existing_title, dict):
        main = existing_title.get("main", "")
        # 避免重复添加徽章
        if label not in str(main):
            existing_title["main"] = f"{label} {main}" if main else f"{label} {level_title}"
            sub = existing_title.get("sub", "")
            if stars not in str(sub):
                existing_title["sub"] = f"{sub} {stars}" if sub else stars
        # 根据难度调整停留时间
        existing_title["fade_in"] = max(existing_title.get("fade_in", 10), profile["title_fade_in"])
        existing_title["stay"] = max(existing_title.get("stay", 40), profile["title_stay"])
        existing_title["fade_out"] = max(existing_title.get("fade_out", 10), profile["title_fade_out"])
    elif level_title:
        mc["title"] = {
            "main": f"{label} §f《{level_title}》",
            "sub": stars,
            "fade_in": profile["title_fade_in"],
            "stay": profile["title_stay"],
            "fade_out": profile["title_fade_out"],
        }


def _amplify_particles(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
    difficulty: int,
) -> None:
    """根据难度缩放粒子数量，或在无粒子时注入。"""
    multiplier = profile.get("particle_multiplier", 1.0)
    pool = profile.get("particle_pool", [])

    if multiplier <= 0:
        return

    existing = mc.get("particle")
    if isinstance(existing, dict):
        base_count = existing.get("count", 30)
        existing["count"] = int(base_count * max(1.0, multiplier))
        base_radius = existing.get("radius", 2.5)
        existing["radius"] = base_radius + (difficulty - 1) * 0.5
    elif pool:
        mc["particle"] = {
            "type": pool[0],
            "count": int(30 * multiplier),
            "radius": 2.0 + difficulty * 0.5,
        }


def _amplify_build_size(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
) -> None:
    """增大平台/建筑尺寸。"""
    bonus = profile.get("platform_size_bonus", 0)
    if bonus <= 0:
        return

    build = mc.get("build")
    if isinstance(build, dict):
        current = build.get("size", 6)
        if isinstance(current, (int, float)):
            build["size"] = int(current) + bonus


def _amplify_decorations(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
    difficulty: int,
) -> None:
    """根据难度追加装饰性建筑（材质从关卡上下文推导）。"""
    count = profile.get("build_decorations", 0)
    if count <= 0:
        return

    theme = _infer_theme(mc)
    primary_size = _infer_primary_size(mc)

    build_multi = mc.get("build_multi")
    if not isinstance(build_multi, list):
        build_multi = []
        mc["build_multi"] = build_multi

    for i in range(count):
        if i >= len(DECORATION_SHAPES):
            break
        template = DECORATION_SHAPES[i]
        angle_rad = math.radians(template["offset_angle"] + i * 30)
        distance = primary_size + 3 + i * 2
        dx = int(round(distance * math.cos(angle_rad)))
        dz = int(round(distance * math.sin(angle_rad)))
        dec_size = max(2, int(primary_size * template["size_ratio"]))
        material = _decoration_material(theme, i)

        build_multi.append({
            "shape": template["shape"],
            "material": material,
            "size": dec_size,
            "offset": {"dx": dx, "dy": 0, "dz": dz},
            "_amplifier_generated": True,
        })


def _amplify_atmosphere(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
) -> None:
    """高难度覆盖天气和时间。"""
    weather = profile.get("weather_override")
    if weather and "weather" not in mc:
        mc["weather"] = weather

    time_val = profile.get("time_override")
    if time_val and "time" not in mc:
        mc["time"] = time_val


def _amplify_bossbar(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
    level_meta: Dict[str, Any],
) -> None:
    """为 D3+ 注入 BossBar 任务追踪。"""
    if not profile.get("bossbar_enabled"):
        return
    if "bossbar" in mc:
        return

    # 从 exp_spec 推断追踪目标
    exp_spec = level_meta.get("experience_spec")
    track_event = ""
    max_count = 0
    bossbar_title = ""

    if isinstance(exp_spec, dict):
        # 从 triggers 找 item_collect 类型来追踪
        for trigger in (exp_spec.get("triggers") or []):
            if not isinstance(trigger, dict):
                continue
            ttype = str(trigger.get("type", "")).lower()
            if ttype == "item_collect":
                target = str(trigger.get("target", ""))
                track_event = f"exp_collect_{target.lower().replace(' ', '_')}" if target else ""
                try:
                    max_count = int(trigger.get("quantity", trigger.get("count", 0)))
                except (ValueError, TypeError):
                    max_count = 0
                bossbar_title = f"{profile['color']}{target or '任务'}进度"
                break

        # 如果没有 item_collect，用 beat 数量做阶段追踪
        if not track_event:
            beats = exp_spec.get("beats")
            if isinstance(beats, list) and len(beats) > 1:
                max_count = len(beats)
                track_event = "_beat_progress"
                bossbar_title = f"{profile['color']}剧情进度"

    if not bossbar_title:
        bossbar_title = f"{profile['color']}关卡进度"

    mc["bossbar"] = {
        "title": bossbar_title,
        "color": profile["bossbar_color"],
        "style": profile["bossbar_style"],
        "progress": 0.0,
    }
    if track_event:
        mc["bossbar"]["track_event"] = track_event
    if max_count > 0:
        mc["bossbar"]["max_count"] = max_count


def _amplify_actionbar(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
) -> None:
    """为 D2+ 注入 actionbar 提示。"""
    if not profile.get("actionbar_hints"):
        return
    if "actionbar" in mc:
        return

    # 从已有 trigger_zones 生成提示
    tzs = mc.get("trigger_zones")
    if isinstance(tzs, list) and tzs:
        first_tz = tzs[0] if isinstance(tzs[0], dict) else {}
        desc = first_tz.get("_exp_desc") or first_tz.get("quest_event", "")
        if desc:
            mc["actionbar"] = f"§b目标：{desc[:30]}"
            return

    # 从 tell 生成精简提示
    tell = mc.get("tell")
    if isinstance(tell, str) and tell:
        # 取 tell 的前 30 个字符作为 actionbar
        clean = tell.replace("§e", "").replace("§a", "").replace("§6", "").strip()
        if clean:
            mc["actionbar"] = f"§b{clean[:30]}"


def _amplify_trigger_zones(
    mc: Dict[str, Any],
    profile: Dict[str, Any],
    difficulty: int,
) -> None:
    """为 D3+ 的触发区添加粒子边界和光柱标记。"""
    if not profile.get("trigger_zone_particles"):
        return

    tzs = mc.get("trigger_zones")
    if not isinstance(tzs, list):
        return

    pool = profile.get("particle_pool", ["END_ROD"])
    use_beacon = profile.get("trigger_zone_beacon", False)

    for i, tz in enumerate(tzs):
        if not isinstance(tz, dict):
            continue
        # 不覆盖已有的粒子设置
        if "particle_border" in tz:
            continue

        particle_type = pool[i % len(pool)] if pool else "END_ROD"
        tz["particle_border"] = {
            "type": particle_type,
            "count": 10 + difficulty * 8,
            "height": min(difficulty, 5),
        }

        if use_beacon:
            tz["beacon"] = {
                "type": "FLAME" if difficulty < 5 else "SOUL_FIRE_FLAME",
                "count": 5 + difficulty * 3,
                "height": 10 + difficulty * 3,
            }


# ─── 辅助：从 exp_spec 推断 difficulty ────────────────────────────────────────

def infer_difficulty_from_spec(exp_spec: Dict[str, Any]) -> int:
    """
    从 experience_spec 的复杂度指标推断 difficulty 等级。

    评分维度：
    - trigger 数量
    - rule 数量
    - beat 数量
    - state 变量数量
    """
    if not isinstance(exp_spec, dict):
        return 1

    triggers = exp_spec.get("triggers")
    trigger_count = len(triggers) if isinstance(triggers, list) else 0

    rules = exp_spec.get("rules")
    rule_count = len(rules) if isinstance(rules, list) else 0

    beats = exp_spec.get("beats")
    beat_count = len(beats) if isinstance(beats, list) else 0

    state = exp_spec.get("state")
    state_count = 0
    if isinstance(state, dict):
        iv = state.get("initial_values")
        if isinstance(iv, dict):
            state_count = len(iv)

    # 综合评分
    score = trigger_count + rule_count * 1.5 + beat_count * 2 + state_count
    if score <= 2:
        return 1
    if score <= 5:
        return 2
    if score <= 10:
        return 3
    if score <= 18:
        return 4
    return 5
