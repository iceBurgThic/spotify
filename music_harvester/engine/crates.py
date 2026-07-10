from __future__ import annotations

from music_harvester.models import Candidate


CRATES = [
    "goblin_rap",
    "dusty_rap",
    "drug_dealer_jazz_hands",
    "night_drive_weird",
    "work_mode",
    "weird_but_plausible",
    "maybe_trash",
    "confirmed_fire",
    "rejected",
    "almost_right",
]


def guess_crate(candidate: Candidate) -> str:
    text = " ".join([candidate.artist, candidate.title, *candidate.playlist_titles]).lower()
    if any(word in text for word in ("dust", "alchemist", "crates", "drums")):
        return "dusty_rap"
    if any(word in text for word in ("goblin", "dirty", "villain", "weird")):
        return "goblin_rap"
    if any(word in text for word in ("night", "drive", "smoke")):
        return "night_drive_weird"
    return "weird_but_plausible"
