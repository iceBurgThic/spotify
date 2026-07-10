from __future__ import annotations

from music_harvester.models import Candidate


FEEDBACK_BONUS = {
    "holy_shit": 8.0,
    "more_like_this": 5.0,
    "keep": 3.0,
    "too_much_right_now": -1.5,
    "almost_wrong": -2.0,
    "never_again": -20.0,
}


def score_candidates(
    candidates: list[Candidate],
    *,
    rules: dict,
    taste_profile: dict,
    feedback: dict[int, list[str]],
    recently_played: set[int],
) -> list[Candidate]:
    anchors = {item.lower() for item in taste_profile.get("anchors", {}).get("artists", [])}

    for candidate in candidates:
        score = candidate.score
        source_count = len(candidate.sources)
        platform_count = len(candidate.platforms)
        title_relevance = playlist_title_score(candidate.playlist_titles, rules)

        score += max(0, source_count - 1) * 6.0
        score += max(0, platform_count - 1) * 5.0
        score += title_relevance

        if candidate.artist.lower() in anchors:
            score += 4.0

        if candidate.id not in recently_played:
            score += 2.0
        else:
            score -= 7.0

        for rating in feedback.get(candidate.id, []):
            score += FEEDBACK_BONUS.get(rating, 0.0)

        if source_count == 1 and any("likes" in title.lower() for title in candidate.playlist_titles):
            score -= 1.5

        candidate.score = round(score, 2)
        candidate.why = explain_score(candidate, source_count, platform_count, title_relevance)

    return sorted(candidates, key=lambda item: item.score, reverse=True)


def playlist_title_score(titles: list[str], rules: dict) -> float:
    buckets = rules.get("title_words", {})
    score = 0.0
    text = " ".join(titles).lower()
    for word in buckets.get("high", []):
        if word in text:
            score += 2.5
    for word in buckets.get("medium", []):
        if word in text:
            score += 1.25
    for word in buckets.get("low", []):
        if word in text:
            score += 0.25
    for word in buckets.get("reject", []):
        if word in text:
            score -= 3.0
    return score


def explain_score(candidate: Candidate, source_count: int, platform_count: int, title_relevance: float) -> str:
    bits: list[str] = []
    if source_count > 1:
        bits.append(f"appeared in {source_count} trusted sources")
    else:
        bits.append(f"came from {candidate.sources[0] if candidate.sources else 'one source'}")
    if platform_count > 1:
        bits.append("crossed platforms")
    if title_relevance > 0:
        bits.append("matched relevant playlist-title language")
    if candidate.spotify_uri:
        bits.append("is Spotify-resolvable")
    elif candidate.soundcloud_url:
        bits.append("is a SoundCloud discovery")
    return "Selected because it " + ", ".join(bits) + "."
