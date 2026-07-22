"""One Pace Downloader — pulls full arcs from onepace.net via the
pixeldrain.eu.cc bypass CDN (no 6 GB/day cap)."""

from __future__ import annotations

import html
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Local module — DNS detection + UAC-elevated netsh switching
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dns_switcher
# Local module — certifi-first TLS trust (see tls_trust.py for the why)
import tls_trust

DISCORD_URL = "https://discord.gg/KHn6AbevZ2"
REDDIT_URL = "https://www.reddit.com/user/nicolasenjah/"
GITHUB_URL = "https://github.com/Nicolaslahri/onepacedownloader"
MUHN_GDRIVE_MIRROR_URL = "https://drive.google.com/drive/folders/1OiT81U_kJulO9ptcBJA0wdZTSQAq83Sl"

# Social brand colors — (fill, hover) for the Discord / Reddit / GitHub
# buttons so they're recognizable at a glance instead of generic outlines.
BRAND_BUTTONS = {
    "Discord": (DISCORD_URL, "#5865F2", "#4752C4"),
    "Reddit":  (REDDIT_URL,  "#FF4500", "#D93A00"),
    "GitHub":  (GITHUB_URL,  "#33383F", "#23272D"),
}

# Default Usenet indexer (Newznab-compatible). End users may swap it for any
# other Newznab indexer in Settings; their own API key is required either way.
USENET_DEFAULT_INDEXER_URL = "https://api.nzbgeek.info"
USENET_DEFAULT_INDEXER_NAME = "NZBGeek"
USENET_SETUP_GUIDE_URL = (
    "https://github.com/Nicolaslahri/onepacedownloader#usenet-setup")

# Central index hosted on a `data` branch in the repo. A GitHub Actions cron
# rebuilds this weekly so every user gets fresh data without each having to
# hammer Pixeldrain / SpykerNZ / nyaa.si themselves. See
# .github/workflows/refresh-index.yml.
REMOTE_INDEX_BASE = (
    "https://raw.githubusercontent.com/Nicolaslahri/onepacedownloader/data")
REMOTE_INDEX_URL = f"{REMOTE_INDEX_BASE}/episode_index.json"
REMOTE_ARCS_URL = f"{REMOTE_INDEX_BASE}/arcs.json"
REMOTE_NYAA_URL = f"{REMOTE_INDEX_BASE}/nyaa_arcs.json"

def _user_dir() -> Path:
    """Writable folder next to the .exe (or .py during dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    """Read-only folder where bundled assets live (inside _MEIPASS when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _user_dir()))
    return Path(__file__).resolve().parent


APP_DIR = _user_dir()
BUNDLED_ARCS_FILE = _bundle_dir() / "arcs.json"
BUNDLED_MUHN_FILE = _bundle_dir() / "muhn_arcs.json"
BUNDLED_NYAA_FILE = _bundle_dir() / "nyaa_arcs.json"
BUNDLED_USENET_FILE = _bundle_dir() / "usenet_arcs.json"
BUNDLED_INDEX_FILE = _bundle_dir() / "episode_index.json"
BUNDLED_ICON_ICO = _bundle_dir() / "icon.ico"
if not BUNDLED_ICON_ICO.exists():
    BUNDLED_ICON_ICO = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
BUNDLED_ICON_PNG = _bundle_dir() / "icon.png"
if not BUNDLED_ICON_PNG.exists():
    BUNDLED_ICON_PNG = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
ARCS_FILE = APP_DIR / "arcs.json"
MUHN_FILE = APP_DIR / "muhn_arcs.json"
NYAA_FILE = APP_DIR / "nyaa_arcs.json"
USENET_FILE = APP_DIR / "usenet_arcs.json"
INDEX_FILE = APP_DIR / "episode_index.json"
CONFIG_FILE = APP_DIR / "config.json"
DEFAULT_DOWNLOADS = APP_DIR / "downloads"

APP_VERSION = "2.0.5"

# Sources
SRC_ONE_PACE = "One Pace"
SRC_MUHN_PACE = "Muhn Pace"
SRC_NYAA = "Nyaa"
SRC_USENET = "Usenet"
SOURCE_LABELS = {
    SRC_ONE_PACE:  "One Pace  (Sub for every arc, Dub for newer arcs)",
    SRC_MUHN_PACE: "Muhn Pace  (English Dub fillers for arcs One Pace hasn't dubbed)",
    SRC_NYAA:      "Nyaa  (Torrents — needs your own torrent client like qBittorrent)",
    SRC_USENET:    "Usenet  (NZB files — needs your own Usenet provider + indexer like NZBGeek)",
}
SOURCE_INFO = {
    SRC_ONE_PACE:
        "Main fan re-cut.  Sub for every arc Romance Dawn → Egghead, "
        "Dub for the newer arcs.  Recommended for sub watchers.",
    SRC_MUHN_PACE:
        "Fan-made English Dub for arcs One Pace hasn't dubbed yet "
        "(Enies Lobby → Wano).  Most users pair this with One Pace "
        "— check the watch-order guide to know which arcs to grab from each.",
    SRC_NYAA:
        "Torrents from nyaa.si, grouped by arc.  Clicking Download opens the "
        "magnet link in your default torrent client (qBittorrent, uTorrent, "
        "etc.).  Handy when pixeldrain is throttled or blocked by your ISP.",
    SRC_USENET:
        "Usenet NZB files indexed from NZBGeek. Needs a Usenet provider "
        "(~$10/mo) + indexer API key — configure once in Settings. Clicking "
        "Get NZB downloads the file and opens it in your SABnzbd / NZBGet.",
}
DUB_GUIDE_URL = "https://www.reddit.com/r/onepace/comments/1rtpukk/one_pace_dub_watch_guide/"

# ============================================================ DESIGN TOKENS
# Brand — "Straw Hat / ship hull" identity. Header stays always-dark; the
# body uses the rest of the palette adapted to light/dark via tuples.
HEADER_BG = "#0E1627"        # deep navy — ship hull
HEADER_FG = "#F8F4E8"        # cream
HEADER_ACCENT = "#E5B85A"    # straw gold (matches hat ribbon)
HEADER_ACCENT_DEEP = "#B58A36"
PRIMARY = "#C8232A"          # ribbon red — download buttons
PRIMARY_HOVER = "#A11C22"
PRIMARY_BG = ("#FFF1F2", "#3A1518")  # tinted bg for selected/focused state
MUTED = "#7B8597"

# Surface elevation — three tiers with real contrast. The body (panel) is
# the darkest, columns sit raised on it (card), inner cards within columns
# pop a bit more. Hairline border ties the system together.
SURFACE_PANEL = ("#E8EAEE", "#0D131F")   # window/body background — darkest
SURFACE_CARD  = ("#FFFFFF", "#1A2332")   # column surface — raised
SURFACE_INNER = ("#F6F7FA", "#212D3F")   # inner cards (banner, source cards)
SURFACE_ROW   = ("#F0F1F4", "#23303F")
SURFACE_HOVER = ("#E5E8EE", "#2C3D5C")
BORDER        = ("#DBDFE5", "#2A3441")
BORDER_STRONG = ("#C0C5CD", "#3A4250")

# Text
TEXT          = ("gray10",  "gray92")
TEXT_MUTED    = ("gray35",  "gray65")
TEXT_DIM      = ("gray50",  "gray50")
LINK          = HEADER_ACCENT  # gold links carry the brand into the body

# Semantic — every accent is theme-aware (light, dark) so dark mode pops too
OK            = ("#1E7E34", "#4CAF50")
WARN          = ("#B97500", "#E5A23A")
DANGER        = ("#A11C22", "#D63B40")
DANGER_HOVER  = ("#7E2A2A", "#9F2A30")
INFO          = ("#3F62A4", "#5C7DC4")   # secondary blue (also magnet)
INFO_HOVER    = ("#2B4977", "#3F62A4")
SECONDARY     = ("#374A6E", "#374A6E")   # neutral filled button
SECONDARY_HOVER = ("#2C3D5C", "#2C3D5C")
OFFICIAL_BG   = ("#E8F5E9", "#1E3A24")   # Galaxy9000 row background
UNOFFICIAL_BG = ("#FFF7E0", "#2E2814")   # other Nyaa pack row background
OFFICIAL_CHIP = ("#1E7E34", "#3FA85A")

# Spacing — 4px base unit. Always use these constants, never raw numbers.
SP_XS = 4
SP_SM = 8
SP_MD = 12
SP_LG = 16
SP_XL = 20
SP_XXL = 24

# Corner radius — only two values app-wide
RADIUS_SM = 6   # rows, badges, small buttons
RADIUS_LG = 10  # cards, modals, large containers

# Typography — five-step ramp.
FAMILY = "Segoe UI"
FAMILY_MONO = "Consolas"
F_XS    = (FAMILY, 9)
F_SM    = (FAMILY, 10)
F_BASE  = (FAMILY, 11)
F_LG    = (FAMILY, 13)
F_XL    = (FAMILY, 16)
F_HERO  = (FAMILY, 22)
F_BOLD_XS   = (FAMILY, 9, "bold")
F_BOLD_SM   = (FAMILY, 10, "bold")
F_BOLD_BASE = (FAMILY, 11, "bold")
F_BOLD_LG   = (FAMILY, 13, "bold")
F_BOLD_XL   = (FAMILY, 16, "bold")
F_HERO_BOLD = (FAMILY, 28, "bold")
F_ITALIC_XS = (FAMILY, 9, "italic")
F_MONO_SM   = (FAMILY_MONO, 10)
F_MONO_BASE = (FAMILY_MONO, 11)
# Button heights — three sizes only
H_SM = 28
H_MD = 34
H_LG = 40

ONEPACE_URL = "https://onepace.net/en/watch"
PIXELDRAIN_API = "https://pixeldrain.com/api/list/{album_id}"
# Pixeldrain.com is the upstream — clean AV reputation, 6 GB/day per-IP cap
# on free tier. We try it first and fall back to the unofficial bypass CDN
# (cdn.pixeldrain.eu.cc) once the cap is hit. Casual users (a couple of
# episodes a day) never burn through their 6 GB and stay on the clean URL.
PIXELDRAIN_FILE = "https://pixeldrain.com/api/file/{file_id}"
BYPASS_FILE = "https://cdn.pixeldrain.eu.cc/{file_id}"
NYAA_URL = "https://nyaa.si/?f=0&c=0_0&q=one+pace&p={page}"
NYAA_MAX_PAGES = 10  # safety cap; query currently ~6 pages of 75 results

# Galaxy9000 is the One Pace project's official Nyaa uploader account. Torrents
# from this account get a green "Official" badge and sort to the top of every
# per-episode source list.
NYAA_OFFICIAL_UPLOADER = "Galaxy9000"
NYAA_OFFICIAL_URL = (
    "https://nyaa.si/user/" + NYAA_OFFICIAL_UPLOADER
    + "?f=0&c=0_0&q=one+pace&p={page}"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

VERSIONS = ["English Subtitles", "English Dub", "English Dub with Closed Captions"]
QUALITIES = ["1080p", "720p", "480p"]


# ---------------------------------------------------------------- helpers ---

_TRANSIENT = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    OSError,  # WinError 10054 surfaces as ConnectionResetError -> OSError
)


def http_get(url: str, *, timeout: int = 30, retries: int = 5) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with tls_trust.urlopen(req, timeout=timeout) as r:
                return r.read()
        except _TRANSIENT as e:
            # A cert failure means every trust store we have was already
            # tried — retrying just stalls the user for ~30s. Bail out.
            if tls_trust.is_cert_error(e):
                raise
            last = e
            time.sleep(min(2 ** attempt, 15))
    raise last if last else RuntimeError("http_get failed")


def open_stream(url: str, *, start: int = 0, timeout: int = 60, retries: int = 5):
    headers = {"User-Agent": UA}
    if start:
        headers["Range"] = f"bytes={start}-"
    req = urllib.request.Request(url, headers=headers)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return tls_trust.urlopen(req, timeout=timeout)
        except _TRANSIENT as e:
            if tls_trust.is_cert_error(e):
                raise
            last = e
            time.sleep(min(2 ** attempt, 15))
    raise last if last else RuntimeError("open_stream failed")


def open_stream_dual(file_id: str, *, start: int = 0, timeout: int = 60,
                      on_log=None, force_bypass: bool = False):
    """Try pixeldrain.com first; fall back to the bypass CDN on rate-limit
    or unreachability. Returns (response, host_label). The host label is
    used in logs so the user can see which path served the bytes.

    Pixeldrain.com applies a per-IP 6 GB/day cap on the free tier — it
    answers with HTTP 429 (rate_limited) or 403 once hit. The bypass CDN
    has no such cap but ships with worse AV reputation, so we only switch
    when we have to.

    Pass `force_bypass=True` to skip pixeldrain.com entirely — used once
    the speed-based detector has already shown the upstream is throttled
    this session (saves an 8-second slow start per subsequent file)."""
    log = on_log or (lambda _m: None)
    if force_bypass:
        return (open_stream(BYPASS_FILE.format(file_id=file_id),
                            start=start, timeout=timeout),
                "cdn.pixeldrain.eu.cc")
    primary = PIXELDRAIN_FILE.format(file_id=file_id)
    headers = {"User-Agent": UA}
    if start:
        headers["Range"] = f"bytes={start}-"
    try:
        req = urllib.request.Request(primary, headers=headers)
        return tls_trust.urlopen(req, timeout=timeout), "pixeldrain.com"
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            log(f"  pixeldrain.com rate-limited (HTTP {e.code}) — falling "
                f"back to bypass CDN for this file")
        else:
            log(f"  pixeldrain.com error {e.code} — falling back to bypass CDN")
    except _TRANSIENT as e:
        log(f"  pixeldrain.com unreachable ({e}) — falling back to bypass CDN")
    return (open_stream(BYPASS_FILE.format(file_id=file_id),
                         start=start, timeout=timeout),
            "cdn.pixeldrain.eu.cc")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "untitled"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ------------------------------------------------------------- arc loader ---

def parse_arcs_from_html(page_html: str) -> list[dict]:
    arc_re = re.compile(
        r"<h2[^>]*>\s*<a[^>]*>([^<]+?)</a>\s*</h2>(.*?)(?=<h2[^>]*>\s*<a|$)",
        re.DOTALL,
    )
    res_re = re.compile(
        r'<span class="flex-1">([^<]+)</span>(.*?)'
        r'(?=<span class="flex-1">|</ul></li><li |$)',
        re.DOTALL,
    )
    link_re = re.compile(
        r'href="(https://pixeldrain\.net/l/([A-Za-z0-9]+))"[^>]*>.*?'
        r'<span class="grow text-center tabular-nums">\s*([0-9]+p)\s*</span>',
        re.DOTALL,
    )

    arcs: list[dict] = []
    for m in arc_re.finditer(page_html):
        title = html.unescape(m.group(1).strip())
        block = m.group(2)
        if "pixeldrain" not in block:
            continue
        resources: dict[str, dict[str, str]] = {}
        for r in res_re.finditer(block):
            name = r.group(1).strip()
            qs: dict[str, str] = {}
            for lk in link_re.finditer(r.group(2)):
                qs[lk.group(3)] = lk.group(2)
            if qs:
                resources[name] = qs
        if resources:
            arcs.append({"title": title, "resources": resources})
    return arcs


def load_arcs() -> list[dict]:
    # Prefer the user-writable copy (refreshed from the web); fall back to bundled.
    for p in (ARCS_FILE, BUNDLED_ARCS_FILE):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return []


def load_muhn_arcs() -> list[dict]:
    """Muhn Pace data is curated (no live scrape) — bundled JSON only."""
    for p in (MUHN_FILE, BUNDLED_MUHN_FILE):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return []


def refresh_arcs_from_web() -> list[dict]:
    page = http_get(ONEPACE_URL, timeout=60).decode("utf-8", errors="replace")
    arcs = parse_arcs_from_html(page)
    if not arcs:
        raise RuntimeError("Could not parse any arcs from onepace.net (page format changed?)")
    ARCS_FILE.write_text(json.dumps(arcs, indent=2, ensure_ascii=False), encoding="utf-8")
    return arcs


# ----------------------------------------------------------------- nyaa ---

# Nyaa's "trusted only" (f=2) filter returns nothing for One Pace because the
# project doesn't upload from a Trusted account, so we pull the full list and
# rely on title-matching to drop unrelated results. Remake/red rows are skipped.

_NYAA_ROW_RE = re.compile(
    r'<tr class="(default|success|warning)">(.*?)</tr>', re.DOTALL
)
_NYAA_TITLE_RE = re.compile(
    r'<a href="/view/\d+"[^>]*title="([^"]+)"'
)
_NYAA_MAGNET_RE = re.compile(
    r'href="(magnet:\?xt=urn:btih:[^"]+)"'
)
_NYAA_TD_RE = re.compile(
    r'<td class="text-center"(?:\s+data-timestamp="\d+")?\s*>([^<]*)</td>'
)
# Uploaders mark superseded releases with "(Defunct)" / "(Deprecated)" /
# "(Old)" in the title. They sit on the same arc and add noise; filter them
# at parse time so they never reach the UI.
_NYAA_DEPRECATED_RE = re.compile(
    r"[\(\[]\s*(?:defunct|deprecated|outdated|old|superseded|obsolete)\s*[\)\]]",
    re.IGNORECASE,
)


def parse_nyaa_html(page_html: str) -> list[dict]:
    """Extract torrent rows from one nyaa.si search page."""
    rows: list[dict] = []
    for m in _NYAA_ROW_RE.finditer(page_html):
        block = m.group(2)
        title_m = _NYAA_TITLE_RE.search(block)
        magnet_m = _NYAA_MAGNET_RE.search(block)
        if not title_m or not magnet_m:
            continue
        title = html.unescape(title_m.group(1))
        # Skip uploads tagged as defunct / deprecated by the uploader
        if _NYAA_DEPRECATED_RE.search(title):
            continue
        tds = [html.unescape(t).strip() for t in _NYAA_TD_RE.findall(block)]
        # Expected: [size, date, seeders, leechers, downloads]
        size = tds[0] if len(tds) > 0 else ""
        date = tds[1] if len(tds) > 1 else ""
        seeders = int(tds[2]) if len(tds) > 2 and tds[2].isdigit() else 0
        leechers = int(tds[3]) if len(tds) > 3 and tds[3].isdigit() else 0
        rows.append({
            "title": title,
            "magnet": html.unescape(magnet_m.group(1)),
            "size": size,
            "date": date,
            "seeders": seeders,
            "leechers": leechers,
        })
    return rows


_NYAA_ARC_ALIASES: dict[str, list[str]] = {
    # Nyaa uploaders use the Japanese romanization "Arabasta" for Alabasta.
    "Alabasta": ["Arabasta"],
    # Some uploads spell the arc "Whiskey Peak" (extra e) instead of "Whisky Peak".
    "Whisky Peak": ["Whiskey Peak"],
}


def bucket_by_arc(torrents: list[dict], arc_titles: list[str]) -> list[dict]:
    """Group torrents into arc buckets. Longest arc-name match wins.
    Unmatched (or full-series packs) land in an 'Other / Packs' bucket."""
    # Build a (needle, arc) list of all match candidates — each canonical arc
    # plus its aliases. Match longest needle first so 'Whole Cake Island' wins
    # over 'Whole Cake', 'Post-Enies Lobby' wins over 'Enies Lobby', etc.
    needles: list[tuple[str, str]] = []
    for a in arc_titles:
        needles.append((a, a))
        for alias in _NYAA_ARC_ALIASES.get(a, []):
            needles.append((alias, a))
    needles.sort(key=lambda p: len(p[0]), reverse=True)

    buckets: dict[str, list[dict]] = {a: [] for a in arc_titles}
    other: list[dict] = []
    for t in torrents:
        low = t["title"].lower()
        arc = next((arc for needle, arc in needles if needle.lower() in low), None)
        if arc:
            buckets[arc].append(t)
        else:
            other.append(t)

    # Sort each bucket: official (Galaxy9000) first, then by seeders desc.
    def _sort_key(r: dict) -> tuple[int, int]:
        is_official = 1 if r.get("uploader") == NYAA_OFFICIAL_UPLOADER else 0
        return (-is_official, -int(r.get("seeders", 0)))

    out: list[dict] = []
    for arc in arc_titles:
        ts = sorted(buckets[arc], key=_sort_key)
        if ts:
            out.append({"title": arc, "torrents": ts})
    if other:
        out.append({
            "title": "Other / Packs",
            "torrents": sorted(other, key=_sort_key),
        })
    return out


_NYAA_LAST_PAGE_RE = re.compile(
    r'<div class="pagination-page-info">[^<]*\bout of\s+(\d+)\s+results',
    re.IGNORECASE,
)


_BTIH_RE = re.compile(r"btih:([A-Fa-f0-9]+)")


def _extract_btih(magnet: str) -> str:
    """Pull the BitTorrent info-hash out of a magnet URI. Returns lowercase
    hex; empty string if the magnet is malformed."""
    m_ = _BTIH_RE.search(magnet or "")
    return m_.group(1).lower() if m_ else ""


def fetch_official_btihs() -> set[str]:
    """Scrape Galaxy9000's One Pace torrent listing and return the set of
    btih hashes. Used to mark torrents as 'Official' across our main scrape."""
    btihs: set[str] = set()
    for page in range(1, NYAA_MAX_PAGES + 1):
        try:
            html_page = http_get(
                NYAA_OFFICIAL_URL.format(page=page), timeout=60
            ).decode("utf-8", errors="replace")
        except Exception:
            continue
        rows = parse_nyaa_html(html_page)
        if not rows and ('<tr class="default">' not in html_page
                          and '<tr class="success">' not in html_page):
            break
        for r in rows:
            h = _extract_btih(r.get("magnet", ""))
            if h:
                btihs.add(h)
        time.sleep(0.5)
    return btihs


