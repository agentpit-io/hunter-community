from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.services.database import get_signal_report_html

router = APIRouter()


@router.get("/v1/signal/report/{report_id}", response_class=HTMLResponse)
async def get_signal_report(report_id: int):
    html = get_signal_report_html(report_id)
    if not html:
        raise HTTPException(status_code=404, detail="report not found")
    return HTMLResponse(content=html)
