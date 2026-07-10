from __future__ import annotations

from music_harvester.models import SourceConfig
from music_harvester.sources.base import SourceAdapter, SourceUnavailable
from music_harvester.sources.lastfm import LastFmSource
from music_harvester.sources.listenbrainz import ListenBrainzSource
from music_harvester.sources.soundcloud import SoundCloudSource
from music_harvester.sources.spotify import SpotifySource
from music_harvester.sources.text_import import TextImportSource


def adapter_for(source: SourceConfig) -> SourceAdapter:
    platform = source.platform.lower()
    if platform == "spotify":
        return SpotifySource(source)
    if platform == "soundcloud":
        return SoundCloudSource(source)
    if platform == "text":
        return TextImportSource(source)
    if platform == "lastfm":
        return LastFmSource(source)
    if platform == "listenbrainz":
        return ListenBrainzSource(source)
    raise SourceUnavailable(f"Unsupported source platform: {source.platform}")
