# app/db.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
)

# .env 에서 읽는 설정 (예: MONGO_URI, DB_NAME)
from app.config import settings


_client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None  # 다른 모듈에서 import 해서 사용


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 애플리케이션 수명주기에 맞춰 MongoDB 연결/해제.
    main.py 에서: app = FastAPI(lifespan=lifespan)
    """
    global _client, db

    # 연결 생성
    _client = AsyncIOMotorClient(
        settings.MONGO_URI,
        uuidRepresentation="standard",  # UUID 쓰는 경우 안전
        serverSelectionTimeoutMS=5000,
    )

    # 연결 확인 (실패 시 예외)
    await _client.admin.command("ping")

    # 데이터베이스 핸들
    db = _client[settings.DB_NAME]

    # 자주 조회하는 필드에 인덱스 생성 (없으면 생성, 있으면 재사용)
    # submissions 문서는 다음과 같은 필드를 가짐:
    # _id, user_id, assignment_id, language, code, status, score,
    # fail_tags[], feedback[{case,message}], metrics{timeMs,memoryMB},
    # finalized, created_at, updated_at
    await db.submissions.create_index("status")
    await db.submissions.create_index("user_id")
    await db.submissions.create_index([("created_at", -1)])

    try:
        yield
    finally:
        # 연결 종료
        if _client is not None:
            _client.close()


def get_db() -> AsyncIOMotorDatabase:
    """
    의존성 주입이나 모듈 간 접근에 사용.
    """
    if db is None:
        raise RuntimeError("MongoDB is not initialized. Did you attach lifespan?")
    return db


def submissions_coll():
    """
    submissions 컬렉션 헬퍼.
    사용 예: await submissions_coll().insert_one(doc)
    """
    return get_db().submissions
