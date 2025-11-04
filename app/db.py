from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI
from app.config import settings

_client: AsyncIOMotorClient | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = AsyncIOMotorClient(settings.MONGO_URI)
    # 연결 확인 (실패 시 예외 발생)
    await _client.admin.command("ping")
    print("[DB] Connected to MongoDB")
    try:
        yield
    finally:
        _client.close()
        print("[DB] MongoDB connection closed")

def get_db():
    if _client is None:
        raise RuntimeError("Mongo client is not initialized")
    return _client[settings.DB_NAME]
