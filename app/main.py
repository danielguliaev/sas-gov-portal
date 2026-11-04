"""ASGI entrypoint; all legal documents require API authentication."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.core import APP_ENV, error_handler, init_database, seed_demo
from app import legal, editor, portals, hierarchy


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

WEB = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/{path:path}", include_in_schema=False)
def site(path: str):
    if path.startswith(("api/", "static/")):
        raise HTTPException(404, "Ресурс не найден.")
    return FileResponse(WEB / "index.html")
