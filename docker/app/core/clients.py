"""Hand-off clients for external download apps on the user's home server:

  - SABnzbdClient  — queues Usenet NZBs into a SABnzbd instance
  - QBittorrentClient — queues torrent magnets into a qBittorrent instance

Both are stdlib-only (urllib). "Hand-off" means we only push the job; the
external app downloads to its own folders. We never see the bytes.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from . import tls_trust
from .downloader import fmt_bytes

_TIMEOUT = 20


class ClientError(Exception):
    """Raised when a hand-off client call fails. The message is user-facing."""


# ── SABnzbd ────────────────────────────────────────────────────────────

class SABnzbdClient:
    """Talks to SABnzbd's HTTP API. Needs the base URL + API key from the
    SABnzbd 'Config -> General -> API Key' page."""

    def __init__(self, url: str, api_key: str, category: str = ""):
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.category = category or ""

    def _call(self, params: dict) -> dict:
        if not self.url or not self.api_key:
            raise ClientError("SABnzbd URL or API key not configured.")
        params = {**params, "apikey": self.api_key, "output": "json"}
        endpoint = f"{self.url}/api?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(
                endpoint, headers={"User-Agent": "OnePaceDownloader"})
            with tls_trust.urlopen(req, timeout=_TIMEOUT) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise ClientError(f"SABnzbd HTTP {e.code}") from e
        except Exception as e:
            raise ClientError(f"Can't reach SABnzbd: {e}") from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise ClientError("SABnzbd returned a non-JSON response "
                              "(wrong URL, or not SABnzbd?).")
        if isinstance(data, dict) and data.get("status") is False:
            raise ClientError(data.get("error") or "SABnzbd rejected the request.")
        return data

    def test(self) -> str:
        """Verify the connection. Returns the SABnzbd version on success."""
        data = self._call({"mode": "version"})
        return data.get("version", "unknown")

    def add_url(self, nzb_url: str, name: str = "") -> str:
        """Hand an NZB URL to SABnzbd; it fetches and downloads it itself.
        Returns the SABnzbd job id (nzo_id)."""
        params = {"mode": "addurl", "name": nzb_url}
        if self.category:
            params["cat"] = self.category
        if name:
            params["nzbname"] = name
        data = self._call(params)
        ids = data.get("nzo_ids") or []
        if not ids:
            raise ClientError("SABnzbd accepted the request but queued nothing.")
        return ids[0]

    def queue(self) -> list[dict]:
        """Active SABnzbd downloads (filtered to our category if one is set).
        Best-effort — returns [] when SABnzbd isn't configured or reachable,
        so a polling caller never breaks."""
        if not self.url or not self.api_key:
            return []
        try:
            data = self._call({"mode": "queue"})
        except ClientError:
            return []
        q = data.get("queue") or {}
        try:
            kbps = float(q.get("kbpersec") or 0)
        except (TypeError, ValueError):
            kbps = 0.0
        speed = fmt_bytes(kbps * 1024) + "/s" if kbps > 0 else ""
        out: list[dict] = []
        for s in q.get("slots", []):
            if self.category and s.get("cat") != self.category:
                continue
            try:
                pct = float(s.get("percentage") or 0) / 100.0
            except (TypeError, ValueError):
                pct = 0.0
            downloading = (s.get("status") or "").lower() == "downloading"
            out.append({
                "source": "usenet",
                "name": s.get("filename") or "NZB",
                "progress": max(0.0, min(pct, 1.0)),
                "speed": speed if downloading else "",
                "status": "downloading" if downloading else "queued",
                "eta": s.get("timeleft") or "",
            })
        return out


# ── qBittorrent ────────────────────────────────────────────────────────

class QBittorrentClient:
    """Talks to qBittorrent's Web API (v2). Needs the Web UI URL plus the
    Web UI username/password (Tools -> Options -> Web UI)."""

    def __init__(self, url: str, username: str = "", password: str = "",
                 category: str = ""):
        self.url = (url or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.category = category or ""
        self._opener = None

    def _build_opener(self):
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))

    def _login(self) -> None:
        if not self.url:
            raise ClientError("qBittorrent URL not configured.")
        self._opener = self._build_opener()
        data = urllib.parse.urlencode({
            "username": self.username,
            "password": self.password,
        }).encode()
        # qBittorrent's CSRF check wants a same-origin Referer header.
        req = urllib.request.Request(
            f"{self.url}/api/v2/auth/login",
            data=data,
            headers={"Referer": self.url, "User-Agent": "OnePaceDownloader"},
        )
        try:
            with self._opener.open(req, timeout=_TIMEOUT) as r:
                body = r.read().decode(errors="ignore").strip()
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise ClientError("qBittorrent rejected login (HTTP 403). "
                                  "Check the username/password.") from e
            raise ClientError(f"qBittorrent HTTP {e.code}") from e
        except Exception as e:
            raise ClientError(f"Can't reach qBittorrent: {e}") from e
        if body != "Ok.":
            raise ClientError("qBittorrent login failed — wrong username "
                              "or password.")

    def _post(self, path: str, fields: dict) -> str:
        if self._opener is None:
            self._login()
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            headers={"Referer": self.url, "User-Agent": "OnePaceDownloader"},
        )
        try:
            with self._opener.open(req, timeout=_TIMEOUT) as r:
                return r.read().decode(errors="ignore").strip()
        except urllib.error.HTTPError as e:
            raise ClientError(f"qBittorrent HTTP {e.code}") from e
        except Exception as e:
            raise ClientError(f"qBittorrent request failed: {e}") from e

    def _get(self, path: str) -> str:
        if self._opener is None:
            self._login()
        req = urllib.request.Request(
            f"{self.url}{path}",
            headers={"Referer": self.url, "User-Agent": "OnePaceDownloader"},
        )
        try:
            with self._opener.open(req, timeout=_TIMEOUT) as r:
                return r.read().decode(errors="ignore").strip()
        except urllib.error.HTTPError as e:
            raise ClientError(f"qBittorrent HTTP {e.code}") from e
        except Exception as e:
            raise ClientError(f"qBittorrent request failed: {e}") from e

    def test(self) -> str:
        """Verify login + connection. Returns the qBittorrent version."""
        self._login()
        return self._get("/api/v2/app/version")

    def add_magnet(self, magnet: str) -> None:
        """Queue a magnet link into qBittorrent."""
        fields = {"urls": magnet}
        if self.category:
            fields["category"] = self.category
        result = self._post("/api/v2/torrents/add", fields)
        if result.lower() not in ("ok.", ""):
            raise ClientError(f"qBittorrent rejected the magnet: {result}")

    def torrents(self) -> list[dict]:
        """Currently-downloading torrents (filtered to our category if set).
        Best-effort — returns [] when qBittorrent isn't configured or
        reachable, so a polling caller never breaks."""
        if not self.url:
            return []
        try:
            if self._opener is None:
                self._login()
            path = "/api/v2/torrents/info?filter=downloading"
            if self.category:
                path += "&category=" + urllib.parse.quote(self.category)
            items = json.loads(self._get(path))
        except (ClientError, json.JSONDecodeError, ValueError):
            return []
        out: list[dict] = []
        for t in items:
            dl = t.get("dlspeed") or 0
            out.append({
                "source": "nyaa",
                "name": t.get("name") or "torrent",
                "progress": float(t.get("progress") or 0),
                "speed": fmt_bytes(dl) + "/s" if dl > 0 else "",
                "status": "downloading",
                "eta": "",
            })
        return out