def refresh_nyaa_from_web(arc_titles: list[str]) -> list[dict]:
    """Scrape every page of the One Pace search on nyaa.si and bucket by arc.
    Also pulls Galaxy9000's uploads separately so we can mark them official."""
    all_rows: list[dict] = []
    expected_total: int | None = None
    for page in range(1, NYAA_MAX_PAGES + 1):
        try:
            html_page = http_get(NYAA_URL.format(page=page), timeout=60).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            # One flaky page shouldn't abort the whole scrape; skip and continue.
            continue
        if expected_total is None:
            tm = _NYAA_LAST_PAGE_RE.search(html_page)
            if tm:
                expected_total = int(tm.group(1))
        rows = parse_nyaa_html(html_page)
        # End-of-results detection: only stop when the page actually has no
        # result rows at all (the "No results found" page). We can't use row
        # count thresholds because filtered-out `danger` rows make every page
        # look "short".
        if not rows and ('<tr class="default">' not in html_page
                          and '<tr class="success">' not in html_page):
            break
        all_rows.extend(rows)
        if expected_total is not None and len(all_rows) >= expected_total:
            break
        time.sleep(0.5)  # be polite, also helps with rate-limit hiccups

    if not all_rows:
        raise RuntimeError("Could not parse any torrents from nyaa.si (page format changed?)")

    # Tag torrents uploaded by the official One Pace account
    official_btihs = fetch_official_btihs()
    for r in all_rows:
        r["uploader"] = (NYAA_OFFICIAL_UPLOADER
                         if _extract_btih(r["magnet"]) in official_btihs
                         else "")

    bucketed = bucket_by_arc(all_rows, arc_titles)
    payload = {"scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "arcs": bucketed}
    NYAA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return bucketed


def load_nyaa_arcs() -> list[dict]:
    """Load cached Nyaa arc buckets — user-writable copy first, bundled fallback."""
    for p in (NYAA_FILE, BUNDLED_NYAA_FILE):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
            # Accept both {"arcs": [...]} envelope and a bare list.
            if isinstance(data, dict) and "arcs" in data:
                return data["arcs"]
            if isinstance(data, list):
                return data
    return []


def load_usenet_arcs() -> list[dict]:
    """Load cached Usenet arc buckets — user-writable copy first, bundled
    fallback. Bare-list shape: [{arc, episodes, packs}, ...]."""
    for p in (USENET_FILE, BUNDLED_USENET_FILE):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "arcs" in data:
                return data["arcs"]
    return []


def load_episode_index() -> dict:
    """Load the unified per-episode index (v2). Returns the envelope with
    'arcs' (each containing 'episodes' and 'arc_packs') and 'specials'.
    Returns an empty stub if no index file is present."""
    for p in (INDEX_FILE, BUNDLED_INDEX_FILE):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
    return {"arcs": [], "specials": []}


def episode_title(ep: dict, *, max_len: int = 0) -> str:
    """Preferred display string for an episode. Uses SpykerNZ canonical
    titles when available (`{NN}  Romance Dawn, the Dawn of an Adventure`),
    falls back to the auto-generated `{arc} {NN}` form otherwise. When
    `max_len` is set, the result is truncated with an ellipsis to fit narrow
    UI rows; callers that have room (logs, status panel, dialogs) pass 0."""
    canonical = ep.get("canonical_title")
    if canonical:
        title = f"{ep.get('num', 0):02d}  {canonical}"
    else:
        title = ep.get("display_title", "?")
    if max_len and len(title) > max_len:
        return title[:max_len - 1] + "…"
    return title


# -------------------------------------------------------------- downloads ---

class DownloadCancelled(Exception):
    pass


class Downloader:
    """Downloads a pixeldrain album via the bypass CDN, file by file.

    If `file_filter` is set, only files whose Pixeldrain ID is in the set are
    downloaded — used by v2 to grab individual episodes instead of full albums.
    Pass None (or omit) to keep the v1 behavior of downloading the whole album.
    """

    def __init__(
        self,
        album_id: str,
        dest_dir: Path,
        *,
        on_status,
        on_progress,
        on_log,
        cancel_evt: threading.Event,
        file_filter: set[str] | None = None,
        subfolder: str | None = None,
    ) -> None:
        self.album_id = album_id
        self.dest_dir = dest_dir
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_log = on_log
        self.cancel_evt = cancel_evt
        self.file_filter = file_filter
        # Custom subfolder name (defaults to album title when None)
        self.subfolder = subfolder
        # Flipped on the moment the speed-based detector judges pixeldrain.com
        # too slow — subsequent files in the same album skip it outright.
        self._prefer_bypass = False

    def fetch_album(self) -> dict:
        data = json.loads(http_get(PIXELDRAIN_API.format(album_id=self.album_id)))
        if not data.get("success", True) and "files" not in data:
            raise RuntimeError(f"Pixeldrain API error: {data}")
        return data

    def run(self) -> None:
        album = self.fetch_album()
        files = album.get("files", [])
        title = album.get("title") or self.album_id
        if self.file_filter is not None:
            files = [f for f in files if f.get("id") in self.file_filter]
            if not files:
                self.on_log(
                    f"Album {title!r}: no files matched the filter — nothing to do."
                )
                return
        folder_name = sanitize_filename(self.subfolder or title)
        target = self.dest_dir / folder_name
        target.mkdir(parents=True, exist_ok=True)
        self.on_log(f"Album: {title} ({len(files)} file{'s' if len(files) != 1 else ''}) -> {target}")

        total = len(files)
        for idx, f in enumerate(files, 1):
            if self.cancel_evt.is_set():
                raise DownloadCancelled()
            name = sanitize_filename(f["name"])
            size = int(f.get("size", 0))
            out = target / name
            self.on_status(f"[{idx}/{total}] {name}")
            self._download_one(f["id"], out, size, idx, total)

        self.on_log(f"Done: {title}")

    def _download_one(self, file_id: str, out: Path, size: int, idx: int, total: int) -> None:
        partial = out.with_suffix(out.suffix + ".part")
        if out.exists() and (size == 0 or out.stat().st_size == size):
            self.on_log(f"  skip (already complete): {out.name}")
            self.on_progress(1.0, "already downloaded", idx, total, 0)
            return

        start = partial.stat().st_size if partial.exists() else 0
        attempt = 0
        last_exc: Exception | None = None
        while attempt < 5:
            attempt += 1
            try:
                self._stream_to(file_id, partial, start, size, idx, total)
                if size and partial.stat().st_size != size:
                    raise IOError(
                        f"size mismatch: expected {size}, got {partial.stat().st_size}"
                    )
                if out.exists():
                    out.unlink()
                partial.rename(out)
                return
            except DownloadCancelled:
                raise
            except Exception as e:
                last_exc = e
                start = partial.stat().st_size if partial.exists() else 0
                wait = min(2 ** attempt, 30)
                self.on_log(f"  retry {attempt}/5 after error: {e} (sleep {wait}s)")
                if self.cancel_evt.wait(wait):
                    raise DownloadCancelled()
        raise RuntimeError(f"failed after 5 attempts: {last_exc}")

    def _stream_to(
        self, file_id: str, partial: Path, start: int, size: int, idx: int, total: int
    ) -> None:
        # Switch off pixeldrain.com to the bypass CDN if it's running slow —
        # catches the daily 6 GB cap (which just throttles, doesn't error)
        # AND any other source of sluggishness. Judges after a brief
        # warm-up so a slow TCP ramp-up doesn't trigger the switch.
        SLOW_AFTER_SEC = 8.0
        SLOW_BYTES_PER_SEC = 500 * 1024   # 500 KB/s — clearly throttled

        resp, host = open_stream_dual(
            file_id, start=start, on_log=self.on_log,
            force_bypass=self._prefer_bypass)
        if start == 0:
            suffix = " (forced — pixeldrain.com was slow earlier)" \
                if self._prefer_bypass else ""
            self.on_log(f"  source: {host}{suffix}")

        mode = "ab" if start else "wb"
        t0 = time.time()
        t_last = t0
        bytes_now = start
        display_start = start

        try:
            with open(partial, mode) as f:
                while True:
                    if self.cancel_evt.is_set():
                        raise DownloadCancelled()
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_now += len(chunk)
                    now = time.time()

                    # Mid-stream switch when pixeldrain.com is slow.
                    if (host == "pixeldrain.com"
                            and not self._prefer_bypass
                            and (now - t0) >= SLOW_AFTER_SEC):
                        avg_bps = (bytes_now - display_start) / max(now - t0, 0.001)
                        if avg_bps < SLOW_BYTES_PER_SEC:
                            self.on_log(
                                f"  pixeldrain.com is slow "
                                f"({fmt_bytes(avg_bps)}/s) — switching to "
                                f"bypass CDN at {fmt_bytes(bytes_now)}")
                            self._prefer_bypass = True
                            try:
                                try:
                                    resp.close()
                                except Exception:
                                    pass
                                resp = open_stream(
                                    BYPASS_FILE.format(file_id=file_id),
                                    start=bytes_now, timeout=60)
                                host = "cdn.pixeldrain.eu.cc"
                                display_start = bytes_now
                                t0 = now
                                t_last = now
                            except Exception as e:
                                self.on_log(
                                    f"  bypass switch failed ({e}) -- "
                                    f"continuing on pixeldrain.com")

                    if now - t_last >= 0.2:
                        speed = (bytes_now - display_start) / max(now - t0, 0.001)
                        frac = bytes_now / size if size else 0.0
                        self.on_progress(frac, fmt_bytes(speed) + "/s", idx, total, bytes_now)
                        t_last = now

            speed = (bytes_now - display_start) / max(time.time() - t0, 0.001)
            self.on_progress(1.0, fmt_bytes(speed) + "/s", idx, total, bytes_now)
        finally:
            try:
                resp.close()
            except Exception:
                pass


# ------------------------------------------------------------- config -----

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- gui -----

def _quality_rank(q: str) -> int:
    m_ = re.match(r"(\d+)p", q)
    return int(m_.group(1)) if m_ else 0


def _fmt_size(bytes_: int) -> str:
    mb = bytes_ / 1024 / 1024
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


# Windows reserved filenames (case-insensitive, with or without extension)
_WIN_RESERVED = {"con", "prn", "aux", "nul",
                  "com1", "com2", "com3", "com4", "com5",
                  "com6", "com7", "com8", "com9",
                  "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
                  "lpt6", "lpt7", "lpt8", "lpt9"}


def _make_brand_button(parent, label: str, *, width: int = 96,
                       height: int | None = None):
    """Create a brand-colored social button (Discord blue / Reddit orange /
    GitHub charcoal) that opens the matching URL. Falls back to a neutral
    button for unknown labels."""
    entry = BRAND_BUTTONS.get(label)
    if entry:
        url, fg, hover = entry
    else:
        url, fg, hover = "", SECONDARY, SECONDARY_HOVER
    return ctk.CTkButton(
        parent, text=label, width=width,
        height=height if height is not None else H_SM,
        fg_color=fg, hover_color=hover, text_color="#FFFFFF",
        font=F_BOLD_XS,
        command=lambda u=url: webbrowser.open(u) if u else None)


def _safe_nzb_filename(title: str, guid: str) -> str:
    """Produce a Windows-safe `<title>.nzb` filename for the given release.

    Strips: <>:"/\\|?* + ASCII control chars, trailing dots/spaces (which
    Windows silently drops, causing later lookup mismatches), and avoids
    reserved device names (CON, NUL, COM1, ...). Truncates the stem to 120
    chars. Falls back to `<guid>.nzb` if sanitization leaves nothing.

    The caller is responsible for the final MAX_PATH check (the full path
    including the save folder may still exceed 260 on legacy Windows)."""
    # 1. Strip illegal chars + ASCII control bytes
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title or "")
    # 2. Collapse runs of underscores produced by step 1
    s = re.sub(r"_{2,}", "_", s)
    # 3. Strip trailing dots/spaces (Windows quirk — silently dropped)
    s = s.rstrip(". ")
    # 4. Length-cap the stem
    s = s[:120].rstrip(". ") or guid
    # 5. Avoid reserved device names
    stem_no_ext = re.sub(r"\.nzb$", "", s, flags=re.IGNORECASE)
    if stem_no_ext.lower() in _WIN_RESERVED:
        s = f"_{s}"
    # 6. Ensure .nzb extension
    if not s.lower().endswith(".nzb"):
        s += ".nzb"
    return s


class Tooltip:
    """Lightweight tooltip — shows a Toplevel with the given text after a
    short hover delay. Use `attach(widget, text)` to install on any widget
    (or any subset of widgets that should share the same tip)."""

    _delay_ms = 450

    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self._delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        # Theme-aware tooltip — black in dark mode, white in light. Reads as
        # part of the app rather than a Win95 yellow-box throwback.
        is_dark = ctk.get_appearance_mode().lower() == "dark"
        bg = "#23303F" if is_dark else "#FFFFFF"
        fg = "#F0F0F0" if is_dark else "#1A1A1A"
        border_col = "#3A4250" if is_dark else "#C8CCD4"
        outer = tk.Frame(tip, background=border_col, bd=0)
        outer.pack()
        tk.Label(
            outer, text=self.text,
            background=bg, foreground=fg,
            font=("Segoe UI", 9),
            padx=10, pady=6, relief="flat", bd=0,
            wraplength=320, justify="left",
        ).pack(padx=1, pady=1)
        self._tip = tip

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# Source page identity — drives which arcs/episodes are shown and how the
# detail pane is built. Keys are internal; labels feed the tab strip.
_SRC_LABEL = {"onepace": "One Pace", "muhn": "Muhn Pace",
              "nyaa": "Nyaa", "usenet": "Usenet"}
_LABEL_SRC = {v: k for k, v in _SRC_LABEL.items()}
_SRC_BLURB = {
    "onepace": "Fan re-cut. Sub for every arc, Dub for the newer ones.",
    "muhn":    "Fan-made English Dub filling arcs One Pace hasn't dubbed yet (Enies Lobby → Wano).",
    "nyaa":    "Torrents from nyaa.si — official One Pace uploads are tagged with a green Official badge.",
    "usenet":  "NZB files from NZBGeek — needs your own Usenet provider + indexer API key (configure in Settings).",
}


