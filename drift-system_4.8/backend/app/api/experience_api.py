"""
experience_api.py — Phase 6 + Phase 7: Experience Observability & Authoring API
=================================================================================

Phase 6 (Observability):
  GET  /experience/state/{player_id}    — 玩家视角: 进度 + 状态 + 当前规则
  GET  /experience/debug/{player_id}    — 开发者视角: 最后一次事件完整 debug 信息
  GET  /experience/timeline/{player_id} — 事件时间线（最近 10 条）
  GET  /experience/history/{player_id}  — 完整历史
  DELETE /experience/reset/{player_id}  — 清除指定玩家的 debug 记录（测试用）

Phase 7 (Authoring):
  POST /experience/preview   — 文本 → DesignSpec + ExperienceSpec 摘要 + warnings
  POST /experience/validate  — 校验 DesignSpec / 文本，返回 completeness_score
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.core.runtime.experience_runtime import (
    experience_runtime_engine,
    experience_state_store,
    _load_level_doc,
)
from app.core.runtime.experience_debug_store import (
    experience_debug_store,
    summarize_progress,
)
from app.core.runtime.rule_document_generator import (
    generate_rule_document,
    rule_document_to_mc_tells,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/experience", tags=["Experience"])


def _resolve_level_id(player_id: str) -> Optional[str]:
    """
    获取玩家当前关卡 ID。
    优先从 quest_runtime snapshot 读，退而从 story_engine 读。
    """
    try:
        from app.core.quest.runtime import quest_runtime
        snap = quest_runtime.get_runtime_snapshot(player_id)
        if isinstance(snap, dict) and snap.get("level_id"):
            return str(snap["level_id"])
    except Exception:
        pass
    try:
        from app.core.story.story_engine import story_engine
        ps = story_engine.players.get(player_id) or {}
        level_obj = ps.get("level")
        if level_obj is not None:
            return str(getattr(level_obj, "level_id", level_obj) or "")
    except Exception:
        pass
    return None


def _load_exp_spec(level_id: str) -> Optional[Dict[str, Any]]:
    doc = _load_level_doc(level_id)
    if not doc:
        return None
    return (doc.get("meta") or {}).get("experience_spec")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Player View
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/state/{player_id}")
def get_experience_state(player_id: str) -> Dict[str, Any]:
    """
    玩家视角: 返回当前 experience state、进度摘要、活跃规则、总体状态。

    Response shape:
    {
      "level_id": "...",
      "state": {"collected_count": 2, "timer": 35},
      "progress": {"goal": "collected_count >= 3", "current": 2, "remaining": 1, ...},
      "active_rules": [...],
      "status": "in_progress" | "win" | "lose",
      "timeline": [...]    // 最近事件摘要
    }
    """
    level_id = _resolve_level_id(player_id)

    # 从 debug store 取最后一次 outcome（用于确定 status）
    last_debug = experience_debug_store.get_debug_view(player_id)
    last_outcome: Optional[str] = None
    if isinstance(last_debug, dict):
        last_outcome = last_debug.get("outcome")

    # 读取当前 state
    current_state: Dict[str, Any] = {}
    if level_id:
        loaded = experience_runtime_engine.get_current_state(player_id, level_id)
        if loaded:
            current_state = loaded

    # 过滤内部元数据键
    public_state = {k: v for k, v in current_state.items() if not k.startswith("_")}

    # 加载 exp_spec 生成进度摘要
    exp_spec = _load_exp_spec(level_id) if level_id else None
    progress_summary = summarize_progress(public_state, exp_spec, last_outcome)

    # 时间线（精简）
    timeline = experience_debug_store.get_timeline(player_id)

    return {
        "status": progress_summary.get("status", "in_progress"),
        "level_id": level_id,
        "state": public_state,
        "progress": progress_summary.get("progress"),
        "active_rules": progress_summary.get("active_rules", []),
        "timeline": timeline,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Debug View
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/debug/{player_id}")
def get_experience_debug(player_id: str) -> Dict[str, Any]:
    """
    开发者视角: 返回最后一次事件的完整 debug 信息。

    Response shape:
    {
      "last_event": "exp_collect_gem",
      "matched_trigger": "item_collect",
      "state_before": {...},
      "state_after": {...},
      "triggered_rules": [...],
      "rule_evaluation": [{"rule": "collected_count >= 3", "result": false}],
      "outcome": null | "win" | "lose" | ...
    }
    """
    debug_view = experience_debug_store.get_debug_view(player_id)
    if debug_view is None:
        return {
            "player_id": player_id,
            "message": "No events recorded yet for this player.",
            "last_event": None,
            "matched_trigger": None,
            "state_before": {},
            "state_after": {},
            "triggered_rules": [],
            "rule_evaluation": [],
            "outcome": None,
        }

    return {
        "player_id": player_id,
        "last_event": debug_view.get("event"),
        "event_type": debug_view.get("event_type"),
        "quest_event": debug_view.get("quest_event"),
        "matched_trigger": debug_view.get("matched_trigger"),
        "matched_triggers": debug_view.get("matched_triggers", []),
        "state_before": debug_view.get("state_before", {}),
        "state_after": debug_view.get("state_after", {}),
        "triggered_rules": debug_view.get("triggered_rules", []),
        "rule_evaluation": debug_view.get("rule_evaluation", []),
        "outcome": debug_view.get("outcome"),
        "ts_ms": debug_view.get("ts_ms"),
        "level_id": debug_view.get("level_id"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Timeline View
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/timeline/{player_id}")
def get_experience_timeline(player_id: str) -> Dict[str, Any]:
    """
    事件时间线: 返回最近 10 条事件的精简记录。

    Response shape:
    {
      "player_id": "...",
      "count": 3,
      "timeline": [
        {"ts_ms": ..., "event": "exp_collect_gem", "state": {"collected_count": 1}, "outcome": null},
        ...
      ]
    }
    """
    timeline = experience_debug_store.get_timeline(player_id)
    return {
        "player_id": player_id,
        "count": len(timeline),
        "timeline": timeline,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Full History (开发者用)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/history/{player_id}")
def get_experience_history(player_id: str) -> Dict[str, Any]:
    """返回最近 MAX_TIMELINE_LEN 条完整事件记录（含 state_before/after）。"""
    entries = experience_debug_store.get_all_entries(player_id)
    return {
        "player_id": player_id,
        "count": len(entries),
        "entries": entries,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reset (测试 / 调试用)
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/reset/{player_id}")
def reset_experience(player_id: str, level_id: Optional[str] = None) -> Dict[str, Any]:
    """
    重置玩家的 experience state 和 debug 记录。
    可选 ?level_id= 只重置特定关卡。
    """
    experience_debug_store.clear(player_id)

    if level_id:
        experience_state_store.delete_state(player_id, level_id)
        return {"status": "ok", "cleared": f"{player_id}/{level_id}"}

    # 没指定 level_id 时重置当前关卡
    resolved = _resolve_level_id(player_id)
    if resolved:
        experience_state_store.delete_state(player_id, resolved)
    return {"status": "ok", "cleared": f"{player_id}/{resolved or 'unknown'}"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7: Experience Authoring API
# ─────────────────────────────────────────────────────────────────────────────

class _PreviewRequest(BaseModel):
    text: str


class _ValidateRequest(BaseModel):
    text: Optional[str] = None
    design_spec: Optional[Dict[str, Any]] = None


class RuleDocRequest(BaseModel):
    level_id: str
    design_text: str = ""
    use_llm: bool = True


def _build_design_spec_from_dict(d: Dict[str, Any]):
    """从字典重建 DesignSpec（用于 validate 端点直接传入结构体时）。"""
    from app.core.runtime.experience_design_parser import DesignSpec, TriggerSpec
    triggers = []
    for t in (d.get("triggers") or []):
        if isinstance(t, dict):
            triggers.append(TriggerSpec(
                event=t.get("event", ""),
                action=t.get("action", ""),
                raw=t.get("raw", ""),
                trigger_type=t.get("trigger_type", "unknown"),
            ))
        elif isinstance(t, str):
            # 支持 "event → action" 字符串格式
            import re
            m = re.match(r"(.+?)\s*(?:→|->)\s*(.+)", t)
            if m:
                triggers.append(TriggerSpec(
                    event=m.group(1).strip(),
                    action=m.group(2).strip(),
                    raw=t,
                    trigger_type="unknown",
                ))
    return DesignSpec(
        goal=d.get("goal", ""),
        win_condition=d.get("win_condition", ""),
        lose_condition=d.get("lose_condition", ""),
        triggers=triggers,
        time_limit=d.get("time_limit"),
        state_vars=d.get("state_vars") or {},
        raw_text=d.get("raw_text", ""),
    )


@router.post("/preview")
def preview_experience_design(req: _PreviewRequest) -> Dict[str, Any]:
    """
    Phase 7 核心端点: 文本 → DesignSpec + ExperienceSpec 摘要 + warnings

    输入:
        {"text": "玩家需要收集三块宝石才能解锁神庙大门..."}

    输出:
        {
          "design_spec": {
            "goal": "收集三块宝石",
            "win_condition": "collected_count >= 3",
            "lose_condition": "guard_detected == True",
            "triggers": [...],
            "time_limit": null,
            "state_vars": {"collected_count": 0, "guard_detected": false}
          },
          "experience_spec_summary": {
            "rule_count": 2,
            "trigger_count": 3,
            "has_win_condition": true,
            ...
          },
          "warnings": []
        }

    设计约束（Phase 7）:
    - 不允许直接进入 runtime（必须经过 design_spec）
    - warnings 明确标出缺失结构
    """
    from app.core.runtime.experience_design_parser import (
        parse_design_text,
        to_experience_spec,
        generate_warnings,
    )
    from app.core.runtime.experience_spec_compiler import experience_spec_summary

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    from app.core.runtime.experience_design_parser import validate_design_spec

    design = parse_design_text(text)
    exp_spec = to_experience_spec(design)
    warnings = generate_warnings(design)
    summary = experience_spec_summary(exp_spec)
    _, completeness_score = validate_design_spec(design)
    rule_doc = generate_rule_document(exp_spec, text, use_llm=True)

    return {
        "design_spec": design.to_dict(),
        "experience_spec_summary": summary,
        "completeness_score": completeness_score,
        "rule_document": rule_doc,
        "warnings": warnings,
    }


@router.post("/rule-doc")
def get_rule_document(req: RuleDocRequest) -> Dict[str, Any]:
    """生成 RuleDocument：将 ExperienceSpec 转换为玩家可读规则文档。"""
    spec: Optional[Dict[str, Any]] = None
    level_id = str(req.level_id or "").strip()
    if level_id:
        spec = _load_exp_spec(level_id)

    if not spec and req.design_text:
        from app.core.runtime.experience_spec_compiler import compile_experience_spec

        spec = compile_experience_spec(req.design_text, use_llm=req.use_llm)

    if not spec:
        raise HTTPException(
            status_code=404,
            detail="No ExperienceSpec found. Provide design_text or inject first.",
        )

    rule_doc = generate_rule_document(spec, req.design_text, use_llm=req.use_llm)
    mc_tells = rule_document_to_mc_tells(rule_doc)

    return {
        "level_id": level_id,
        "rule_document": rule_doc,
        "mc_tells": mc_tells,
        "tell_count": len(mc_tells),
    }


@router.post("/validate")
def validate_experience_design(req: _ValidateRequest) -> Dict[str, Any]:
    """
    校验 DesignSpec（或从文本解析后校验）。

    输入（二选一）:
        {"text": "..."}               — 先解析再校验
        {"design_spec": {...}}        — 直接校验已有的 DesignSpec

    输出:
        {
          "valid": true,
          "issues": [],
          "completeness_score": 0.85,
          "design_spec": {...}        // 解析/使用的设计规格
        }
    """
    from app.core.runtime.experience_design_parser import (
        parse_design_text,
        validate_design_spec,
        generate_warnings,
    )

    if req.text:
        design = parse_design_text(req.text.strip())
    elif req.design_spec:
        design = _build_design_spec_from_dict(req.design_spec)
    else:
        raise HTTPException(
            status_code=400,
            detail="必须提供 'text' 或 'design_spec' 之一",
        )

    issues, score = validate_design_spec(design)
    warnings = generate_warnings(design)

    # 合并 issues + warnings（去重）
    all_issues: List[str] = list(issues)
    for w in warnings:
        if w not in all_issues:
            all_issues.append(w)

    return {
        "valid": len(issues) == 0,
        "issues": all_issues,
        "completeness_score": score,
        "design_spec": design.to_dict(),
    }


# =====================================================================
# Phase 10：Simulation Engine
# =====================================================================

def _resolve_experience_spec(
    level_id: Optional[str],
    experience_spec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """从 level_id 或直接传入的 spec 获取 experience_spec。"""
    if experience_spec:
        return experience_spec
    if level_id:
        level_id_clean = str(level_id).strip()
        level_doc = _load_level_doc(level_id_clean)
        if level_doc is None:
            raise HTTPException(status_code=404, detail=f"Level '{level_id_clean}' not found")
        meta = level_doc.get("meta") or {}
        spec = meta.get("experience_spec")
        if not isinstance(spec, dict):
            raise HTTPException(
                status_code=422,
                detail=f"Level '{level_id_clean}' has no experience_spec in meta",
            )
        return spec
    raise HTTPException(
        status_code=400,
        detail="必须提供 'level_id' 或 'experience_spec' 之一",
    )


class _SimulateRequest(BaseModel):
    level_id: Optional[str] = None          # 从已有关卡加载 spec
    experience_spec: Optional[Dict[str, Any]] = None  # 直接传入 spec
    n: int = 200                            # Monte Carlo 次数
    seed: Optional[int] = None             # 随机种子（可复现）
    max_steps: int = 30                    # 每局最大步数


@router.post("/simulate")
def simulate_level(req: _SimulateRequest) -> Dict[str, Any]:
    """
    Phase 10 — 关卡 Monte Carlo 模拟。

    输入（二选一）:
        {"level_id": "exp_runtime_test_001"}          — 从关卡文件加载 ExperienceSpec
        {"experience_spec": {...}}                     — 直接传入 ExperienceSpec

    可选：
        "n": 200         — 模拟次数（10–1000）
        "seed": 42       — 随机种子
        "max_steps": 30  — 每局最大步数

    输出：
        {
          "win_rate": 0.78,
          "avg_steps": 6.2,
          "fail_reasons": {"guard_detected": 0.15},
          "difficulty": "medium",
          "insights": ["..."],
          "_meta": {...}
        }
    """
    from app.core.runtime.simulation_engine import simulate_experience_spec

    # ── 1. 获取 experience_spec ──────────────────────────────────────
    spec = _resolve_experience_spec(req.level_id, req.experience_spec)

    # ── 2. 参数校验 ──────────────────────────────────────────────────
    n = max(10, min(1000, req.n))
    max_steps = max(5, min(200, req.max_steps))

    # ── 3. 运行模拟 ──────────────────────────────────────────────────
    result = simulate_experience_spec(spec, n=n, seed=req.seed, max_steps=max_steps)
    result["level_id"] = req.level_id or "(inline)"
    return result


# =====================================================================
# Phase 11：Design Optimization Engine
# =====================================================================

class _OptimizeRequest(BaseModel):
    level_id: Optional[str] = None
    experience_spec: Optional[Dict[str, Any]] = None
    target_win_rate: float = 0.5    # 目标胜率（0–1）
    k: int = 30                     # 变体数量
    n_sim: int = 150                # 每个变体模拟次数


@router.post("/optimize")
def optimize_level(req: _OptimizeRequest) -> Dict[str, Any]:
    """
    Phase 11 — 关卡自动平衡优化。

    输入（二选一）：
        {"level_id": "gem_temple_v1"}
        {"experience_spec": {...}}

    可选：
        "target_win_rate": 0.5   — 目标胜率（默认 0.5 = 中等难度）
        "k": 30                  — 变体数量（10–60）
        "n_sim": 150             — 每变体模拟次数（50–300）

    输出：
        {
          "best_spec":          {...},
          "candidates":         [{rank, score, win_rate, avg_steps, difficulty, diff_summary, spec}, ...],
          "original_win_rate":  0.9,
          "optimized_win_rate": 0.52,
          "target_win_rate":    0.5,
        }
    """
    from app.core.runtime.spec_optimizer import find_best_spec

    # ── 1. 获取 experience_spec ──────────────────────────────────────
    spec = _resolve_experience_spec(req.level_id, req.experience_spec)

    # ── 2. 参数校验 ──────────────────────────────────────────────────
    target = max(0.05, min(0.95, req.target_win_rate))
    k      = max(10, min(60, req.k))
    n_sim  = max(50, min(300, req.n_sim))

    # ── 3. 运行优化 ──────────────────────────────────────────────────
    result = find_best_spec(spec, target_win_rate=target, k=k, n_sim=n_sim)
    result["level_id"] = req.level_id or "(inline)"
    return result


# =====================================================================
# Phase 11B：Apply Optimized Spec -> 关卡注入
# =====================================================================

class _ApplySpecRequest(BaseModel):
    level_id: str
    player_id: str
    spec: Dict[str, Any]  # ExperienceSpec JSON (from Auto Balance)


@router.post("/apply-spec")
def apply_optimized_spec(req: _ApplySpecRequest) -> Dict[str, Any]:
    """
    将 Auto Balance 产出的最优 ExperienceSpec 直接写入关卡文件并激活。
    面板 "Apply Best Spec" 按钮调用此端点，实现 P11 优化→注入的完整闭环。
    """
    import json as _json
    from app.core.runtime.experience_spec_compiler import experience_spec_summary

    level_id = str(req.level_id).strip()
    player_id = str(req.player_id).strip()

    level_doc = _load_level_doc(level_id)
    if level_doc is None:
        raise HTTPException(status_code=404, detail=f"Level '{level_id}' not found")

    # 写入 spec 并持久化（使用与 _load_level_doc 相同的搜索路径）
    level_doc.setdefault("meta", {})["experience_spec"] = req.spec

    from app.core.story.story_loader import (
        _find_level_path as _slp_find,
        _candidate_filenames as _slp_candidates,
        DATA_DIR as _DATA_DIR,
    )
    level_path: Optional[str] = None
    for _fn in _slp_candidates(level_id):
        _found = _slp_find(_fn)
        if _found and os.path.isfile(_found):
            level_path = _found
            break
    if level_path is None:
        # 关卡文件不存在，写入 generated/ 目录（_find_level_path 也会搜索此目录）
        _gen_dir = os.path.join(_DATA_DIR, "generated")
        os.makedirs(_gen_dir, exist_ok=True)
        level_path = os.path.join(_gen_dir, f"{level_id}.json")
    with open(level_path, "w", encoding="utf-8") as f:
        _json.dump(level_doc, f, ensure_ascii=False, indent=2)

    # 清除旧 debug 快照，避免残留
    try:
        experience_debug_store.clear(player_id)
    except Exception:
        pass

    summary = experience_spec_summary(req.spec)
    return {
        "status": "ok",
        "level_id": level_id,
        "player_id": player_id,
        "experience_spec_summary": summary,
        "method": "apply-spec",
    }

