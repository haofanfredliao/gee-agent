"""FastAPI 应用入口：健康检查 + 挂载 /chat。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes_chat import router as chat_router
from backend.app.core.config import ALLOWED_ORIGINS

app = FastAPI(title="GEE Geo Assistant API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(chat_router, prefix="/chat", tags=["chat"])
