"""FastAPI entrypoint: starts the control loop and serves the operator API and /metrics."""
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics
from .api import router
from .config import settings
from .loop import Engine
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per simulator request is noise
_ID = re.compile(r"/recommendations/[^/]+/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.api_rolling = metrics.Rolling()
    app.state.engine = Engine(settings, Store(settings.database_path))
    await app.state.engine.start()
    yield
    await app.state.engine.stop()


app = FastAPI(title="Jalani Control Tower API", version=settings.version, lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        handler = getattr(route, "path", None) or _ID.sub("/recommendations/{id}/", request.url.path)
        if handler != "/metrics":
            dt = time.perf_counter() - t0
            metrics.HTTP_REQUESTS.labels(handler, request.method, str(status)).inc()
            metrics.HTTP_LATENCY.labels(handler).observe(dt)
            request.app.state.api_rolling.add(dt * 1000, status < 500)


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(router)
