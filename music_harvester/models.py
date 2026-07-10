from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceConfig:
    name: str
    platform: str
    source_type: str
    weight: float = 1.0
    url: str | None = None
    username: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        return cls(
            name=data["name"],
            platform=data["platform"],
            source_type=data.get("type") or data.get("source_type"),
            weight=float(data.get("weight", 1.0)),
            url=data.get("url"),
            username=data.get("username"),
        )

    @property
    def locator(self) -> str:
        return self.url or self.username or ""


@dataclass
class RawTrack:
    source_name: str
    platform: str
    artist: str
    title: str
    album: str | None = None
    platform_track_id: str | None = None
    url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    source_context: str | None = None
    playlist_title: str | None = None
    position: int | None = None


@dataclass
class Candidate:
    id: int
    artist: str
    title: str
    album: str | None
    spotify_uri: str | None
    soundcloud_url: str | None
    score: float
    sources: list[str]
    platforms: list[str]
    playlist_titles: list[str]
    why: str
    pools: list[str] = field(default_factory=list)
    score_components: dict[str, float] = field(default_factory=dict)
    bridge_source_score: float = 0.0
    bridge_match_types: list[str] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str | None = None
