"""FastAPI entry point for the One Pace Downloader Docker container."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .config import AUTO_REFRESH, PORT, load_config, save_config
from .core.episode_index import try_remote_refresh
from .core.log import log as _log

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: refresh the index from GitHub if AUTO_REFRESH is on.

    Any failure here is non-fatal — the app falls back to the index
    bundled in the image rather than refusing to start."""
    if AUTO_REFRESH:
        try:
            cfg = load_config()
            if try_remote_refresh(cfg, log=_log):
                save_config(cfg)
        except Exception as e:
            _log(f"[startup] index refresh skipped: {e}")
    yield


app = FastAPI(
    title="One Pace Downloader",
    version="2.0.5",
    lifespan=lifespan,
)

# ── Register API routers ─────────────────────────────────────────────
from .api.routes_arcs import router as arcs_router
from .api.routes_episodes import router as episodes_router
from .api.routes_downloads import router as downloads_router
from .api.routes_settings import router as settings_router
from .api.routes_system import router as system_router
from .api.routes_integrations import router as integrations_router

app.include_router(arcs_router)
app.include_router(episodes_router)
app.include_router(downloads_router)
app.include_router(settings_router)
app.include_router(system_router)
app.include_router(integrations_router)


# ── Static files (Web UI) ────────────────────────────────────────────
@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
