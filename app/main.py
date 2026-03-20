"""Crypto Service main application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import router
from .db import engine, Base

app = FastAPI(
    title="Crypto Service",
    description="Wallet, tokenization, receipts, funding, and withdrawals",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    """Create database tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


app.include_router(router)


@app.get("/")
async def root():
    return {"service": "crypto", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
