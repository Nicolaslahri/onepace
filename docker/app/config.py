"""Environment-driven configuration for the Docker container."""

from __future__ import annotations

import json
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CONFIG_FILE = CONFIG_DIR / "config.json"
INDEX_FILE = CONFIG_DIR / "episode_index.json"

# ── Bundled data file (read-only, shipped inside the image) ────────────
BUNDLED_INDEX_FILE = DATA_DIR / "episode_index.json"

# ── Remote index (GitHub data branch) ─────────────────────────────────
REMOTE_INDEX_BASE = (
    "https://raw.githubusercontent.com/Nicolaslahri/onepacedownloader/data")
REMOTE_INDEX_URL = f"{REMOTE_INDEX_BASE}/episode_index.json"

# ── Pixeldrain ─────────────────────────────────────────────────────────
PIXELDRAIN_API = "https://pixeldrain.com/api/list/{album_id}"
PIXELDRAIN_FILE = "https://pixeldrain.com/api/file/{file_id}"
BYPASS_FILE = "https://cdn.pixeldrain.eu.cc/{file_id}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

APP_VERSION = "2.0.5"

# Git commit the image was built from — baked in by the Dockerfile via a
# build arg. Empty / "dev" for local builds. Used for the update check.
GIT_SHA = os.environ.get("GIT_SHA", "dev")

# Repo the update check queries.
GITHUB_REPO = "Nicolaslahri/onepacedownloader"

VERSIONS = ["English Subtitles", "English Dub", "English Dub with Closed Captions"]
QUALITIES = ["1080p", "720p", "480p"]

# ── Environment tunables ──────────────────────────────────────────────
PORT = int(os.environ.get("PORT", "7654"))
DEFAULT_VERSION = os.environ.get("DEFAULT_VERSION", "English Subtitles")
DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "1080p")
AUTO_REFRESH = os.environ.get("AUTO_REFRESH", "true").lower() in ("1", "true", "yes")

# ── Integration defaults (env → config.json override → these) ─────────
# These let docker-compose pre-seed a SABnzbd / qBittorrent / NZBGeek
# setup; the Settings UI persists any changes into config.json.
INTEGRATION_DEFAULTS = {
    "sabnzbd_url":        os.environ.get("SABNZBD_URL", ""),
    "sabnzbd_api_key":    os.environ.get("SABNZBD_API_KEY", ""),
    "sabnzbd_category":   os.environ.get("SABNZBD_CATEGORY", ""),
    "qbittorrent_url":    os.environ.get("QBITTORRENT_URL", ""),
    "qbittorrent_user":   os.environ.get("QBITTORRENT_USER", ""),
    "qbittorrent_pass":   os.environ.get("QBITTORRENT_PASS", ""),
    "qbittorrent_category": os.environ.get("QBITTORRENT_CATEGORY", ""),
    "nzbgeek_url":        os.environ.get("NZBGEEK_URL", "https://api.nzbgeek.info"),
    "nzbgeek_api_key":    os.environ.get("NZBGEEK_API_KEY", ""),
}


def get_setting(cfg: dict, key: str):
    """Resolve an integration setting: config.json value wins, else the
    env-seeded default."""
    val = cfg.get(key)
    if val not in (None, ""):
        return val
    return INTEGRATION_DEFAULTS.get(key, "")


# ── Persistent config (JSON in /config) ───────────────────────────────
def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
