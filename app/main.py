from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db import lifespan
from app.routers import submissions

app = FastAPI(title="Cloudemy API", lifespan=lifespan)

# React 개발서버 허용(CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

app.include_router(submissions.router)
