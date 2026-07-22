"""Endpoints for the external-app integrations:

  - Nyaa torrent listing per arc
  - Usenet NZB hand-off to SABnzbd
  - Torrent magnet hand-off to qBittorrent
  - Connection tests for SABnzbd / qBittorrent / NZBGeek
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException

from ..config import get_setting, load_config
from ..core.clients import ClientError, QBittorrentClient, SABnzbdClient
from ..core.episode_index import (
    collect_torrents,
    load_episode_index,
    usenet_source_for,
)
from ..core.log import log as _log
from ..core import tls_trust
from ..core.models import (
    SendResult,
    SettingsPayload,
    TorrentSendRequest,
    UsenetSendRequest,
)

router = APIRouter(prefix="/api", tags=["integrations"])


# ── Helpers ───────────────────────────────────────────────────────────

def _merged(cfg: dict, payload: SettingsPayload | None, key: str):
    """Prefer an inline value from the request (lets the user test before
    saving), then the saved config / env default."""
    if payload is not None:
        val = getattr(payload, key, None)
        if val not in (None, ""):
            return val
    return get_setting(cfg, key)


def _find_arc(arc_title: str) -> dict:
    index = load_episode_index()
    for arc in index.get("arcs", []):
        if arc.get("title") == arc_title:
            return arc
    raise HTTPException(404, f"Arc {arc_title!r} not found")


# ── Nyaa torrent listing ──────────────────────────────────────────────

@router.get("/arcs/{arc_title}/torrents")
def list_torrents(arc_title: str):
    """All Nyaa torrents for an arc — whole-arc packs + per-episode."""
    return collect_torrents(_find_arc(arc_title))


# ── Usenet → SABnzbd ──────────────────────────────────────────────────

@router.post("/usenet/send", response_model=SendResult)
def usenet_send(req: UsenetSendRequest):
    """Queue the selected episodes' NZBs into SABnzbd. We build the NZBGeek
    download URL and let SABnzbd fetch it (addurl)."""
    cfg = load_config()
    nzbgeek_url = (get_setting(cfg, "nzbgeek_url") or "").rstrip("/")
    nzbgeek_key = get_setting(cfg, "nzbgeek_api_key")
    if not nzbgeek_url or not nzbgeek_key:
        raise HTTPException(400, "NZBGeek indexer not configured — add it in Settings.")

    sab = SABnzbdClient(
        get_setting(cfg, "sabnzbd_url"),
        get_setting(cfg, "sabnzbd_api_key"),
        get_setting(cfg, "sabnzbd_category"),
    )
    if not sab.url or not sab.api_key:
        raise HTTPException(400, "SABnzbd not configured — add it in Settings.")

    arc = _find_arc(req.arc_title)
    eps = [e for e in arc.get("episodes", []) if e.get("num") in req.episode_nums]
    result = SendResult()

    for ep in eps:
        num = ep.get("num")
        src = usenet_source_for(ep, req.quality)
        if not src or not src.get("guid"):
            result.failed += 1
            result.messages.append(f"Episode {num}: no Usenet release available")
            continue
        nzb_url = f"{nzbgeek_url}/api?" + urllib.parse.urlencode(
            {"t": "get", "id": src["guid"], "apikey": nzbgeek_key})
        try:
            sab.add_url(nzb_url, name=src.get("release_title", ""))
            result.sent += 1
        except ClientError as e:
            result.failed += 1
            result.messages.append(f"Episode {num}: {e}")

    _log(f"SABnzbd send: {result.sent} queued, {result.failed} failed "
         f"({req.arc_title})")
    if result.sent == 0 and result.failed > 0:
        # All failed — surface the first reason as the error.
        raise HTTPException(502, result.messages[0] if result.messages
                            else "Nothing was queued.")
    return result


# ── Nyaa → qBittorrent ────────────────────────────────────────────────

@router.post("/torrents/send", response_model=SendResult)
def torrents_send(req: TorrentSendRequest):
    """Queue the selected magnet links into qBittorrent."""
    if not req.magnets:
        raise HTTPException(400, "No torrents selected.")
    cfg = load_config()
    qb = QBittorrentClient(
        get_setting(cfg, "qbittorrent_url"),
        get_setting(cfg, "qbittorrent_user"),
        get_setting(cfg, "qbittorrent_pass"),
        get_setting(cfg, "qbittorrent_category"),
    )
    if not qb.url:
        raise HTTPException(400, "qBittorrent not configured — add it in Settings.")

    try:
        qb._login()
    except ClientError as e:
        raise HTTPException(502, str(e))

    result = SendResult()
    for magnet in req.magnets:
        try:
            qb.add_magnet(magnet)
            result.sent += 1
        except ClientError as e:
            result.failed += 1
            result.messages.append(str(e))
    _log(f"qBittorrent send: {result.sent} queued, {result.failed} failed")
    return result


# ── Live transfer status ──────────────────────────────────────────────

@router.get("/clients/status")
def clients_status():
    """Live progress of hand-off downloads — the SABnzbd queue and the
    active qBittorrent torrents. Polled by the web UI. Best-effort: an
    unconfigured or unreachable client simply contributes nothing."""
    cfg = load_config()
    transfers = []

    sab = SABnzbdClient(
        get_setting(cfg, "sabnzbd_url"),
        get_setting(cfg, "sabnzbd_api_key"),
        get_setting(cfg, "sabnzbd_category"),
    )
    transfers += sab.queue()

    qb = QBittorrentClient(
        get_setting(cfg, "qbittorrent_url"),
        get_setting(cfg, "qbittorrent_user"),
        get_setting(cfg, "qbittorrent_pass"),
        get_setting(cfg, "qbittorrent_category"),
    )
    transfers += qb.torrents()

    return {"transfers": transfers}


# ── Connection tests ──────────────────────────────────────────────────

@router.post("/integrations/test/sabnzbd")
def test_sabnzbd(payload: SettingsPayload):
    cfg = load_config()
    client = SABnzbdClient(
        _merged(cfg, payload, "sabnzbd_url"),
        _merged(cfg, payload, "sabnzbd_api_key"),
    )
    try:
        version = client.test()
        return {"ok": True, "message": f"Connected — SABnzbd {version}"}
    except ClientError as e:
        return {"ok": False, "message": str(e)}


@router.post("/integrations/test/qbittorrent")
def test_qbittorrent(payload: SettingsPayload):
    cfg = load_config()
    client = QBittorrentClient(
        _merged(cfg, payload, "qbittorrent_url"),
        _merged(cfg, payload, "qbittorrent_user"),
        _merged(cfg, payload, "qbittorrent_pass"),
    )
    try:
        version = client.test()
        return {"ok": True, "message": f"Connected — qBittorrent {version}"}
    except ClientError as e:
        return {"ok": False, "message": str(e)}


@router.post("/integrations/test/nzbgeek")
def test_nzbgeek(payload: SettingsPayload):
    cfg = load_config()
    url = (_merged(cfg, payload, "nzbgeek_url") or "").rstrip("/")
    key = _merged(cfg, payload, "nzbgeek_api_key")
    if not url or not key:
        return {"ok": False, "message": "NZBGeek URL or API key is missing."}
    test_url = f"{url}/api?" + urllib.parse.urlencode(
        {"t": "search", "apikey": key, "limit": 1})
    try:
        req = urllib.request.Request(
            test_url, headers={"User-Agent": "OnePaceDownloader"})
        with tls_trust.urlopen(req, timeout=20) as r:
            body = r.read()[:600].lower()
        if b"<error" in body or b"invalid api" in body:
            return {"ok": False, "message": "Invalid API key."}
        return {"ok": True, "message": "Connected — NZBGeek API key valid."}
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"ok": False, "message": "Invalid API key (HTTP 403)."}
        return {"ok": False, "message": f"NZBGeek HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "message": f"Connection failed: {e}"}
