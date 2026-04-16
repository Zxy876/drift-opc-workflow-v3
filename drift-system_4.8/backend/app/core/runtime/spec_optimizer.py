"""
spec_optimizer.py  —  Drift Design Optimization Engine (Phase 11)
=================================================================
自动调整 ExperienceSpec，使关卡胜率接近目标值（默认 0.5）。

流程：
  1. generate_variants  — 对 base_spec 做维度变异，产生 k 个候选
  2. evaluate_variants  — 并行 Monte Carlo 模拟每个候选
  3. score_variant      — 按目标胜率评分
  4. find_best_spec     — 综合排序，返回 Top-3 + 原始对比

设计约束：
  ❌ 不使用外部依赖 / RL / 大模型
  ✅ 纯 Python 标准库
  ✅ 不破坏现有 ExperienceSpec schema（probability 为可选扩展）
"""

from __future__ import annotations

import copy
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────────────────────

_COL_COND_RE = re.compile(
    r"(collected_count\s*>=?\s*)(\d+)"
)
_TIMER_COND_RE = re.compile(
    r"(timer\s*<=?\s*)(\d+)"
)


def _deep(spec: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝，保持原始 spec 不变。"""
    return copy.deepcopy(spec)


def _get_win_threshold(spec: Dict[str, Any]) -> Optional[int]:
    """从 win 规则中取 collected_count >= N 的 N。"""
    for rule in spec.get("rules") or []:
        if rule.get("type") == "win":
            m = _COL_COND_RE.search(str(rule.get("condition") or ""))
            if m:
                return int(m.group(2))
    return None


# 中文数字映射
_NUM_ZH = {1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九",10:"十"}


def _set_win_threshold(spec: Dict[str, Any], n: int) -> None:
    """就地修改 win 规则中 collected_count >= N 为 collected_count >= n，并同步更新 desc。"""
    for rule in spec.get("rules") or []:
        if rule.get("type") == "win":
            cond = str(rule.get("condition") or "")
            new_cond = _COL_COND_RE.sub(lambda m: m.group(1) + str(n), cond)
            if new_cond != cond:
                rule["condition"] = new_cond
                # 同步更新 desc 中的数字（中文数字 + 阿拉伯数字）
                desc = rule.get("desc", "")
                if desc:
                    zh_n = _NUM_ZH.get(n, str(n))
                    desc = re.sub(r'[一二三四五六七八九十]+(?=颗|个|枚|块)', zh_n, desc)
                    desc = re.sub(r'(?<=收集)\d+', str(n), desc)
                    desc = re.sub(r'(?<=collect\s)\d+', str(n), desc, flags=re.IGNORECASE)
                    rule["desc"] = desc
                return


def _get_initial_timer(spec: Dict[str, Any]) -> Optional[int]:
    state = spec.get("state") or {}
    iv = (state.get("initial_values") or {}) if isinstance(state, dict) else {}
    v = iv.get("timer")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return None


def _set_initial_timer(spec: Dict[str, Any], t: int) -> None:
    state = spec.get("state")
    if not isinstance(state, dict):
        return
    iv = state.get("initial_values")
    if isinstance(iv, dict) and "timer" in iv:
        iv["timer"] = t


def _get_collect_delta(spec: Dict[str, Any]) -> int:
    """返回 item_collect trigger 中 collected_count 的增量（默认 1）。"""
    for trigger in (spec.get("triggers") or []):
        if str(trigger.get("type", "")).lower() == "item_collect":
            action = trigger.get("action")
            if isinstance(action, dict):
                return int(action.get("collected_count", 1))
    return 1


def _set_collect_delta(spec: Dict[str, Any], delta: int) -> None:
    """修改所有 item_collect trigger 的 collected_count 增量。"""
    for trigger in (spec.get("triggers") or []):
        if str(trigger.get("type", "")).lower() == "item_collect":
            action = trigger.get("action")
            if isinstance(action, dict) and "collected_count" in action:
                action["collected_count"] = delta


def _get_guard_prob(spec: Dict[str, Any]) -> float:
    """取 guard_wanders 概率（probability 可选字段）；若无则返回默认 0.15。"""
    for trigger in (spec.get("triggers") or []):
        if "guard" in str(trigger.get("target") or "").lower():
            p = trigger.get("probability")
            if p is not None:
                try:
                    return float(p)
                except (TypeError, ValueError):
                    pass
    return 0.15   # simulation_engine 默认 guard_wanders weight=0.15


def _set_guard_prob(spec: Dict[str, Any], prob: float) -> None:
    """
    设置 guard 触发概率。
    本字段用于告知 simulation_engine 守卫 weight；
    同时写入 triggers[*].probability（可选扩展，向后兼容）。
    """
    # 直接在 spec root 放置 _guard_weight 供 simulation_engine 读取
    spec["_guard_weight"] = round(prob, 4)
    for trigger in (spec.get("triggers") or []):
        if "guard" in str(trigger.get("target") or "").lower():
            trigger["probability"] = round(prob, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1：Spec Mutation Engine
# ─────────────────────────────────────────────────────────────────────────────

def generate_variants(base_spec: Dict[str, Any], k: int = 20) -> List[Dict[str, Any]]:
    """
    对 base_spec 沿 4 个维度变异，产生 k 个合法 ExperienceSpec 变体。

    变异维度：
      1. win_threshold   — collected_count >= N，N ∈ [2, 10]
      2. timer           — initial_values.timer ∈ [20, 120]
      3. guard_prob      — 守卫出现概率 ∈ [0.05, 0.40]
      4. collect_delta   — item_collect +1 或 +2（稀有情况）

    保证：
      - 深拷贝，不修改 base_spec
      - 不新增必填 schema 字段
    """
    rng = random.Random(42)

    base_threshold = _get_win_threshold(base_spec)
    base_timer     = _get_initial_timer(base_spec)
    base_guard     = _get_guard_prob(base_spec)
    base_delta     = _get_collect_delta(base_spec)

    has_threshold = base_threshold is not None
    has_timer     = base_timer is not None

    variants: List[Dict[str, Any]] = []

    for _ in range(k):
        v = _deep(base_spec)

        # ── 确保变体中至少有一条 lose 规则（使优化有意义）────────────────
        # 若原始 spec 完全没有 lose 规则，optimizer 自动注入 guard_detected 条件；
        # 原始 spec 不受影响，只修改变体。
        has_lose = any(r.get("type") == "lose" for r in v.get("rules") or [])
        if not has_lose:
            v.setdefault("rules", []).append({
                "type": "lose",
                "condition": "guard_detected == true",
                "desc": "(optimizer) guard patrol",
            })
            # 确保 state 中有 guard_detected 初始值
            state_block = v.setdefault("state", {})
            if isinstance(state_block, dict):
                iv = state_block.setdefault("initial_values", {})
                if "guard_detected" not in iv:
                    iv["guard_detected"] = False

        # 1. win_threshold
        if has_threshold:
            new_t = rng.randint(2, 10)
            _set_win_threshold(v, new_t)

        # 2. timer
        if has_timer:
            # 偏向短计时（难度更高）→ 指数分布逼近
            raw = rng.expovariate(1 / 40)          # 均值40
            new_timer = max(20, min(120, int(raw + 20)))
            _set_initial_timer(v, new_timer)

        # 3. guard_prob（weight，而非概率；range 扩大以覆盖更宽难度区间）
        new_guard = round(rng.uniform(0.05, 2.5), 3)
        _set_guard_prob(v, new_guard)

        # 4. collect_delta（10% 概率升为 +2）
        new_delta = 2 if rng.random() < 0.10 else 1
        if has_threshold:
            _set_collect_delta(v, new_delta)

        variants.append(v)

    return variants


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2：Batch Simulation（并行）
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_variants(
    specs: List[Dict[str, Any]],
    n: int = 150,
    max_workers: int = 4,
) -> List[Dict[str, Any]]:
    """
    并行 Monte Carlo 模拟每个变体。

    返回列表，每项：
    {
      "spec":       dict,
      "win_rate":   float,
      "avg_steps":  float,
      "difficulty": str,
      "fail_reasons": dict,
    }
    """
    from app.core.runtime.simulation_engine import simulate_experience_spec

    def _run_one(idx: int, spec: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        result = simulate_experience_spec(spec, n=n, seed=idx)
        return idx, {
            "spec":         spec,
            "win_rate":     result.get("win_rate"),
            "avg_steps":    result.get("avg_steps"),
            "difficulty":   result.get("difficulty", "unknown"),
            "fail_reasons": result.get("fail_reasons", {}),
        }

    results: List[Optional[Dict[str, Any]]] = [None] * len(specs)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, i, s): i for i, s in enumerate(specs)}
        for fut in as_completed(futures):
            idx, data = fut.result()
            results[idx] = data

    return [r for r in results if r is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3：Scoring Function
# ─────────────────────────────────────────────────────────────────────────────

def score_variant(result: Dict[str, Any], target_win_rate: float = 0.5) -> float:
    """
    评分逻辑（越高越好，范围 ∈ [0, 1]）：

      base_score = 1 - abs(win_rate - target)

    惩罚：
      win_rate < 0.1  → -0.2
      win_rate > 0.9  → -0.2
    """
    win_rate = result.get("win_rate")
    if win_rate is None:
        return 0.0

    score = 1.0 - abs(win_rate - target_win_rate)

    # 极端惩罚
    if win_rate < 0.10:
        score -= 0.20
    elif win_rate > 0.90:
        score -= 0.20

    return round(max(0.0, min(1.0, score)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4：选择最优 Spec
# ─────────────────────────────────────────────────────────────────────────────

def find_best_spec(
    base_spec: Dict[str, Any],
    target_win_rate: float = 0.5,
    k: int = 30,
    n_sim: int = 150,
) -> Dict[str, Any]:
    """
    主流程：
      1. 生成 k 个变体
      2. 并行模拟评估
      3. 打分排序
      4. 返回 Top-3 + 原始 spec 对比

    返回结构：
    {
      "best_spec":          dict,        # 评分最高的 spec
      "candidates":         [            # Top-3
        {
          "rank":       1,
          "score":      0.98,
          "win_rate":   0.51,
          "avg_steps":  8.2,
          "difficulty": "medium",
          "spec":       dict,
          "diff_summary": str,           # 与原始 spec 的差异摘要
        },
        ...
      ],
      "original_win_rate":  float,
      "optimized_win_rate": float,       # top-1 胜率
      "target_win_rate":    float,
    }
    """
    from app.core.runtime.simulation_engine import simulate_experience_spec

    # ── 原始 spec 模拟 ────────────────────────────────────────────────
    orig_result = simulate_experience_spec(base_spec, n=n_sim, seed=0)
    original_win_rate = orig_result.get("win_rate") or 0.0

    # ── 生成并评估变体 ────────────────────────────────────────────────
    variants = generate_variants(base_spec, k=k)
    evaluated = evaluate_variants(variants, n=n_sim, max_workers=4)

    # ── 打分排序 ──────────────────────────────────────────────────────
    scored = sorted(
        evaluated,
        key=lambda r: score_variant(r, target_win_rate),
        reverse=True,
    )

    # ── 构建 Top-3 candidates ─────────────────────────────────────────
    candidates = []
    for rank, ev in enumerate(scored[:3], start=1):
        candidates.append({
            "rank":         rank,
            "score":        score_variant(ev, target_win_rate),
            "win_rate":     ev.get("win_rate"),
            "avg_steps":    ev.get("avg_steps"),
            "difficulty":   ev.get("difficulty"),
            "fail_reasons": ev.get("fail_reasons", {}),
            "spec":         ev.get("spec"),
            "diff_summary": _diff_summary(base_spec, ev.get("spec") or {}),
        })

    best_spec   = candidates[0]["spec"] if candidates else base_spec
    best_win    = candidates[0]["win_rate"] if candidates else original_win_rate

    # 若原始 spec 无 lose 规则，在结果中给出提示
    base_has_lose = any(r.get("type") == "lose" for r in (base_spec.get("rules") or []))
    warnings: List[str] = []
    if not base_has_lose:
        warnings.append(
            "原始 spec 没有 lose 规则，optimizer 已为变体自动添加 guard_detected 守卫条件以使优化有意义。"
            "建议在关卡设计中明确添加失败条件。"
        )

    return {
        "best_spec":          best_spec,
        "candidates":         candidates,
        "original_win_rate":  original_win_rate,
        "optimized_win_rate": best_win,
        "target_win_rate":    target_win_rate,
        "warnings":           warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 内部 diff 工具
# ─────────────────────────────────────────────────────────────────────────────

def _diff_summary(orig: Dict[str, Any], variant: Dict[str, Any]) -> str:
    """生成人类可读的变体变化摘要（与原始 spec 的差异）。"""
    parts: List[str] = []

    orig_t  = _get_win_threshold(orig)
    new_t   = _get_win_threshold(variant)
    if orig_t is not None and new_t is not None and orig_t != new_t:
        parts.append(f"win_threshold {orig_t}→{new_t}")

    orig_tm = _get_initial_timer(orig)
    new_tm  = _get_initial_timer(variant)
    if orig_tm is not None and new_tm is not None and orig_tm != new_tm:
        parts.append(f"timer {orig_tm}→{new_tm}")

    orig_g  = round(_get_guard_prob(orig), 3)
    new_g   = round(_get_guard_prob(variant), 3)
    if abs(orig_g - new_g) > 0.005:
        parts.append(f"guard_prob {orig_g:.2f}→{new_g:.2f}")

    orig_d  = _get_collect_delta(orig)
    new_d   = _get_collect_delta(variant)
    if orig_d != new_d:
        parts.append(f"collect_delta +{orig_d}→+{new_d}")

    return ", ".join(parts) if parts else "基线相同"
