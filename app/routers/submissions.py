import json
import logging
import os
from typing import Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, constr
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

# Redis (async)
from redis.asyncio import Redis

# Mongo (전역 연결)
from app.db import get_db

router = APIRouter(prefix="/submissions", tags=["submissions"])

# ====== 공통 ======
STATUSES = {"QUEUED", "FAILED", "COMPLETED", "TIMEOUT", "FINALIZED"}
QUEUE_NAME = os.getenv("QUEUE_SUBMISSIONS", "queue:submissions")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


async def _enqueue_to_queue(message: dict) -> None:
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await r.lpush(QUEUE_NAME, json.dumps(message))
    finally:
        await r.close()

def _default_metrics() -> Dict[str, int]:
    return {"timeMs": 0, "memoryMB": 0}

# ====== Pydantic 모델 ======
class SubmissionCreate(BaseModel):
    assignment_id: constr(strip_whitespace=True, min_length=1)
    language: constr(strip_whitespace=True, min_length=1) = "python"
    code: str

class FeedbackItem(BaseModel):
    case: str
    message: str

class Metrics(BaseModel):
    timeMs: int = 0
    memoryMB: int = 0

class SubmissionOut(BaseModel):
    submission_id: str
    user_id: Optional[str] = None
    assignment_id: str
    language: str = "python"
    status: constr(pattern="^(QUEUED|FAILED|COMPLETED|TIMEOUT|FINALIZED)$")
    score: float = 0
    fail_tags: List[str] = Field(default_factory=list)
    feedback: List[FeedbackItem] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    finalized: bool = False

class SubmissionQueued(BaseModel):
    submission_id: str
    status: str = "QUEUED"
    attempt: int = 1

class FinalizeIn(BaseModel):
    note: Optional[str] = None

class FinalizeOut(BaseModel):
    submission_id: str
    status: str = "FINALIZED"
    finalized: bool = True

# ====== 헬퍼 ======
def COLL():
    return get_db().submissions

async def _get_doc_or_404(submission_id: str) -> dict:
    doc = await COLL().find_one({"_id": submission_id})
    if not doc:
        raise HTTPException(status_code=404, detail="submission not found")
    return doc

def _doc_to_out(doc: dict) -> SubmissionOut:
    return SubmissionOut(
        submission_id=doc["_id"],
        user_id=doc.get("user_id"),
        assignment_id=doc["assignment_id"],
        language=doc.get("language", "python"),
        status=doc.get("status", "QUEUED"),
        score=float(doc.get("score", 0) or 0),
        fail_tags=list(doc.get("fail_tags", [])),
        feedback=[FeedbackItem(**x) for x in doc.get("feedback", [])],
        metrics=Metrics(**(doc.get("metrics") or _default_metrics())),
        finalized=bool(doc.get("finalized", False)),
    )

# ====== (1) 코드 제출: POST /submissions ======
@router.post(
    "",
    response_model=SubmissionQueued,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(payload: SubmissionCreate):
    # 1) DB 저장 (status=QUEUED)
    doc = {
        "_id": str(ObjectId()),
        "user_id": "u1",  # 데모/시연: 하드코딩 사용자
        "assignment_id": payload.assignment_id,
        "language": payload.language,
        "code": payload.code,
        "status": "QUEUED",
        "score": 0,
        "fail_tags": [],
        "feedback": [],
        "metrics": _default_metrics(),
        "finalized": False,
        "attempt": 1,  # 시연 고정
    }
    try:
        await COLL().insert_one(doc)
    except PyMongoError as exc:
        logging.getLogger(__name__).exception("Failed to insert submission")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc

    # 2) Redis 큐 등록
    try:
        await _enqueue_to_queue(
            {
                "submission_id": doc["_id"],
                "assignment_id": doc["assignment_id"],
                "language": doc["language"],
            }
        )
    except RedisError as exc:
        logging.getLogger(__name__).exception("Failed to enqueue submission")
        await COLL().delete_one({"_id": doc["_id"]})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="queue unavailable",
        ) from exc

    return SubmissionQueued(submission_id=doc["_id"], status="QUEUED", attempt=1)

# ====== (3) 제출 결과 조회: GET /submissions/{id} ======
@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: str):
    doc = await _get_doc_or_404(submission_id)
    return _doc_to_out(doc)

# ====== (4) 최종 제출 확정: POST /submissions/{id}/finalize ======
@router.post("/{submission_id}/finalize", response_model=FinalizeOut)
async def finalize_submission(submission_id: str, body: FinalizeIn):
    doc = await _get_doc_or_404(submission_id)

    # 이미 최종화면 idempotent하게 응답
    if doc.get("finalized"):
        return FinalizeOut(submission_id=submission_id)

    # 잠금 + 상태 FINALIZED
    res = await COLL().update_one(
        {"_id": submission_id, "finalized": {"$ne": True}},
        {
            "$set": {
                "status": "FINALIZED",
                "finalized": True,
                "finalize_note": body.note,
            }
        },
    )
    if res.matched_count == 0:
        # 경쟁상황으로 이미 finalize 된 경우
        doc = await _get_doc_or_404(submission_id)
        if not doc.get("finalized"):
            raise HTTPException(409, "finalize conflict")
    return FinalizeOut(submission_id=submission_id)
