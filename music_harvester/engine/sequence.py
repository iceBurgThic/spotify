from __future__ import annotations

from collections import Counter, deque

from music_harvester.engine.pools import pool_mix_for_mode
from music_harvester.models import Candidate


def sequence_playlist(candidates: list[Candidate], rules: dict, length: int | None = None, mode: str = "balanced_discovery") -> list[Candidate]:
    target = int(length or rules.get("playlist_length", 40))
    max_per_artist = int(rules.get("max_tracks_per_artist", 3))
    min_artist_distance = int(rules.get("min_distance_same_artist", 10))
    max_per_source = int(rules.get("max_tracks_per_single_source", 5))
    pool_mix = pool_mix_for_mode(rules, mode)

    pool = sorted(candidates, key=lambda item: item.score, reverse=True)
    selected: list[Candidate] = []
    artist_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    pool_counts: Counter[str] = Counter()
    recent_artists: deque[str] = deque(maxlen=min_artist_distance)

    while pool and len(selected) < target:
        index = choose_next(
            pool,
            selected,
            artist_counts,
            source_counts,
            pool_counts,
            recent_artists,
            max_per_artist,
            max_per_source,
            pool_mix,
            target,
        )
        if index is None:
            break
        item = pool.pop(index)
        selected.append(item)
        artist_counts[item.artist.lower()] += 1
        for source in item.sources:
            source_counts[source] += 1
        for pool_name in item.pools:
            pool_counts[pool_name] += 1
        recent_artists.append(item.artist.lower())

    return selected


def choose_next(
    pool: list[Candidate],
    selected: list[Candidate],
    artist_counts: Counter[str],
    source_counts: Counter[str],
    pool_counts: Counter[str],
    recent_artists: deque[str],
    max_per_artist: int,
    max_per_source: int,
    pool_mix: dict[str, float],
    target: int,
) -> int | None:
    viable: list[tuple[float, int]] = []
    for index, candidate in enumerate(pool[:80]):
        artist = candidate.artist.lower()
        if artist_counts[artist] >= max_per_artist:
            continue
        if artist in recent_artists:
            continue
        if any(source_counts[source] >= max_per_source for source in candidate.sources):
            continue
        if would_make_source_run(selected, candidate):
            continue
        viable.append((candidate.score + pool_need_bonus(candidate, pool_counts, pool_mix, target), index))
    if not viable:
        return None
    return max(viable, key=lambda item: item[0])[1]


def would_make_source_run(selected: list[Candidate], candidate: Candidate) -> bool:
    if len(selected) < 2:
        return False
    candidate_sources = set(candidate.sources)
    if not candidate_sources:
        return False
    return all(candidate_sources.intersection(item.sources) for item in selected[-2:])


def pool_need_bonus(candidate: Candidate, pool_counts: Counter[str], pool_mix: dict[str, float], target: int) -> float:
    if not pool_mix:
        return 0.0
    bonus = 0.0
    for pool_name in candidate.pools:
        desired = pool_mix.get(pool_name)
        if not desired:
            continue
        target_count = max(1, round(desired * target))
        if pool_counts[pool_name] < target_count:
            bonus += 2.0 * (target_count - pool_counts[pool_name]) / target_count
    return min(bonus, 4.0)
