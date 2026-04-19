# backend/app/main.py

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Routers
from app.api.tree_api import router as tree_router
from app.api.dsl_api import router as dsl_router
from app.api.hint_api import router as hint_router
try:
    from app.api.world_api import router as world_router
except Exception as e:
    world_router = None
    print(f">>> World router disabled: {e}")
try:
    from app.api.story_api import router as story_router
except Exception as e:
    story_router = None
    print(f">>> Story router disabled: {e}")
try:
    from app.api.npc_api import router as npc_router
except Exception as e:
    npc_router = None
    print(f">>> NPC router disabled: {e}")
try:
    from app.api.tutorial_api import router as tutorial_router
except Exception as e:
    tutorial_router = None
    print(f">>> Tutorial router disabled: {e}")
try:
    from app.routers import ai_router
except Exception as e:
    ai_router = None
    print(f">>> AI router disabled: {e}")
try:
    from app.routers.minimap import router as minimap_router
except Exception as e:
    minimap_router = None
    print(f">>> MiniMap router disabled: {e}")
try:
    from app.api.minimap_api import router as minimap_png_router
except Exception as e:
    minimap_png_router = None
    print(f">>> MiniMapPNG router disabled: {e}")
from app.api.experience_api import router as experience_router
from app.api.evolution_api import router as evolution_router
from app.api.github_projects import router as github_router

# Core
try:
    from app.core.story.story_loader import list_levels, load_level
    from app.core.story.story_engine import story_engine
except Exception as e:
    print(f">>> Story core disabled: {e}")

    def list_levels():
        return []

    def load_level(_level_id):
        return None

    story_engine = None


# -----------------------------
# App 初始化
# -----------------------------
app = FastAPI(title="DriftSystem · Heart Levels + Story Engine")


# -----------------------------
# CORS（允许前端/MC 调用）
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# 注册全部路由
# -----------------------------
app.include_router(tree_router,        tags=["Tree"])
app.include_router(dsl_router,         tags=["DSL"])
app.include_router(hint_router,        tags=["Hint"])
if world_router is not None:
    app.include_router(world_router,   tags=["World"])
if story_router is not None:
    app.include_router(story_router,   tags=["Story"])
if npc_router is not None:
    app.include_router(npc_router,     tags=["NPC"])
if tutorial_router is not None:
    app.include_router(tutorial_router, tags=["Tutorial"])
if ai_router is not None:
    app.include_router(ai_router.router, tags=["AI"])
if minimap_router is not None:
    app.include_router(minimap_router, tags=["MiniMap"])
if minimap_png_router is not None:
    app.include_router(minimap_png_router, tags=["MiniMapPNG"])
app.include_router(experience_router,  tags=["Experience"])
app.include_router(evolution_router,   tags=["Evolution"])
app.include_router(github_router,      tags=["GitHub"])


# -----------------------------
# 启动日志（不再访问不存在的属性）
# -----------------------------
print(">>> DriftSystem loaded: TREE + DSL + HINT + WORLD + STORY + AI + MINIMAP + PNG")
if story_engine is not None:
    print(">>> Total Levels:", len(story_engine.graph.all_levels()))
    print(">>> Spiral triggers:", len(story_engine.minimap.positions))
print(">>> Heart Universe backend ready.")


# -----------------------------
# Levels API
# -----------------------------
@app.get("/levels")
def api_list_levels():
    return {"status": "ok", "levels": list_levels()}


@app.get("/levels/{level_id}")
def api_get_level(level_id: str):
    try:
        lv = load_level(level_id)
        return {"status": "ok", "level": lv.__dict__}
    except FileNotFoundError:
        return {"status": "error", "msg": f"Level {level_id} not found"}


# -----------------------------
# Home / 状态
# -----------------------------
@app.get("/")
def home():
    story_state = {}
    if story_engine is not None:
        try:
            story_state = story_engine.get_public_state()
        except Exception:
            story_state = {"status": "degraded"}
    return {
        "status": "running",
        "routes": [
            "/levels",
            "/story/*",
            "/world/*",
            "/ai/*",
            "/minimap/*",
            "/minimap/png/*",
        ],
        "story_state": story_state,
    }


# -----------------------------
# 静态文件：面板（:8000/panel/drift-experience-panel.html）
# -----------------------------
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
# 兜底：如果 __file__ 路径计算不准，也检查 HOME 下的标准位置
if not os.path.exists(os.path.join(_repo_root, "drift-experience-panel.html")):
    _home = os.path.expanduser("~")
    _fallback = os.path.join(_home, "drift-opc-workflow-v3")
    if os.path.exists(os.path.join(_fallback, "drift-experience-panel.html")):
        _repo_root = _fallback
if os.path.exists(os.path.join(_repo_root, "drift-experience-panel.html")):
    app.mount("/panel", StaticFiles(directory=_repo_root, html=True), name="panel")