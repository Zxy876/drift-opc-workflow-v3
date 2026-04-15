from fastapi import APIRouter
from pydantic import BaseModel
from app.core.hint.engine import HintEngine
from app.core.tree.engine import TreeEngine

router = APIRouter()
tree_engine = TreeEngine()
try:
    engine = HintEngine(tree_engine)
    _hint_ok = True
except ValueError:
    engine = None
    _hint_ok = False

class HintInput(BaseModel):
    content: str

@router.post("/")
def hint(data: HintInput):
    if not _hint_ok:
        return {"error": "Hint engine unavailable (OPENAI_API_KEY not set)"}
    return engine.get_hint(data.content)
