"""System / health endpoints."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from fastapi import APIRouter

from ..config import (
    APP_VERSION,
    GIT_SHA,
    GITHUB_REPO,
    MEDIA_DIR,
    load_config,
    save_config,
)
from ..core.episode_index import load_episode_index, try_remote_refresh
from ..core.log import all_entries as _all_log_entries, log as _log
from ..core import tls_trust

_SXXEYY_RE = re.compile(r"\bs(\d{2})e(\d{2})\b", re.IGNORECASE)
_VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts")

router = APIRouter(prefix="/api", tags=["system"])

# Cached update-check result — GitHub's API is only hit once an hour.
_update_cache: dict = {"checked_at": 0.0, "result": None}
_UPDATE_TTL = 3600


@router.get("/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "media_dir": str(MEDIA_DIR),
        "media_writable": os.access(MEDIA_DIR, os.W_OK),
    }


@router.post("/refresh")
def refresh_index():
    """Trigger a remote index refresh from the GitHub data branch."""
    cfg = load_config()
    messages: list[str] = []

    def collect(msg: str) -> None:
        messages.append(msg)
        _log(msg)

    ok = try_remote_refresh(cfg, log=collect)
    if ok:
        save_config(cfg)
    return {"success": ok, "messages": messages}


@router.get("/stats")
def stats():
    """Media library stats."""
    index = load_episode_index()
    total_arcs = len(index.get("arcs", []))
    total_eps = sum(
        len(a.get("episodes", [])) for a in index.get("arcs", [])
    )

    # Count downloaded episodes — use the same sNNeMM video-file rule as
    # /api/downloaded so the LIBRARY widget can't drift from the per-arc
    # progress badges (subtitle sidecars etc. don't inflate the count).
    plex_dir = MEDIA_DIR / "One Pace"
    downloaded = 0
    if plex_dir.exists():
        for season_dir in plex_dir.iterdir():
            if not season_dir.is_dir():
                continue
            for f in season_dir.iterdir():
                if (f.is_file()
                        and f.suffix.lower() in _VIDEO_EXTS
                        and _SXXEYY_RE.search(f.name)):
                    downloaded += 1

    return {
        "total_arcs": total_arcs,
        "total_episodes": total_eps,
        "downloaded_episodes": downloaded,
    }


@router.get("/downloaded")
def downloaded_keys():
    """Set of (season, episode) keys already present in /media, derived
    from the Plex/Jellyfin folder layout written by the organize step.
    The frontend uses this to draw the green "Saved" chips and the
    per-arc N/M progress badges."""
    keys: set[str] = set()
    plex_dir = MEDIA_DIR / "One Pace"
    if not plex_dir.exists():
        return {"keys": []}
    for season_dir in plex_dir.iterdir():
        if not season_dir.is_dir():
            continue
        for f in season_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in _VIDEO_EXTS:
                continue
            m = _SXXEYY_RE.search(f.name)
            if m:
                keys.add(f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}")
    return {"keys": sorted(keys)}


@router.get("/log")
def log_entries():
    """Recent activity from the in-memory ring buffer (~250 lines)."""
    return {"entries": _all_log_entries()}


@router.get("/update")
def update_check():
    """Report whether a newer build of the Docker app is available.

    Compares the git commit this image was built from (GIT_SHA, baked in
    at build time) against the latest commit that touched `docker/` on
    GitHub. The result is cached for an hour so we barely touch the API.
    """
    now = time.time()
    cached = _update_cache["result"]
    if cached is not None and now - _update_cache["checked_at"] < _UPDATE_TTL:
        return cached

    result = {
        "current": GIT_SHA[:12] if GIT_SHA else "",
        "latest": None,
        "update_available": False,
    }

    # No meaningful check for local/dev builds — GIT_SHA isn't a real commit.
    if GIT_SHA and GIT_SHA != "dev":
        try:
            url = (f"https://api.github.com/repos/{GITHUB_REPO}"
                   f"/commits?path=docker&per_page=1")
            req = urllib.request.Request(url, headers={
                "User-Agent": "OnePaceDownloader",
                "Accept": "application/vnd.github+json",
            })
            with tls_trust.urlopen(req, timeout=10) as r:
                commits = json.loads(r.read())
            if commits:
                latest = commits[0].get("sha", "")
                result["latest"] = latest[:12]
                result["update_available"] = bool(
                    latest and latest != GIT_SHA)
        except Exception:
            # Offline / rate-limited — just report "no update known".
            pass

    _update_cache["result"] = result
    _update_cache["checked_at"] = now
    return result
