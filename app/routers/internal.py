from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, conlist
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import os

from app.db import db

router = APIRouter(prefix="/internal", tags=["internal"])

# 보안 토큰 (Runner → Backend 콜백 보호)
RESULT_TOKEN = os.getenv("INTERNAL_RESULT_TOKEN", "secret")

def _now_utc():
    return datetime.now(timezone.utc)

class FeedbackItem(BaseModel):
    case: str
    message: str

class Metrics(BaseModel):
    timeMs: int = 0
    memoryMB: int = 0

class ResultIn(BaseModel):
    status: str  # "COMPLETED" | "FAILED" | "TIMEOUT"
    score: float = 0
    fail_tags: List[str] = Field(default_factory=list)
    feedback: List[FeedbackItem] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)

class OkOut(BaseModel):
    ok: bool = True
    submission_id: str
    status: str

COLL = lambda: db.submissions  # type: ignore

@router.post(
    "/submissions/{submission_id}/result",
    response_model=OkOut,
    status_code=status.HTTP_200_OK,
)
async def post_result_callback(
    submission_id: str,
    payload: ResultIn,
    x_result_token: str = Header(None, alias="X-Result-Token"),
):
    # 0) 토큰 검증
    if not x_result_token or x_result_token != RESULT_TOKEN:
        raise HTTPException(status_code=401, detail="invalid result token")

    # 1) 문서 조회
    doc = await COLL().find_one({"_id": submission_id})
    if not doc:
        raise HTTPException(status_code=404, detail="submission not found")

    # 2) 이미 FINALIZED이면 무시 (idempotent OK)
    if doc.get("finalized") is True or doc.get("status") == "FINALIZED":
        return OkOut(ok=True, submission_id=submission_id, status=doc.get("status", "FINALIZED"))

    # 3) 결과 반영
    if payload.status not in {"COMPLETED", "FAILED", "TIMEOUT"}:
        raise HTTPException(status_code=400, detail="invalid status")

    update_doc = {
        "status": payload.status,
        "score": float(payload.score or 0),
        "fail_tags": list(payload.fail_tags or []),
        "feedback": [fi.model_dump() for fi in (payload.feedback or [])],
        "metrics": payload.metrics.model_dump(),
        "updated_at": _now_utc(),
    }

    await COLL().update_one({"_id": submission_id}, {"$set": update_doc})
    return OkOut(ok=True, submission_id=submission_id, status=payload.status)
