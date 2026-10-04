import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ammirror.models import (
    AppleTrack,
    Matched,
    PlaylistMapping,
    Unmatched,
    UnmatchedReason,
    YtmCandidate,
)

log = logging.getLogger(__name__)

_SCHEMA_V1 = """
CREATE TABLE matches (
    apple_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    score REAL NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('search', 'pin')),
    updated_at TEXT NOT NULL
);
CREATE TABLE unmatched (
    apple_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    reason TEXT NOT NULL,
    candidate_video_id TEXT,
    candidate_title TEXT,
    candidate_artists TEXT,
    candidate_album TEXT,
    candidate_duration_s INTEGER,
    candidate_score REAL,
    updated_at TEXT NOT NULL
);
CREATE TABLE playlists (
    apple_playlist_id TEXT PRIMARY KEY,
    ytm_playlist_id TEXT NOT NULL,
    apple_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE owned_likes (
    video_id TEXT PRIMARY KEY,
    apple_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Artists are stored joined with this separator; it never appears in names.
_ARTIST_SEP = "\x1f"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class UnmatchedRow:
    apple_id: str
    title: str
    artist: str
    reason: str
    candidate_video_id: str | None
    candidate_title: str | None
    candidate_score: float | None


class State:
    """SQLite-backed sync state. Every write commits immediately."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._db = sqlite3.connect(path, isolation_level=None)
        self._migrate()

    def _migrate(self) -> None:
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version == 0:
            log.debug("state: applying schema migration to version 1")
            self._db.executescript("BEGIN;" + _SCHEMA_V1 + "PRAGMA user_version = 1; COMMIT;")

    def close(self) -> None:
        self._db.close()

    # matches

    def get_match(self, apple_id: str) -> Matched | None:
        row = self._db.execute(
            "SELECT video_id, score, method FROM matches WHERE apple_id = ?", (apple_id,)
        ).fetchone()
        return Matched(row[0], row[1], row[2]) if row else None

    def put_match(
        self, apple_id: str, video_id: str, score: float, method: Literal["search", "pin"]
    ) -> None:
        self._db.execute(
            "INSERT INTO matches VALUES (?, ?, ?, ?, ?) ON CONFLICT(apple_id) DO UPDATE SET "
            "video_id = excluded.video_id, score = excluded.score, method = excluded.method, "
            "updated_at = excluded.updated_at",
            (apple_id, video_id, score, method, _now()),
        )

    # unmatched

    def put_unmatched(self, track: AppleTrack, result: Unmatched) -> None:
        c = result.candidate
        self._db.execute(
            "INSERT OR REPLACE INTO unmatched VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                track.key,
                track.title,
                track.artist,
                result.reason.value,
                c.video_id if c else None,
                c.title if c else None,
                _ARTIST_SEP.join(c.artists) if c else None,
                c.album if c else None,
                c.duration_s if c else None,
                result.candidate_score,
                _now(),
            ),
        )

    def get_unmatched(self, apple_id: str) -> Unmatched | None:
        row = self._db.execute(
            "SELECT reason, candidate_video_id, candidate_title, candidate_artists, "
            "candidate_album, candidate_duration_s, candidate_score "
            "FROM unmatched WHERE apple_id = ?",
            (apple_id,),
        ).fetchone()
        if row is None:
            return None
        reason, vid, title, artists, album, duration_s, score = row
        candidate = (
            YtmCandidate(
                vid,
                title,
                tuple(artists.split(_ARTIST_SEP)) if artists else (),
                album,
                duration_s,
            )
            if vid
            else None
        )
        return Unmatched(UnmatchedReason(reason), candidate, score)

    def clear_unmatched(self, apple_id: str) -> None:
        self._db.execute("DELETE FROM unmatched WHERE apple_id = ?", (apple_id,))

    def list_unmatched(self) -> list[UnmatchedRow]:
        rows = self._db.execute(
            "SELECT apple_id, title, artist, reason, candidate_video_id, candidate_title, "
            "candidate_score FROM unmatched ORDER BY artist, title"
        ).fetchall()
        return [UnmatchedRow(*r) for r in rows]

    # playlists

    def playlist_mappings(self) -> dict[str, PlaylistMapping]:
        rows = self._db.execute(
            "SELECT apple_playlist_id, ytm_playlist_id, apple_name FROM playlists"
        ).fetchall()
        return {r[0]: PlaylistMapping(*r) for r in rows}

    def put_playlist(self, apple_playlist_id: str, ytm_playlist_id: str, apple_name: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO playlists VALUES (?, ?, ?, ?)",
            (apple_playlist_id, ytm_playlist_id, apple_name, _now()),
        )

    # likes

    def owned_likes(self) -> dict[str, str]:
        return dict(self._db.execute("SELECT video_id, apple_id FROM owned_likes").fetchall())

    def add_owned_like(self, video_id: str, apple_id: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO owned_likes VALUES (?, ?, ?)", (video_id, apple_id, _now())
        )

    def remove_owned_like(self, video_id: str) -> None:
        self._db.execute("DELETE FROM owned_likes WHERE video_id = ?", (video_id,))
