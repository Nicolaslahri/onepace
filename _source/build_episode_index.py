"""Offline tool that builds episode_index.json — the unified episode-level
data file the v2 UI is driven from.

Run this manually before each release:

    python _source/build_episode_index.py

It walks every One Pace and Muhn Pace Pixeldrain album, fetches the file
list (with sizes + file IDs), parses each filename to extract
(arc, episode_num, version, quality), then merges per-episode Nyaa
torrents from nyaa_arcs.json and Usenet releases from usenet_arcs.json.

Output: _source/episode_index.json — bundled into the .exe by PyInstaller.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Reuse helpers + constants from the main app
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tls_trust  # noqa: E402
from onepace_downloader import (  # noqa: E402
    PIXELDRAIN_API,
    _NYAA_ARC_ALIASES,
    http_get,
    load_arcs,
    load_muhn_arcs,
    load_nyaa_arcs,
    load_usenet_arcs,
)

OUT_FILE = Path(__file__).resolve().parent / "episode_index.json"

# GitHub API: cheap way to ask "did SpykerNZ change?" without downloading 500
# .nfo files. One HTTP call returns the latest commit SHA on the branch.
SPYKER_LATEST_COMMIT_API = (
    "https://api.github.com/repos/SpykerNZ/one-pace-for-plex/commits/main")


def hash_arcs(arcs: list[dict]) -> str:
    """Stable hash of (title, album_id) tuples across all arcs. Changes only
    when the arc list or any album ID changes — i.e. exactly the things that
    would invalidate a cached Pixeldrain walk."""
    fingerprint = []
    for a in arcs:
        fingerprint.append(a.get("title", ""))
        for ver, qs in (a.get("resources") or {}).items():
            for q, album in qs.items():
                fingerprint.append(f"{ver}|{q}|{album}")
    return hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _existing_index_has_data() -> bool:
    """True if the user-writable INDEX_FILE has a non-empty 'arcs' list with
    real source entries. Used to decide if the fast path can reuse it."""
    try:
        from onepace_downloader import INDEX_FILE as _USER_INDEX_FILE
        path = _USER_INDEX_FILE
        if not path.exists():
            path = OUT_FILE
    except Exception:
        path = OUT_FILE
    if not path.exists():
        return False
    try:
        idx = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    arcs = idx.get("arcs") or []
    if not arcs:
        return False
    # Any onepace source anywhere? If we're missing them the cache is useless.
    return any(
        s.get("kind") == "onepace"
        for arc in arcs
        for ep in arc.get("episodes", [])
        for s in ep.get("sources", [])
    )


def _load_existing_index() -> dict:
    """Load the current episode_index.json — caller is expected to have
    confirmed via _existing_index_has_data() first."""
    try:
        from onepace_downloader import INDEX_FILE as _USER_INDEX_FILE
        path = _USER_INDEX_FILE if _USER_INDEX_FILE.exists() else OUT_FILE
    except Exception:
        path = OUT_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_spyker_latest_sha(timeout: int = 15) -> str | None:
    """Single GitHub API call: returns the SHA of the latest commit on
    SpykerNZ/one-pace-for-plex@main. Used to skip re-fetching all 500 .nfo
    files when the repo hasn't changed since last refresh.

    Returns None on any failure (network down, rate-limited, etc.) so the
    caller can fall back to the slow path."""
    try:
        req = urllib.request.Request(
            SPYKER_LATEST_COMMIT_API,
            headers={"User-Agent": "OnePaceDownloader (refresh-cache)"})
        with tls_trust.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()).get("sha")
    except Exception:
        return None

# --- SpykerNZ canonical episode metadata source ---------------------------
# Each One Pace arc maps 1:1 to a Spyker "Season N" folder (Season 1 =
# Romance Dawn, ..., Season 36 = Egghead). Each .nfo file inside is the
# Plex/Jellyfin episode-details XML for that episode. We pull title + plot
# from there to surface real episode names in the UI and to write proper
# .nfo files alongside downloads in Plex/Jellyfin output mode.
SPYKER_REPO = "SpykerNZ/one-pace-for-plex"
SPYKER_BRANCH = "main"
SPYKER_RAW = f"https://raw.githubusercontent.com/{SPYKER_REPO}/{SPYKER_BRANCH}/"
SPYKER_TREE_API = (
    f"https://api.github.com/repos/{SPYKER_REPO}/git/trees/"
    f"{SPYKER_BRANCH}?recursive=1"
)

_NFO_SEASON_RE = re.compile(r"<season>(\d+)</season>")
_NFO_EPISODE_RE = re.compile(r"<episode>(\d+)</episode>")
_NFO_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_NFO_PLOT_RE = re.compile(r"<plot>(.*?)</plot>", re.DOTALL)


def fetch_spyker_metadata(log=print) -> dict[tuple[int, int], dict]:
    """Pull every episode .nfo from SpykerNZ/one-pace-for-plex and parse out
    title + plot. Returns {(season, episode): {"title": str, "plot": str}}.
    Season 1..36 maps to canonical arc index 0..35; Season 0 is Specials.

    Uses GitHub's tree API once to enumerate files, then raw.githubusercontent.com
    (un-rate-limited CDN) to fetch each .nfo in parallel. Safe to call from
    the in-app Refresh worker — failures degrade gracefully to empty."""
    log("Fetching SpykerNZ episode metadata...")
    tree_payload = json.loads(http_get(SPYKER_TREE_API, timeout=30).decode("utf-8"))
    nfos = [
        e["path"] for e in tree_payload.get("tree", [])
        if e.get("type") == "blob"
        and e.get("path", "").startswith("One Pace/")
        and e.get("path", "").endswith(".nfo")
        and not e["path"].endswith("/season.nfo")
    ]
    log(f"  {len(nfos)} episode .nfo files in {SPYKER_REPO}")

    def fetch_one(path: str) -> tuple[str, str] | None:
        url = SPYKER_RAW + urllib.parse.quote(path)
        try:
            return path, http_get(url, timeout=20).decode("utf-8")
        except Exception:
            return None

    out: dict[tuple[int, int], dict] = {}
    parsed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(fetch_one, nfos):
            if not result:
                continue
            _path, content = result
            ms = _NFO_SEASON_RE.search(content)
            me = _NFO_EPISODE_RE.search(content)
            mt = _NFO_TITLE_RE.search(content)
            if not (ms and me and mt):
                continue
            mp = _NFO_PLOT_RE.search(content)
            out[(int(ms.group(1)), int(me.group(1)))] = {
                "title": html.unescape(mt.group(1).strip()),
                "plot": html.unescape(mp.group(1).strip()) if mp else "",
            }
            parsed += 1
    log(f"  Parsed {parsed} canonical titles + plots")
    return out

# Filename parser shared by One Pace album files and Nyaa torrent titles.
# Strips the [One Pace][src-eps] (or just [One Pace]) prefix, then matches
# the canonical arc name (or alias) followed by an episode number.
_PREFIX_RE = re.compile(
    r"^.*?\[One Pace\]\s*(?:\[[^\]]+\]\s*)?",
    re.IGNORECASE,
)
_QUALITY_RE = re.compile(r"\[(\d{3,4}p)\]")
_FILESIZE_KEYS = ("size", "size_bytes", "size_in_bytes")


def parse_episode(title: str, needles: list[tuple[str, str]]) -> tuple[str, int | None] | None:
    """Return (canonical_arc, ep_num or None). ep_num=None means full-arc pack.
    needles must be sorted longest-first."""
    body = _PREFIX_RE.sub("", title)
    body_low = body.lower()
    for needle, canon in needles:
        if body_low.startswith(needle.lower()):
            rest = body[len(needle):].lstrip(" -")
            m_ = re.match(r"(\d+)", rest)
            if m_:
                return canon, int(m_.group(1))
            return canon, None
    return None


# Muhn Pace uses inconsistent arc names + filename prefixes. Map each Muhn arc
# to the canonical One Pace arc, and collect alternate filename spellings we
# need to recognize. Built from inspecting every Muhn album's file list.
MUHN_ARC_MAP: dict[str, tuple[str, list[str]]] = {
    # muhn_arc_title -> (canonical_onepace_arc, [filename_spellings])
    "Enies Lobby (gap-fill)":   ("Enies Lobby",       ["Enies Lobby"]),
    "Post Enies Lobby":         ("Post-Enies Lobby",  ["Post Enies Lobby", "Post-Enies Lobby"]),
    "Thriller Bark":            ("Thriller Bark",     ["Thriller Bark", "Thrillerbark"]),
    "Sabaody Archipelago":      ("Sabaody Archipelago", ["Sabaody Archipelago", "Sabaody"]),
    "Amazon Lily":              ("Amazon Lily",       ["Amazon Lily"]),
    "Impel Down":               ("Impel Down",        ["Impel Down"]),
    "Marineford":               ("Marineford",        ["Marineford"]),
    "Post Marineford":          ("Post-War",          ["Post War", "Post-War", "Post Marineford"]),
    "Fishman Island":           ("Fishman Island",    ["Fishman Island", "Fishmanisland"]),
    "Fishman Island Ep 15":     ("Fishman Island",    ["Fishman Island"]),
    "Punk Hazard":              ("Punk Hazard",       ["Punk Hazard", "Punkhazard"]),
    "Dressrosa":                ("Dressrosa",         ["Dressrosa"]),
    "Zou":                      ("Zou",               ["Zou"]),
    "Whole Cake Island":        ("Whole Cake Island", ["Whole Cake Island", "Wholecake", "Whole Cake"]),
    "Wano (Acts 1+2)":          ("Wano",              ["Wano"]),
    "Wano (Act 3)":             ("Wano",              ["Wano"]),
}

# Strip [Muhn Pace], [Muhnpace], or similar prefixes before parsing
_MUHN_PREFIX_RE = re.compile(r"^\s*\[Muhn\s*Pace\]\s*", re.IGNORECASE)


def parse_muhn_episode(filename: str, spellings: list[str]) -> int | None:
    """Find the episode number in a Muhn Pace filename. Tries each provided
    spelling (e.g. 'Thriller Bark' vs 'Thrillerbark'). Returns None for
    non-episode files (album metadata, ReadMe-style entries)."""
    body = _MUHN_PREFIX_RE.sub("", filename).strip()
    body_low = body.lower()
    for spelling in spellings:
        slow = spelling.lower()
        if body_low.startswith(slow):
            rest = body[len(spelling):].lstrip(" -")
            m_ = re.match(r"(\d+)", rest)
            if m_:
                return int(m_.group(1))
    # Fall back: any "<spelling>[ -]NN" anywhere in the filename
    for spelling in spellings:
        pat = re.escape(spelling) + r"\s*[-]?\s*(\d+)"
        m_ = re.search(pat, body, re.IGNORECASE)
        if m_:
            return int(m_.group(1))
    return None


def fetch_album(album_id: str) -> list[dict]:
    """Fetch a Pixeldrain album's file list. Returns [{id, name, size}]."""
    data = json.loads(http_get(PIXELDRAIN_API.format(album_id=album_id), timeout=60))
    files = data.get("files", [])
    out = []
    for f in files:
        size = next((f[k] for k in _FILESIZE_KEYS if k in f), 0)
        out.append({"id": f.get("id"), "name": f.get("name"), "size": int(size or 0)})
    return out


