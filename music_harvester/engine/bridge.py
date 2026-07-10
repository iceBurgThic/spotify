from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from music_harvester.db.store import Store
from music_harvester.engine.explain import escape_md
from music_harvester.engine.normalize import normalize_key, split_artist_track
from music_harvester.http import ApiError
from music_harvester.models import RawTrack, SourceConfig
from music_harvester.sources import SourceUnavailable, adapter_for
from music_harvester.sources.spotify import SpotifyClient


BRIDGE_TITLE_WORDS = (
    "weird",
    "strange",
    "experimental",
    "heavy",
    "electronic",
    "rap",
    "leftfield",
    "noise",
    "chaos",
    "bridge",
    "genreless",
    "abrasive",
)


@dataclass
class BridgeResult:
    bridge_run_id: int
    sources_checked: int
    sources_ingested: int
    high_confidence_sources: int


def bridge_discover(
    store: Store,
    *,
    artists: list[str],
    tracks: list[str],
    source_urls: list[str],
    search_limit: int = 10,
) -> BridgeResult:
    seeds = normalize_seeds(artists, tracks)
    seed_type = "tracks" if tracks and not artists else "artists" if artists and not tracks else "mixed"
    bridge_run_id = store.create_bridge_run(seed_type, artists + tracks)
    sources = candidate_sources(seeds, source_urls, search_limit)

    checked = 0
    ingested = 0
    high_confidence = 0
    for source in sources:
        checked += 1
        source_id = store.upsert_source(source)
        try:
            harvested = adapter_for(source).harvest()
        except (SourceUnavailable, ApiError, OSError, RuntimeError) as exc:
            store.add_error(source.name, str(exc), source_id)
            store.add_bridge_source(bridge_run_id, source_id, "inaccessible", 0, [], str(exc))
            continue

        count = store.add_raw_tracks(source_id, harvested)
        ingested += 1 if count else 0
        match_type, confidence, matched, notes = evaluate_bridge_source(source, harvested, seeds)
        if match_type == "exact_all_seeds":
            high_confidence += 1
        store.add_bridge_source(bridge_run_id, source_id, match_type, confidence, matched, notes)

    write_bridge_sources_md(store, bridge_run_id)
    return BridgeResult(bridge_run_id, checked, ingested, high_confidence)


def normalize_seeds(artists: list[str], tracks: list[str]) -> list[dict]:
    seeds: list[dict] = []
    for artist in artists:
        seeds.append({"type": "artist", "value": artist, "artist": artist, "key": artist.lower()})
    for track in tracks:
        parsed = split_artist_track(track)
        if parsed:
            artist, title = parsed
            seeds.append({"type": "track", "value": track, "artist": artist, "title": title, "key": normalize_key(artist, title)})
        else:
            seeds.append({"type": "track", "value": track, "artist": "", "title": track, "key": track.lower()})
    return seeds


def candidate_sources(seeds: list[dict], source_urls: list[str], search_limit: int) -> list[SourceConfig]:
    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for index, url in enumerate(source_urls, 1):
        source = source_from_url(f"bridge_user_{index}", url, 3.0)
        if source and source.locator not in seen:
            seen.add(source.locator)
            sources.append(source)

    try:
        client = SpotifyClient()
        for seed in sorted(seeds, key=lambda item: len(item["value"]), reverse=True):
            for playlist in client.search_playlists(seed["value"], limit=search_limit):
                external = (playlist.get("external_urls") or {}).get("spotify")
                if not external or external in seen:
                    continue
                seen.add(external)
                name = slug(f"bridge_spotify_{playlist.get('name') or 'playlist'}_{playlist.get('id')}")
                sources.append(SourceConfig(name=name, platform="spotify", source_type="playlist", url=external, weight=2.0))
    except Exception:
        pass

    return sources


def source_from_url(name: str, url: str, weight: float) -> SourceConfig | None:
    if "open.spotify.com/playlist" in url:
        return SourceConfig(name=name, platform="spotify", source_type="playlist", url=url, weight=weight)
    if "soundcloud.com" in url:
        return SourceConfig(name=name, platform="soundcloud", source_type="playlist", url=url, weight=weight)
    return None


def evaluate_bridge_source(source: SourceConfig, tracks: list[RawTrack], seeds: list[dict]) -> tuple[str, float, list[str], str]:
    matched = sorted({seed["value"] for seed in seeds if seed_matches_tracks(seed, tracks)})
    title_score = bridge_title_score([track.playlist_title or "" for track in tracks] + [source.name])
    direct_bonus = 25.0 if source.name.startswith("bridge_user_") else 0.0
    density = bridge_density(tracks, seeds)

    if len(matched) == len(seeds) and seeds:
        has_track_seed = any(seed["type"] == "track" for seed in seeds)
        confidence = (120.0 if has_track_seed else 100.0) + title_score + direct_bonus + density
        return "exact_all_seeds", confidence, matched, "source contains every bridge seed"
    if matched:
        confidence = 30.0 + (10.0 * len(matched)) + title_score + direct_bonus + density
        match_type = "near_bridge" if title_score else "exact_some_seeds"
        return match_type, confidence, matched, "source contains some bridge seeds and may still define useful context"
    if title_score:
        return "title_match_only", title_score + direct_bonus, matched, "source title/context suggests bridge material"
    return "near_bridge", max(5.0, direct_bonus), matched, "weak bridge candidate retained for review"


def seed_matches_tracks(seed: dict, tracks: list[RawTrack]) -> bool:
    if seed["type"] == "artist":
        needle = seed["artist"].lower()
        return any(needle in track.artist.lower() for track in tracks)
    return any(normalize_key(track.artist, track.title) == seed["key"] for track in tracks)


def bridge_density(tracks: list[RawTrack], seeds: list[dict]) -> float:
    if not tracks or not seeds:
        return 0.0
    hits = 0
    for track in tracks:
        text = f"{track.artist} {track.title} {track.playlist_title or ''}".lower()
        if any(seed["artist"].lower() and seed["artist"].lower() in text for seed in seeds):
            hits += 1
    return min(50.0, hits * 3.0)


def bridge_title_score(values: list[str]) -> float:
    text = " ".join(values).lower()
    return 15.0 if any(word in text for word in BRIDGE_TITLE_WORDS) else 0.0


def write_bridge_sources_md(store: Store, bridge_run_id: int) -> None:
    rows = store.bridge_sources_for_run(bridge_run_id)
    output = Path("output")
    output.mkdir(exist_ok=True)
    lines = [
        "| Source | Platform | Match | Confidence | Matched Seeds | Notes |",
        "| ------ | -------- | ----- | ---------- | ------------- | ----- |",
    ]
    for row in rows:
        matched = ", ".join(json.loads(row["matched_seeds_json"] or "[]"))
        lines.append(
            f"| {escape_md(row['name'] or 'unknown')} | {escape_md(row['platform'] or '')} | {escape_md(row['match_type'])} | {float(row['confidence_score']):.1f} | {escape_md(matched)} | {escape_md(row['notes'] or '')} |"
        )
    (output / "bridge_sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:80] or "bridge_source"
