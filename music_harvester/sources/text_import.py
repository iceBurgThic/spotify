from __future__ import annotations

from pathlib import Path

from music_harvester.engine.normalize import split_artist_track
from music_harvester.models import RawTrack
from music_harvester.sources.base import SourceAdapter, SourceUnavailable


class TextImportSource(SourceAdapter):
    def harvest(self) -> list[RawTrack]:
        if not self.source.url:
            raise SourceUnavailable("Text imports need a file path in url.")
        path = Path(self.source.url)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise SourceUnavailable(f"Text import not found: {path}")

        tracks: list[RawTrack] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parsed = split_artist_track(stripped)
            if not parsed:
                continue
            artist, title = parsed
            tracks.append(
                RawTrack(
                    source_name=self.source.name,
                    platform="text",
                    artist=artist,
                    title=title,
                    source_context="text_import",
                    playlist_title=self.source.name,
                    position=index,
                )
            )
        return tracks