def detect_quality(filename: str, fallback: str = "") -> str:
    """Extract '1080p' / '720p' / etc. from a filename, else return fallback."""
    m_ = _QUALITY_RE.search(filename)
    return m_.group(1) if m_ else fallback


def build_needles() -> list[tuple[str, str]]:
    """(arc_name_or_alias, canonical) pairs, longest first."""
    arcs = load_arcs()
    needles: list[tuple[str, str]] = []
    for a in arcs:
        canon = a["title"]
        needles.append((canon, canon))
        for alias in _NYAA_ARC_ALIASES.get(canon, []):
            needles.append((alias, canon))
    needles.sort(key=lambda p: len(p[0]), reverse=True)
    return needles


def build(log=print, cancel_evt=None, cache: dict | None = None,
          force: bool = False) -> Path:
    """Re-build episode_index.json. `log` receives one-line status updates
    (e.g. for the in-app refresh worker). `cancel_evt` is an optional
    threading.Event — when set, the loop bails out early. Returns the path
    to the written file.

    Cache shape (in-out, mutated when present):
        {
            "arcs_hash": "...",      # sha256 of arc list — skip Pixeldrain when matched
            "spyker_sha": "...",     # SpykerNZ@main commit SHA — skip .nfo refetch when matched
        }

    If `cache` is provided and `force` is False, we use it to skip the
    expensive Pixeldrain / SpykerNZ work when content hasn't changed."""
    arcs = load_arcs()
    muhn = load_muhn_arcs()
    nyaa = load_nyaa_arcs()
    usenet = load_usenet_arcs()
    needles = build_needles()

    # ------------------------------------------------------- cache check ---
    # Decide whether to use the fast path: copy onepace+muhn sources from the
    # existing episode_index, skip Pixeldrain entirely, and only re-merge
    # Nyaa + Usenet (which are cheap, local file reads — Nyaa was already
    # refreshed by the caller before us).
    fresh_arcs_hash = hash_arcs(arcs)
    cached_arcs_hash = (cache or {}).get("arcs_hash")
    skip_pixeldrain = (
        not force
        and cache is not None
        and cached_arcs_hash == fresh_arcs_hash
        and _existing_index_has_data()
    )

    # Index keyed by (arc, ep_num) -> list of source dicts
    by_episode: dict[tuple[str, int], list[dict]] = {}
    arc_packs: dict[str, list[dict]] = {a["title"]: [] for a in arcs}
    specials: list[dict] = []  # truly arc-less content (sub-cuts, fan letter, etc.)

    if skip_pixeldrain:
        log(f"Arcs unchanged since last refresh — reusing cached onepace + "
            f"muhn sources from existing index (saved ~30-60s of Pixeldrain "
            f"calls).")
        existing = _load_existing_index()
        # Copy onepace + muhn sources back into the by_episode map so the
        # downstream merge (nyaa, usenet, spyker) layers on as usual.
        # Carry arc_packs for onepace/muhn (none today, but future-proof).
        for arc in existing.get("arcs", []):
            title = arc.get("title", "")
            for ep in arc.get("episodes", []):
                ep_num = ep.get("num")
                if ep_num is None:
                    continue
                for s in ep.get("sources", []):
                    if s.get("kind") in ("onepace", "muhn"):
                        by_episode.setdefault((title, ep_num), []).append(
                            dict(s))
        kept = sum(len(v) for v in by_episode.values())
        log(f"  Reused {kept} onepace+muhn source entries from cache.")
    else:
        log(f"Walking {len(arcs)} One Pace arcs...")
        for arc in arcs:
            if cancel_evt is not None and cancel_evt.is_set():
                raise RuntimeError("cancelled")
            title = arc["title"]
            for version, qualities in arc["resources"].items():
                for quality, album_id in qualities.items():
                    try:
                        files = fetch_album(album_id)
                    except Exception as e:
                        log(f"  [skip] {title} {version} {quality}: {e}")
                        continue
                    for f in files:
                        parsed = parse_episode(f["name"], needles)
                        if not parsed:
                            continue
                        parc, pep = parsed
                        if parc != title or pep is None:
                            continue
                        by_episode.setdefault((title, pep), []).append({
                            "kind": "onepace",
                            "version": version,
                            "quality": detect_quality(f["name"], quality),
                            "size_bytes": f["size"],
                            "album_id": album_id,
                            "file_id": f["id"],
                            "filename": f["name"],
                        })
                    # Be polite to pixeldrain
                    time.sleep(0.1)
            log(f"  {title}: indexed")

        log(f"Walking {len(muhn)} Muhn Pace arcs...")
        for arc in muhn:
            if cancel_evt is not None and cancel_evt.is_set():
                raise RuntimeError("cancelled")
            muhn_title = arc["title"]
            album_id = arc.get("album_id")
            if not album_id:
                continue
            mapping = MUHN_ARC_MAP.get(muhn_title)
            if not mapping:
                log(f"  [skip] {muhn_title}: no canonical mapping")
                continue
            canonical, spellings = mapping
            try:
                files = fetch_album(album_id)
            except Exception as e:
                log(f"  [skip] {muhn_title}: {e}")
                continue
            added = 0
            for f in files:
                ep = parse_muhn_episode(f["name"], spellings)
                if ep is None:
                    continue
                by_episode.setdefault((canonical, ep), []).append({
                    "kind": "muhn",
                    "version": "English Dub",
                    "quality": detect_quality(f["name"], "varies"),
                    "size_bytes": f["size"],
                    "album_id": album_id,
                    "file_id": f["id"],
                    "filename": f["name"],
                })
                added += 1
            log(f"  {muhn_title} -> {canonical}: {added} episodes")

    log(f"Walking {sum(len(b['torrents']) for b in nyaa)} Nyaa torrents...")
    for bucket in nyaa:
        bucket_arc = bucket["title"]
        for t in bucket["torrents"]:
            parsed = parse_episode(t["title"], needles)
            torrent_src = {
                "kind": "nyaa",
                "quality": detect_quality(t["title"], "varies"),
                "size_str": t.get("size", ""),
                "size_bytes": _parse_size_str(t.get("size", "")),
                "magnet": t["magnet"],
                "seeders": t.get("seeders", 0),
                "torrent_title": t["title"],
                "uploader": t.get("uploader", ""),
            }
            if not parsed:
                specials.append(torrent_src)
                continue
            parc, pep = parsed
            if pep is None:
                # Full-arc pack
                arc_packs.setdefault(parc, []).append(torrent_src)
            else:
                by_episode.setdefault((parc, pep), []).append(torrent_src)

    # ----- Usenet: merge from usenet_arcs.json (no network) ---------------
    # This is intentionally offline — Usenet refresh-from-NZBGeek would need
    # the user's own API key, which the app build pipeline doesn't have.
    # The bundled usenet_arcs.json is regenerated separately via the scraper
    # script (`_scratch_usenet_scrape.py`) when the maintainer chooses to.
    usenet_ep_count = 0
    usenet_pack_count = 0
    for u_arc in usenet:
        u_title = u_arc.get("title") or u_arc.get("arc")  # back-compat
        if not u_title:
            continue
        for u_ep in u_arc.get("episodes", []):
            for q in u_ep.get("qualities", []):
                src = {
                    "kind": "usenet",
                    "quality": q.get("quality", "?"),
                    "size_bytes": q.get("size_bytes", 0),
                    "guid": q["guid"],
                    "release_title": q.get("title", ""),
                    "pub_date": q.get("pub_date", ""),
                    "extended": q.get("extended", False),
                }
                by_episode.setdefault((u_title, u_ep["ep"]), []).append(src)
                usenet_ep_count += 1
        for p in u_arc.get("packs", []):
            arc_packs.setdefault(u_title, []).append({
                "kind": "usenet",
                "quality": p.get("quality", "?"),
                "size_bytes": p.get("size_bytes", 0),
                "guid": p["guid"],
                "release_title": p.get("title", ""),
                "pub_date": p.get("pub_date", ""),
                "anime_eps": p.get("anime_eps", ""),
            })
            usenet_pack_count += 1
    log(f"Grafted {usenet_ep_count} Usenet ep sources + "
        f"{usenet_pack_count} arc packs across {len(usenet)} arcs.")

    # Re-shape into arc-grouped output, preserving canonical arc order
    out_arcs: list[dict] = []
    for arc in arcs:
        title = arc["title"]
        eps: list[dict] = []
        ep_nums = sorted({e for a, e in by_episode if a == title})
        for ep in ep_nums:
            sources = by_episode[(title, ep)]
            # Sort sources for stable UI: onepace > muhn > nyaa > usenet,
            # with the official Galaxy9000 uploader winning ties inside Nyaa.
            sources.sort(key=lambda s: (
                {"onepace": 0, "muhn": 1, "nyaa": 2, "usenet": 3}.get(
                    s["kind"], 9),
                0 if s.get("uploader") == "Galaxy9000" else 1,
                -_quality_rank(s.get("quality", "")),
                -int(s.get("seeders", 0)) if s.get("kind") == "nyaa" else 0,
                s.get("version", ""),
            ))
            eps.append({
                "num": ep,
                "display_title": f"{title} {ep:02d}",
                "sources": sources,
            })
        out_arcs.append({
            "title": title,
            "episodes": eps,
            "arc_packs": sorted(
                arc_packs.get(title, []),
                key=lambda s: (
                    0 if s.get("uploader") == "Galaxy9000" else 1,
                    -int(s.get("seeders", 0)),
                ),
            ),
        })

    # ----- SpykerNZ canonical titles + plots ------------------------------
    # Stage-2 optimization: check the repo's latest commit SHA first (single
    # API call, <1s). If unchanged since last cache, reuse the canonical
    # titles already in the existing episode_index and skip the 500-file
    # .nfo refetch (saves ~5-15s).
    spyker_latest = fetch_spyker_latest_sha() if cache is not None else None
    cached_spyker_sha = (cache or {}).get("spyker_sha")
    spyker: dict = {}
    spyker_reused = False
    if (not force and cache is not None and spyker_latest
            and cached_spyker_sha == spyker_latest
            and _existing_index_has_data()):
        log("SpykerNZ commit unchanged — reusing cached canonical titles.")
        # Build spyker dict from existing index so the matching loop below
        # still runs uniformly (handles the rare case of newly-added episodes
        # picking up their canonical titles from the cache too).
        try:
            existing = _load_existing_index()
            for arc_idx, arc_obj in enumerate(existing.get("arcs", [])):
                season = arc_idx + 1
                for ep in arc_obj.get("episodes", []):
                    if ep.get("canonical_title"):
                        spyker[(season, ep["num"])] = {
                            "title": ep["canonical_title"],
                            "plot": ep.get("plot", ""),
                        }
            spyker_reused = True
        except Exception:
            spyker = {}
    if not spyker_reused:
        try:
            spyker = fetch_spyker_metadata(log=log)
        except Exception as e:
            log(f"  [warn] SpykerNZ fetch failed, skipping canonical titles: {e}")
            spyker = {}

    matched = 0
    for arc_idx, arc_obj in enumerate(out_arcs):
        season = arc_idx + 1
        for ep in arc_obj["episodes"]:
            meta = spyker.get((season, ep["num"]))
            if not meta:
                continue
            ep["canonical_title"] = meta["title"]
            if meta["plot"]:
                ep["plot"] = meta["plot"]
            matched += 1
    if spyker:
        log(f"  Matched canonical metadata to {matched} episodes")

    payload = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "arcs": out_arcs,
        "specials": specials,
    }
    # Update the cache so the next refresh can take the fast path. We write
    # the fresh values whether we used the cache or not — that way if the
    # underlying remote changed, the next refresh sees the new fingerprint.
    if cache is not None:
        cache["arcs_hash"] = fresh_arcs_hash
        if spyker_latest:
            cache["spyker_sha"] = spyker_latest
        cache["last_refresh"] = payload["scraped_at"]
    # When called from the running app, write into the user-writable INDEX_FILE
    # location (next to the .exe) so subsequent launches pick it up. The CLI
    # entry below still writes into _source/ for dev builds.
    try:
        from onepace_downloader import INDEX_FILE as _USER_INDEX_FILE
        out_path = _USER_INDEX_FILE
    except Exception:
        out_path = OUT_FILE
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    total_eps = sum(len(a["episodes"]) for a in out_arcs)
    total_sources = sum(len(e["sources"]) for a in out_arcs for e in a["episodes"])
    log(
        f"Wrote {out_path.name}: {len(out_arcs)} arcs, {total_eps} episodes, "
        f"{total_sources} source entries, {len(specials)} specials."
    )
    return out_path


def main() -> None:
    """CLI entry point — keep writing into _source/ for the dev workflow."""
    build(log=print)


def _quality_rank(q: str) -> int:
    m_ = re.match(r"(\d+)p", q)
    return int(m_.group(1)) if m_ else 0


_SIZE_UNITS = {
    "B": 1, "KB": 1024, "KIB": 1024,
    "MB": 1024 ** 2, "MIB": 1024 ** 2,
    "GB": 1024 ** 3, "GIB": 1024 ** 3,
    "TB": 1024 ** 4, "TIB": 1024 ** 4,
}


def _parse_size_str(s: str) -> int:
    """Parse a string like '681.8 MiB' or '2.4 GB' to bytes."""
    m_ = re.match(r"\s*([\d.]+)\s*([KMGTP]?I?B)\b", s, re.IGNORECASE)
    if not m_:
        return 0
    value = float(m_.group(1))
    unit = m_.group(2).upper()
    return int(value * _SIZE_UNITS.get(unit, 1))


if __name__ == "__main__":
    main()
