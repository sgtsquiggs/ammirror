from ammirror.models import AppleTrack, YtmCandidate


def track(
    n: int | str,
    *,
    title: str | None = None,
    artist: str = "Artist",
    album: str = "Album",
    duration_ms: int | None = 200_000,
    catalog: bool = True,
) -> AppleTrack:
    return AppleTrack(
        library_id=f"i.lib{n}",
        catalog_id=f"{n}" if catalog else None,
        title=title or f"Song {n}",
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        isrc=None,
    )


def cand(
    video_id: str,
    title: str,
    artists: tuple[str, ...] = ("Artist",),
    *,
    album: str | None = "Album",
    duration_s: int | None = 200,
) -> YtmCandidate:
    return YtmCandidate(video_id, title, artists, album, duration_s)
