from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from freshbid.service import DemoRecommendationRequest, FreshBidService, OutcomeRequest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
service = FreshBidService(BACKEND_ROOT / "data" / "freshbid_api.sqlite3")

app = FastAPI(title="FreshBid Demo API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ActorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="demo-manager", min_length=1, max_length=80)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "freshbid"}


@app.post("/api/recommendations", status_code=201)
def create_recommendation(request: DemoRecommendationRequest) -> dict:
    return service.create_recommendation(request)


@app.get("/api/recommendations/{recommendation_id:path}")
def get_recommendation(recommendation_id: str) -> dict:
    return _handle(service.get_recommendation, recommendation_id)


@app.post("/api/recommendations/{recommendation_id:path}/approve")
def approve(recommendation_id: str, request: ActorRequest) -> dict:
    return _handle(service.approve, recommendation_id, request.actor)


@app.post("/api/recommendations/{recommendation_id:path}/reject")
def reject(recommendation_id: str, request: ActorRequest) -> dict:
    return _handle(service.reject, recommendation_id, request.actor)


@app.post("/api/recommendations/{recommendation_id:path}/apply")
def apply_price(recommendation_id: str, request: ActorRequest) -> dict:
    return _handle(service.apply, recommendation_id, request.actor)


@app.post("/api/recommendations/{recommendation_id:path}/verify")
def verify_price(recommendation_id: str, request: ActorRequest) -> dict:
    return _handle(service.verify, recommendation_id, request.actor)


@app.post("/api/recommendations/{recommendation_id:path}/outcomes")
def record_outcome(recommendation_id: str, request: OutcomeRequest) -> dict:
    return _handle(service.record_outcome, recommendation_id, request)


def _handle(function, *args):
    try:
        return function(*args)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# 构建前端后，由同一个端口提供网页，演示时只需启动一个命令。
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{page_path:path}", include_in_schema=False)
    def frontend(page_path: str):
        return FileResponse(FRONTEND_DIST / "index.html")


def run() -> None:
    uvicorn.run("freshbid.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
