from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from music_harvester.http import ApiError, request_json
from music_harvester.models import RawTrack, SourceConfig
from music_harvester.sources.base import SourceAdapter, SourceUnavailable


PLAYLIST_RE = re.compile(r"playlist/([A-Za-z0-9]+)")
USER_RE = re.compile(r"user/([^/?]+)")


class SpotifyClient:
    def __init__(self, token_path: Path = Path(".spotify-token.json")):
        self.token_path = token_path

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        return request_json("GET", self._url(path_or_url), headers=self._headers(), params=params)

    def post(self, path_or_url: str, body: dict | None = None) -> dict:
        return request_json("POST", self._url(path_or_url), headers=self._headers(), body=body or {})

    def search_playlists(self, query: str, limit: int = 10) -> list[dict]:
        result = self.get("/search", params={"type": "playlist", "limit": limit, "q": query})
        return [item for item in result.get("playlists", {}).get("items", []) if item]

    def _headers(self) -> dict[str, str]:
        token = self._valid_token()
        return {"Authorization": f"Bearer {token['access_token']}"}

    def _valid_token(self) -> dict:
        if not self.token_path.exists():
            raise SourceUnavailable("Spotify is not authorized yet. Run the web app and Connect Spotify first.")

        token = json.loads(self.token_path.read_text(encoding="utf-8"))
        if time.time() * 1000 < float(token.get("expires_at", 0)) - 60_000:
            return token
        if not token.get("refresh_token"):
            raise SourceUnavailable("Spotify token expired and has no refresh token.")

        client_id = os.environ.get("SPOTIFY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise SourceUnavailable("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required to refresh Spotify auth.")

        auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        refreshed = request_json(
            "POST",
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}"},
            form={"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
        )
        token.update(refreshed)
        token["expires_at"] = int(time.time() * 1000) + int(refreshed.get("expires_in", 3600)) * 1000
        self.token_path.write_text(json.dumps(token, indent=2), encoding="utf-8")
        return token

    @staticmethod
    def _url(path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return f"https://api.spotify.com/v1{path_or_url}"


class SpotifySource(SourceAdapter):
    def __init__(self, source: SourceConfig, client: SpotifyClient | None = None):
        super().__init__(source)
        self.client = client or SpotifyClient()

    def harvest(self) -> list[RawTrack]:
        if self.source.source_type == "playlist":
            playlist_id = extract_playlist_id(self.source.locator)
            if not playlist_id:
                raise SourceUnavailable(f"Could not parse Spotify playlist URL for {self.source.name}.")
            return self._playlist_tracks(playlist_id)

        if self.source.source_type in {"profile", "user", "user_playlists"}:
            user_id = extract_user_id(self.source.locator) or self.source.username
            if not user_id:
                raise SourceUnavailable(f"Could not parse Spotify user/profile URL for {self.source.name}.")
            return self._user_playlists(user_id)

        raise SourceUnavailable(f"Unsupported Spotify source type: {self.source.source_type}")

    def _playlist_tracks(self, playlist_id: str) -> list[RawTrack]:
        playlist = self.client.get(f"/playlists/{playlist_id}", params={"fields": "name"})
        playlist_title = playlist.get("name") or self.source.name
        tracks: list[RawTrack] = []
        url = f"/playlists/{playlist_id}/tracks"
        params = {"limit": 100}
        position = 0
        while url:
            page = self.client.get(url, params=params)
            params = None
            for item in page.get("items", []):
                position += 1
                track = item.get("track") or {}
                if not track or track.get("is_local"):
                    continue
                artists = track.get("artists") or []
                if not artists:
                    continue
                tracks.append(
                    RawTrack(
                        source_name=self.source.name,
                        platform="spotify",
                        artist=artists[0].get("name", ""),
                        title=track.get("name", ""),
                        album=(track.get("album") or {}).get("name"),
                        platform_track_id=track.get("id"),
                        url=(track.get("external_urls") or {}).get("spotify"),
                        payload={"spotify_uri": track.get("uri"), "track": track},
                        source_context="playlist",
                        playlist_title=playlist_title,
                        position=position,
                    )
                )
            url = page.get("next")
        return tracks

    def _user_playlists(self, user_id: str) -> list[RawTrack]:
        tracks: list[RawTrack] = []
        url = f"/users/{user_id}/playlists"
        params = {"limit": 50}
        while url:
            page = self.client.get(url, params=params)
            params = None
            for playlist in page.get("items", []):
                try:
                    tracks.extend(self._playlist_tracks(playlist["id"]))
                except ApiError:
                    continue
            url = page.get("next")
        return tracks


def extract_playlist_id(value: str) -> str | None:
    match = PLAYLIST_RE.search(value or "")
    if match:
        return match.group(1)
    if value and "/" not in value and "spotify" not in value:
        return value
    return None


def extract_user_id(value: str) -> str | None:
    match = USER_RE.search(value or "")
    if match:
        return match.group(1)
    parsed = urlparse(value or "")
    if parsed.netloc.endswith("spotify.com") and parsed.path:
        return parsed.path.strip("/").split("/")[-1]
    return None
