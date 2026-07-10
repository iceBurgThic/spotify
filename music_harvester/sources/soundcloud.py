from __future__ import annotations

import os

from music_harvester.http import request_json
from music_harvester.models import RawTrack, SourceConfig
from music_harvester.sources.base import SourceAdapter, SourceUnavailable


class SoundCloudSource(SourceAdapter):
    def harvest(self) -> list[RawTrack]:
        client_id = os.environ.get("SOUNDCLOUD_CLIENT_ID")
        if not client_id:
            raise SourceUnavailable("SOUNDCLOUD_CLIENT_ID is not set; skipping SoundCloud source.")
        if not self.source.url:
            raise SourceUnavailable("SoundCloud sources need a URL.")

        resolved = request_json(
            "GET",
            "https://api-v2.soundcloud.com/resolve",
            params={"url": self.source.url, "client_id": client_id},
        )

        kind = resolved.get("kind")
        if kind == "playlist":
            return self._playlist(resolved)
        if kind == "track":
            return [self._track(resolved, "direct_track", None, 1)]
        if kind == "user":
            return self._user(resolved, client_id)
        raise SourceUnavailable(f"Unsupported SoundCloud resolved kind: {kind}")

    def _user(self, user: dict, client_id: str) -> list[RawTrack]:
        source_type = self.source.source_type
        suffix = {
            "user_likes": "likes",
            "user_tracks": "tracks",
            "user_reposts": "reposts",
        }.get(source_type)
        if not suffix:
            raise SourceUnavailable(f"Unsupported SoundCloud user source type: {source_type}")

        url = f"https://api-v2.soundcloud.com/users/{user['id']}/{suffix}"
        tracks: list[RawTrack] = []
        position = 0
        params = {"client_id": client_id, "limit": 100}
        while url:
            page = request_json("GET", url, params=params)
            params = None
            for item in page.get("collection", []):
                position += 1
                track = item.get("track") if "track" in item else item
                if track and track.get("kind") == "track":
                    tracks.append(self._track(track, suffix, None, position))
            url = page.get("next_href")
        return tracks

    def _playlist(self, playlist: dict) -> list[RawTrack]:
        title = playlist.get("title")
        return [
            self._track(track, "playlist", title, index)
            for index, track in enumerate(playlist.get("tracks", []), 1)
            if track.get("title")
        ]

    def _track(self, track: dict, context: str, playlist_title: str | None, position: int | None) -> RawTrack:
        user = track.get("user") or {}
        return RawTrack(
            source_name=self.source.name,
            platform="soundcloud",
            artist=user.get("username") or track.get("publisher_metadata", {}).get("artist") or "Unknown Artist",
            title=track.get("title", ""),
            album=None,
            platform_track_id=str(track.get("id")) if track.get("id") else None,
            url=track.get("permalink_url"),
            payload=track,
            source_context=context,
            playlist_title=playlist_title or self.source.name,
            position=position,
        )