class App(ctk.CTk):
    """v2 — three-column master-detail. Left: arcs. Middle: episodes for the
    selected arc (multi-select). Right: cross-source comparison for the
    highlighted episode with per-quality download/magnet buttons."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title(f"One Pace Downloader v{APP_VERSION}")
        
        self.config_data = load_config()
        
        # Load saved window position/geometry
        geom = self.config_data.get("geometry")
        if geom:
            try:
                self.geometry(geom)
            except Exception:
                self.geometry("1180x780")
        else:
            self.geometry("1180x780")
        self.minsize(960, 560)
        
        maximized = self.config_data.get("maximized", False)
        if maximized:
            try:
                self.state("zoomed")
            except Exception:
                pass
                
        self._apply_icon()

        # If the user previously saved an Appearance preference, honor it
        appearance = self.config_data.get("appearance")
        if appearance in ("System", "Light", "Dark"):
            ctk.set_appearance_mode(appearance.lower())

        self.arcs = load_arcs()
        self.muhn_arcs = load_muhn_arcs()
        self.nyaa_arcs = load_nyaa_arcs()
        self.usenet_arcs = load_usenet_arcs()
        self.index = load_episode_index()

        self.save_dir = ctk.StringVar(
            value=self.config_data.get("save_folder", str(DEFAULT_DOWNLOADS))
        )
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_arc_list())

        # Active source page — "onepace" / "muhn" / "nyaa". The arc + episode +
        # detail panes all filter by this so each source has its own view.
        prev_src = self.config_data.get("source", "onepace")
        if prev_src not in ("onepace", "muhn", "nyaa"):
            prev_src = "onepace"
        self.current_source = prev_src

        # Nyaa-only sub-view: "eps" (per-episode torrents) or "packs"
        # (full-arc packs). Toggled by a segmented button in the middle column
        # when the Nyaa tab is active.
        self.nyaa_view = "eps"

        # Selection state for the master-detail flow
        self.selected_arc: dict | None = None
        self.selected_ep_for_detail: int | None = None
        # ep_num -> BooleanVar for the middle column checkboxes
        self.ep_check_vars: dict[int, ctk.BooleanVar] = {}
        # Widget refs for incremental selection updates — destroying and
        # rebuilding the entire arc/episode list on every click is what
        # makes the app feel sluggish. These maps let us mutate only the
        # two rows whose state changed.
        self._arc_row_widgets: dict[str, dict] = {}
        self._ep_row_widgets: dict[int, dict] = {}

        # Download infrastructure
        self.cancel_evt = threading.Event()
        self.worker: threading.Thread | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self.status_var = ctk.StringVar(value="Pick an arc on the left to get started →")

        self._log_visible = False
        self.refresh_in_progress = False
        # Set of filenames present in save_dir — used to mark episode rows
        # as "Saved". Populated by _scan_downloaded_files; refreshed on
        # save-folder change and on every download completion.
        self.downloaded_files: set[str] = set()
        # 'sNNeMM' keys from Plex-organized files in save_dir; lets the Saved
        # badge keep working after the user enables media-server mode.
        self.saved_plex_keys: set[str] = set()
        # Active download bookkeeping for the right-column status panel.
        # `download_title` is the user-facing label set when a download starts
        # ("Wano 03 — Sub 1080p"); the rest is updated by progress events.
        self.download_title: str | None = None
        self.download_state: dict | None = None  # {frac, speed, bytes_now, idx, total}
        # Slow-download nudge: once we've seen a sustained < 300 KB/s after the
        # initial TCP ramp, surface a friendly "speeds vary, hang in there" note
        # so the user doesn't think the app is broken. Sticky for the session.
        self.slow_download_noted: bool = False
        self._dl_started_at: float | None = None
        self._download_panel_widgets: dict = {}

        self._updating_checks = False
        self._settings_panel_ref = None
        self._last_clicked_ep_num = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._refresh_nyaa_view_toggle()
        # Initial scan of the save folder so the first arc click already
        # shows Saved badges where files exist.
        self._refresh_downloaded_files(rerender=False)
        self._render_arc_list()
        self._render_episode_list()
        self._render_source_panel()
        self.after(80, self._drain_ui_queue)
        # Stage 3 of smart refresh: silently check onepace.net in the
        # background a couple of seconds after the UI is responsive. If
        # something changed since our cached arcs hash, surface a tiny
        # "↻ updates" badge on the Refresh button so users notice.
        self.after(2500, self._check_for_updates_async)

        if not self.index.get("arcs"):
            self._log(
                "No episode_index.json found — run "
                "_source/build_episode_index.py to populate per-episode data."
            )

    # ---- UI construction ----

    def _apply_icon(self) -> None:
        # Title bar icon (Windows) — silently skip if asset missing in dev.
        if BUNDLED_ICON_ICO.exists():
            try:
                self.iconbitmap(default=str(BUNDLED_ICON_ICO))
            except tk.TclError:
                pass
        if BUNDLED_ICON_PNG.exists():
            try:
                self._app_icon = tk.PhotoImage(file=str(BUNDLED_ICON_PNG))
                self.iconphoto(True, self._app_icon)
            except tk.TclError:
                self._app_icon = None
        else:
            self._app_icon = None

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")  # most consistent across Windows for color overrides
        except tk.TclError:
            pass

        # Info banner under the Source picker.
        style.configure("Info.TFrame", background="#FBF6E8")
        style.configure("Info.TLabel",
                        background="#FBF6E8", foreground="#6B5A1F",
                        font=("Segoe UI", 9))
        style.configure("InfoLink.TLabel",
                        background="#FBF6E8", foreground="#0066CC",
                        font=("Segoe UI", 9, "underline"))

        # Primary action button (Download / Download all) — ribbon red.
        style.configure(
            "Primary.TButton",
            background=PRIMARY, foreground="#FFFFFF",
            borderwidth=0, focusthickness=0, padding=(14, 7),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", PRIMARY_HOVER), ("disabled", "#9CA3AF")],
            foreground=[("disabled", "#E5E7EB")],
        )

        # Subtle outline button (Browse, Open folder, Refresh).
        style.configure(
            "Ghost.TButton",
            padding=(10, 6),
            font=("Segoe UI", 9),
        )

        # Header label styles
        style.configure("Header.TFrame", background=HEADER_BG)
        style.configure("HeaderTitle.TLabel",
                        background=HEADER_BG, foreground=HEADER_FG,
                        font=("Segoe UI", 18, "bold"))
        style.configure("HeaderTagline.TLabel",
                        background=HEADER_BG, foreground=HEADER_ACCENT,
                        font=("Segoe UI", 10))
        style.configure("HeaderCredit.TLabel",
                        background=HEADER_BG, foreground=MUTED,
                        font=("Segoe UI", 9, "italic"))

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}

        # ---- HEADER ----
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 12, 16, 12))
        header.pack(fill="x")
        if self._app_icon is not None:
            # Resize the icon to a sane header size by using subsample
            # (PhotoImage subsample is integer factors; 512/72 ≈ 7).
            try:
                hdr_icon = self._app_icon.subsample(7, 7)
                self._hdr_icon_ref = hdr_icon  # keep reference
                tk.Label(
                    header, image=hdr_icon, bg=HEADER_BG, bd=0,
                ).pack(side="left", padx=(0, 14))
            except tk.TclError:
                pass
        text_block = ttk.Frame(header, style="Header.TFrame")
        text_block.pack(side="left", fill="y", expand=False)
        ttk.Label(text_block, text="One Pace Downloader",
                  style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(text_block,
                  text="Grab full arcs in one click.  No daily limit.",
                  style="HeaderTagline.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(header, text="Made by Nicolas",
                  style="HeaderCredit.TLabel").pack(side="right", anchor="se")

        # ---- SOURCE PICKER + INFO BANNER ----
        src = ttk.Frame(self)
        src.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(src, text="Source:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.source_combo = ttk.Combobox(
            src, textvariable=self.source,
            values=[
                SOURCE_LABELS[SRC_ONE_PACE],
                SOURCE_LABELS[SRC_MUHN_PACE],
                SOURCE_LABELS[SRC_NYAA],
            ],
            state="readonly", width=64,
        )
        # Set the visible text based on the stored source key
        self.source_combo.set(SOURCE_LABELS[self._current_source()])
        self.source_combo.pack(side="left", padx=(8, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_change)

        info = ttk.Frame(self, style="Info.TFrame", padding=(12, 8, 12, 8))
        info.pack(fill="x", padx=12, pady=(0, 6))
        self.info_label = ttk.Label(info, text="", style="Info.TLabel", wraplength=820, justify="left")
        self.info_label.pack(side="left", fill="x", expand=True, anchor="w")
        self.info_link = ttk.Label(info, text="Open dub watch-order guide ↗",
                                    style="InfoLink.TLabel", cursor="hand2")
        self.info_link.bind("<Button-1>", lambda _e: webbrowser.open(DUB_GUIDE_URL))
        # Link is shown only for Muhn Pace; gets packed/unpacked by _update_info_banner.
        self._info_link_packed = False

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Save to:").grid(row=0, column=0, sticky="w")
        self.path_entry = ttk.Entry(top, textvariable=self.save_dir)
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(top, text="Browse…", command=self._pick_folder).grid(row=0, column=2)
        ttk.Button(top, text="Open folder", command=self._open_folder).grid(row=0, column=3, padx=(6, 0))
        top.columnconfigure(1, weight=1)

        opt = ttk.Frame(self)
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="Version:").grid(row=0, column=0, sticky="w")
        self.version_combo = ttk.Combobox(opt, textvariable=self.version,
                                          values=VERSIONS, state="readonly", width=34)
        self.version_combo.grid(row=0, column=1, padx=(6, 16))
        self.version_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_arc_rows())

        ttk.Label(opt, text="Quality:").grid(row=0, column=2, sticky="w")
        self.quality_combo = ttk.Combobox(opt, textvariable=self.quality,
                                          values=QUALITIES, state="readonly", width=8)
        self.quality_combo.grid(row=0, column=3, padx=(6, 16))
        self.quality_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_arc_rows())

        self.refresh_btn = ttk.Button(opt, text="Refresh from onepace.net",
                                       style="Ghost.TButton", command=self._refresh_arcs)
        self.refresh_btn.grid(row=0, column=4, padx=(0, 6))
        ttk.Button(opt, text="Download all arcs",
                   style="Primary.TButton",
                   command=self._download_all).grid(row=0, column=5)

        # Arc list
        body = ttk.LabelFrame(self, text="Arcs")
        body.pack(fill="both", expand=True, **pad)

        self.canvas = tk.Canvas(body, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.rows_frame = ttk.Frame(self.canvas)
        self.rows_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.rows_window, width=e.width),
        )
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Progress + log
        bot = ttk.Frame(self)
        bot.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="Ready.")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Label(bot, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(bot, variable=self.progress_var, maximum=1.0)
        self.progress.pack(fill="x", pady=(2, 6))

        btnrow = ttk.Frame(bot)
        btnrow.pack(fill="x")
        self.cancel_btn = ttk.Button(btnrow, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="right")

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=False, **pad)
        self.log_box = tk.Text(log_frame, height=7, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)

        # Follow footer (credit moved into the header)
        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Label(
            footer,
            text="Enjoying the tool?  Follow for updates  →",
            foreground=MUTED,
        ).pack(side="left")
        ttk.Button(
            footer, text="Discord", style="Ghost.TButton", width=10,
            command=lambda: webbrowser.open(DISCORD_URL),
        ).pack(side="left", padx=(8, 4))
        ttk.Button(
            footer, text="Reddit", style="Ghost.TButton", width=10,
            command=lambda: webbrowser.open(REDDIT_URL),
        ).pack(side="left", padx=4)

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ---- source / info banner ----

    def _current_source(self) -> str:
        """Return canonical source key (SRC_ONE_PACE / SRC_MUHN_PACE) regardless of
        whether `self.source` holds the key or the user-facing dropdown label."""
        v = self.source.get()
        if v in SOURCE_LABELS:  # canonical key
            return v
        for key, label in SOURCE_LABELS.items():
            if v == label:
                return key
        return SRC_ONE_PACE

    def _on_source_change(self, _event=None) -> None:
        # Normalize the picker text back to a canonical key so config saves cleanly.
        chosen = self._current_source()
        self.source.set(chosen)
        self.source_combo.set(SOURCE_LABELS[chosen])
        self._apply_source_state()
        self._update_info_banner()
        self._refresh_arc_rows()
        self._persist_settings()

    def _apply_source_state(self) -> None:
        """Enable/disable controls based on the active source."""
        src = self._current_source()
        if src == SRC_MUHN_PACE:
            # Muhn Pace is dub-only; quality varies per album so we let the
            # downloader use whatever the album publishes. Lock the controls
            # so the user sees they don't apply.
            self.version_combo.set("English Dub")
            self.version_combo.configure(state="disabled")
            self.quality_combo.set("(varies)")
            self.quality_combo.configure(state="disabled")
            self.refresh_btn.configure(state="disabled", text="Refresh from onepace.net")
        elif src == SRC_NYAA:
            # Nyaa torrents have varied versions/qualities per upload — the
            # user picks the specific torrent per arc, so global controls don't apply.
            self.version_combo.set("(per torrent)")
            self.version_combo.configure(state="disabled")
            self.quality_combo.set("(per torrent)")
            self.quality_combo.configure(state="disabled")
            self.refresh_btn.configure(state="normal", text="Refresh from nyaa.si")
        else:
            self.version_combo.configure(state="readonly")
            if self.version.get() not in VERSIONS:
                self.version.set("English Subtitles")
            self.quality_combo.configure(state="readonly", values=QUALITIES)
            if self.quality.get() not in QUALITIES:
                self.quality.set("1080p")
            self.refresh_btn.configure(state="normal", text="Refresh from onepace.net")

    def _update_info_banner(self) -> None:
        src = self._current_source()
        self.info_label.configure(text=SOURCE_INFO[src])
        if src == SRC_MUHN_PACE:
            if not self._info_link_packed:
                self.info_link.pack(side="right", padx=(8, 0))
                self._info_link_packed = True
        elif self._info_link_packed:
            self.info_link.pack_forget()
            self._info_link_packed = False

    # ---- arc rows ----

    def _refresh_arc_rows(self) -> None:
        for w in self.rows_frame.winfo_children():
            w.destroy()
        src = self._current_source()
        if src == SRC_MUHN_PACE:
            if not self.muhn_arcs:
                ttk.Label(self.rows_frame, text="(no Muhn Pace data bundled)").pack(padx=10, pady=10)
                return
            for idx, arc in enumerate(self.muhn_arcs):
                self._build_muhn_row(idx, arc)
            return
        if src == SRC_NYAA:
            if not self.nyaa_arcs:
                ttk.Label(
                    self.rows_frame,
                    text="(no Nyaa data yet — click 'Refresh from nyaa.si')",
                ).pack(padx=10, pady=10)
                return
            for idx, arc in enumerate(self.nyaa_arcs):
                self._build_nyaa_row(idx, arc)
            return
        # One Pace
        if not self.arcs:
            ttk.Label(self.rows_frame, text="(no data — click Refresh)").pack(padx=10, pady=10)
            return
        ver = self.version.get()
        qual = self.quality.get()
        for idx, arc in enumerate(self.arcs):
            self._build_arc_row(idx, arc, ver, qual)

    def _build_arc_row(self, idx: int, arc: dict, version: str, quality: str) -> None:
        row = ttk.Frame(self.rows_frame, padding=(8, 4))
        row.grid(row=idx, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)
        bg_tag = "even" if idx % 2 == 0 else "odd"
        if bg_tag == "even":
            row.configure(style="Even.TFrame")

        ttk.Label(row, text=arc["title"], width=42, anchor="w").grid(row=0, column=0, sticky="w")

        chosen_ver, chosen_qual, chosen_id, note = self._resolve_album(arc, version, quality)

        badge = []
        for v in VERSIONS:
            if v in arc["resources"]:
                qs = sorted(arc["resources"][v].keys(), key=lambda q: int(q[:-1]))
                short = {"English Subtitles": "Sub", "English Dub": "Dub",
                         "English Dub with Closed Captions": "Dub-CC"}[v]
                badge.append(f"{short}: {','.join(qs)}")
        ttk.Label(row, text="  •  ".join(badge), foreground="#666").grid(row=0, column=1, sticky="w", padx=(8, 8))

        if chosen_id is None:
            btn = ttk.Button(row, text="Not available",
                             style="Ghost.TButton", state="disabled")
        else:
            label = "Download"
            if note:
                label = f"Download ({note})"
            btn = ttk.Button(
                row, text=label, style="Primary.TButton",
                command=lambda a=arc, alb=chosen_id, cv=chosen_ver, cq=chosen_qual:
                    self._start_download_one(a, alb, cv, cq),
            )
        btn.grid(row=0, column=2, sticky="e")
        row.columnconfigure(1, weight=1)

    def _build_muhn_row(self, idx: int, arc: dict) -> None:
        row = ttk.Frame(self.rows_frame, padding=(8, 4))
        row.grid(row=idx, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)

        ttk.Label(row, text=arc["title"], width=42, anchor="w").grid(row=0, column=0, sticky="w")

        # Badge: episode count + total size, then notes (gap-fill range etc.)
        size_gb = arc.get("total_bytes", 0) / 1024 / 1024 / 1024
        meta = f"{arc.get('file_count', '?')} eps  •  {size_gb:.1f} GB"
        ttk.Label(row, text=meta, foreground="#444").grid(
            row=0, column=1, sticky="w", padx=(8, 8))
        notes = arc.get("notes") or ""
        if notes:
            ttk.Label(row, text=notes, foreground=MUTED,
                      font=("Segoe UI", 9, "italic")).grid(
                row=1, column=1, sticky="w", padx=(8, 8))

        btn = ttk.Button(
            row, text="Download", style="Primary.TButton",
            command=lambda a=arc: self._start_download_one(
                {"title": a["title"]}, a["album_id"], "English Dub", "muhn"),
        )
        btn.grid(row=0, column=2, sticky="e", rowspan=2)
        row.columnconfigure(1, weight=1)

    def _build_nyaa_row(self, idx: int, arc: dict) -> None:
        torrents = arc.get("torrents", [])
        if not torrents:
            return

        # Arc heading + top torrent on the same row
        top = torrents[0]
        row = ttk.Frame(self.rows_frame, padding=(8, 4))
        row.grid(row=idx, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)

        ttk.Label(row, text=arc["title"], width=28, anchor="w",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")

        meta = f"{len(torrents)} torrent{'s' if len(torrents) != 1 else ''}  •  top: {top['size']}  •  {top['seeders']} seeders"
        ttk.Label(row, text=meta, foreground="#444").grid(
            row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Label(row, text=top["title"], foreground=MUTED,
                  font=("Segoe UI", 9, "italic"), wraplength=520, justify="left").grid(
            row=1, column=1, sticky="w", padx=(8, 8))

        ttk.Button(
            row, text="Open magnet", style="Primary.TButton",
            command=lambda m=top["magnet"], t=top["title"]: self._open_magnet(m, t),
        ).grid(row=0, column=2, sticky="e", rowspan=2)
        row.columnconfigure(1, weight=1)

    @staticmethod
    def _resolve_album(arc: dict, version: str, quality: str) -> tuple[str, str, str | None, str]:
        """Return (version_used, quality_used, album_id_or_None, note).
        Falls back to the next-best combination if the requested one isn't present."""
        if version in arc["resources"] and quality in arc["resources"][version]:
            return version, quality, arc["resources"][version][quality], ""

        # same version, best other quality
        if version in arc["resources"]:
            qs = arc["resources"][version]
            best = sorted(qs.keys(), key=lambda q: int(q[:-1]), reverse=True)[0]
            return version, best, qs[best], f"only {best}"

        # other version, requested quality if possible, else best
        for alt in VERSIONS:
            if alt in arc["resources"] and quality in arc["resources"][alt]:
                short = {"English Subtitles": "Sub", "English Dub": "Dub",
                         "English Dub with Closed Captions": "Dub-CC"}[alt]
                return alt, quality, arc["resources"][alt][quality], f"{short} only"

        for alt in VERSIONS:
            if alt in arc["resources"]:
                qs = arc["resources"][alt]
                best = sorted(qs.keys(), key=lambda q: int(q[:-1]), reverse=True)[0]
                short = {"English Subtitles": "Sub", "English Dub": "Dub",
                         "English Dub with Closed Captions": "Dub-CC"}[alt]
                return alt, best, qs[best], f"{short} {best}"

        return version, quality, None, ""

    # ---- handlers ----

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.save_dir.get() or str(DEFAULT_DOWNLOADS))
        if chosen:
            self.save_dir.set(chosen)
            self._persist_settings()

    def _open_folder(self) -> None:
        path = Path(self.save_dir.get())
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # Windows-only, fine here
        except AttributeError:
            self._log(f"Folder: {path}")

    def _persist_settings(self) -> None:
        self.config_data.update({
            "save_folder": self.save_dir.get(),
            "default_version": self.version.get() if self.version.get() in VERSIONS else "English Subtitles",
            "default_quality": self.quality.get() if self.quality.get() in QUALITIES else "1080p",
            "source": self._current_source(),
        })
        save_config(self.config_data)

    def _refresh_arcs(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A download is in progress. Cancel it first.")
            return

        src = self._current_source()
        if src == SRC_NYAA:
            self._set_status("Fetching nyaa.si…")
            # Use the canonical One Pace arc list for bucketing — fall back to
            # whatever is already in self.nyaa_arcs (preserves arc names if
            # arcs.json hasn't been refreshed yet).
            arc_titles = [a["title"] for a in self.arcs] or [
                a["title"] for a in self.nyaa_arcs if a["title"] != "Other / Packs"
            ]
            if not arc_titles:
                self.ui_queue.put((
                    "error",
                    "Refresh One Pace first so I know the arc names to group torrents by.",
                ))
                return

            def task():
                try:
                    bucketed = refresh_nyaa_from_web(arc_titles)
                    total = sum(len(b["torrents"]) for b in bucketed)
                    self.ui_queue.put(("nyaa_arcs", bucketed))
                    self.ui_queue.put((
                        "log",
                        f"Refreshed {len(bucketed)} arc buckets ({total} torrents) from nyaa.si.",
                    ))
                    self.ui_queue.put(("status", "Refreshed."))
                except Exception as e:
                    self.ui_queue.put(("error", f"Refresh failed: {e}"))

            threading.Thread(target=task, daemon=True).start()
            return

        self._set_status("Fetching onepace.net…")

        def task():
            try:
                arcs = refresh_arcs_from_web()
                self.ui_queue.put(("arcs", arcs))
                self.ui_queue.put(("log", f"Refreshed {len(arcs)} arcs."))
                self.ui_queue.put(("status", "Refreshed."))
            except Exception as e:
                self.ui_queue.put(("error", f"Refresh failed: {e}"))

        threading.Thread(target=task, daemon=True).start()

    def _open_magnet(self, magnet: str, title: str = "") -> None:
        """Hand a magnet link to the OS default torrent handler.
        Falls back to clipboard if no handler is registered."""
        try:
            os.startfile(magnet)  # type: ignore[attr-defined]  # Windows ShellExecuteW
            self._log(f"Sent to torrent client: {title or 'magnet'}")
            self._set_status("Magnet sent to your torrent client.")
        except (OSError, AttributeError):
            # No registered magnet handler (or non-Windows during dev).
            self.clipboard_clear()
            self.clipboard_append(magnet)
            self.update()  # ensure clipboard survives app close on Win
            messagebox.showinfo(
                "No torrent client found",
                "Magnet copied to clipboard.\n\n"
                "Install qBittorrent (or another torrent client), open it, "
                "and paste this magnet — it will start downloading.",
            )

    def _start_download_one(self, arc: dict, album_id: str, version: str, quality: str) -> None:
        self._persist_settings()
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A download is already in progress.")
            return
        dest = Path(self.save_dir.get())
        dest.mkdir(parents=True, exist_ok=True)
        self.cancel_evt.clear()
        self._set_cancel_visible(True)
        self._log(f"Starting: {arc['title']} — {version} {quality}")

        def task():
            try:
                Downloader(
                    album_id, dest,
                    on_status=lambda s: self.ui_queue.put(("status", s)),
                    on_progress=self._on_progress,
                    on_log=lambda m: self.ui_queue.put(("log", m)),
                    cancel_evt=self.cancel_evt,
                ).run()
                self.ui_queue.put(("status", f"Done: {arc['title']}"))
                self.ui_queue.put(("done", None))
            except DownloadCancelled:
                self.ui_queue.put(("log", "Cancelled."))
                self.ui_queue.put(("status", "Cancelled."))
                self.ui_queue.put(("done", None))
            except Exception as e:
                self.ui_queue.put(("error", str(e)))
                self.ui_queue.put(("done", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _send_all_nyaa_magnets(self) -> None:
        """Open the top magnet for every Nyaa arc bucket sequentially."""
        magnets = [
            (a["title"], a["torrents"][0]["magnet"])
            for a in self.nyaa_arcs
            if a.get("torrents")
        ]
        if not magnets:
            messagebox.showinfo("Nothing to send", "No Nyaa torrents loaded yet.")
            return
        if not messagebox.askyesno(
            "Send all magnets to torrent client",
            f"This will open {len(magnets)} magnet links in your default torrent "
            "client (qBittorrent / uTorrent / etc.).\n\n"
            "Make sure your client is running. Continue?",
        ):
            return

        # Stagger calls so the OS doesn't drop rapid-fire ShellExecute requests.
        self._log(f"Sending {len(magnets)} magnets to torrent client…")

        def step(i: int) -> None:
            if i >= len(magnets):
                self._set_status(f"Sent {len(magnets)} magnets.")
                self._log("All magnets dispatched.")
                return
            title, magnet = magnets[i]
            try:
                os.startfile(magnet)  # type: ignore[attr-defined]
                self._set_status(f"Sent {i + 1}/{len(magnets)}: {title}")
            except (OSError, AttributeError) as e:
                self._log(f"  Failed on {title}: {e}")
            self.after(250, lambda: step(i + 1))

        step(0)

    def _download_all(self) -> None:
        self._persist_settings()
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A download is already in progress.")
            return

        src = self._current_source()
        if src == SRC_NYAA:
            self._send_all_nyaa_magnets()
            return

        plan: list[tuple[dict, str, str, str]] = []

        if src == SRC_MUHN_PACE:
            if not self.muhn_arcs:
                return
            total_gb = sum(a.get("total_bytes", 0) for a in self.muhn_arcs) / 1024**3
            if not messagebox.askyesno(
                "Download all Muhn Pace arcs",
                f"Queue every Muhn Pace arc — {len(self.muhn_arcs)} albums, "
                f"about {total_gb:.0f} GB total. Continue?"
            ):
                return
            for arc in self.muhn_arcs:
                plan.append(({"title": arc["title"]}, arc["album_id"], "English Dub", "muhn"))
        else:
            if not self.arcs:
                return
            if not messagebox.askyesno(
                "Download all arcs",
                "This will queue every arc using your selected Version + Quality "
                "(falling back per-arc if missing). It can be 100+ GB. Continue?"
            ):
                return
            version = self.version.get()
            quality = self.quality.get()
            for arc in self.arcs:
                v, q, aid, note = self._resolve_album(arc, version, quality)
                if aid:
                    plan.append((arc, aid, v, q))

        dest = Path(self.save_dir.get())
        dest.mkdir(parents=True, exist_ok=True)
        self.cancel_evt.clear()
        self._set_cancel_visible(True)

        self._log(f"Queue: {len(plan)} arcs.")

        def task():
            for i, (arc, album_id, v, q) in enumerate(plan, 1):
                if self.cancel_evt.is_set():
                    break
                self.ui_queue.put(("log", f"[{i}/{len(plan)}] {arc['title']} — {v} {q}"))
                try:
                    Downloader(
                        album_id, dest,
                        on_status=lambda s, a=arc: self.ui_queue.put(
                            ("status", f"{a['title']}: {s}")),
                        on_progress=self._on_progress,
                        on_log=lambda m: self.ui_queue.put(("log", m)),
                        cancel_evt=self.cancel_evt,
                    ).run()
                except DownloadCancelled:
                    self.ui_queue.put(("log", "Cancelled."))
                    break
                except Exception as e:
                    self.ui_queue.put(("log", f"  ERROR on {arc['title']}: {e}"))
            self.ui_queue.put(("status", "All done." if not self.cancel_evt.is_set() else "Cancelled."))
            self.ui_queue.put(("done", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel_evt.set()
        self._log("Cancel requested…")

    def _on_progress(self, frac: float, speed: str, idx: int, total: int, bytes_now: int) -> None:
        self.ui_queue.put(("progress", (frac, speed, idx, total, bytes_now)))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "progress":
                    frac, speed, idx, total, bytes_now = payload
                    self.progress_var.set(min(max(frac, 0.0), 1.0))
                    self._set_status(
                        f"[{idx}/{total}]  {fmt_bytes(bytes_now)}  •  {speed}"
                    )
                elif kind == "log":
                    self._log(payload)
                elif kind == "arcs":
                    self.arcs = payload
                    self._refresh_arc_rows()
                elif kind == "nyaa_arcs":
                    self.nyaa_arcs = payload
                    self._refresh_arc_rows()
                elif kind == "error":
                    self._log("ERROR: " + payload)
                    messagebox.showerror("Error", payload)
                elif kind == "done":
                    self.cancel_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(80, self._drain_ui_queue)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # ======================================================================
    # v2 methods — these definitions appear after the legacy v1 methods
    # above and override them via Python's last-definition-wins semantics.
    # The v1 methods are inert dead code (they reference widgets that
    # v2's _build_ui never creates).
    # ======================================================================

    def _apply_icon(self) -> None:
        """Apply window icon. Skips silently if assets are missing (dev runs)."""
        if BUNDLED_ICON_ICO.exists():
            try:
                self.iconbitmap(default=str(BUNDLED_ICON_ICO))
            except tk.TclError:
                pass
        self._app_icon = None
        if BUNDLED_ICON_PNG.exists():
            try:
                self._app_icon = tk.PhotoImage(file=str(BUNDLED_ICON_PNG))
                self.iconphoto(True, self._app_icon)
            except tk.TclError:
                pass

    # ------------------------------------------------------- UI scaffolding -

    def _build_ui(self) -> None:
        # ---- HEADER ----
        # Header block + thin gold underline strip below it so the brand
        # accent threads into the body, not just the top.
        header_wrap = ctk.CTkFrame(self, fg_color="transparent")
        header_wrap.pack(fill="x", side="top")
        header = ctk.CTkFrame(header_wrap, height=72, corner_radius=0,
                              fg_color=HEADER_BG)
        header.pack(fill="x")
        header.pack_propagate(False)
        # 2px gold rule under the header
        ctk.CTkFrame(header_wrap, height=2, corner_radius=0,
                     fg_color=HEADER_ACCENT).pack(fill="x")

        if self._app_icon is not None:
            try:
                hdr_img = self._app_icon.subsample(6, 6)
                self._hdr_img_ref = hdr_img
                tk.Label(header, image=hdr_img, bg=HEADER_BG, bd=0).pack(
                    side="left", padx=(SP_LG, SP_MD), pady=SP_MD)
            except tk.TclError:
                pass

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.pack(side="left", fill="y", pady=SP_MD)
        ctk.CTkLabel(title_block, text="One Pace Downloader",
                     font=(FAMILY, 18, "bold"),
                     text_color=HEADER_FG).pack(anchor="w")
        # Tagline in muted cream — gold is reserved for structural accents
        ctk.CTkLabel(title_block,
                     text="Pick an arc, tick the episodes you want, hit Download.",
                     font=F_SM,
                     text_color="#C4B89D").pack(anchor="w")

        btn_box = ctk.CTkFrame(header, fg_color="transparent")
        btn_box.pack(side="right", padx=SP_XL, pady=SP_MD)
        # Header buttons read as nav controls — transparent with subtle hover,
        # not solid slabs.
        ctk.CTkButton(
            btn_box, text="DNS", width=80, height=H_SM + 4,
            fg_color="transparent",
            border_width=1, border_color="#2C3D5C",
            hover_color="#1F2A44",
            text_color=HEADER_FG, font=F_SM,
            command=self._open_dns_panel,
        ).pack(side="left", padx=SP_XS)
        # The Refresh button is held by reference so the background
        # update-check can re-style it when new content is detected
        # (see _check_for_updates_async).
        self._refresh_btn = ctk.CTkButton(
            btn_box, text="Refresh", width=110, height=H_SM + 4,
            fg_color="transparent",
            border_width=1, border_color="#2C3D5C",
            hover_color="#1F2A44",
            text_color=HEADER_FG, font=F_SM,
            command=self._refresh_all,
        )
        self._refresh_btn.pack(side="left", padx=SP_XS)
        ctk.CTkButton(
            btn_box, text="Settings", width=110, height=H_SM + 4,
            fg_color="transparent",
            border_width=1, border_color="#2C3D5C",
            hover_color="#1F2A44",
            text_color=HEADER_FG, font=F_SM,
            command=self._open_settings_panel,
        ).pack(side="left", padx=SP_XS)

        # ---- SOURCE TAB STRIP ----
        # Pill-style segmented control sitting in a soft container — the
        # selected tab pops as a rounded ribbon-red pill, the others blend
        # into the strip until hovered.
        tab_row = ctk.CTkFrame(self, fg_color="transparent")
        tab_row.pack(fill="x", padx=SP_MD, pady=(SP_MD, SP_XS))
        self._tab_labels = self._compute_tab_labels()
        self._tab_key_by_label = {v: k for k, v in self._tab_labels.items()}

        # Outer pill frame so the segmented button reads as a single
        # contained control rather than three loose buttons on the panel.
        tab_pill = ctk.CTkFrame(
            tab_row,
            fg_color=("#DDE1E8", "#1F2A3D"),
            corner_radius=20,
        )
        tab_pill.pack(side="left", padx=(0, SP_LG))
        self.source_tabs = ctk.CTkSegmentedButton(
            tab_pill,
            values=list(self._tab_labels.values()),
            command=self._on_source_change,
            font=F_BOLD_SM,
            height=H_MD,
            corner_radius=18,
            border_width=0,
            fg_color=("#DDE1E8", "#1F2A3D"),
            selected_color=PRIMARY,
            selected_hover_color=PRIMARY_HOVER,
            unselected_color=("#DDE1E8", "#1F2A3D"),
            unselected_hover_color=("#CFD4DB", "#2C3D5C"),
            text_color=("gray25", "gray85"),
            text_color_disabled=("gray50", "gray45"),
        )
        self.source_tabs.pack(padx=4, pady=4)
        self.source_tabs.set(self._tab_labels[self.current_source])

        # Small colored dot beside the blurb to match the active source
        info_wrap = ctk.CTkFrame(tab_row, fg_color="transparent")
        info_wrap.pack(side="left", fill="x", expand=True, padx=(0, SP_LG))
        self.source_dot = ctk.CTkLabel(
            info_wrap, text="●", font=F_BASE,
            text_color=PRIMARY, width=14,
        )
        self.source_dot.pack(side="left")
        self.source_info = ctk.CTkLabel(
            info_wrap, text="", anchor="w",
            font=F_SM,
            text_color=TEXT_MUTED,
            wraplength=720, justify="left",
        )
        self.source_info.pack(side="left", fill="x", expand=True,
                              padx=(SP_SM, 0))

        # ---- BODY: three columns ----
        # Body is a soft tinted panel; each column sits ON it as a raised
        # card. This gives the arc/episode lists a real surface so rows
        # don't float on the window background.
        body = ctk.CTkFrame(self, fg_color=SURFACE_PANEL)
        body.pack(fill="both", expand=True, padx=SP_SM, pady=(SP_XS, 0))
        body.grid_columnconfigure(0, weight=0, minsize=230)
        body.grid_columnconfigure(1, weight=1, minsize=360)
        body.grid_columnconfigure(2, weight=1, minsize=360)
        body.grid_rowconfigure(0, weight=1)

        # Column 1: arcs — raised card with gentle corner radius
        col1 = ctk.CTkFrame(body, fg_color=SURFACE_CARD,
                            corner_radius=RADIUS_LG)
        col1.grid(row=0, column=0, sticky="nsew",
                  padx=(SP_XS, SP_XS), pady=SP_SM)
        col1.grid_columnconfigure(0, weight=1)
        col1.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(col1, text="ARCS", anchor="w",
                     font=F_BOLD_SM,
                     text_color=TEXT_MUTED).grid(
            row=0, column=0, sticky="ew",
            padx=SP_SM, pady=(SP_SM, SP_XS))
        search_entry = ctk.CTkEntry(
            col1, textvariable=self.search_var,
            placeholder_text="Search arcs… (Esc to clear)",
            height=H_SM, font=F_SM)
        search_entry.grid(row=1, column=0, sticky="ew",
                          padx=SP_SM, pady=(0, SP_SM))
        search_entry.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.arc_scroll = ctk.CTkScrollableFrame(
            col1, label_text="", fg_color="transparent")
        self.arc_scroll.grid(row=2, column=0, sticky="nsew",
                             padx=0, pady=(0, SP_SM))

        # Column 2: episodes — raised card matching column 1
        col2 = ctk.CTkFrame(body, fg_color=SURFACE_CARD,
                            corner_radius=RADIUS_LG)
        col2.grid(row=0, column=1, sticky="nsew",
                  padx=SP_XS, pady=SP_SM)
        col2.grid_columnconfigure(0, weight=1)
        col2.grid_rowconfigure(2, weight=1)
        self.episodes_header = ctk.CTkLabel(col2, text="EPISODES",
                                            anchor="w",
                                            font=F_BOLD_SM,
                                            text_color=TEXT_MUTED)
        self.episodes_header.grid(row=0, column=0, sticky="ew",
                                  padx=SP_SM, pady=(SP_SM, SP_XS))
        ep_tools = ctk.CTkFrame(col2, fg_color="transparent")
        ep_tools.grid(row=1, column=0, sticky="ew",
                      padx=SP_SM, pady=(0, SP_SM))
        self.select_all_btn = ctk.CTkButton(
            ep_tools, text="Select all", width=120, height=H_SM,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            font=F_SM,
            command=self._toggle_select_all)
        self.select_all_btn.pack(side="left")
        self.nyaa_view_toggle = ctk.CTkSegmentedButton(
            ep_tools,
            values=["Individual", "Packs"],
            command=self._on_nyaa_view_change,
            height=H_SM,
            font=F_SM,
            selected_color=PRIMARY,
            selected_hover_color=PRIMARY_HOVER,
        )
        self.download_selected_btn = ctk.CTkButton(
            ep_tools, text="Download selected", height=H_SM,
            font=F_BOLD_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=self._download_selected_episodes)
        self.download_selected_btn.pack(side="right")
        self.selection_size_lbl = ctk.CTkLabel(
            ep_tools, text="", font=F_XS, text_color=TEXT_MUTED
        )
        self.selection_size_lbl.pack(side="right", padx=(0, 12))
        self.episode_scroll = ctk.CTkScrollableFrame(
            col2, label_text="", fg_color="transparent")
        self.episode_scroll.grid(row=2, column=0, sticky="nsew",
                                 padx=0, pady=(0, SP_SM))

        # Column 3: sources / download / idle — raised card matching the others
        col3 = ctk.CTkFrame(body, fg_color=SURFACE_CARD,
                            corner_radius=RADIUS_LG)
        col3.grid(row=0, column=2, sticky="nsew",
                  padx=(SP_XS, SP_XS), pady=SP_SM)
        col3.grid_columnconfigure(0, weight=1)
        col3.grid_rowconfigure(1, weight=1)
        self.source_header = ctk.CTkLabel(col3, text="DETAILS",
                                          anchor="w",
                                          font=F_BOLD_SM,
                                          text_color=TEXT_MUTED)
        self.source_header.grid(row=0, column=0, sticky="ew",
                                padx=SP_SM, pady=(SP_SM, SP_XS))
        self.source_scroll = ctk.CTkScrollableFrame(
            col3, label_text="", fg_color="transparent")
        self.source_scroll.grid(row=1, column=0, sticky="nsew",
                                padx=0, pady=(0, SP_SM))

        # Speed up scrolling in columns
        for sf in (self.arc_scroll, self.episode_scroll, self.source_scroll):
            sf._on_mousewheel = lambda event, f=sf: f._canvas.yview_scroll(int(-3 * (event.delta / 120)), "units")

        # ---- FOOTER: save folder + progress + status ----
        footer = ctk.CTkFrame(self, corner_radius=0,
                              fg_color=SURFACE_PANEL,
                              height=110)
        footer.pack(fill="x", side="bottom")

        fr1 = ctk.CTkFrame(footer, fg_color="transparent")
        fr1.pack(fill="x", padx=SP_MD, pady=(SP_MD, SP_XS))
        save_entry = ctk.CTkEntry(
            fr1, textvariable=self.save_dir,
            height=H_SM, font=F_SM,
            placeholder_text="Save folder…",
        )
        save_entry.pack(side="left", fill="x", expand=True, padx=(0, SP_XS))
        save_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        save_entry.bind("<Return>", lambda _e: self._persist_settings())
        ctk.CTkButton(
            fr1, text="Browse", width=80, height=H_SM,
            font=F_SM, fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            command=self._pick_folder,
        ).pack(side="left", padx=(0, SP_XS))
        ctk.CTkButton(
            fr1, text="Open", width=70, height=H_SM,
            font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=self._open_folder,
        ).pack(side="left", padx=(0, SP_XS))
        # Cancel button only mounts via _set_cancel_visible while running
        self._cancel_btn_parent = fr1
        self.cancel_btn = ctk.CTkButton(
            fr1, text="Cancel", width=80, height=H_SM,
            font=F_SM,
            fg_color=DANGER, hover_color=DANGER_HOVER,
            command=self._cancel)

        # Thin branded progress bar — slim secondary indicator. Hidden
        # entirely when the right-column hero panel is showing detailed
        # progress (no need for two bars).
        fr2 = ctk.CTkFrame(footer, fg_color="transparent")
        fr2.pack(fill="x", padx=SP_MD, pady=SP_XS)
        self.progress = ctk.CTkProgressBar(
            fr2, height=6,
            progress_color=PRIMARY,
            fg_color=("#E8E8EC", "#1F2733"),
            border_width=0,
        )
        self.progress.set(0)
        self.progress.pack(fill="x")

        fr3 = ctk.CTkFrame(footer, fg_color="transparent")
        fr3.pack(fill="x", padx=SP_MD, pady=(SP_XS, SP_SM))
        ctk.CTkLabel(fr3, textvariable=self.status_var,
                     font=F_SM, anchor="w",
                     text_color=TEXT_MUTED).pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(
            fr3, text="Log ▾", width=70, command=self._toggle_log,
            fg_color="transparent",
            hover_color=SURFACE_HOVER,
            text_color=LINK,
            font=F_SM,
        ).pack(side="right")

        # Collapsible log panel (hidden by default; toggled by Log button)
        self.log_panel = ctk.CTkFrame(self, height=140)
        self.log_textbox = ctk.CTkTextbox(
            self.log_panel, height=120, font=("Consolas", 9))
        self.log_textbox.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_textbox.configure(state="disabled")

    # ----------------------------------------------------- source switch ---

    def _compute_tab_labels(self) -> dict:
        """Return {source_key: tab_label} with arc counts baked in."""
        out: dict[str, str] = {}
        for key, base in _SRC_LABEL.items():
            count = sum(1 for a in self.index.get("arcs", [])
                        if self._arc_has_source(a, key))
            out[key] = f"{base} · {count}" if count else base
        return out

    def _on_source_change(self, label: str) -> None:
        new_src = self._tab_key_by_label.get(label, _LABEL_SRC.get(label, "onepace"))
        if new_src == self.current_source:
            return
        self.current_source = new_src
        self.config_data["source"] = new_src
        save_config(self.config_data)
        # Reset selection — different source means different arc/ep universe
        self.selected_arc = None
        self.selected_ep_for_detail = None
        self.ep_check_vars.clear()
        self._refresh_nyaa_view_toggle()
        self._render_arc_list()
        self._render_episode_list()
        self._render_source_panel()

    def _on_nyaa_view_change(self, label: str) -> None:
        new_view = "packs" if label == "Packs" else "eps"
        if new_view == self.nyaa_view:
            return
        self.nyaa_view = new_view
        self.selected_ep_for_detail = None
        self.ep_check_vars.clear()
        self._render_episode_list()
        self._render_source_panel()

    def _refresh_nyaa_view_toggle(self) -> None:
        """Show the Individual/Packs toggle on the Nyaa tab, hide elsewhere.
        Auto-pick a sensible default whenever the arc changes: jump to Packs
        only when the new arc has no per-episode torrents but does have packs;
        otherwise reset to Individual (the more common case)."""
        if self.current_source == "nyaa" and self.selected_arc is not None:
            cov = self._nyaa_arc_coverage(self.selected_arc)
            if cov["per_ep_covered"] == 0 and cov["pack_count"] > 0:
                self.nyaa_view = "packs"
            else:
                self.nyaa_view = "eps"

        if self.current_source == "nyaa":
            if not self.nyaa_view_toggle.winfo_ismapped():
                self.nyaa_view_toggle.pack(side="left", padx=(12, 0))
            self.nyaa_view_toggle.set(
                "Packs" if self.nyaa_view == "packs" else "Individual")
        else:
            if self.nyaa_view_toggle.winfo_ismapped():
                self.nyaa_view_toggle.pack_forget()

    def _arc_has_source(self, arc: dict, source: str) -> bool:
        """True if the arc has at least one episode or pack from the given source."""
        for ep in arc.get("episodes", []):
            for s in ep.get("sources", []):
                if s.get("kind") == source:
                    return True
        # Nyaa's arc_packs predate the kind field — treat them all as nyaa.
        # Usenet packs are tagged kind="usenet" by the merger.
        for p in arc.get("arc_packs", []):
            if source == "nyaa" and not p.get("kind"):
                return True
            if p.get("kind") == source:
                return True
        return False

    def _arc_source_counts(self, arc: dict, source: str) -> tuple[int, int]:
        """(episodes_with_this_source, packs_for_this_source)."""
        ep_count = sum(
            1 for ep in arc.get("episodes", [])
            if any(s.get("kind") == source for s in ep.get("sources", []))
        )
        # Nyaa packs predate the kind field — treat un-tagged packs as nyaa.
        # Usenet packs carry kind="usenet" from the merger.
        if source == "nyaa":
            pack_count = sum(1 for p in arc.get("arc_packs", [])
                              if not p.get("kind"))
        else:
            pack_count = sum(1 for p in arc.get("arc_packs", [])
                              if p.get("kind") == source)
        return ep_count, pack_count

    def _arc_saved_count(self, arc: dict) -> tuple[int, int]:
        """(saved_episodes, total_episodes_with_active_source). Used to show
        per-arc download progress badges. Nyaa and Usenet don't track saves
        locally (magnets / NZBs are handled by the user's torrent / Usenet
        client) so both always return (0, 0)."""
        if self.current_source in ("nyaa", "usenet"):
            return 0, 0
        eps = [ep for ep in arc.get("episodes", [])
               if any(s.get("kind") == self.current_source
                      for s in ep.get("sources", []))]
        saved = sum(1 for ep in eps if self._is_episode_saved(ep, arc))
        return saved, len(eps)

    def _compute_arc_meta(self, arc: dict) -> str:
        """Render the right-aligned meta text for an arc row. Surfaces
        download progress (✓ saved/total) when the user has any of the arc
        downloaded; otherwise shows the source's content counts."""
        if self.current_source == "nyaa":
            cov = self._nyaa_arc_coverage(arc)
            bits = []
            if cov["total"]:
                bits.append(f"{cov['per_ep_covered']}/{cov['total']} eps")
            if cov["pack_count"]:
                bits.append("+ pack" if cov["pack_count"] == 1
                            else f"+ {cov['pack_count']} packs")
            return " ".join(bits) if bits else "—"
        ep_count, pack_count = self._arc_source_counts(arc, self.current_source)
        saved, total = self._arc_saved_count(arc)
        bits = []
        if total and saved:
            bits.append(f"✓ {saved}/{total}")
        elif ep_count:
            bits.append(f"{ep_count} ep" + ("s" if ep_count != 1 else ""))
        if pack_count:
            bits.append(f"{pack_count} pack" + ("s" if pack_count != 1 else ""))
        return " · ".join(bits) if bits else ""

    def _refresh_arc_meta_labels(self) -> None:
        """Re-compute and re-apply the meta text on every arc row in place,
        so download progress badges update without rebuilding the arc list."""
        for arc in self.index.get("arcs", []):
            widgets = self._arc_row_widgets.get(arc.get("title"))
            if not widgets:
                continue
            meta_lbl = widgets.get("meta_lbl")
            if not meta_lbl:
                continue
            meta_lbl.configure(text=self._compute_arc_meta(arc))

    # --------------------------------------------- Plex / Jellyfin layout --

    def _arc_index_for_title(self, title: str) -> int | None:
        """0-based canonical arc index for a given arc title (Romance Dawn=0,
        ..., Egghead=35). Used to derive the Plex/Jellyfin Season N folder."""
        for i, a in enumerate(self.index.get("arcs", [])):
            if a.get("title") == title:
                return i
        return None

    @staticmethod
    def _build_nfo_xml(season: int, ep_num: int, title: str, plot: str) -> str:
        """Render a Plex/Jellyfin episodedetails.nfo for a single episode.
        Mirrors the schema SpykerNZ ships, so existing Plex/Jellyfin scrapers
        configured for One Pace pick up our downloads without re-configuration."""
        import xml.sax.saxutils as su
        t = su.escape(title or f"Episode {ep_num:02d}")
        p = su.escape(plot or "")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<episodedetails>\n'
            f'  <title>{t}</title>\n'
            '  <showtitle>One Pace</showtitle>\n'
            f'  <season>{season}</season>\n'
            f'  <episode>{ep_num}</episode>\n'
            f'  <plot>{p}</plot>\n'
            '</episodedetails>\n'
        )

    def _organize_for_plex_layout(
        self,
        downloaded_subfolder: Path,
        file_ids: set[str],
        arc: dict,
        log,
    ) -> None:
        """If 'Organize for media server' is enabled, walk the just-downloaded
        files in `downloaded_subfolder` and move each into Plex/Jellyfin layout:

            <save_dir>/One Pace/Season {N}/One Pace - sNNeMM - {title}.{ext}

        Also writes a sibling .nfo with title + plot when canonical metadata
        exists. Best-effort: any per-file failure is logged and we keep going.
        Safe to call from a worker thread — uses the provided `log` callback
        for status, not `self._log` directly. No-op if the toggle is off or
        the arc isn't recognized."""
        if not self.config_data.get("organize_for_media_server", False):
            return
        arc_title = arc.get("title", "")
        arc_idx = self._arc_index_for_title(arc_title)
        if arc_idx is None:
            log(f"  [warn] Plex organize: arc {arc_title!r} not found in index")
            return

        season = arc_idx + 1
        dest_root = downloaded_subfolder.parent
        season_dir = dest_root / "One Pace" / f"Season {season}"
        try:
            season_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log(f"  [warn] Couldn't create {season_dir}: {e}")
            return

        # file_id -> (episode dict, source dict) for episodes we just pulled
        ep_for_file: dict[str, tuple[dict, dict]] = {}
        for ep in arc.get("episodes", []):
            for src in ep.get("sources", []):
                if src.get("file_id") in file_ids:
                    ep_for_file[src["file_id"]] = (ep, src)

        if not ep_for_file:
            log(f"  [warn] Plex organize: no matching episodes found "
                f"for file_ids in {arc_title}")
            return

        moved = 0
        for fid, (ep, src) in ep_for_file.items():
            raw_name = src.get("filename", "")
            if not raw_name:
                continue
            src_path = downloaded_subfolder / sanitize_filename(raw_name)
            if not src_path.exists():
                # Pixeldrain may serve a slightly different filename than
                # what's indexed. Try matching by the CRC hash that One
                # Pace embeds in every filename, e.g. [565515E3].
                hash_match = re.search(r"\[([0-9A-Fa-f]{6,8})\]", raw_name)
                found = False
                if hash_match:
                    crc = hash_match.group(1).upper()
                    for p in downloaded_subfolder.glob("*"):
                        if (p.is_file()
                                and not p.name.endswith(".part")
                                and crc in p.name.upper()):
                            src_path = p
                            found = True
                            break
                if not found:
                    # Last resort: single-file subfolder
                    candidates = [
                        p for p in downloaded_subfolder.glob("*")
                        if p.is_file() and not p.name.endswith(".part")
                    ]
                    if len(candidates) == 1:
                        src_path = candidates[0]
                    else:
                        log(f"  [warn] Plex organize: file not found — "
                            f"{sanitize_filename(raw_name)}")
                        continue
            ext = src_path.suffix
            ep_num = ep.get("num", 0)
            canonical = ep.get("canonical_title", "")
            title_part = sanitize_filename(canonical) if canonical else f"Episode {ep_num:02d}"
            new_name = f"One Pace - s{season:02d}e{ep_num:02d} - {title_part}{ext}"
            target = season_dir / new_name
            # On Windows, freshly-downloaded files may be briefly locked
            # by Defender / Search Indexer.  Retry a few times with a
            # short pause so we don't silently skip the organize.
            rename_ok = False
            for _attempt in range(6):
                try:
                    if target.exists():
                        target.unlink()
                    src_path.rename(target)
                    rename_ok = True
                    break
                except PermissionError:
                    time.sleep(0.5)
                except Exception as e:
                    log(f"  [warn] Couldn't move "
                        f"{src_path.name} -> {target.name}: {e}")
                    break
            if not rename_ok:
                if _attempt == 5:
                    log(f"  [warn] File locked, skipping: "
                        f"{src_path.name}")
                continue
            moved += 1
            if canonical or ep.get("plot"):
                try:
                    target.with_suffix(".nfo").write_text(
                        self._build_nfo_xml(
                            season, ep_num, canonical, ep.get("plot", "")),
                        encoding="utf-8")
                except Exception as e:
                    log(f"  [warn] Couldn't write .nfo for {target.name}: {e}")

        if moved:
            log(f"  Organized {moved} file(s) into "
                f"One Pace/Season {season}/")
            # If the original subfolder is now empty, prune it
            try:
                if downloaded_subfolder.exists() and not any(
                        downloaded_subfolder.iterdir()):
                    downloaded_subfolder.rmdir()
            except OSError:
                pass

    def _retroactive_plex_organize(self) -> None:
        """Scan the save folder and organize any previously-downloaded files
        into Plex/Jellyfin layout. Called when the user enables the media-
        server setting so files downloaded before the toggle are covered."""
        save = Path(self.save_dir.get() or "")
        if not save.exists() or not save.is_dir():
            return

        # Build a lookup: sanitized_filename -> (arc_idx, ep_num, canonical,
        # plot, raw_filename) from the full index.
        file_map: dict[str, tuple[int, int, str, str, str]] = {}
        for arc_idx, arc in enumerate(self.index.get("arcs", [])):
            for ep in arc.get("episodes", []):
                for src in ep.get("sources", []):
                    fn = src.get("filename")
                    if not fn:
                        continue
                    key = sanitize_filename(fn)
                    file_map[key] = (
                        arc_idx,
                        ep.get("num", 0),
                        ep.get("canonical_title", ""),
                        ep.get("plot", ""),
                        fn,
                    )

        plex_dir = save / "One Pace"
        moved = 0
        prunable: set[Path] = set()
        for dirpath, _, filenames in os.walk(save):
            dp = Path(dirpath)
            # Skip files already in the One Pace/ tree
            try:
                dp.relative_to(plex_dir)
                continue
            except ValueError:
                pass
            for fn in filenames:
                if fn.endswith(".part") or fn.endswith(".nfo"):
                    continue
                info = file_map.get(fn)
                if info is None:
                    continue
                arc_idx, ep_num, canonical, plot, _raw = info
                season = arc_idx + 1
                season_dir = save / "One Pace" / f"Season {season}"
                try:
                    season_dir.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                src_path = dp / fn
                ext = src_path.suffix
                title_part = (sanitize_filename(canonical)
                              if canonical else f"Episode {ep_num:02d}")
                new_name = (f"One Pace - s{season:02d}e{ep_num:02d}"
                            f" - {title_part}{ext}")
                target = season_dir / new_name
                rename_ok = False
                for _attempt in range(6):
                    try:
                        if target.exists():
                            target.unlink()
                        src_path.rename(target)
                        rename_ok = True
                        break
                    except PermissionError:
                        time.sleep(0.5)
                    except Exception:
                        break
                if not rename_ok:
                    continue
                moved += 1
                prunable.add(dp)
                if canonical or plot:
                    try:
                        target.with_suffix(".nfo").write_text(
                            self._build_nfo_xml(
                                season, ep_num, canonical, plot),
                            encoding="utf-8")
                    except Exception:
                        pass

        # Prune empty subfolders left behind
        for d in prunable:
            try:
                if d != save and d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

        if moved:
            self._log(f"Organized {moved} existing file(s) into "
                      f"Plex/Jellyfin layout.")
            self._refresh_downloaded_files()

    # ----------------------------------------------------- coverage stats --
    # Nyaa torrents are sourced from the same One Pace releases that Pixeldrain
    # hosts — many users believe torrents are higher-quality, so we surface
    # coverage explicitly: per-arc and globally.

    def _nyaa_arc_coverage(self, arc: dict) -> dict:
        """Return per-ep / pack coverage info for an arc against the Nyaa source.
        'total' counts only canonical One Pace episodes (Muhn-only eps don't
        get a quality boost from torrents since the torrents are One Pace cuts).
        Arc-packs are treated as covering every canonical episode of the arc."""
        # Canonical = episodes that have at least one One Pace source row
        canonical = [
            ep for ep in arc.get("episodes", [])
            if any(s.get("kind") == "onepace" for s in ep.get("sources", []))
        ]
        total = len(canonical)
        per_ep_covered = sum(
            1 for ep in canonical
            if any(s.get("kind") == "nyaa" for s in ep.get("sources", []))
        )
        packs = arc.get("arc_packs", [])
        # Any pack is assumed to cover the whole arc — pack titles like
        # "[One Pace][1-7] Romance Dawn [1080p]" follow that convention.
        full_arc_covered = total if packs else per_ep_covered
        return {
            "per_ep_covered": per_ep_covered,
            "total": total,
            "pack_count": len(packs),
            "full_arc_covered": full_arc_covered,
        }

    def _compute_global_coverage(self) -> dict:
        """Compute the headline stat shown in the Nyaa source blurb. Cached
        after first call since the index doesn't change at runtime."""
        if getattr(self, "_global_cov_cache", None) is not None:
            return self._global_cov_cache
        per_ep = 0
        total = 0
        pack_only = 0
        uncovered = 0
        arcs_with_packs = 0
        for arc in self.index.get("arcs", []):
            cov = self._nyaa_arc_coverage(arc)
            total += cov["total"]
            per_ep += cov["per_ep_covered"]
            if cov["pack_count"]:
                arcs_with_packs += 1
                pack_only += cov["total"] - cov["per_ep_covered"]
            else:
                uncovered += cov["total"] - cov["per_ep_covered"]
        self._global_cov_cache = {
            "per_ep": per_ep,
            "pack_only": pack_only,  # eps covered via pack but not per-ep
            "uncovered": uncovered,
            "total": total,
            "arcs_with_packs": arcs_with_packs,
        }
        return self._global_cov_cache

    # ----------------------------------------------------- arc list pane ----

    def _render_arc_list(self, rebuild: bool = False) -> None:
        if not hasattr(self, "_last_arc_list_source") or self._last_arc_list_source != self.current_source:
            rebuild = True
            self._last_arc_list_source = self.current_source

        if rebuild or not self._arc_row_widgets:
            for w in self.arc_scroll.winfo_children():
                w.destroy()
            self._arc_row_widgets.clear()

        self.source_info.configure(text=_SRC_BLURB.get(self.current_source, ""))

        query = self.search_var.get().strip().lower()
        arcs = self.index.get("arcs", [])
        if not arcs:
            ctk.CTkLabel(self.arc_scroll,
                text="No episode_index.json found.\n\nRun:\npython _source/build_episode_index.py",
                justify="left",
                font=("Segoe UI", 9),
                text_color=("gray40", "gray70")).pack(padx=12, pady=12)
            return

        filtered = [a for a in arcs if self._arc_has_source(a, self.current_source)]

        if rebuild or not self._arc_row_widgets:
            for arc in filtered:
                self._build_arc_button(arc)

        any_visible = False
        for arc in filtered:
            title = arc["title"]
            widget_dict = self._arc_row_widgets.get(title)
            if not widget_dict:
                continue
            row = widget_dict["row"]
            
            matches = not query or query in title.lower()
            if matches:
                if not row.winfo_ismapped():
                    row.pack(fill="x", padx=SP_XS, pady=1)
                any_visible = True
            else:
                if row.winfo_ismapped():
                    row.pack_forget()

        if not hasattr(self, "_no_arcs_label") or not self._no_arcs_label.winfo_exists():
            self._no_arcs_label = ctk.CTkLabel(
                self.arc_scroll,
                text="",
                font=("Segoe UI", 9),
                text_color=("gray40", "gray70"),
            )
        
        if not any_visible:
            self._no_arcs_label.configure(
                text=("(no arcs match)" if query
                      else f"(no arcs available from {_SRC_LABEL[self.current_source]})")
            )
            if not self._no_arcs_label.winfo_ismapped():
                self._no_arcs_label.pack(padx=10, pady=10)
        else:
            try:
                if self._no_arcs_label.winfo_ismapped():
                    self._no_arcs_label.pack_forget()
            except Exception:
                pass

    def _build_arc_button(self, arc: dict) -> None:
        title = arc["title"]
        is_selected = (self.selected_arc is not None
                       and self.selected_arc.get("title") == title)

        # Selection = solid ribbon-red bg with white text; non-selected =
        # transparent with hover tint. Single frame keeps Tk geometry sane.
        if is_selected:
            base_color = PRIMARY
            hover_color = PRIMARY
            text_color = ("white", "white")
            meta_text_color = ("#FFE5A8", "#FFE5A8")
            title_font = F_BOLD_BASE
        else:
            base_color = "transparent"
            hover_color = SURFACE_HOVER
            text_color = TEXT
            meta_text_color = TEXT_MUTED
            title_font = F_BASE

        meta = self._compute_arc_meta(arc)

        row = ctk.CTkFrame(self.arc_scroll, fg_color=base_color,
                           corner_radius=RADIUS_SM,
                           height=36)
        row.pack(fill="x", padx=SP_XS, pady=1)
        row.pack_propagate(False)

        title_lbl = ctk.CTkLabel(
            row, text=title, anchor="w",
            font=title_font, text_color=text_color,
        )
        title_lbl.pack(side="left", fill="x", expand=True,
                       padx=(SP_MD, SP_SM))

        meta_lbl = None
        if meta:
            meta_lbl = ctk.CTkLabel(
                row, text=meta, anchor="e",
                font=F_XS, text_color=meta_text_color,
            )
            meta_lbl.pack(side="right", padx=(SP_SM, SP_MD))

        # Stash refs so _update_arc_selection can mutate this row in place
        # instead of triggering a full list re-render on every click.
        self._arc_row_widgets[title] = {
            "row": row, "title_lbl": title_lbl, "meta_lbl": meta_lbl,
        }

        def _click(_e=None, a=arc) -> None:
            self._on_arc_selected(a)

        def _enter(_e=None) -> None:
            is_sel = (self.selected_arc is not None
                      and self.selected_arc.get("title") == title)
            if not is_sel:
                row.configure(fg_color=SURFACE_HOVER)

        def _leave(_e=None) -> None:
            is_sel = (self.selected_arc is not None
                      and self.selected_arc.get("title") == title)
            row.configure(fg_color=PRIMARY if is_sel else "transparent")

        for w in (row, title_lbl) + ((meta_lbl,) if meta_lbl else ()):
            w.bind("<Button-1>", _click)
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
            w.configure(cursor="hand2")

        if len(title) > 24:
            Tooltip(title_lbl, title)
            Tooltip(row, title)

    def _on_arc_selected(self, arc: dict) -> None:
        prev_title = (self.selected_arc.get("title")
                      if self.selected_arc else None)
        new_title = arc["title"]
        self.selected_arc = arc
        self.selected_ep_for_detail = None
        self.ep_check_vars.clear()
        self._refresh_nyaa_view_toggle()
        # Incremental visual update — only the two affected arc rows
        # change colour, instead of destroying and rebuilding all 36.
        self._update_arc_selection(prev_title, new_title)
        self._render_episode_list()
        self._render_source_panel()

    def _update_arc_selection(self, prev_title: str | None,
                              new_title: str | None) -> None:
        """Mutate just the two rows whose selection state changed. Much
        faster than re-rendering the whole arc list on every click."""
        for title, is_sel in ((prev_title, False), (new_title, True)):
            if not title:
                continue
            widgets = self._arc_row_widgets.get(title)
            if not widgets:
                continue
            row = widgets["row"]
            title_lbl = widgets["title_lbl"]
            meta_lbl = widgets["meta_lbl"]
            if is_sel:
                row.configure(fg_color=PRIMARY)
                title_lbl.configure(font=F_BOLD_BASE,
                                    text_color=("white", "white"))
                if meta_lbl:
                    meta_lbl.configure(text_color=("#FFE5A8", "#FFE5A8"))
            else:
                row.configure(fg_color="transparent")
                title_lbl.configure(font=F_BASE, text_color=TEXT)
                if meta_lbl:
                    meta_lbl.configure(text_color=TEXT_MUTED)

    # ------------------------------------------------- episode list pane ---

    def _render_episode_list(self) -> None:
        for w in self.episode_scroll.winfo_children():
            w.destroy()
        self.ep_check_vars.clear()
        self._ep_row_widgets.clear()
        self._refresh_selection_buttons()
        arc = self.selected_arc
        if not arc:
            self.episodes_header.configure(text="Episodes")
            ctk.CTkLabel(
                self.episode_scroll, justify="left",
                text="← Pick an arc on the left.\n\n"
                     "Episodes for that arc will show up here. Tick the\n"
                     "boxes for the ones you want, then click "
                     "'Download selected'.",
                font=("Segoe UI", 10),
                text_color=("gray40", "gray70"),
            ).pack(padx=18, pady=24, anchor="w")
            return
        src = self.current_source

        # Coverage banner (Nyaa tab only) — always at the top regardless of
        # sub-view so users see the headline before the rows.
        if src == "nyaa":
            self._build_nyaa_coverage_banner(arc)

        # Nyaa tab is split into Individual / Packs sub-views.
        if src == "nyaa" and self.nyaa_view == "packs":
            packs = arc.get("arc_packs", [])
            self.episodes_header.configure(
                text=f"Full-arc packs  —  {arc['title']}  ({len(packs)})  •  Nyaa")
            if not packs:
                ctk.CTkLabel(
                    self.episode_scroll, justify="left", anchor="w",
                    text="(no full-arc torrent packs for this arc — "
                         "check the Individual tab for per-episode torrents)",
                    font=("Segoe UI", 9), wraplength=380,
                    text_color=("gray40", "gray70"),
                ).pack(padx=12, pady=12)
                return
            for pack in packs:
                self._build_arc_pack_row(pack)
            return

        # Default path: per-episode rows for the current source
        eps = [
            ep for ep in arc.get("episodes", [])
            if any(s.get("kind") == src for s in ep.get("sources", []))
        ]
        suffix = (" — Individual" if src == "nyaa" else "")
        self.episodes_header.configure(
            text=f"Episodes  —  {arc['title']}  ({len(eps)})  •  {_SRC_LABEL[src]}{suffix}")
        for ep in eps:
            self._build_episode_row(ep)
        self._refresh_selection_buttons()
        if not eps:
            empty_msg = (
                "(no per-episode torrents — flip to Packs above for full-arc options)"
                if src == "nyaa"
                else f"(no {_SRC_LABEL[src]} episodes for this arc)"
            )
            ctk.CTkLabel(
                self.episode_scroll, justify="left", anchor="w",
                text=empty_msg,
                font=("Segoe UI", 9), wraplength=380,
                text_color=("gray40", "gray70"),
            ).pack(padx=12, pady=12)

    def _build_nyaa_coverage_banner(self, arc: dict) -> None:
        cov = self._nyaa_arc_coverage(arc)
        total, per_ep, pack_n = cov["total"], cov["per_ep_covered"], cov["pack_count"]
        if total == 0:
            heading, body = "No data", "This arc has no canonical episode list."
            accent, bg = TEXT_MUTED, ("#F4F5F8", "#1F2733")
        elif per_ep == total:
            heading = "Full coverage"
            body = f"{total}/{total} episodes have per-episode torrents."
            accent, bg = OK, ("#E8F5E9", "#1E3A24")
        elif pack_n and per_ep == 0:
            heading = "Full coverage via pack"
            body = (f"No per-episode torrents, but {pack_n} full-arc pack"
                    f"{'s' if pack_n != 1 else ''} cover"
                    f"{'s' if pack_n == 1 else ''} all {total} episodes.")
            accent, bg = OK, ("#E8F5E9", "#1E3A24")
        elif pack_n:
            heading = "Full coverage"
            body = (f"{per_ep}/{total} per-episode + {pack_n} full-arc pack"
                    f"{'s' if pack_n != 1 else ''} fill the gap.")
            accent, bg = OK, ("#E8F5E9", "#1E3A24")
        else:
            heading = "Partial coverage"
            body = (f"{per_ep}/{total} per-episode, "
                    f"{total - per_ep} not available on Nyaa.")
            accent, bg = WARN, ("#FFF7E0", "#2E2814")

        body_card = ctk.CTkFrame(
            self.episode_scroll, fg_color=bg,
            corner_radius=RADIUS_SM,
        )
        body_card.pack(fill="x", padx=SP_SM, pady=(SP_SM, SP_SM))
        row1 = ctk.CTkFrame(body_card, fg_color="transparent")
        row1.pack(fill="x", padx=SP_MD, pady=(SP_SM, 0))
        ctk.CTkLabel(row1, text="●", font=F_BASE,
                     text_color=accent, width=14).pack(side="left")
        ctk.CTkLabel(row1, text=heading, anchor="w",
                     font=F_BOLD_SM, text_color=accent).pack(
            side="left", padx=(SP_XS, 0))
        ctk.CTkLabel(
            body_card, text=body, anchor="w", justify="left",
            font=F_SM, text_color=TEXT,
            wraplength=400,
        ).pack(fill="x", padx=(SP_XL + SP_XS, SP_MD), pady=(0, SP_SM))

    def _build_episode_row(self, ep: dict) -> None:
        is_active = (self.selected_ep_for_detail == ep["num"])
        # Active row = subtle tinted bg; non-active = transparent with hover.
        # No separate ribbon frame — Tk packs that with fill="y" balloon the
        # row's container.
        row = ctk.CTkFrame(
            self.episode_scroll,
            fg_color=("#FFF6E0", "#2A2010") if is_active else "transparent",
            corner_radius=RADIUS_SM,
            height=32,
        )
        row.pack(fill="x", padx=SP_XS, pady=1)
        row.pack_propagate(False)
        var = ctk.BooleanVar(value=False)
        var.trace_add("write", lambda *_: not getattr(self, "_updating_checks", False) and self._refresh_selection_buttons())
        cb = ctk.CTkCheckBox(
            row, text="", variable=var, width=20,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            border_color=BORDER_STRONG,
        )
        cb.pack(side="left", padx=(SP_SM, SP_SM))
        cb._canvas.bind("<Button-1>", lambda event, num=ep["num"]: self._on_checkbox_clicked(event, num), add="+")
        self.ep_check_vars[ep["num"]] = var

        # Only count sources from the active page
        sources = [s for s in ep.get("sources", [])
                   if s.get("kind") == self.current_source]
        version_pref = self.config_data.get("default_version",
                                            "English Subtitles")
        quality_pref = self.config_data.get("default_quality", "1080p")

        if self.current_source == "onepace":
            best = self._best_source_for(ep, "onepace",
                                         version_pref, quality_pref)
            extras = sorted(
                {s.get("quality", "?") for s in sources
                 if s.get("quality") != (best or {}).get("quality")},
                key=lambda q: -_quality_rank(q),
            )
            if best:
                ver_short = {"English Subtitles": "Sub",
                             "English Dub": "Dub",
                             "English Dub with Closed Captions":
                                 "Dub-CC"}.get(best.get("version", ""), "?")
                detail = (f"{ver_short} {best.get('quality', '')}  •  "
                          f"{_fmt_size(best.get('size_bytes', 0))}")
                if extras:
                    detail += f"  ({', '.join(extras)})"
            else:
                detail = "(no source)"
        elif self.current_source == "muhn":
            best = self._best_source_for(ep, "muhn",
                                         "English Dub", quality_pref)
            if best:
                detail = (f"Dub {best.get('quality', 'varies')}  •  "
                          f"{_fmt_size(best.get('size_bytes', 0))}")
            else:
                detail = "(no source)"
        elif self.current_source == "nyaa":
            # Prefer Galaxy9000 (official One Pace uploader); fall back to most-seeded.
            official = [s for s in sources
                        if s.get("uploader") == NYAA_OFFICIAL_UPLOADER]
            pool = official or sources
            best = max(pool, key=lambda s: int(s.get("seeders", 0)),
                       default=None)
            if best is not None:
                badge = "✓ Official  •  " if best.get("uploader") == NYAA_OFFICIAL_UPLOADER else ""
                detail = (f"{badge}{_fmt_size(best.get('size_bytes', 0))}  •  "
                          f"⛵{best.get('seeders', 0)}"
                          f"  ({len(sources)} torrent"
                          f"{'s' if len(sources) != 1 else ''})")
            else:
                detail = "(no torrents)"
        else:  # usenet
            # No seeders / uploader concept — just pick the highest-quality
            # release as the headline and surface the alternatives count.
            best = max(sources,
                       key=lambda s: _quality_rank(s.get("quality", "")),
                       default=None)
            if best is not None:
                qual = best.get("quality", "?")
                extras_count = len(sources) - 1
                tail = (f"  (+{extras_count} more)" if extras_count > 0 else "")
                detail = (f"{qual}  •  "
                          f"{_fmt_size(best.get('size_bytes', 0))}{tail}")
            else:
                detail = "(no NZB)"

        # Title left, detail muted right. Compact rows — no extra pady on
        # the labels (the row's height=32 + checkbox pack already breathes).
        click_row = ctk.CTkFrame(row, fg_color="transparent")
        click_row.pack(side="left", fill="x", expand=True, padx=(0, SP_XS))

        is_saved = self._is_episode_saved(ep, self.selected_arc)

        # Truncate the row title to fit the narrow episode column — canonical
        # titles can be 40+ chars and otherwise push the Saved chip and the
        # quality/size detail off the right edge. Tooltip carries the full
        # title for anyone who wants to read it.
        full_title = episode_title(ep)
        row_title = episode_title(ep, max_len=28)

        # Saved chip is packed FIRST (side="right") so it claims its full
        # width before detail_lbl + title_lbl get their parcels. That way,
        # space pressure squeezes the longer text (title), not the chip.
        if is_saved:
            saved_chip = ctk.CTkLabel(
                click_row, text=" ✓ Saved ",
                font=F_BOLD_XS,
                fg_color=OFFICIAL_CHIP,
                text_color=("white", "white"),
                corner_radius=RADIUS_SM,
            )
            saved_chip.pack(side="right", padx=(0, SP_XS))
        detail_lbl = ctk.CTkLabel(
            click_row, text=detail, anchor="e",
            font=F_XS,
            text_color=TEXT_MUTED,
        )
        detail_lbl.pack(side="right", padx=(SP_SM, SP_SM))
        title_lbl = ctk.CTkLabel(
            click_row, text=row_title, anchor="w",
            font=F_BOLD_BASE if is_active else F_BASE,
            text_color=TEXT,
        )
        title_lbl.pack(side="left", padx=(SP_XS, SP_SM))
        if full_title != row_title:
            Tooltip(title_lbl, full_title)

        # Refs for _update_episode_selection — avoids re-rendering all 59
        # episodes when the user clicks one to view its sources.
        self._ep_row_widgets[ep["num"]] = {
            "row": row, "title_lbl": title_lbl,
        }

        def _click(_e=None, e=ep) -> None:
            self._on_episode_clicked(e)

        def _double_click(_e=None, e=ep) -> None:
            self._toggle_checkbox(e["num"])

        def _enter(_e=None) -> None:
            active_ep = self.selected_ep_for_detail
            if active_ep != ep["num"]:
                click_row.configure(fg_color=SURFACE_HOVER)

        def _leave(_e=None) -> None:
            active_ep = self.selected_ep_for_detail
            active_bg = ("#FFF6E0", "#2A2010")
            click_row.configure(fg_color=active_bg if active_ep == ep["num"] else "transparent")

        for w in (click_row, title_lbl, detail_lbl):
            w.bind("<Button-1>", _click)
            w.bind("<Double-Button-1>", _double_click)
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
            w.configure(cursor="hand2")

    def _build_arc_pack_row(self, pack: dict) -> None:
        size_str = _fmt_size(pack.get("size_bytes", 0))
        seeders = pack.get("seeders", 0)
        title = pack.get("torrent_title", "(pack)")
        is_official = pack.get("uploader") == NYAA_OFFICIAL_UPLOADER

        # Official packs get a warmer green; unofficial sit on a desaturated
        # cream so the eye lands on the trusted ones first.
        card = ctk.CTkFrame(
            self.episode_scroll,
            fg_color=OFFICIAL_BG if is_official else UNOFFICIAL_BG,
            border_width=1,
            border_color=BORDER if is_official else BORDER,
            corner_radius=RADIUS_SM,
        )
        card.pack(fill="x", padx=SP_SM, pady=SP_XS)

        # Line 1: title
        title_lbl = ctk.CTkLabel(
            card, text=title,
            anchor="w", font=F_SM,
            text_color=TEXT,
            wraplength=380, justify="left",
        )
        title_lbl.pack(fill="x", padx=SP_MD, pady=(SP_SM, SP_XS))
        if len(title) > 60:
            Tooltip(title_lbl, title)

        # Line 2: Official badge + size/seeders chip + magnet button
        line2 = ctk.CTkFrame(card, fg_color="transparent")
        line2.pack(fill="x", padx=SP_MD, pady=(0, SP_SM))

        ctk.CTkButton(
            line2, text="Open magnet", width=120, height=H_SM,
            font=F_BOLD_XS,
            fg_color=INFO, hover_color=INFO_HOVER,
            command=lambda p=pack: self._open_magnet(
                p["magnet"], p.get("torrent_title", "")),
        ).pack(side="right")

        if is_official:
            badge = self._chip(
                line2, "✓ Official",
                fg=OFFICIAL_CHIP, txt=("white", "white"),
            )
            badge.pack(side="left")
            Tooltip(badge,
                    "Uploaded by Galaxy9000 — the official One Pace Nyaa "
                    "account. Matches what onepace.net hosts.")

        ctk.CTkLabel(
            line2,
            text=f"{size_str}",
            font=F_BOLD_XS,
            text_color=TEXT,
        ).pack(side="left", padx=(SP_SM if is_official else 0, SP_XS))
        ctk.CTkLabel(
            line2,
            text=f"·  ↑ {seeders}",
            font=F_BOLD_XS, text_color=OK,
        ).pack(side="left", padx=(SP_XS, 0))

    def _on_episode_clicked(self, ep: dict) -> None:
        prev_num = self.selected_ep_for_detail
        new_num = ep["num"]
        self.selected_ep_for_detail = new_num
        self._last_clicked_ep_num = new_num
        # Update only the prev + new episode rows (background + font); skip
        # the full middle-column re-render so clicking feels instant.
        self._update_episode_selection(prev_num, new_num)
        self._render_source_panel()

    def _update_episode_selection(self, prev_num: int | None,
                                  new_num: int | None) -> None:
        active_bg = ("#FFF6E0", "#2A2010")
        for num, is_active in ((prev_num, False), (new_num, True)):
            if num is None:
                continue
            widgets = self._ep_row_widgets.get(num)
            if not widgets:
                continue
            widgets["row"].configure(
                fg_color=active_bg if is_active else "transparent")
            widgets["title_lbl"].configure(
                font=F_BOLD_BASE if is_active else F_BASE)

    def _toggle_select_all(self) -> None:
        if not self.ep_check_vars:
            return
        any_checked = any(var.get() for var in self.ep_check_vars.values())
        new = not any_checked
        self._updating_checks = True
        try:
            for var in self.ep_check_vars.values():
                var.set(new)
        finally:
            self._updating_checks = False
        self._refresh_selection_buttons()

    def _refresh_selection_buttons(self) -> None:
        """Update the Select all + Download selected labels based on how many
        episode checkboxes are currently ticked."""
        if not hasattr(self, "select_all_btn"):
            return
        total = len(self.ep_check_vars)
        ticked = sum(1 for v in self.ep_check_vars.values() if v.get())
        
        # Calculate selected size
        total_bytes = 0
        version_pref = self.config_data.get("default_version", "English Subtitles")
        quality_pref = self.config_data.get("default_quality", "1080p")
        src = self.current_source

        ticked_ep_nums = {num for num, v in self.ep_check_vars.items() if v.get()}
        if ticked_ep_nums and self.selected_arc:
            for ep in self.selected_arc.get("episodes", []):
                if ep["num"] in ticked_ep_nums:
                    best = self._best_source_for(ep, src, version_pref, quality_pref)
                    if best:
                        total_bytes += int(best.get("size_bytes", 0))

        if ticked > 0:
            size_str = _fmt_size(total_bytes) if total_bytes > 0 else "0 MB"
            self.selection_size_lbl.configure(text=f"{ticked} selected ({size_str})")
        else:
            self.selection_size_lbl.configure(text="")

        if total == 0:
            self.select_all_btn.configure(text="Select all")
            self.download_selected_btn.configure(text="Download selected")
            return
        if ticked == 0:
            self.select_all_btn.configure(text=f"Select all ({total})")
            self.download_selected_btn.configure(text="Download selected")
        elif ticked == total:
            self.select_all_btn.configure(text="Deselect all")
            verb = ("Open" if self.current_source == "nyaa" else "Download")
            self.download_selected_btn.configure(
                text=f"{verb} {ticked} episode{'s' if ticked != 1 else ''}")
        else:
            self.select_all_btn.configure(text=f"Select all ({total})")
            verb = ("Open" if self.current_source == "nyaa" else "Download")
            self.download_selected_btn.configure(
                text=f"{verb} {ticked} of {total}")

    # ---------------------------------------------- source panel (column 3) -

    def _render_source_panel(self) -> None:
        for w in self.source_scroll.winfo_children():
            w.destroy()
        self._download_panel_widgets.clear()
        # 1. Active download takes priority over everything — replaces the
        # source/quality panel with a prominent download dashboard so users
        # can actually see progress without squinting at the 4px footer bar.
        if self.download_title is not None:
            self._render_download_panel()
            return
        src = self.current_source
        # 2. No episode selected — render a useful idle panel instead of
        # leaving 380px of dead space.
        if not self.selected_arc or self.selected_ep_for_detail is None:
            self._render_idle_panel(src)
            return
        ep = next((e for e in self.selected_arc["episodes"]
                   if e["num"] == self.selected_ep_for_detail), None)
        if not ep:
            return
        self.source_header.configure(
            text=f"{_SRC_LABEL[src]}  —  {episode_title(ep)}")
        # Only show the active source's rows — the other sources have their
        # own tab.
        sources = [s for s in ep.get("sources", []) if s.get("kind") == src]
        card_header = {
            "onepace": "ONE PACE",
            "muhn":    "MUHN PACE  (English Dub)",
            "nyaa":    "NYAA  (Torrents)",
            "usenet":  "USENET  (NZB)",
        }[src]
        if sources:
            self._build_source_card(card_header, sources, ep, src)
        else:
            self._build_placeholder_card(
                card_header,
                f"No {_SRC_LABEL[src]} options for this episode.")

    def _render_download_panel(self) -> None:
        """Hero download status panel — combines title, big percentage and
        progress bar into a single tinted band, with chip-row stats below."""
        self.source_header.configure(text="DOWNLOADING")

        # Hero card with faint red tinted background — visually distinct
        # from the standard source cards so progress reads "this is happening
        # right now" at a glance.
        wrap = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=("#FFF5F5", "#2A1817"),
            border_width=1, border_color=("#F2C8CC", "#5A2A2C"),
        )
        wrap.pack(fill="x", padx=SP_SM, pady=SP_SM)

        title_lbl = ctk.CTkLabel(
            wrap, text=self.download_title or "(starting…)",
            font=F_BOLD_LG,
            text_color=TEXT,
            anchor="w", wraplength=360, justify="left",
        )
        title_lbl.pack(fill="x", padx=SP_LG, pady=(SP_LG, SP_SM))

        # Percent + speed together as a hero pair
        hero_row = ctk.CTkFrame(wrap, fg_color="transparent")
        hero_row.pack(fill="x", padx=SP_LG, pady=(0, SP_SM))
        pct_lbl = ctk.CTkLabel(
            hero_row, text="0%", font=F_HERO_BOLD,
            text_color=PRIMARY, anchor="w",
        )
        pct_lbl.pack(side="left")
        speed_lbl = ctk.CTkLabel(
            hero_row, text="—",
            font=F_BOLD_BASE, text_color=TEXT_MUTED,
            anchor="e",
        )
        speed_lbl.pack(side="right")

        # Branded progress bar
        bar = ctk.CTkProgressBar(
            wrap, height=10,
            progress_color=PRIMARY,
            fg_color=("#F3E5DA", "#2A1817"),
            border_width=0,
        )
        bar.pack(fill="x", padx=SP_LG, pady=(0, SP_SM))
        bar.set(0)

        # Friendly "speed varies" note — hidden by default, packed only after
        # _update_download_panel observes a sustained slow rate. Sticky for the
        # session once shown.
        slow_note = ctk.CTkLabel(
            wrap, text=(
                "Pixeldrain's free CDN throttles in waves — fast one minute, "
                "slow the next. Nothing we can tune, just how it is. "
                "Download finishes either way."),
            font=F_ITALIC_XS, text_color=TEXT_MUTED,
            wraplength=360, justify="left", anchor="w",
        )
        # Single horizontal chip row for the remaining stats
        chip_row = ctk.CTkFrame(wrap, fg_color="transparent")
        chip_row.pack(fill="x", padx=SP_LG, pady=(SP_XS, SP_LG))
        bytes_lbl = ctk.CTkLabel(
            chip_row, text="—", font=F_SM,
            text_color=TEXT, anchor="w",
        )
        bytes_lbl.pack(side="left")
        file_lbl = ctk.CTkLabel(
            chip_row, text="", font=F_SM,
            text_color=TEXT_MUTED, anchor="e",
        )
        file_lbl.pack(side="right")
        eta_lbl = ctk.CTkLabel(
            chip_row, text="", font=F_SM,
            text_color=TEXT_MUTED, anchor="center",
        )
        eta_lbl.pack(side="right", padx=(0, SP_MD))

        # Big Cancel button (slightly less shouty than the prior brick red)
        ctk.CTkButton(
            wrap, text="Cancel download", height=H_MD,
            font=F_BOLD_SM,
            fg_color=DANGER, hover_color=DANGER_HOVER,
            command=self._cancel,
        ).pack(fill="x", padx=SP_LG, pady=(0, SP_LG))

        # Save-folder hint card (separate card, neutral surface)
        folder_card = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_CARD,
            border_width=1, border_color=BORDER,
        )
        folder_card.pack(fill="x", padx=SP_SM, pady=(0, SP_SM))
        ctk.CTkLabel(
            folder_card, text="SAVING TO",
            font=F_BOLD_XS,
            text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", padx=SP_LG, pady=(SP_MD, 0))
        ctk.CTkLabel(
            folder_card, text=self.save_dir.get() or "(no folder set)",
            font=F_MONO_SM,
            wraplength=340, justify="left", anchor="w",
            text_color=TEXT,
        ).pack(fill="x", padx=SP_LG, pady=(SP_XS, SP_XS))
        ctk.CTkButton(
            folder_card, text="Open folder", height=H_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            font=F_SM,
            command=self._open_folder,
        ).pack(padx=SP_LG, pady=(0, SP_MD), anchor="w")

        self._download_panel_widgets = {
            "title": title_lbl,
            "bar": bar,
            "pct": pct_lbl,
            "bytes": bytes_lbl,
            "speed": speed_lbl,
            "file": file_lbl,
            "eta": eta_lbl,
            "slow_note": slow_note,
            "wrap": wrap,
        }
        # Re-pack the slow-note immediately if the session has already shown
        # it (e.g. starting another download after the first slow one).
        if self.slow_download_noted:
            try:
                slow_note.pack(in_=wrap, fill="x",
                                padx=SP_LG, pady=(0, SP_SM),
                                before=chip_row)
            except Exception:
                pass
        # Hide the thin footer bar — the hero is louder and they'd dance
        # in sync, which feels redundant.
        try:
            self.progress.pack_forget()
        except Exception:
            pass
        if self.download_state is not None:
            self._update_download_panel(self.download_state)

    def _update_download_panel(self, st: dict) -> None:
        """Live-update the download panel labels from a progress event."""
        w = self._download_panel_widgets
        if not w:
            return
        frac = max(0.0, min(1.0, st.get("frac", 0.0)))
        bytes_now = int(st.get("bytes_now", 0))
        speed = st.get("speed", "—")
        idx = int(st.get("idx", 0))
        total = int(st.get("total", 0))
        size_total = int(st.get("size_total", 0))

        try:
            w["bar"].set(frac)
        except Exception:
            pass
        w["pct"].configure(text=f"{int(frac * 100)}%")
        if size_total > 0:
            w["bytes"].configure(
                text=f"{fmt_bytes(bytes_now)}  of  {fmt_bytes(size_total)}")
        else:
            w["bytes"].configure(text=fmt_bytes(bytes_now))
        w["speed"].configure(text=str(speed))
        w["file"].configure(
            text=(f"File {idx} of {total}" if total > 1 else ""))
        # Parse current speed → bytes per second, reused for ETA + slow check
        bps = 0.0
        try:
            sp = str(speed)
            if sp.endswith("/s"):
                m_ = re.match(r"\s*([\d.]+)\s*([KMG]?B)", sp)
                if m_:
                    val = float(m_.group(1))
                    unit = {"B": 1, "KB": 1024, "MB": 1024 ** 2,
                            "GB": 1024 ** 3}.get(m_.group(2), 1)
                    bps = val * unit
        except Exception:
            bps = 0.0

        # ETA
        eta_str = ""
        if size_total > bytes_now and bps > 0:
            secs = int((size_total - bytes_now) / bps)
            if secs < 60:
                eta_str = f"{secs}s left"
            elif secs < 3600:
                eta_str = f"{secs // 60}m {secs % 60}s left"
            else:
                eta_str = f"{secs // 3600}h {(secs % 3600) // 60}m left"
        w["eta"].configure(text=eta_str)

        # Slow-download nudge — once the download has been running > 15s and
        # the sustained rate is below 300 KB/s, surface the friendly note.
        # Stays sticky for the rest of the session so the user only sees it
        # appear once instead of toggling on every brief slowdown.
        now = time.time()
        if self._dl_started_at is None and bytes_now > 0:
            self._dl_started_at = now
        if (not self.slow_download_noted
                and self._dl_started_at is not None
                and now - self._dl_started_at > 15
                and 0 < bps < 300 * 1024):
            self.slow_download_noted = True
            slow_w = w.get("slow_note")
            wrap = w.get("wrap")
            chip_row = w["bytes"].master  # chip_row holds bytes_lbl
            if slow_w and wrap:
                try:
                    slow_w.pack(in_=wrap, fill="x",
                                 padx=SP_LG, pady=(0, SP_SM),
                                 before=chip_row)
                except Exception:
                    pass

    def _render_idle_panel(self, src: str) -> None:
        """Right-column content when nothing is selected and nothing is
        downloading. Quick-orient cards in the same elevation system as
        every other card in the app."""
        self.source_header.configure(text="QUICK GUIDE")

        # ---- How to download ----
        guide = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_CARD,
            border_width=1, border_color=BORDER,
        )
        guide.pack(fill="x", padx=SP_SM, pady=(SP_SM, SP_SM))
        ctk.CTkLabel(
            guide, text="HOW TO DOWNLOAD",
            font=F_BOLD_SM, text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=SP_LG, pady=(SP_MD, SP_SM))
        steps = [
            ("1", "Pick a source above (One Pace, Muhn Pace, Nyaa, or Usenet)."),
            ("2", "Pick an arc from the list on the left."),
            ("3", "Tick the episodes you want in the middle column."),
            ("4", "Hit Download selected, choose quality, go."),
        ]
        for num, text in steps:
            row = ctk.CTkFrame(guide, fg_color="transparent")
            row.pack(fill="x", padx=SP_LG, pady=SP_XS)
            # Solid gold circle + dark navy number — looks premium, doesn't
            # read as muddy brown like the previous tinted version.
            circle = ctk.CTkLabel(
                row, text=num,
                font=F_BOLD_BASE,
                fg_color=HEADER_ACCENT,
                text_color=HEADER_BG,
                corner_radius=14,
                width=28, height=28,
            )
            circle.pack(side="left")
            ctk.CTkLabel(
                row, text=text, font=F_BASE,
                wraplength=320, justify="left", anchor="w",
                text_color=TEXT,
            ).pack(side="left", fill="x", expand=True, padx=(SP_MD, 0))
        ctk.CTkLabel(guide, text="", height=2).pack()

        # ---- Save folder ----
        folder_card = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_CARD,
            border_width=1, border_color=BORDER,
        )
        folder_card.pack(fill="x", padx=SP_SM, pady=(0, SP_SM))
        ctk.CTkLabel(
            folder_card, text="SAVE FOLDER",
            font=F_BOLD_SM,
            text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=SP_LG, pady=(SP_MD, 0))
        ctk.CTkLabel(
            folder_card, text=self.save_dir.get() or "(not set)",
            font=F_MONO_SM,
            wraplength=340, justify="left", anchor="w",
            text_color=TEXT,
        ).pack(fill="x", padx=SP_LG, pady=(SP_XS, SP_SM))
        btn_row = ctk.CTkFrame(folder_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=SP_LG, pady=(0, SP_MD))
        ctk.CTkButton(
            btn_row, text="Browse", height=H_SM, width=90,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            font=F_SM,
            command=self._pick_folder,
        ).pack(side="left", padx=(0, SP_XS))
        ctk.CTkButton(
            btn_row, text="Open", height=H_SM, width=70,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            font=F_SM,
            command=self._open_folder,
        ).pack(side="left")

        # ---- Help links ----
        help_card = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_CARD,
            border_width=1, border_color=BORDER,
        )
        help_card.pack(fill="x", padx=SP_SM, pady=(0, SP_SM))
        ctk.CTkLabel(
            help_card, text="NEED HELP?",
            font=F_BOLD_SM,
            text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=SP_LG, pady=(SP_MD, SP_XS))
        link_row = ctk.CTkFrame(help_card, fg_color="transparent")
        link_row.pack(fill="x", padx=SP_LG, pady=(0, SP_MD))
        for label in ("Discord", "Reddit", "GitHub"):
            _make_brand_button(link_row, label, width=84).pack(
                side="left", padx=(0, SP_XS))

    def _build_source_card(self, header: str, sources: list[dict],
                           ep: dict, kind: str) -> None:
        if not sources:
            return
        card = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_CARD,
            border_width=1, border_color=BORDER,
        )
        card.pack(fill="x", padx=SP_SM, pady=SP_SM)
        ctk.CTkLabel(card, text=header, font=F_BOLD_SM,
                     text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x", padx=SP_LG,
                                       pady=(SP_MD, SP_XS))
        ctk.CTkFrame(card, height=1, fg_color=BORDER,
                     corner_radius=0).pack(fill="x", padx=SP_LG)
        for s in sources:
            self._build_source_row(card, s, ep, kind)
        if kind == "muhn":
            self._build_muhn_gdrive_fallback(card)
        ctk.CTkLabel(card, text="", height=2).pack()

    def _build_muhn_gdrive_fallback(self, parent: ctk.CTkFrame) -> None:
        """Emergency fallback row shown under Muhn Pace download options —
        if pixeldrain is throttled or down, users can grab the same files
        from a Google Drive mirror."""
        ctk.CTkFrame(parent, height=1, fg_color=BORDER,
                     corner_radius=0).pack(fill="x", padx=SP_LG,
                                            pady=(SP_XS, 0))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=SP_LG, pady=(SP_XS, 0))
        ctk.CTkLabel(
            row, text="Pixeldrain down?",
            font=F_XS, text_color=TEXT_MUTED, anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Open Google Drive mirror",
            width=180, height=H_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK, hover_color=SURFACE_HOVER,
            font=F_SM,
            command=lambda: webbrowser.open(MUHN_GDRIVE_MIRROR_URL),
        ).pack(side="right")

    def _build_placeholder_card(self, header: str, msg: str) -> None:
        card = ctk.CTkFrame(
            self.source_scroll, corner_radius=RADIUS_LG,
            fg_color=SURFACE_PANEL,
            border_width=1, border_color=BORDER,
        )
        card.pack(fill="x", padx=SP_SM, pady=SP_SM)
        ctk.CTkLabel(card, text=header, font=F_BOLD_SM,
                     anchor="w",
                     text_color=TEXT_DIM).pack(
            fill="x", padx=SP_LG, pady=(SP_MD, 0))
        ctk.CTkLabel(card, text=msg, font=F_XS, anchor="w",
                     text_color=TEXT_MUTED, wraplength=360,
                     justify="left").pack(
            fill="x", padx=SP_LG, pady=(SP_XS, SP_MD))

    def _build_source_row(self, parent: ctk.CTkFrame, s: dict, ep: dict,
                          kind: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=SP_LG, pady=SP_XS)
        size_str = _fmt_size(s.get("size_bytes", 0))

        if kind == "onepace":
            ver_short = {"English Subtitles": "Sub",
                         "English Dub": "Dub",
                         "English Dub with Closed Captions": "Dub-CC"}.get(
                s.get("version", ""), "—")
            self._chip(row, ver_short).pack(side="left")
            ctk.CTkLabel(row, text=s.get("quality", "—"),
                         font=F_BOLD_BASE, text_color=TEXT,
                         anchor="w").pack(side="left", padx=(SP_SM, 0))
            ctk.CTkButton(
                row, text="Download", width=110, height=H_SM,
                font=F_BOLD_XS,
                fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                command=lambda src=s, e=ep:
                    self._download_single_episode_source(e, src),
            ).pack(side="right", padx=(SP_SM, 0))
            ctk.CTkLabel(row, text=size_str, font=F_SM,
                         text_color=TEXT_MUTED, anchor="e").pack(
                side="right", padx=(0, SP_SM))

        elif kind == "muhn":
            self._chip(row, "Dub").pack(side="left")
            ctk.CTkLabel(row, text=s.get("quality", "varies"),
                         font=F_BOLD_BASE, text_color=TEXT,
                         anchor="w").pack(side="left", padx=(SP_SM, 0))
            ctk.CTkButton(
                row, text="Download", width=110, height=H_SM,
                font=F_BOLD_XS,
                fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                command=lambda src=s, e=ep:
                    self._download_single_episode_source(e, src),
            ).pack(side="right", padx=(SP_SM, 0))
            ctk.CTkLabel(row, text=size_str, font=F_SM,
                         text_color=TEXT_MUTED, anchor="e").pack(
                side="right", padx=(0, SP_SM))

        elif kind == "usenet":
            self._chip(row, s.get("quality", "—")).pack(side="left")
            release_title = s.get("release_title", "")
            # Truncated release-name hint so users can tell duplicates apart
            short = release_title.split("]")[-1].strip()[:36] if release_title else ""
            ctk.CTkLabel(
                row, text=short or "NZB", font=F_BOLD_BASE,
                text_color=TEXT, anchor="w",
            ).pack(side="left", padx=(SP_SM, 0))
            ctk.CTkButton(
                row, text="Get NZB", width=110, height=H_SM,
                font=F_BOLD_XS,
                fg_color=INFO, hover_color=INFO_HOVER,
                command=lambda src=s, e=ep:
                    self._download_usenet_nzb(src, e),
            ).pack(side="right", padx=(SP_SM, 0))
            ctk.CTkLabel(row, text=size_str, font=F_SM,
                         text_color=TEXT_MUTED, anchor="e").pack(
                side="right", padx=(0, SP_SM))

        elif kind == "nyaa":
            seeders = s.get("seeders", 0)
            is_official = s.get("uploader") == NYAA_OFFICIAL_UPLOADER
            self._chip(row, s.get("quality", "—")).pack(side="left")
            if is_official:
                badge = self._chip(
                    row, "✓ Official",
                    fg=OFFICIAL_CHIP,
                    txt=("white", "white"),
                )
                badge.pack(side="left", padx=(SP_SM, 0))
                Tooltip(badge,
                        "Uploaded by Galaxy9000 — the official One Pace "
                        "Nyaa account. Matches what onepace.net hosts.")
            ctk.CTkLabel(row, text=f"↑ {seeders}",
                         font=F_BOLD_XS, text_color=OK,
                         anchor="w").pack(side="left", padx=(SP_SM, 0))
            ctk.CTkButton(
                row, text="Open magnet", width=120, height=H_SM,
                font=F_BOLD_XS,
                fg_color=INFO, hover_color=INFO_HOVER,
                command=lambda m=s["magnet"],
                               t=s.get("torrent_title", ""):
                    self._open_magnet(m, t),
            ).pack(side="right", padx=(SP_SM, 0))
            ctk.CTkLabel(row, text=size_str, font=F_SM,
                         text_color=TEXT_MUTED, anchor="e").pack(
                side="right", padx=(0, SP_SM))

    @staticmethod
    def _chip(parent, text: str, *, fg=None, txt=None):
        """Small rounded pill — used for Sub/Dub/quality/Official labels."""
        return ctk.CTkLabel(
            parent, text=f" {text} ",
            font=F_BOLD_XS,
            fg_color=fg if fg is not None else ("#E8EAF0", "#2C3340"),
            text_color=txt if txt is not None else TEXT_MUTED,
            corner_radius=RADIUS_SM,
        )

    # ------------------------------------------------- download actions ---

    def _download_single_episode_source(self, ep: dict, source: dict) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A download is already in progress.")
            return
        dest = Path(self.save_dir.get())
        ok, msg = self._validate_save_dir(dest)
        if not ok:
            messagebox.showwarning("Folder problem", msg)
            return
        self.cancel_evt.clear()
        self._set_cancel_visible(True)

        album_id = source["album_id"]
        file_id = source["file_id"]
        ver_short = {"English Subtitles": "Sub", "English Dub": "Dub",
                     "English Dub with Closed Captions": "Dub-CC"}.get(
            source.get("version", ""),
            "Dub" if source["kind"] == "muhn" else "?")
        folder_name = (f"{self.selected_arc['title']} - "
                       f"{ver_short} {source.get('quality', '')}").strip()
        # Promote the download to the prominent right-column status panel
        # so users can see exactly what's happening at a glance.
        self.download_title = (f"{episode_title(ep)}  —  "
                                f"{ver_short} {source.get('quality', '')}")
        self.download_state = {
            "frac": 0.0,
            "speed": "starting…",
            "bytes_now": 0,
            "idx": 0,
            "total": 1,
            "size_total": int(source.get("size_bytes", 0)),
        }
        self._render_source_panel()
        self._log(f"Starting: {episode_title(ep)} — "
                  f"{ver_short} {source.get('quality', '')}")

        arc_for_organize = self.selected_arc
        def task():
            try:
                Downloader(
                    album_id, dest,
                    on_status=lambda s: self.ui_queue.put(("status", s)),
                    on_progress=self._on_progress,
                    on_log=lambda m: self.ui_queue.put(("log", m)),
                    cancel_evt=self.cancel_evt,
                    file_filter={file_id},
                    subfolder=folder_name,
                ).run()
                self._organize_for_plex_layout(
                    dest / sanitize_filename(folder_name),
                    {file_id}, arc_for_organize,
                    log=lambda m: self.ui_queue.put(("log", m)),
                )
                self.ui_queue.put(("status", f"Done: {episode_title(ep)}"))
                self.ui_queue.put(("done", None))
            except DownloadCancelled:
                self.ui_queue.put(("log", "Cancelled."))
                self.ui_queue.put(("status", "Cancelled."))
                self.ui_queue.put(("done", None))
            except Exception as e:
                self.ui_queue.put(("error", str(e)))
                self.ui_queue.put(("done", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _download_selected_episodes(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A download is already in progress.")
            return
        if not self.selected_arc:
            return
        chosen = [num for num, var in self.ep_check_vars.items() if var.get()]
        if not chosen:
            messagebox.showinfo("Nothing selected",
                "Tick at least one episode in the middle column first.")
            return

        # Nyaa: confirm + hand magnets to torrent client
        if self.current_source == "nyaa":
            self._send_selected_nyaa_magnets(chosen)
            return

        # Usenet: confirm + fetch one .nzb per episode and let SABnzbd / NZBGet
        # pick them up. No version/quality dialog (Usenet GUIDs are per-release
        # already); just pick the user's preferred quality if available.
        if self.current_source == "usenet":
            self._send_selected_usenet_nzbs(chosen)
            return

        # One Pace / Muhn: let the user pick version + quality for the batch
        # (defaults to their saved Settings preference). Total-size preview
        # shows what they're committing to.
        chosen_eps = [
            ep for ep in self.selected_arc["episodes"] if ep["num"] in chosen
        ]
        dlg = BatchDownloadDialog(
            self,
            source=self.current_source,
            episodes=chosen_eps,
            default_version=self.config_data.get("default_version",
                                                  "English Subtitles"),
            default_quality=self.config_data.get("default_quality", "1080p"),
        )
        self.wait_window(dlg)
        if dlg.confirmed is not True:
            return
        version_pref = dlg.chosen_version
        quality_pref = dlg.chosen_quality
        src_kind = self.current_source

        plan_full: list[tuple[str, str, str, str]] = []
        skipped: list[str] = []
        for ep in self.selected_arc["episodes"]:
            if ep["num"] not in chosen:
                continue
            src = self._best_source_for(ep, src_kind, version_pref, quality_pref)
            if not src:
                skipped.append(episode_title(ep))
                continue
            ver_short = {"English Subtitles": "Sub",
                         "English Dub": "Dub",
                         "English Dub with Closed Captions": "Dub-CC"}.get(
                src.get("version", ""),
                "Dub" if src_kind == "muhn" else "?")
            sub = (f"{self.selected_arc['title']} - "
                   f"{ver_short} {src.get('quality', '')}").strip()
            plan_full.append((episode_title(ep), src["album_id"],
                              src["file_id"], sub))

        if not plan_full:
            messagebox.showinfo("No downloadable sources",
                f"None of the selected episodes have a "
                f"{_SRC_LABEL[src_kind]} source.")
            return
        if skipped:
            self._log(
                f"Skipping {len(skipped)} episode(s) with no "
                f"{_SRC_LABEL[src_kind]} source: {', '.join(skipped[:5])}"
                f"{'…' if len(skipped) > 5 else ''}")

        dest = Path(self.save_dir.get())
        ok, msg = self._validate_save_dir(dest)
        if not ok:
            messagebox.showwarning("Folder problem", msg)
            return
        self.cancel_evt.clear()
        self._set_cancel_visible(True)
        total_size = sum(
            int(self._best_source_for(ep, src_kind, version_pref,
                                       quality_pref).get("size_bytes", 0))
            for ep in self.selected_arc["episodes"]
            if ep["num"] in chosen
            and self._best_source_for(ep, src_kind, version_pref,
                                       quality_pref) is not None
        )
        # Surface the batch download in the right-column status panel
        self.download_title = (
            f"{self.selected_arc['title']}  —  "
            f"{len(plan_full)} episode{'s' if len(plan_full) != 1 else ''} "
            f"({version_pref.split()[-1] if 'Sub' not in version_pref else 'Sub'} {quality_pref})"
        )
        self.download_state = {
            "frac": 0.0, "speed": "starting…", "bytes_now": 0,
            "idx": 0, "total": len(plan_full), "size_total": total_size,
        }
        self._render_source_panel()
        self._log(f"Queued {len(plan_full)} episode(s).")

        # Coalesce by (album_id, subfolder) so we hit each album once
        groups: dict[tuple[str, str], set[str]] = {}
        for _display, album_id, file_id, sub in plan_full:
            groups.setdefault((album_id, sub), set()).add(file_id)

        arc_for_organize = self.selected_arc
        def task():
            for i, ((album_id, sub), file_ids) in enumerate(groups.items(), 1):
                if self.cancel_evt.is_set():
                    break
                self.ui_queue.put(("log",
                    f"[{i}/{len(groups)}] album {album_id} "
                    f"({len(file_ids)} file(s))"))
                try:
                    Downloader(
                        album_id, dest,
                        on_status=lambda s: self.ui_queue.put(("status", s)),
                        on_progress=self._on_progress,
                        on_log=lambda m: self.ui_queue.put(("log", m)),
                        cancel_evt=self.cancel_evt,
                        file_filter=file_ids,
                        subfolder=sub,
                    ).run()
                    self._organize_for_plex_layout(
                        dest / sanitize_filename(sub),
                        file_ids, arc_for_organize,
                        log=lambda m: self.ui_queue.put(("log", m)),
                    )
                except DownloadCancelled:
                    self.ui_queue.put(("log", "Cancelled."))
                    break
                except Exception as e:
                    self.ui_queue.put(("log",
                        f"  ERROR on album {album_id}: {e}"))
            self.ui_queue.put((
                "status",
                "All done." if not self.cancel_evt.is_set() else "Cancelled."))
            self.ui_queue.put(("done", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    @staticmethod
    def _best_source_for(ep: dict, kind: str, version_pref: str,
                         quality_pref: str) -> dict | None:
        """Pick the best `kind` source for an episode (onepace/muhn), falling
        back across quality and version when the exact preference is missing."""
        candidates = [s for s in ep.get("sources", []) if s.get("kind") == kind]
        if not candidates:
            return None
        # Exact version + quality match wins
        for s in candidates:
            if (s.get("version") == version_pref
                    and s.get("quality") == quality_pref):
                return s
        # Same version, best available quality
        same_ver = [s for s in candidates if s.get("version") == version_pref]
        if same_ver:
            return max(same_ver,
                       key=lambda s: _quality_rank(s.get("quality", "")))
        # Quality match across any version
        for s in candidates:
            if s.get("quality") == quality_pref:
                return s
        # Whatever has the highest quality
        return max(candidates,
                   key=lambda s: _quality_rank(s.get("quality", "")))

    def _send_selected_nyaa_magnets(self, chosen_ep_nums: list[int]) -> None:
        """Open the top Nyaa magnet for each selected episode (Nyaa tab only).
        Prefers the official Galaxy9000 release; otherwise picks most-seeded."""
        magnets: list[tuple[str, str]] = []  # (display, magnet)
        skipped: list[str] = []
        for ep in self.selected_arc["episodes"]:
            if ep["num"] not in chosen_ep_nums:
                continue
            nyaa_sources = [s for s in ep.get("sources", [])
                            if s.get("kind") == "nyaa"]
            if not nyaa_sources:
                skipped.append(episode_title(ep))
                continue
            # Official first, then most-seeded
            official = [s for s in nyaa_sources
                        if s.get("uploader") == NYAA_OFFICIAL_UPLOADER]
            pool = official or nyaa_sources
            best = max(pool, key=lambda s: int(s.get("seeders", 0)))
            magnets.append((episode_title(ep), best["magnet"]))
        if not magnets:
            messagebox.showinfo(
                "No torrents",
                "None of the selected episodes have a Nyaa torrent.")
            return
        if not messagebox.askyesno(
            "Send magnets to torrent client",
            f"Open {len(magnets)} magnet link(s) in your default torrent "
            "client (qBittorrent / uTorrent / etc.)?\n\n"
            "Make sure the client is running.",
        ):
            return
        if skipped:
            self._log(
                f"Skipped {len(skipped)} episode(s) without a Nyaa torrent: "
                f"{', '.join(skipped[:5])}"
                f"{'…' if len(skipped) > 5 else ''}")
        self._log(f"Sending {len(magnets)} magnets to torrent client…")

        first_failure_shown = [False]

        def step(i: int) -> None:
            if i >= len(magnets):
                self._set_status(f"Sent {len(magnets)} magnets.")
                self._log("All magnets dispatched.")
                return
            title, magnet = magnets[i]
            ok = self._open_magnet(magnet, title,
                                    _suppress_dialog=first_failure_shown[0])
            if not ok:
                if not first_failure_shown[0]:
                    first_failure_shown[0] = True
                    self._show_no_torrent_client_dialog()
                self._log("Aborting batch — no torrent client registered.")
                return
            self._set_status(f"Sent {i + 1}/{len(magnets)}: {title}")
            self.after(250, lambda: step(i + 1))

        step(0)

    def _send_selected_usenet_nzbs(self, chosen_ep_nums: list[int]) -> None:
        """Fetch one .nzb per selected episode from the configured indexer
        and hand each to the OS default Usenet client. Picks the user's
        preferred quality where available; falls back to the highest tier."""
        cfg = self.config_data
        if not (cfg.get("usenet_indexer_url") and cfg.get("usenet_api_key")):
            self._show_usenet_setup_prompt()
            return

        quality_pref = cfg.get("default_quality", "1080p")
        plan: list[tuple[str, dict]] = []  # (display_title, usenet_source)
        skipped: list[str] = []
        for ep in self.selected_arc["episodes"]:
            if ep["num"] not in chosen_ep_nums:
                continue
            usources = [s for s in ep.get("sources", [])
                         if s.get("kind") == "usenet"]
            if not usources:
                skipped.append(episode_title(ep))
                continue
            # Prefer the user's quality, else fall back to highest tier.
            preferred = next(
                (s for s in usources if s.get("quality") == quality_pref),
                None)
            if preferred is None:
                preferred = max(
                    usources,
                    key=lambda s: _quality_rank(s.get("quality", "")))
            plan.append((episode_title(ep), preferred))

        if not plan:
            messagebox.showinfo(
                "No NZBs",
                "None of the selected episodes have a Usenet source.")
            return
        if not messagebox.askyesno(
            "Fetch NZBs",
            f"Download {len(plan)} .nzb file(s) and hand them to your "
            "Usenet client (SABnzbd / NZBGet)?\n\n"
            "Each NZB is queued one after the other with a short delay "
            "so your indexer doesn't rate-limit you.",
        ):
            return
        if skipped:
            self._log(
                f"Skipped {len(skipped)} episode(s) without a Usenet source: "
                f"{', '.join(skipped[:5])}"
                f"{'…' if len(skipped) > 5 else ''}")
        self._log(f"Queuing {len(plan)} NZB fetch(es)…")

        # Sequential with a small gap — keeps the indexer happy and avoids
        # piling Toplevel popups if SABnzbd isn't registered yet.
        def step(i: int) -> None:
            if i >= len(plan):
                self._set_status(f"Queued {len(plan)} NZB(s).")
                self._log("All NZB fetches dispatched.")
                return
            title, src = plan[i]
            self._download_usenet_nzb(src, ep=None)
            self._set_status(f"NZB {i + 1}/{len(plan)}: {title}")
            # 800ms keeps us under most indexer rate limits (NZBGeek is
            # typically 30 req/min — we'd never hit that here).
            self.after(800, lambda: step(i + 1))

        step(0)

    # --------------------------------------------------------- magnets ---

    def _open_magnet(self, magnet: str, title: str = "",
                      _suppress_dialog: bool = False) -> bool:
        """Hand a magnet to the OS default torrent handler. Returns True on
        success. On failure, copies the magnet to clipboard and (unless
        suppressed) shows a 'install qBittorrent' dialog with a direct link.
        Callers in a batch should pass `_suppress_dialog=True` for the 2nd+
        magnet so we don't spam 25 popups."""
        try:
            os.startfile(magnet)  # type: ignore[attr-defined]
            self._log(f"Sent to torrent client: {title or 'magnet'}")
            self._set_status("Magnet sent to your torrent client.")
            return True
        except (OSError, AttributeError):
            self.clipboard_clear()
            self.clipboard_append(magnet)
            self.update()
            if _suppress_dialog:
                return False
            self._show_no_torrent_client_dialog()
            return False

    def _show_no_torrent_client_dialog(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("No torrent client found")
        dlg.geometry("440x230")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.after(150, lambda: (dlg.grab_set()
                                if dlg.winfo_exists() else None))
        ctk.CTkLabel(dlg, text="No torrent client registered",
                     font=("Segoe UI", 14, "bold")).pack(pady=(14, 4))
        ctk.CTkLabel(
            dlg, justify="left",
            text="Magnet copied to your clipboard.\n\n"
                 "To open it, install a torrent client (qBittorrent is the "
                 "usual recommendation — free, open-source, no ads). After "
                 "install, just click any magnet button again.",
            font=("Segoe UI", 10), wraplength=400,
            text_color=("gray20", "gray80"),
        ).pack(padx=20, pady=4)
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(pady=14)
        ctk.CTkButton(
            btn_row, text="Get qBittorrent", width=140,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=lambda: webbrowser.open("https://www.qbittorrent.org/")
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="OK", width=100,
            fg_color="#374A6E", hover_color="#2C3D5C",
            command=dlg.destroy,
        ).pack(side="left", padx=6)

    # ----------------------------------------------- Usenet NZB fetch -----

    def _download_usenet_nzb(self, source: dict, ep: dict | None = None) -> None:
        """Fetch the .nzb file from the user's configured Newznab indexer
        using *their* API key, save it under the user's downloads folder,
        then hand it to the OS default handler (SABnzbd / NZBGet).

        We never ship NZB content or API keys — the indexer URL + key live
        in the user's config.json only.

        Per-GUID in-flight guard prevents double-clicks from spawning two
        concurrent fetches against the same release (which would burn
        indexer quota and race on the dest path)."""
        cfg = self.config_data
        url_base = (cfg.get("usenet_indexer_url") or "").rstrip("/")
        api_key = cfg.get("usenet_api_key") or ""
        if not url_base or not api_key:
            self._show_usenet_setup_prompt()
            return

        guid = source.get("guid")
        if not guid:
            messagebox.showerror(
                "Usenet",
                "This release has no GUID — can't fetch the NZB.")
            return

        if not hasattr(self, "_usenet_inflight"):
            self._usenet_inflight: set[str] = set()
        if guid in self._usenet_inflight:
            self._set_status("That NZB is already being fetched…")
            return
        self._usenet_inflight.add(guid)

        release_title = source.get("release_title") or "release"
        safe = _safe_nzb_filename(release_title, guid)
        dest_dir = Path(self.save_dir.get() or DEFAULT_DOWNLOADS)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe
        # Path-length sanity check (MAX_PATH = 260 on legacy Windows). Truncate
        # the filename further if the full path would overflow.
        if len(str(dest)) > 240:
            stem_budget = 240 - len(str(dest_dir)) - len("__") - len(".nzb")
            if stem_budget > 20:
                stem = re.sub(r'\.nzb$', "", safe, flags=re.IGNORECASE)
                safe = stem[:stem_budget] + "_" + guid[:8] + ".nzb"
            else:
                # Last-resort: just use the GUID as the filename.
                safe = f"{guid}.nzb"
            dest = dest_dir / safe

        api_url = (f"{url_base}/api?"
                   + urllib.parse.urlencode({
                       "t": "get", "id": guid, "apikey": api_key}))
        self._set_status(f"Fetching NZB: {release_title[:60]}…")
        self._log(f"Usenet GET: {release_title}")

        def task() -> None:
            try:
                req = urllib.request.Request(
                    api_url,
                    headers={"User-Agent": f"OnePaceDownloader/{APP_VERSION}"})
                with tls_trust.urlopen(req, timeout=60) as r:
                    blob = r.read()
                if len(blob) < 200 or b"<nzb" not in blob[:2000]:
                    # Likely an error response, not an NZB
                    raise ValueError(
                        "Indexer didn't return an NZB. "
                        "Check your API key and that the indexer is up.")
                dest.write_bytes(blob)
                self.ui_queue.put(("usenet_nzb_ready", str(dest)))
            except Exception as e:  # noqa: BLE001
                self.ui_queue.put(("usenet_nzb_error", str(e)))
            finally:
                # Always drop the in-flight marker so a retry is possible
                self._usenet_inflight.discard(guid)

        threading.Thread(target=task, daemon=True).start()

    def _on_usenet_nzb_ready(self, path: str) -> None:
        """Open the saved .nzb with the OS default handler (SABnzbd etc.).
        If nothing is registered for .nzb, fall back to showing the file in
        Explorer so the user can sort it themselves."""
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            self._log(f"NZB handed to Usenet client: {Path(path).name}")
            self._set_status("NZB saved and opened in your Usenet client.")
        except (OSError, AttributeError):
            # No registered handler for .nzb — open the folder instead
            try:
                os.startfile(str(Path(path).parent))  # type: ignore[attr-defined]
            except Exception:
                pass
            self._log("No .nzb handler registered — opened folder instead.")
            self._set_status("NZB saved. Open it in SABnzbd / NZBGet manually.")

    def _on_usenet_nzb_error(self, msg: str) -> None:
        self._log(f"Usenet NZB fetch failed: {msg}")
        self._set_status("Usenet NZB fetch failed.")
        messagebox.showerror("Usenet", f"Couldn't fetch NZB:\n\n{msg}")

    def _show_usenet_setup_prompt(self) -> None:
        """Friendly nudge when the user clicks Get NZB without configuring
        the indexer. Uses the shared ModalOverlay so it matches the look of
        the Settings / DNS panels."""
        overlay = ModalOverlay(self, "Set up Usenet", width=480, height=320)
        card = overlay.card

        ctk.CTkLabel(card, text="Add your Usenet indexer",
                     font=F_BOLD_XL, text_color=TEXT, anchor="w").pack(
            fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ctk.CTkLabel(
            card, anchor="w", justify="left", wraplength=400,
            font=F_SM, text_color=TEXT_MUTED,
            text=("Usenet downloads need your own indexer URL + API key. "
                  "Configure them once in Settings and the Get NZB buttons "
                  "will start working.\n\n"
                  "Don't have a Usenet provider yet? You'll need one "
                  "(e.g. Newshosting, Eweka) plus an indexer like NZBGeek "
                  "to use this feature."),
        ).pack(fill="x", padx=SP_XL, pady=(0, SP_LG))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(side="bottom", pady=(SP_SM, SP_XL))
        ctk.CTkButton(
            btn_row, text="Open Settings", width=140, height=H_MD,
            font=F_BOLD_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=lambda: (overlay.destroy(),
                             self._open_settings_panel()),
        ).pack(side="left", padx=SP_XS)
        ctk.CTkButton(
            btn_row, text="Setup guide", width=120, height=H_MD,
            font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG, text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=lambda: webbrowser.open(USENET_SETUP_GUIDE_URL),
        ).pack(side="left", padx=SP_XS)
        ctk.CTkButton(
            btn_row, text="Close", width=90, height=H_MD,
            font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG, text_color=TEXT_MUTED,
            hover_color=SURFACE_HOVER,
            command=overlay.destroy,
        ).pack(side="left", padx=SP_XS)

    # ----------------------------------------------- DNS / settings ------

    def _open_dns_panel(self) -> None:
        self._dns_panel_ref = DnsPanel(self)

    def _open_settings_panel(self) -> None:
        self._settings_panel_ref = SettingsPanel(self)

    # --------------------------------------------- updates-available badge

    def _set_refresh_badge(self, available: bool) -> None:
        """Update the header Refresh button to advertise pending updates.
        Yellow-tinted + arrow when something new is on onepace.net since
        our last full refresh; plain when in sync."""
        btn = getattr(self, "_refresh_btn", None)
        if btn is None:
            return
        try:
            if available:
                btn.configure(
                    text="↻  Refresh", text_color=HEADER_ACCENT,
                    border_color=HEADER_ACCENT)
            else:
                btn.configure(
                    text="Refresh", text_color=HEADER_FG,
                    border_color="#2C3D5C")
        except Exception:
            pass

    def _check_for_updates_async(self) -> None:
        """Fire-and-forget: HEAD request against the central data branch on
        GitHub and compare ETag with the cached one. If newer, flip the
        Refresh button to its "↻ updates available" badge state.

        Runs in a daemon thread so a slow / blocked CDN never freezes the
        UI. All failures are silent — at worst the user just doesn't see
        the badge."""
        cached = (self.config_data.get("refresh_cache") or {}).get(
            "remote_etag")

        def task() -> None:
            try:
                # HEAD is cheap (~200ms). raw.githubusercontent serves ETag.
                req = urllib.request.Request(
                    REMOTE_INDEX_URL, method="HEAD",
                    headers={"User-Agent":
                             f"OnePaceDownloader/{APP_VERSION}"})
                with tls_trust.urlopen(req, timeout=15) as r:
                    etag = r.headers.get("ETag") or ""
                if etag and etag != cached:
                    self.ui_queue.put(("updates_available", True))
            except Exception:
                # GitHub unreachable, data branch missing, etc. — fail silent.
                # On the next user-initiated Refresh we'll log the failure
                # and fall back to the local scrape.
                pass

        threading.Thread(target=task, daemon=True).start()

    def _refresh_all(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy",
                "A download is in progress. Cancel it first.")
            return
        force = bool(getattr(self, "_force_full_refresh", False))
        if not force and not messagebox.askyesno(
            "Check for updates",
            "Pull the latest episode index. Normally this is a quick "
            "download (~2 seconds) of a pre-built catalog that's "
            "regenerated weekly on GitHub.\n\n"
            "Continue?"):
            return

        self._set_status(
            "Checking for updates…")
        # Switch progress bar into indeterminate mode for the duration so
        # users see the app is actually working.
        try:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        except Exception:
            pass
        self.refresh_in_progress = True

        # Clear the "updates available" badge once a real refresh starts —
        # whether it finds anything new or not, we're about to be in sync.
        self._set_refresh_badge(False)

        if force:
            # One-shot — clear the flag so the next refresh is remote-first.
            self._force_full_refresh = False
            self.ui_queue.put(("log", "Force-full local rebuild requested — "
                                "skipping the central catalog."))

        def task():
            try:
                if not force and self._try_remote_refresh():
                    # Happy path: GitHub data branch served the catalog.
                    self.ui_queue.put(("status", "Sources refreshed."))
                    return
                # Either user asked for force, or GitHub is unreachable /
                # missing the data branch. Fall back to the per-user scrape.
                self._do_local_refresh(force=force)
            except Exception as e:
                self.ui_queue.put(("error", f"Refresh failed: {e}"))
            finally:
                self.ui_queue.put(("refresh_done", None))

        threading.Thread(target=task, daemon=True).start()

    def _try_remote_refresh(self) -> bool:
        """Fetch the pre-built index from the repo's `data` branch.
        Returns True on success (and reloads the index), False if anything
        goes wrong so the caller can fall back to the local scrape.

        The catalog is small (~500 KB) so a full GET is fast and we don't
        bother with HEAD + ETag — the request itself is the check."""
        self.ui_queue.put(("log",
            f"Fetching latest index from GitHub (data branch)…"))
        try:
            req = urllib.request.Request(
                REMOTE_INDEX_URL,
                headers={"User-Agent": f"OnePaceDownloader/{APP_VERSION}"})
            with tls_trust.urlopen(req, timeout=30) as r:
                etag = r.headers.get("ETag") or ""
                blob = r.read()
        except Exception as e:
            self.ui_queue.put(("log",
                f"Central refresh unavailable ({e}) — falling back to "
                "local scrape."))
            return False

        # Skip the disk write + reload if nothing actually changed.
        cached_etag = (self.config_data.get("refresh_cache") or {}).get(
            "remote_etag")
        if etag and etag == cached_etag and INDEX_FILE.exists():
            self.ui_queue.put(("log", "Already up to date."))
            self.ui_queue.put(("reload_index", None))
            return True

        # Quick sanity-check it's valid JSON with the shape we expect before
        # overwriting the user's working copy.
        try:
            data = json.loads(blob)
            if not isinstance(data.get("arcs"), list):
                raise ValueError("Remote payload missing 'arcs' list")
        except Exception as e:
            self.ui_queue.put(("log",
                f"Remote payload looks malformed ({e}) — falling back."))
            return False

        INDEX_FILE.write_bytes(blob)
        rc = dict(self.config_data.get("refresh_cache", {}))
        if etag:
            rc["remote_etag"] = etag
        rc["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.config_data["refresh_cache"] = rc
        save_config(self.config_data)

        scraped = data.get("scraped_at", "?")
        self.ui_queue.put(("log",
            f"Pulled fresh index (scraped {scraped}, "
            f"{len(data['arcs'])} arcs)."))
        self.ui_queue.put(("reload_index", None))
        return True

    def _do_local_refresh(self, force: bool = False) -> None:
        """Per-user fallback scrape — the original behavior. Used when the
        central catalog is unreachable, or when the user explicitly asks for
        a full rebuild via Settings."""
        refresh_cache = dict(self.config_data.get("refresh_cache", {}))
        self.ui_queue.put(("log", "Refreshing onepace.net…"))
        refresh_arcs_from_web()
        self.ui_queue.put(("log", "Refreshing nyaa.si…"))
        arc_titles = [a["title"] for a in load_arcs()]
        if arc_titles:
            refresh_nyaa_from_web(arc_titles)
        self.ui_queue.put(("log", "Rebuilding episode index…"))
        import build_episode_index
        build_episode_index.build(
            log=lambda m: self.ui_queue.put(("log", m)),
            cancel_evt=self.cancel_evt,
            cache=refresh_cache,
            force=force,
        )
        self.config_data["refresh_cache"] = refresh_cache
        save_config(self.config_data)
        self.ui_queue.put(("reload_index", None))
        self.ui_queue.put(("status", "Sources refreshed."))
        self.ui_queue.put(("log", "All sources up to date."))

    # ----------------------------------------------- folder & settings ---

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.save_dir.get() or str(DEFAULT_DOWNLOADS))
        if chosen:
            self.save_dir.set(chosen)
            self._persist_settings()
            ok, msg = self._validate_save_dir(Path(chosen))
            if not ok:
                messagebox.showwarning("Folder problem", msg)

    @staticmethod
    def _validate_save_dir(path: Path) -> tuple[bool, str]:
        """Sanity-check the save folder before kicking off a download.
        Returns (ok, message). Creates the folder if missing."""
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, (
                f"Couldn't create the save folder:\n  {path}\n\n{e}\n\n"
                "Pick a different folder.")
        if not os.access(path, os.W_OK):
            return False, (
                f"This folder isn't writable:\n  {path}\n\n"
                "Pick a folder you have permission to write to "
                "(e.g. Documents or your Desktop).")
        return True, ""

    def _open_folder(self) -> None:
        path = Path(self.save_dir.get())
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            self._log(f"Folder: {path}")

    def _persist_settings(self) -> None:
        prev = self.config_data.get("save_folder", "")
        new = self.save_dir.get()
        self.config_data["save_folder"] = new
        save_config(self.config_data)
        # When the user picks a new folder, re-scan it so Saved badges
        # reflect the new location's contents.
        if prev != new:
            self._refresh_downloaded_files(rerender=True)

    # ---------------------------------------------- downloaded-file scan -

    def _scan_downloaded_files(self) -> tuple[set[str], set[str]]:
        """Walk the save folder collecting (filenames, plex_keys).
        filenames is the set of every non-.part basename seen. plex_keys are
        normalized 'sNNeMM' identifiers extracted from any Plex-organized
        downloads, so the Saved badge keeps working even after the user has
        enabled media-server mode and we've renamed files."""
        save = Path(self.save_dir.get() or "")
        if not save.exists() or not save.is_dir():
            return set(), set()
        names: set[str] = set()
        plex_keys: set[str] = set()
        plex_re = re.compile(r"\bs(\d{2})e(\d{2})\b", re.IGNORECASE)
        try:
            for dirpath, _, filenames in os.walk(save):
                for fn in filenames:
                    if fn.endswith(".part"):
                        continue
                    names.add(fn)
                    m = plex_re.search(fn)
                    if m:
                        plex_keys.add(
                            f"s{m.group(1)}e{m.group(2)}".lower())
        except OSError:
            pass
        return names, plex_keys

    def _refresh_downloaded_files(self, rerender: bool = True) -> None:
        """Update the cached downloaded-file set and optionally re-render
        the episode list so Saved badges refresh."""
        self.downloaded_files, self.saved_plex_keys = (
            self._scan_downloaded_files())
        if rerender:
            # Update per-arc progress badges in place (no full re-render)
            self._refresh_arc_meta_labels()
            if self.selected_arc is not None:
                self._render_episode_list()

    def _is_episode_saved(self, ep: dict, arc: dict | None = None) -> bool:
        """True if any source for the active tab has a filename already
        present in save_dir, or if a Plex-organized counterpart for this
        (arc, episode) exists."""
        for s in ep.get("sources", []):
            if s.get("kind") != self.current_source:
                continue
            fn = s.get("filename")
            if fn and sanitize_filename(fn) in self.downloaded_files:
                return True
            if fn and fn in self.downloaded_files:
                return True
        if arc is not None and self.saved_plex_keys:
            arc_idx = self._arc_index_for_title(arc.get("title", ""))
            if arc_idx is not None:
                key = f"s{arc_idx + 1:02d}e{ep.get('num', 0):02d}"
                if key in self.saved_plex_keys:
                    return True
        return False

    # ----------------------------------------------- progress / log -----

    def _toggle_log(self) -> None:
        if self._log_visible:
            self.log_panel.pack_forget()
            self._log_visible = False
        else:
            self.log_panel.pack(fill="x", side="bottom",
                                padx=12, pady=(0, 8))
            self._log_visible = True

    def _cancel(self) -> None:
        self.cancel_evt.set()
        self._log("Cancel requested…")

    def _set_cancel_visible(self, visible: bool) -> None:
        """Pack/unpack the Cancel button so it only appears when something is
        actually cancellable. A permanently-visible disabled red button looks
        broken."""
        try:
            if visible and not self.cancel_btn.winfo_ismapped():
                self.cancel_btn.pack(side="left")
            elif not visible and self.cancel_btn.winfo_ismapped():
                self.cancel_btn.pack_forget()
        except Exception:
            pass

    def _on_progress(self, frac: float, speed: str, idx: int,
                     total: int, bytes_now: int) -> None:
        self.ui_queue.put(("progress", (frac, speed, idx, total, bytes_now)))

    def _log(self, msg: str) -> None:
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", msg + "\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "progress":
                    frac, speed, idx, total, bytes_now = payload
                    if str(self.progress.cget("mode")) == "indeterminate":
                        # A download started while refresh was running; reset
                        try:
                            self.progress.stop()
                            self.progress.configure(mode="determinate")
                        except Exception:
                            pass
                    self.progress.set(min(max(frac, 0.0), 1.0))
                    self._set_status(
                        f"[{idx}/{total}]  {fmt_bytes(bytes_now)}  •  {speed}")
                    # Live-update the prominent right-column download panel
                    if self.download_state is not None:
                        st = self.download_state
                        st["frac"] = frac
                        st["speed"] = speed
                        st["idx"] = idx
                        st["total"] = total
                        st["bytes_now"] = bytes_now
                        self._update_download_panel(st)
                elif kind == "log":
                    self._log(payload)
                elif kind == "error":
                    self._log("ERROR: " + payload)
                    # Auto-expand the log so the user sees what happened
                    if not self._log_visible:
                        self._toggle_log()
                    messagebox.showerror("Error", payload)
                elif kind == "done":
                    self._set_cancel_visible(False)
                    self.download_title = None
                    self.download_state = None
                    self._download_panel_widgets.clear()
                    # Reset the slow-detection timer (but keep
                    # slow_download_noted sticky for the session).
                    self._dl_started_at = None
                    try:
                        self.progress.set(0)
                        if not self.progress.winfo_ismapped():
                            self.progress.pack(fill="x")
                    except Exception:
                        pass
                    # Re-scan save folder so the just-completed episode
                    # shows up as Saved without needing an app restart.
                    self._refresh_downloaded_files(rerender=True)
                    self._render_source_panel()
                elif kind == "reload_index":
                    # New episode_index.json was just written by Refresh —
                    # reload and re-render every column.
                    self.index = load_episode_index()
                    self._tab_labels = self._compute_tab_labels()
                    self._tab_key_by_label = {v: k for k, v in self._tab_labels.items()}
                    self.source_tabs.configure(values=list(self._tab_labels.values()))
                    self.source_tabs.set(self._tab_labels[self.current_source])
                    # Drop the cached coverage so it recomputes against the new index
                    self._global_cov_cache = None
                    self._render_arc_list()
                    self._render_episode_list()
                    self._render_source_panel()
                elif kind == "usenet_nzb_ready":
                    self._on_usenet_nzb_ready(payload)
                elif kind == "usenet_nzb_error":
                    self._on_usenet_nzb_error(payload)
                elif kind == "updates_available":
                    self._set_refresh_badge(bool(payload))
                elif kind == "usenet_test_result":
                    success, msg = payload
                    if hasattr(self, "_settings_panel_ref") and self._settings_panel_ref and self._settings_panel_ref.winfo_exists():
                        color = OK if success else DANGER
                        self._settings_panel_ref.test_status_lbl.configure(text=msg, text_color=color)
        except queue.Empty:
            pass
        # Slow the polling cadence when idle to be friendlier on battery.
        idle = (self.worker is None or not self.worker.is_alive()) \
               and not getattr(self, "refresh_in_progress", False)
        self.after(200 if idle else 80, self._drain_ui_queue)

    def _toggle_checkbox(self, num: int) -> None:
        if num in self.ep_check_vars:
            var = self.ep_check_vars[num]
            var.set(not var.get())

    def _on_checkbox_clicked(self, event, num: int) -> None:
        is_shift = (event.state & 0x0001) != 0
        target_state = not self.ep_check_vars[num].get()
        
        prev_last = getattr(self, "_last_clicked_ep_num", None)
        self._last_clicked_ep_num = num
        
        if is_shift and prev_last is not None and prev_last != num:
            arc = self.selected_arc
            if not arc:
                return
            src = self.current_source
            visible_eps = [
                e["num"] for e in arc.get("episodes", [])
                if any(s.get("kind") == src for s in e.get("sources", []))
            ]
            if num in visible_eps and prev_last in visible_eps:
                idx1 = visible_eps.index(prev_last)
                idx2 = visible_eps.index(num)
                start_idx, end_idx = min(idx1, idx2), max(idx1, idx2)
                
                self._updating_checks = True
                try:
                    for idx in range(start_idx, end_idx + 1):
                        ep_num = visible_eps[idx]
                        self.ep_check_vars[ep_num].set(target_state)
                finally:
                    self._updating_checks = False
                self._refresh_selection_buttons()

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Exit", "A download is currently in progress. Exit anyway?"):
                return
            self.cancel_evt.set()
        
        is_zoomed = (self.state() == "zoomed")
        self.config_data["maximized"] = is_zoomed
        if not is_zoomed:
            self.config_data["geometry"] = self.geometry()
            
        self.config_data.update({
            "save_folder": self.save_dir.get(),
            "source": self.current_source,
        })
        save_config(self.config_data)
        self.destroy()


# ======================================================== Modal Overlay ===

class ModalOverlay(ctk.CTkFrame):
    """An elegant in-app overlay modal container. Dims the main app window
    and centers a card containing the modal panel's content."""
    
    def __init__(self, parent, title_text: str, width: int = 560, height: int = 480) -> None:
        overlay_color = ("#D0D4DC", "#0A101D")
        super().__init__(parent, fg_color=overlay_color)
        
        self.parent_app = parent
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        self.card = ctk.CTkFrame(
            self,
            width=width,
            height=height,
            fg_color=SURFACE_PANEL,
            corner_radius=RADIUS_LG,
            border_width=1,
            border_color=BORDER,
        )
        self.card.place(relx=0.5, rely=0.5, anchor="center")
        self.card.grid_propagate(False)
        self.card.pack_propagate(False)

        self.bind("<Button-1>", lambda _: "break")
        # Esc closes the modal — bound on the toplevel so it fires regardless
        # of which inner widget has focus.
        self._esc_bind = parent.winfo_toplevel().bind(
            "<Escape>", lambda _e: self.destroy(), add="+")
        self.focus_set()
        
    def destroy(self) -> None:
        # Release the Esc binding so it doesn't pile up across modal opens.
        try:
            self.parent_app.winfo_toplevel().unbind("<Escape>", self._esc_bind)
        except Exception:
            pass
        super().destroy()
        try:
            self.parent_app.focus_set()
        except Exception:
            pass


# ======================================================== Batch Download =

class BatchDownloadDialog(ModalOverlay):
    """Confirmation modal for 'Download selected' on One Pace / Muhn tabs.
    Shows the count, a per-batch version+quality picker, and a live total-size
    estimate computed from the episode index."""

    def __init__(self, parent, *, source: str, episodes: list[dict],
                 default_version: str, default_quality: str) -> None:
        super().__init__(parent, "Confirm batch download", width=560, height=480)
        self.source = source
        self.episodes = episodes
        self.confirmed = False
        self.chosen_version = default_version
        self.chosen_quality = default_quality

        btn_row = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row.pack(side="bottom", pady=(SP_LG, SP_MD))
        ctk.CTkButton(
            btn_row, text="Cancel", width=120, height=H_MD,
            font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=self._cancel,
        ).pack(side="left", padx=SP_XS)
        ctk.CTkButton(
            btn_row, text="Start download", width=180, height=H_MD,
            font=F_BOLD_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=self._confirm,
        ).pack(side="left", padx=SP_XS)

        ctk.CTkLabel(
            self.card,
            text=f"Download {len(episodes)} episode"
                 f"{'s' if len(episodes) != 1 else ''}",
            font=F_BOLD_XL, text_color=TEXT, anchor="w",
        ).pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_XS))
        ctk.CTkLabel(
            self.card, text=f"From {_SRC_LABEL[source]}",
            font=F_SM, text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", padx=SP_XL, pady=(0, SP_MD))

        opt = ctk.CTkFrame(
            self.card, fg_color=SURFACE_CARD, corner_radius=RADIUS_LG,
            border_width=1, border_color=BORDER,
        )
        opt.pack(fill="x", padx=SP_XL, pady=SP_SM)

        if source == "onepace":
            ctk.CTkLabel(opt, text="VERSION",
                         font=F_BOLD_SM, text_color=TEXT_MUTED,
                         anchor="w").pack(fill="x",
                                           padx=SP_LG, pady=(SP_MD, 2))
            self.version_var = ctk.StringVar(value=default_version)
            ctk.CTkComboBox(
                opt, variable=self.version_var, values=VERSIONS,
                state="readonly", height=H_SM, font=F_SM,
                border_color=BORDER,
                button_color=SECONDARY,
                button_hover_color=SECONDARY_HOVER,
                dropdown_fg_color=SURFACE_INNER,
                dropdown_hover_color=SURFACE_HOVER,
                dropdown_text_color=TEXT,
                fg_color=SURFACE_INNER,
                command=lambda _v: self._update_size_preview(),
            ).pack(fill="x", padx=SP_LG, pady=(0, SP_MD))
        else:
            self.version_var = ctk.StringVar(value="English Dub")

        ctk.CTkLabel(opt, text="QUALITY",
                     font=F_BOLD_SM, text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x",
                                       padx=SP_LG, pady=(SP_SM, 2))
        self.quality_var = ctk.StringVar(value=default_quality)
        ctk.CTkComboBox(
            opt, variable=self.quality_var, values=QUALITIES,
            state="readonly", height=H_SM, font=F_SM,
            border_color=BORDER,
            button_color=SECONDARY,
            button_hover_color=SECONDARY_HOVER,
            dropdown_fg_color=SURFACE_INNER,
            dropdown_hover_color=SURFACE_HOVER,
            dropdown_text_color=TEXT,
            fg_color=SURFACE_INNER,
            command=lambda _v: self._update_size_preview(),
        ).pack(fill="x", padx=SP_LG, pady=(0, SP_MD))

        size_card = ctk.CTkFrame(
            self.card, fg_color=("#FBF6E8", "#1F2A18"),
            corner_radius=RADIUS_LG,
            border_width=1, border_color=BORDER,
        )
        size_card.pack(fill="x", padx=SP_XL, pady=SP_SM)
        ctk.CTkLabel(
            size_card, text="TOTAL DOWNLOAD SIZE",
            font=F_BOLD_SM, text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", padx=SP_LG, pady=(SP_MD, 0))
        self.size_lbl = ctk.CTkLabel(
            size_card, text="—",
            font=F_BOLD_LG, text_color=TEXT, anchor="w",
        )
        self.size_lbl.pack(fill="x", padx=SP_LG, pady=(0, 2))
        self.skipped_lbl = ctk.CTkLabel(
            size_card, text="",
            font=F_XS, text_color=WARN, anchor="w",
            wraplength=440, justify="left",
        )
        self.skipped_lbl.pack(fill="x", padx=SP_LG, pady=(0, SP_MD))

        self._update_size_preview()

    def _update_size_preview(self) -> None:
        ver = self.version_var.get()
        qual = self.quality_var.get()
        total_bytes = 0
        skipped = 0
        for ep in self.episodes:
            best = App._best_source_for(ep, self.source, ver, qual)
            if best:
                total_bytes += int(best.get("size_bytes", 0))
            else:
                skipped += 1
        self.size_lbl.configure(
            text=_fmt_size(total_bytes) if total_bytes else "—")
        self.skipped_lbl.configure(
            text=(f"⚠ {skipped} episode(s) have no matching source — "
                   "will be skipped." if skipped else ""))

    def _confirm(self) -> None:
        self.chosen_version = self.version_var.get()
        self.chosen_quality = self.quality_var.get()
        self.confirmed = True
        self.destroy()

    def _cancel(self) -> None:
        self.confirmed = False
        self.destroy()


# ============================================================= DNS panel ==

class DnsPanel(ModalOverlay):
    """One-click DNS switcher — status card on top, action buttons next,
    collapsible explainer at the bottom. Uses the shared modal token set."""

    def __init__(self, parent: App) -> None:
        super().__init__(parent, "DNS Switcher", width=560, height=460)

        ctk.CTkLabel(
            self.card, text="DNS Switcher",
            font=F_BOLD_XL, text_color=TEXT, anchor="w",
        ).pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_XS))
        ctk.CTkLabel(
            self.card, text="Bypass ISP-level DNS blocks of the download CDN.",
            font=F_SM, text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", padx=SP_XL, pady=(0, SP_MD))

        card = ctk.CTkFrame(
            self.card, fg_color=SURFACE_CARD, corner_radius=RADIUS_LG,
            border_width=1, border_color=BORDER,
        )
        card.pack(fill="x", padx=SP_XL, pady=SP_SM)
        self.state_lbl = ctk.CTkLabel(
            card, text="Detecting…",
            anchor="w", font=F_BOLD_LG, text_color=TEXT,
        )
        self.state_lbl.pack(fill="x", padx=SP_LG, pady=(SP_MD, SP_XS))
        self.iface_lbl = ctk.CTkLabel(
            card, text="", anchor="w", font=F_MONO_SM,
            text_color=TEXT_MUTED)
        self.iface_lbl.pack(fill="x", padx=SP_LG, pady=(0, 2))
        self.dns_lbl = ctk.CTkLabel(
            card, text="", anchor="w", font=F_MONO_SM,
            text_color=TEXT_MUTED)
        self.dns_lbl.pack(fill="x", padx=SP_LG, pady=(0, SP_MD))

        btn_row1 = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row1.pack(fill="x", padx=SP_XL, pady=(SP_MD, SP_XS))
        self.cloudflare_btn = ctk.CTkButton(
            btn_row1, text="Switch to Cloudflare (1.1.1.1)",
            height=H_LG, font=F_BOLD_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=self._switch_cloudflare)
        self.cloudflare_btn.pack(fill="x")

        btn_row2 = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row2.pack(fill="x", padx=SP_XL, pady=SP_XS)
        self.google_btn = ctk.CTkButton(
            btn_row2, text="Try Google DNS (8.8.8.8) instead",
            height=H_MD, font=F_SM,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            command=self._switch_google)
        self.google_btn.pack(side="left", fill="x", expand=True,
                              padx=(0, SP_XS))
        self.revert_btn = ctk.CTkButton(
            btn_row2, text="Revert to DHCP",
            height=H_MD, font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=self._revert)
        self.revert_btn.pack(side="left", fill="x", expand=True,
                              padx=(SP_XS, 0))

        self.status_lbl = ctk.CTkLabel(
            self.card, text="", font=F_XS,
            text_color=TEXT_MUTED)
        self.status_lbl.pack(pady=(SP_SM, 0))

        self._explainer_visible = False
        self.explainer_toggle = ctk.CTkButton(
            self.card, text="Why might I need this? ▾",
            fg_color="transparent",
            hover_color=SURFACE_HOVER,
            text_color=LINK,
            font=F_XS,
            command=self._toggle_explainer)
        self.explainer_toggle.pack(pady=(SP_MD, 0))
        self.explainer_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        ctk.CTkLabel(
            self.explainer_frame, justify="left",
            text="Some Indian ISPs (Jio, Airtel, BSNL, ACT) block the "
                 "Pixeldrain CDN at the DNS level — the app loads but "
                 "downloads never start. Switching DNS to Cloudflare or "
                 "Google bypasses that block. If one stops working, try "
                 "the other.",
            font=F_XS, wraplength=460,
            text_color=TEXT_MUTED,
        ).pack(padx=SP_XL, pady=(SP_XS, SP_XS), anchor="w", fill="x")
        ctk.CTkButton(
            self.explainer_frame, text="Full troubleshooting guide →",
            fg_color="transparent",
            hover_color=SURFACE_HOVER,
            text_color=LINK, font=F_XS,
            command=lambda: webbrowser.open(
                "https://github.com/Nicolaslahri/onepacedownloader#downloads-not-starting")
        ).pack(pady=(0, SP_SM))

        ctk.CTkButton(
            self.card, text="Close panel",
            height=H_MD, font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=self.destroy
        ).pack(side="bottom", pady=(0, SP_MD))

        self._refresh_state()

    def _refresh_state(self) -> None:
        iface = dns_switcher.detect_active_interface()
        if not iface:
            self.state_lbl.configure(text="No active network detected")
            self.iface_lbl.configure(
                text="Connect to Wi-Fi or Ethernet, then reopen this panel.")
            self.dns_lbl.configure(text="")
            self.cloudflare_btn.configure(state="disabled")
            self.google_btn.configure(state="disabled")
            self.revert_btn.configure(state="disabled")
            return
        ips, dhcp = dns_switcher.get_current_dns(iface)
        on_cloudflare = (dns_switcher.CLOUDFLARE_PRIMARY in ips)
        on_google = (dns_switcher.GOOGLE_PRIMARY in ips)
        if on_cloudflare:
            self.state_lbl.configure(
                text="● Using Cloudflare DNS", text_color=OK)
        elif on_google:
            self.state_lbl.configure(
                text="● Using Google DNS", text_color=OK)
        elif dhcp:
            self.state_lbl.configure(
                text="Using your ISP's DNS (auto)", text_color=TEXT)
        else:
            self.state_lbl.configure(
                text="Using static custom DNS", text_color=TEXT)
        self.iface_lbl.configure(text=f"Interface: {iface}")
        if dhcp:
            ips_str = "auto via DHCP"
            if ips:
                ips_str += f"  →  {', '.join(ips)}"
        else:
            ips_str = ", ".join(ips) if ips else "none configured"
        self.dns_lbl.configure(text=f"DNS: {ips_str}")

    def _switch_cloudflare(self) -> None:
        self._do_switch(dns_switcher.switch_to_cloudflare,
                        dns_switcher.CLOUDFLARE_PRIMARY,
                        "Cloudflare")

    def _switch_google(self) -> None:
        self._do_switch(dns_switcher.switch_to_google,
                        dns_switcher.GOOGLE_PRIMARY,
                        "Google")

    def _do_switch(self, fn, expected_primary: str, label: str) -> None:
        iface = dns_switcher.detect_active_interface()
        if not iface:
            self.status_lbl.configure(text="No active interface — can't switch.")
            return
        before, _ = dns_switcher.get_current_dns(iface)
        self.status_lbl.configure(text="Awaiting UAC prompt…")
        self.update()
        ok = fn(iface)
        if not ok:
            self.status_lbl.configure(text="UAC declined — DNS unchanged.")
            return
        cfg = self.parent_app.config_data
        if "dns_backup" not in cfg:
            cfg["dns_backup"] = {"iface": iface, "ips": before}
            save_config(cfg)
        after = dns_switcher.wait_for_dns_change(iface, before)
        if expected_primary in after:
            self.status_lbl.configure(
                text=f"Switched to {label} at {time.strftime('%H:%M')}.")
        else:
            self.status_lbl.configure(
                text="Command ran but DNS didn't change. "
                     "Check Windows network settings.")
        self._refresh_state()

    def _toggle_explainer(self) -> None:
        if self._explainer_visible:
            self.explainer_frame.pack_forget()
            self.explainer_toggle.configure(text="Why might I need this? ▾")
            self._explainer_visible = False
        else:
            self.explainer_frame.pack(fill="x", pady=(2, 8))
            self.explainer_toggle.configure(text="Why might I need this? ▴")
            self._explainer_visible = True

    def _revert(self) -> None:
        iface = dns_switcher.detect_active_interface()
        if not iface:
            self.status_lbl.configure(text="No active interface.")
            return
        before, _ = dns_switcher.get_current_dns(iface)
        self.status_lbl.configure(text="Awaiting UAC prompt…")
        self.update()
        ok = dns_switcher.revert_to_dhcp(iface)
        if not ok:
            self.status_lbl.configure(text="UAC declined — DNS unchanged.")
            return
        dns_switcher.wait_for_dns_change(iface, before)
        self.status_lbl.configure(
            text=f"Reverted at {time.strftime('%H:%M')}.")
        self._refresh_state()


# ========================================================= Settings panel ==

class SettingsPanel(ModalOverlay):
    """Modal overlay for default version/quality, appearance, and quick links."""

    DEFAULT_VERSION = "English Subtitles"
    DEFAULT_QUALITY = "1080p"
    DEFAULT_APPEARANCE = "System"
    DEFAULT_ORGANIZE = False
    DEFAULT_USENET_URL = USENET_DEFAULT_INDEXER_URL

    # Sidebar navigation — name → builder method.
    _SECTION_ORDER = ("General", "Output", "Usenet", "Updates", "About")

    def __init__(self, parent: App) -> None:
        super().__init__(parent, "Settings", width=720, height=520)
        cfg = parent.config_data
        self._original_appearance = cfg.get(
            "appearance", self.DEFAULT_APPEARANCE)

        # Shared state vars (read by _save / _restore_defaults regardless of
        # which sections the user actually opened).
        self.version_var = ctk.StringVar(
            value=cfg.get("default_version", self.DEFAULT_VERSION))
        self.quality_var = ctk.StringVar(
            value=cfg.get("default_quality", self.DEFAULT_QUALITY))
        self.appearance_var = ctk.StringVar(value=self._original_appearance)
        self.organize_var = ctk.BooleanVar(
            value=cfg.get("organize_for_media_server", self.DEFAULT_ORGANIZE))
        self.usenet_url_var = ctk.StringVar(
            value=cfg.get("usenet_indexer_url", self.DEFAULT_USENET_URL))
        self.usenet_key_var = ctk.StringVar(
            value=cfg.get("usenet_api_key", ""))

        # ---- Title bar (title + close X) ----
        titlebar = ctk.CTkFrame(self.card, fg_color="transparent", height=44)
        titlebar.pack(fill="x", padx=SP_XL, pady=(SP_LG, SP_SM))
        titlebar.pack_propagate(False)
        ctk.CTkLabel(titlebar, text="Settings", font=F_BOLD_XL,
                     text_color=TEXT, anchor="w").pack(side="left")
        ctk.CTkButton(
            titlebar, text="✕", width=32, height=32, font=F_BOLD_BASE,
            fg_color="transparent", hover_color=SURFACE_HOVER,
            text_color=TEXT_MUTED, command=self._rollback_and_close,
        ).pack(side="right")

        # ---- Footer (Restore / Save) pinned to the bottom ----
        self._build_footer_band()

        # ---- Body: left sidebar nav + right content pane ----
        body = ctk.CTkFrame(self.card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=SP_XL, pady=(0, SP_MD))

        self._sidebar = ctk.CTkFrame(
            body, fg_color=SURFACE_CARD, corner_radius=RADIUS_LG,
            width=148, border_width=1, border_color=BORDER)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._content = ctk.CTkFrame(
            body, fg_color=SURFACE_CARD, corner_radius=RADIUS_LG,
            border_width=1, border_color=BORDER)
        self._content.pack(side="left", fill="both", expand=True,
                           padx=(SP_MD, 0))

        self._section_frames: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_section: str | None = None
        for i, name in enumerate(self._SECTION_ORDER):
            btn = ctk.CTkButton(
                self._sidebar, text=name, anchor="w",
                font=F_SM, height=H_MD, corner_radius=RADIUS_SM,
                fg_color="transparent", text_color=TEXT,
                hover_color=SURFACE_HOVER,
                command=lambda n=name: self._show_section(n))
            btn.pack(fill="x", padx=SP_XS,
                     pady=(SP_SM if i == 0 else 2, 2))
            self._nav_buttons[name] = btn

        self._show_section("General")

    # ----------------------------------------------- section plumbing ----

    def _show_section(self, name: str) -> None:
        if name == self._active_section:
            return
        if (self._active_section
                and self._active_section in self._section_frames):
            self._section_frames[self._active_section].pack_forget()
        # Build the section's widgets lazily the first time it's opened.
        if name not in self._section_frames:
            frame = ctk.CTkFrame(self._content, fg_color="transparent")
            builder = {
                "General": self._build_general_section,
                "Output":  self._build_output_section,
                "Usenet":  self._build_usenet_section,
                "Updates": self._build_updates_section,
                "About":   self._build_about_section,
            }[name]
            builder(frame)
            self._section_frames[name] = frame
        self._section_frames[name].pack(fill="both", expand=True,
                                        padx=SP_LG, pady=SP_LG)
        for n, btn in self._nav_buttons.items():
            if n == name:
                btn.configure(fg_color=PRIMARY, text_color="#FFFFFF")
            else:
                btn.configure(fg_color="transparent", text_color=TEXT)
        self._active_section = name

    @staticmethod
    def _section_title(parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=F_BOLD_LG, text_color=TEXT,
                     anchor="w").pack(fill="x", pady=(0, SP_MD))

    @staticmethod
    def _field_label(parent, text: str, *, first: bool = False) -> None:
        ctk.CTkLabel(parent, text=text, font=F_BOLD_SM, text_color=TEXT,
                     anchor="w").pack(
            fill="x", pady=((0 if first else SP_MD), 2))

    @staticmethod
    def _help_text(parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=F_XS, text_color=TEXT_MUTED,
                     anchor="w", justify="left", wraplength=460).pack(
            fill="x", pady=(2, 0))

    def _combo(self, parent, variable, values, command=None):
        return ctk.CTkComboBox(
            parent, variable=variable, values=values, state="readonly",
            height=H_MD, font=F_SM,
            border_color=BORDER,
            button_color=SECONDARY, button_hover_color=SECONDARY_HOVER,
            dropdown_fg_color=SURFACE_INNER,
            dropdown_hover_color=SURFACE_HOVER,
            dropdown_text_color=TEXT, fg_color=SURFACE_INNER,
            command=command)

    # --------------------------------------------------- section builders -

    def _build_general_section(self, parent) -> None:
        self._section_title(parent, "General")
        self._field_label(parent, "Default version", first=True)
        self._help_text(parent,
            "Used when you click Download selected without picking "
            "per-batch.")
        self._combo(parent, self.version_var, VERSIONS).pack(
            fill="x", pady=(SP_XS, 0))
        self._field_label(parent, "Default quality")
        self._combo(parent, self.quality_var, QUALITIES).pack(
            fill="x", pady=(SP_XS, 0))
        self._field_label(parent, "Appearance")
        self._combo(parent, self.appearance_var,
                    ["System", "Light", "Dark"],
                    command=lambda v: ctk.set_appearance_mode(
                        v.lower())).pack(fill="x", pady=(SP_XS, 0))

    def _build_output_section(self, parent) -> None:
        self._section_title(parent, "Output organization")
        ctk.CTkCheckBox(
            parent, text="Organize for Plex / Jellyfin",
            variable=self.organize_var, font=F_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            border_color=BORDER_STRONG,
        ).pack(fill="x", anchor="w", pady=(0, SP_SM))
        self._help_text(parent,
            "After each download, files are renamed into\n"
            "One Pace / Season N / One Pace - sNNeMM - Title.mkv\n"
            "with episode metadata (.nfo) written alongside, so Plex and "
            "Jellyfin pick the show up automatically. Canonical titles come "
            "from the SpykerNZ/one-pace-for-plex schema.")

    def _build_usenet_section(self, parent) -> None:
        self._section_title(parent, "Usenet")
        self._help_text(parent,
            "Optional — only needed for the Usenet source. The bundled "
            "release IDs are NZBGeek-specific, so you'll need an NZBGeek "
            "account + API key. Stored locally in config.json, never "
            "uploaded.")
        self._field_label(parent, "Indexer URL")
        ctk.CTkEntry(
            parent, textvariable=self.usenet_url_var, height=H_MD,
            font=F_SM, fg_color=SURFACE_INNER, border_color=BORDER,
            placeholder_text="https://api.nzbgeek.info",
        ).pack(fill="x", pady=(SP_XS, 0))
        self._field_label(parent, "API key")
        ctk.CTkEntry(
            parent, textvariable=self.usenet_key_var, height=H_MD,
            font=F_SM, show="•", fg_color=SURFACE_INNER,
            border_color=BORDER,
            placeholder_text="(paste your indexer API key)",
        ).pack(fill="x", pady=(SP_XS, 0))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(SP_MD, SP_XS))
        for txt, cmd, w in (
            ("Get my API key",
             lambda: webbrowser.open(
                 "https://nzbgeek.info/dashboard.php?myaccount"), 130),
            ("Setup guide",
             lambda: webbrowser.open(USENET_SETUP_GUIDE_URL), 96),
            ("Test connection", self._test_usenet_connection, 124),
        ):
            ctk.CTkButton(
                row, text=txt, height=H_SM, width=w,
                fg_color="transparent", border_width=1,
                border_color=BORDER_STRONG, text_color=LINK,
                hover_color=SURFACE_HOVER, font=F_SM, command=cmd,
            ).pack(side="left", padx=(0, SP_XS))
        self.test_status_lbl = ctk.CTkLabel(
            parent, text="", font=F_XS, anchor="w")
        self.test_status_lbl.pack(fill="x", pady=(SP_XS, 0))

    def _build_updates_section(self, parent) -> None:
        self._section_title(parent, "Updates")
        rc = self.parent_app.config_data.get("refresh_cache", {})
        last = rc.get("last_refresh", "—") or "—"
        self._help_text(parent,
            f"Last refresh: {last}\n\n"
            "The Refresh button pulls a pre-built catalog that's "
            "regenerated weekly on GitHub — usually about 2 seconds. "
            "Force a full local rebuild only if the index looks broken or "
            "you've edited the bundled JSON files (60-90 seconds).")
        ctk.CTkButton(
            parent, text="Force full rebuild", height=H_MD, width=200,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG, text_color=LINK,
            hover_color=SURFACE_HOVER, font=F_SM,
            command=self._force_full_rebuild,
        ).pack(anchor="w", pady=(SP_MD, 0))

    def _build_about_section(self, parent) -> None:
        self._section_title(parent, "About")
        ctk.CTkLabel(
            parent, text=f"One Pace Downloader  ·  v{APP_VERSION}",
            font=F_BOLD_BASE, text_color=TEXT, anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            parent, text="Made by Nicolas  ·  MIT licensed",
            font=F_SM, text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", pady=(2, SP_MD))
        self._help_text(parent,
            "A downloader for the One Pace fan re-cut of One Piece. "
            "Not affiliated with One Pace, Toei, or Shueisha.")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(SP_LG, 0))
        for label in ("Discord", "Reddit", "GitHub"):
            _make_brand_button(row, label, width=100, height=H_MD).pack(
                side="left", padx=(0, SP_XS))

    def _test_usenet_connection(self) -> None:
        url = self.usenet_url_var.get().strip()
        key = self.usenet_key_var.get().strip()
        if not key:
            self.test_status_lbl.configure(text="❌ API key is empty", text_color=DANGER)
            return
            
        self.test_status_lbl.configure(text="⏳ Testing connection...", text_color=TEXT_MUTED)
        
        def task():
            test_url = f"{url}/api?t=search&apikey={key}&limit=1"
            try:
                req = urllib.request.Request(
                    test_url,
                    headers={"User-Agent": f"OnePaceDownloader/{APP_VERSION}"}
                )
                with tls_trust.urlopen(req, timeout=10) as r:
                    content = r.read()
                content_str = content.decode("utf-8", errors="replace")
                if 'error code="100"' in content_str or "Incorrect credentials" in content_str:
                    self.parent_app.ui_queue.put(("usenet_test_result", (False, "❌ Invalid API key")))
                else:
                    self.parent_app.ui_queue.put(("usenet_test_result", (True, "✅ Connection successful!")))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    self.parent_app.ui_queue.put(("usenet_test_result", (False, "❌ Invalid API key (403 Forbidden)")))
                else:
                    self.parent_app.ui_queue.put(("usenet_test_result", (False, f"❌ HTTP Error {e.code}")))
            except Exception as e:
                self.parent_app.ui_queue.put(("usenet_test_result", (False, f"❌ Connection failed: {type(e).__name__}")))
                
        threading.Thread(target=task, daemon=True).start()

    def _build_footer_band(self) -> None:
        """Action row pinned to the bottom of the card. Pinned first (and
        with side=bottom) so the body above can never push it off-screen."""
        action_row = ctk.CTkFrame(self.card, fg_color="transparent")
        action_row.pack(side="bottom", anchor="e", padx=SP_XL,
                        pady=(SP_SM, SP_LG))
        ctk.CTkButton(
            action_row, text="Restore defaults", width=140, height=H_MD,
            font=F_SM,
            fg_color="transparent", border_width=1,
            border_color=BORDER_STRONG,
            text_color=LINK,
            hover_color=SURFACE_HOVER,
            command=self._restore_defaults).pack(side="left", padx=SP_XS)
        ctk.CTkButton(
            action_row, text="Save & close", width=160, height=H_MD,
            font=F_BOLD_SM,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            command=self._save).pack(side="left", padx=SP_XS)
        # Thin divider above the action row so it reads as a distinct band.
        ctk.CTkFrame(self.card, height=1, fg_color=BORDER).pack(
            side="bottom", fill="x", padx=SP_XL)

    def _restore_defaults(self) -> None:
        self.version_var.set(self.DEFAULT_VERSION)
        self.quality_var.set(self.DEFAULT_QUALITY)
        self.appearance_var.set(self.DEFAULT_APPEARANCE)
        self.organize_var.set(self.DEFAULT_ORGANIZE)
        ctk.set_appearance_mode(self.DEFAULT_APPEARANCE.lower())

    def _save(self) -> None:
        cfg = self.parent_app.config_data
        was_organized = cfg.get("organize_for_media_server", False)
        cfg["default_version"] = self.version_var.get()
        cfg["default_quality"] = self.quality_var.get()
        cfg["appearance"] = self.appearance_var.get()
        cfg["organize_for_media_server"] = bool(self.organize_var.get())
        cfg["usenet_indexer_url"] = self.usenet_url_var.get().strip()
        cfg["usenet_api_key"] = self.usenet_key_var.get().strip()
        save_config(cfg)
        # When the user just turned on Plex/Jellyfin mode, retroactively
        # organize any files that were downloaded before the toggle.
        if cfg["organize_for_media_server"] and not was_organized:
            self.parent_app._retroactive_plex_organize()
        try:
            self.parent_app._render_episode_list()
            self.parent_app._render_source_panel()
        except Exception:
            pass
        self.destroy()

    def _rollback_and_close(self) -> None:
        if self.appearance_var.get() != self._original_appearance:
            ctk.set_appearance_mode(self._original_appearance.lower())
        self.destroy()

    def _force_full_rebuild(self) -> None:
        if not messagebox.askyesno(
            "Force full rebuild",
            "This will re-fetch every Pixeldrain album and every SpykerNZ "
            ".nfo file from scratch (60-90 seconds). Do it when the index "
            "looks broken or you've edited the bundled JSON files.\n\n"
            "Continue?",
        ):
            return
        self._save()
        self.parent_app._force_full_refresh = True
        cfg = self.parent_app.config_data
        cfg["refresh_cache"] = {}
        save_config(cfg)
        self.parent_app._refresh_all()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
