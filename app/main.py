"""ASGI entrypoint; all legal documents require API authentication."""
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from time import perf_counter
import logging
import json
from threading import Lock
from time import monotonic
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.core import APP_ENV, error_handler, init_database, seed_demo, actor, require, read_db
from app import legal, editor, portals, hierarchy, integration, auth


@asynccontextmanager
async def lifespan(_app):
    init_database()
    seed_demo(include_sample=APP_ENV != "production")
    yield


app = FastAPI(
    title="Правовая библиотека штата Сан Андреас",
    description="Версионируемое API юридической библиотеки и органов власти. Демоданные не являются действующим законом.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_exception_handler(HTTPException, error_handler)

# In-memory throttle for one-process deployments. Use an edge proxy or shared
# rate limiter when running multiple workers. Never trust an unverified X-Forwarded-For.
_rate_windows = defaultdict(deque)
_rate_lock = Lock()


_request_metrics = defaultdict(lambda: {"requests":0,"errors":0,"latency_ms_sum":0.0})
_metrics_lock = Lock()
request_logger = logging.getLogger("sas_portal.requests")


@app.middleware("http")
async def security_and_throttling(request: Request, call_next):
    if request.url.path.startswith("/api/v1/"):
        try:
            limit = int(os.getenv("RATE_LIMIT_PER_MINUTE",
                                  "240" if APP_ENV == "production" else "0"))
            window = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
        except ValueError:
            limit, window = 0, 60
        if limit > 0:
            address = request.client.host if request.client else "unknown"
            now = monotonic()
            with _rate_lock:
                bucket = _rate_windows[address]
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                restricted = len(bucket) >= limit
                if not restricted:
                    bucket.append(now)
            if restricted:
                return JSONResponse({"error":{"code":"RATE_LIMITED",
                    "message":"Слишком много запросов. Повторите позднее."}},
                    status_code=429,headers={"Retry-After":str(window)})
    started = perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        raise
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        if request.url.path.startswith("/api/v1/"):
            route = request.scope.get("route")
            route_name = getattr(route, "path", "unmatched")
            key = request.method + " " + route_name
            with _metrics_lock:
                metric = _request_metrics[key]
                metric["requests"] += 1
                metric["errors"] += int(status >= 400)
                metric["latency_ms_sum"] += elapsed_ms
        if os.getenv("ACCESS_LOG_JSON", "1" if APP_ENV == "production" else "0") == "1":
            # No query string, bearer token, request body or other sensitive data.
            request_logger.info(json.dumps({"method":request.method,
                "route":getattr(request.scope.get("route"),"path","unmatched"),
                "status":status,"duration_ms":round(elapsed_ms,1)}))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response



@app.exception_handler(RequestValidationError)
async def invalid_request(_request, exc):
    return JSONResponse({"error": {"code": "INVALID_REQUEST",
                                   "message": "Проверьте параметры запроса.",
                                   "details": {"fields": [str(e.get("loc")) for e in exc.errors()]}}},
                        status_code=422)


app.include_router(legal.router, prefix="/api/v1")
app.include_router(editor.router, prefix="/api/v1")
app.include_router(hierarchy.router, prefix="/api/v1")
app.include_router(portals.router, prefix="/api/v1")
app.include_router(integration.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")

WEB = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    with read_db() as c:
        c.execute("SELECT 1").fetchone()
    return {"status":"ready"}


@app.get("/api/v1/admin/metrics")
def admin_metrics(current=Depends(actor)):
    require(current, "superadmin")
    with _metrics_lock:
        return {"metrics": {key: dict(value) for key,value in _request_metrics.items()}}


@app.get("/{path:path}", include_in_schema=False)
def site(path: str):
    if path.startswith(("api/", "static/")):
        raise HTTPException(404, "Ресурс не найден.")
    return FileResponse(WEB / "index.html")
