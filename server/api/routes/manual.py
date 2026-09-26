from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/manual", response_class=HTMLResponse, tags=["ui"])
async def manual():
    html_file = Path(__file__).resolve().parents[2] / "manual.html"
    html_content = html_file.read_text(encoding="utf-8")
    return HTMLResponse(content=html_content)
