from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from music_harvester.engine.normalize import normalize_key, normalize_text
from music_harvester.models import Candidate, RawTrack, SourceConfig


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def init(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self.conn.commit()

    def upsert_source(self, source: SourceConfig) -> int:
        self.conn.execute(
            """
            insert into sources (name, platform, source_type, url_or_username, weight)
            values (?, ?, ?, ?, ?)
            on conflict(name) do update set
              platform=excluded.platform,
              source_type=excluded.source_type,
              url_or_username=excluded.url_or_username,
              weight=excluded.weight
            """,
            (source.name, source.platform, source.source_type, source.locator, source.weight),
        )
        self.conn.commit()
        return int(self.conn.execute("select id from sources where name = ?", (source.name,)).fetchone()["id"])

    def add_error(self, source_name: str, message: str, source_id: int | None = None) -> None:
        self.conn.execute(
            "insert into source_errors (source_id, source_name, message) values (?, ?, ?)",
            (source_id, source_name, message),
        )
        self.conn.commit()

    def add_raw_tracks(self, source_id: int, tracks: Iterable[RawTrack]) -> int:
        count = 0
        for track in tracks:
            raw_id = self._insert_raw_track(source_id, track)
            normalized_id = self._upsert_normalized(track)
            self._insert_provenance(normalized_id, source_id, track)
            count += 1 if raw_id else 0
        self.conn.commit()
        return count

    def _insert_raw_track(self, source_id: int, track: RawTrack) -> int:
        cursor = self.conn.execute(
            """
            insert into raw_tracks (
              source_id, platform_track_id, raw_artist, raw_title, raw_album, raw_url, raw_payload_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                track.platform_track_id,
                track.artist,
                track.title,
                track.album,
                track.url,
                json.dumps(track.payload),
            ),
        )
        return int(cursor.lastrowid)

    def _upsert_normalized(self, track: RawTrack) -> int:
        artist = normalize_text(track.artist)
        title = normalize_text(track.title)
        album = normalize_text(track.album) if track.album else None
        key = normalize_key(artist, title)
        spotify_uri = track.payload.get("spotify_uri") if track.platform == "spotify" else None
        soundcloud_url = track.url if track.platform == "soundcloud" else None

        self.conn.execute(
            """
            insert into normalized_tracks (
              canonical_artist, canonical_title, canonical_album, spotify_uri, soundcloud_url, unique_key
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(unique_key) do update set
              spotify_uri=coalesce(normalized_tracks.spotify_uri, excluded.spotify_uri),
              soundcloud_url=coalesce(normalized_tracks.soundcloud_url, excluded.soundcloud_url),
              canonical_album=coalesce(normalized_tracks.canonical_album, excluded.canonical_album)
            """,
            (artist, title, album, spotify_uri, soundcloud_url, key),
        )
        row = self.conn.execute("select id from normalized_tracks where unique_key = ?", (key,)).fetchone()
        return int(row["id"])

    def _insert_provenance(self, normalized_id: int, source_id: int, track: RawTrack) -> None:
        self.conn.execute(
            """
            insert into track_provenance (
              normalized_track_id, source_id, source_context, playlist_title, position
            ) values (?, ?, ?, ?, ?)
            """,
            (normalized_id, source_id, track.source_context, track.playlist_title, track.position),
        )

    def candidates(self) -> list[Candidate]:
        rows = self.conn.execute(
            """
            select
              nt.id,
              nt.canonical_artist,
              nt.canonical_title,
              nt.canonical_album,
              nt.spotify_uri,
              nt.soundcloud_url,
              group_concat(distinct s.name) as source_names,
              group_concat(distinct s.platform) as platforms,
              group_concat(distinct coalesce(tp.playlist_title, '')) as playlist_titles,
              coalesce(max(bs.confidence_score), 0) as bridge_source_score,
              group_concat(distinct bs.match_type) as bridge_match_types,
              sum(s.weight) as source_weight,
              count(distinct s.id) as source_count,
              count(distinct s.platform) as platform_count
            from normalized_tracks nt
            join track_provenance tp on tp.normalized_track_id = nt.id
            join sources s on s.id = tp.source_id
            left join bridge_sources bs on bs.source_id = s.id
            group by nt.id
            """
        ).fetchall()

        return [
            Candidate(
                id=int(row["id"]),
                artist=row["canonical_artist"],
                title=row["canonical_title"],
                album=row["canonical_album"],
                spotify_uri=row["spotify_uri"],
                soundcloud_url=row["soundcloud_url"],
                score=float(row["source_weight"] or 0),
                sources=_csv(row["source_names"]),
                platforms=_csv(row["platforms"]),
                playlist_titles=[item for item in _csv(row["playlist_titles"]) if item],
                why="",
                bridge_source_score=float(row["bridge_source_score"] or 0),
                bridge_match_types=_csv(row["bridge_match_types"]),
            )
            for row in rows
        ]

    def create_bridge_run(self, seed_type: str, seed_values: list[str]) -> int:
        cursor = self.conn.execute(
            "insert into bridge_runs (seed_type, seed_values_json) values (?, ?)",
            (seed_type, json.dumps(seed_values)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_bridge_source(
        self,
        bridge_run_id: int,
        source_id: int | None,
        match_type: str,
        confidence_score: float,
        matched_seeds: list[str],
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            insert into bridge_sources (
              bridge_run_id, source_id, match_type, confidence_score, matched_seeds_json, notes
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (bridge_run_id, source_id, match_type, confidence_score, json.dumps(matched_seeds), notes),
        )
        self.conn.commit()

    def bridge_sources_for_run(self, bridge_run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select
              bs.*,
              s.name,
              s.platform,
              s.source_type,
              s.url_or_username
            from bridge_sources bs
            left join sources s on s.id = bs.source_id
            where bs.bridge_run_id = ?
            order by bs.confidence_score desc
            """,
            (bridge_run_id,),
        ).fetchall()

    def latest_bridge_run_id(self) -> int | None:
        row = self.conn.execute("select id from bridge_runs order by id desc limit 1").fetchone()
        return int(row["id"]) if row else None

    def add_feedback(self, artist: str, title: str, rating: str, note: str | None = None) -> bool:
        key = normalize_key(artist, title)
        row = self.conn.execute("select id from normalized_tracks where unique_key = ?", (key,)).fetchone()
        if not row:
            return False
        self.conn.execute(
            "insert into feedback (normalized_track_id, rating, note) values (?, ?, ?)",
            (int(row["id"]), rating, note),
        )
        self.conn.commit()
        return True

    def feedback_map(self) -> dict[int, list[str]]:
        rows = self.conn.execute("select normalized_track_id, rating from feedback").fetchall()
        result: dict[int, list[str]] = {}
        for row in rows:
            result.setdefault(int(row["normalized_track_id"]), []).append(row["rating"])
        return result

    def recently_played_ids(self, days: int) -> set[int]:
        rows = self.conn.execute(
            "select normalized_track_id from plays where played_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()
        return {int(row["normalized_track_id"]) for row in rows}

    def record_run(self, name: str, mode: str, written: bool, spotify_playlist_id: str | None, track_ids: list[int]) -> int:
        cursor = self.conn.execute(
            "insert into playlist_runs (name, mode, written_to_spotify, spotify_playlist_id) values (?, ?, ?, ?)",
            (name, mode, 1 if written else 0, spotify_playlist_id),
        )
        run_id = int(cursor.lastrowid)
        for track_id in track_ids:
            self.conn.execute(
                "insert into plays (normalized_track_id, playlist_run_id) values (?, ?)",
                (track_id, run_id),
            )
        self.conn.commit()
        return run_id


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split(",") if part]
