from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from bson import ObjectId
from app.db import get_db

router = APIRouter(prefix="/submissions", tags=["submissions"])

def to_str_id(oid: ObjectId) -> str:
    return str(oid)

class SubmissionIn(BaseModel):
    student_id: str = Field(..., examples=["s2025001"])
    assignment_id: str = Field(..., examples=["hw01"])
    code: str = Field(..., description="submitted code")

class SubmissionOut(BaseModel):
    id: str
    student_id: str
    assignment_id: str
    code: str

@router.post("", response_model=SubmissionOut)
async def create_submission(payload: SubmissionIn):
    db = get_db()
    doc = payload.model_dump()
    res = await db.submissions.insert_one(doc)
    saved = await db.submissions.find_one({"_id": res.inserted_id})
    return SubmissionOut(
        id=to_str_id(saved["_id"]),
        student_id=saved["student_id"],
        assignment_id=saved["assignment_id"],
        code=saved["code"],
    )

@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: str):
    db = get_db()
    try:
        _id = ObjectId(submission_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ObjectId")
    doc = await db.submissions.find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return SubmissionOut(
        id=to_str_id(doc["_id"]),
        student_id=doc["student_id"],
        assignment_id=doc["assignment_id"],
        code=doc["code"],
    )
