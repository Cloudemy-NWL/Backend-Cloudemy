from fastapi import FastAPI
from app.db import lifespan
from app.routers import submissions, internal

app = FastAPI(lifespan=lifespan)

app.include_router(submissions.router)
app.include_router(internal.router)
