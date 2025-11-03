import os
from datetime import timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import simple


def create_app() -> FastAPI:
    app = FastAPI(title="Timetable API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(simple.router, prefix="/simple", tags=["simple"])

    return app


app = create_app()


