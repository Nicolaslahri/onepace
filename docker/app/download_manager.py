"""Singleton download queue manager — runs downloads in a background thread
and exposes state to SSE clients."""

from __future__ import annotations

import asyncio
import threading
import uuid

from .config import MEDIA_DIR
from .core.downloader import Downloader, DownloadCancelled
from .core.episode_index import best_source_for
from .core.log import log
from .core.models import DownloadStatus
from .core.organize import organize_for_plex


class _Job:
    """Internal representation of a queued/active download."""
    def __init__(self, *, job_id: str, arc: dict, episodes: list[dict],
                 source_kind: str, version: str, quality: str,
                 index: dict):
        self.id = job_id
        self.arc = arc
        self.episodes = episodes
        self.source_kind = source_kind
        self.version = version
        self.quality = quality
        self.index = index
        self.status = "queued"
        self.progress = 0.0
        self.speed = ""
        self.current_file = ""
        self.current_idx = 0
        self.total_files = len(episodes)
        self.error: str | None = None
        self.cancel_evt = threading.Event()

    def to_model(self) -> DownloadStatus:
        return DownloadStatus(
            id=self.id,
            arc_title=self.arc.get("title", ""),
            status=self.status,
            progress=self.progress,
            speed=self.speed,
            current_file=self.current_file,
            current_idx=self.current_idx,
            total_files=self.total_files,
            error=self.error,
        )


class DownloadManager:
    """Thread-safe download queue.  One download runs at a time; the rest
    wait in a FIFO queue."""

    def __init__(self):
        self._jobs: dict[str, _Job] = {}
        self._queue: list[str] = []  # job IDs in FIFO order
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_loop, daemon=True)
        self._wake = threading.Event()
        self._sse_listeners: list[asyncio.Queue] = []
        self._worker.start()

    # ── Public API ────────────────────────────────────────────────────

    def enqueue(self, *, arc: dict, episodes: list[dict],
                source_kind: str, version: str, quality: str,
                index: dict) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = _Job(
            job_id=job_id, arc=arc, episodes=episodes,
            source_kind=source_kind, version=version, quality=quality,
            index=index,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._queue.append(job_id)
        self._broadcast(job)
        self._wake.set()
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.cancel_evt.set()
            if job.status == "queued":
                job.status = "cancelled"
                if job_id in self._queue:
                    self._queue.remove(job_id)
        self._broadcast(job)
        return True

    def get_status(self, job_id: str) -> DownloadStatus | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_model() if job else None

    def all_statuses(self) -> list[DownloadStatus]:
        with self._lock:
            return [j.to_model() for j in self._jobs.values()]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._sse_listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._sse_listeners.remove(q)
        except ValueError:
            pass

    # ── Background worker ─────────────────────────────────────────────

    def _run_loop(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                job = self._next_job()
                if not job:
                    break
                self._execute(job)

    def _next_job(self) -> _Job | None:
        with self._lock:
            while self._queue:
                jid = self._queue.pop(0)
                job = self._jobs.get(jid)
                if job and job.status == "queued":
                    return job
        return None

    def _execute(self, job: _Job):
        job.status = "downloading"
        self._broadcast(job)
        index = job.index
        log(f"Download started: {job.arc.get('title','?')} "
            f"({len(job.episodes)} ep, {job.source_kind} {job.quality})")

        try:
            # Group episodes by album_id for efficient batch downloading
            album_groups: dict[str, tuple[str, set[str], list[dict]]] = {}
            for ep in job.episodes:
                src = best_source_for(ep, job.source_kind,
                                      job.version, job.quality)
                if not src:
                    log(f"  No source found for episode {ep.get('num', '?')}")
                    continue
                album_id = src.get("album_id", "")
                file_id = src.get("file_id", "")
                if not album_id or not file_id:
                    continue
                if album_id not in album_groups:
                    album_groups[album_id] = (
                        src.get("subfolder") or "",
                        set(),
                        [],
                    )
                album_groups[album_id][1].add(file_id)
                album_groups[album_id][2].append(ep)

            if not album_groups:
                job.status = "error"
                job.error = "No downloadable sources found"
                self._broadcast(job)
                return

            total_eps = sum(len(g[2]) for g in album_groups.values())
            done_eps = 0

            for album_id, (subfolder, file_ids, eps) in album_groups.items():
                if job.cancel_evt.is_set():
                    raise DownloadCancelled()

                def on_status(msg: str):
                    job.current_file = msg

                def on_progress(frac, speed, idx, total, _bytes):
                    completed = done_eps + (idx - 1) + frac
                    job.progress = min(completed / total_eps, 1.0) if total_eps else 0.0
                    job.speed = speed
                    job.current_idx = done_eps + idx
                    job.total_files = total_eps
                    self._broadcast(job)

                dl = Downloader(
                    album_id,
                    MEDIA_DIR,
                    on_status=on_status,
                    on_progress=on_progress,
                    on_log=log,
                    cancel_evt=job.cancel_evt,
                    file_filter=file_ids,
                    subfolder=subfolder or None,
                )
                downloaded_sub = dl.run()

                # Plex organize (always on in Docker mode)
                if downloaded_sub.exists():
                    organize_for_plex(
                        downloaded_sub, file_ids, job.arc, index,
                        MEDIA_DIR, log=log,
                    )

                done_eps += len(eps)

            job.status = "done"
            job.progress = 1.0
            job.speed = ""
            log(f"Download complete: {job.arc.get('title','?')}")

        except DownloadCancelled:
            job.status = "cancelled"
            log(f"Download cancelled: {job.arc.get('title','?')}")

        except Exception as e:
            job.status = "error"
            job.error = str(e)
            log(f"Download error ({job.arc.get('title','?')}): {e}")

        self._broadcast(job)
        self._evict_old_history()

    def _evict_old_history(self) -> None:
        """Stop completed jobs from piling up forever — keep at most the
        50 most recent done/error/cancelled ones. Dict insertion order
        gives us the enqueue order, so the slice is correct."""
        with self._lock:
            terminal = [jid for jid, j in self._jobs.items()
                        if j.status in ("done", "error", "cancelled")]
            excess = len(terminal) - 50
            for jid in terminal[:excess] if excess > 0 else ():
                self._jobs.pop(jid, None)

    # ── SSE broadcast ─────────────────────────────────────────────────

    def _broadcast(self, job: _Job):
        data = job.to_model().model_dump()
        dead: list[asyncio.Queue] = []
        for q in self._sse_listeners:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)


# Module-level singleton
manager = DownloadManager()
