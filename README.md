# qrator

High quality nerd shit for making Spotify playlists from trusted human taste instead of algorithm soup.

Two modes:

- `npm start`: tiny manual Spotify playlist GUI.
- `python -m music_harvester.main`: source-driven playlist generator.

## Manual GUI

Create a Spotify Developer app and add this redirect URI:

```text
http://127.0.0.1:8787/callback
```

Then:

```bash
cp .env.example .env
npm start
```

Fill `.env` with your Spotify client ID/secret, open:

```text
http://127.0.0.1:8787
```

Connect Spotify, pick tracks, create playlist, act like this was normal.

## Harvester

Install the Python bits:

```bash
python -m pip install -r requirements.txt
```

Edit:

```text
music_harvester/config/sources.yaml
music_harvester/config/taste_profile.yaml
music_harvester/config/rules.yaml
```

Then:

```bash
python -m music_harvester.main ingest
python -m music_harvester.main generate --mode balanced_discovery --length 40
```

It saves:

```text
output/candidates.json
output/final_playlist.md
output/rejected.md
```

Write only after you like the preview:

```bash
python -m music_harvester.main write-spotify --playlist-name "qrator found this"
```

Default modes are neutral: `balanced_discovery`, `high_trust`, `weird_pull`, `bridge_builder`, and `heavy_motion`.

Tracks can belong to multiple pools at once: `anchors`, `adjacent`, `outer_ring`, `wildcards`, `bridge_tracks`, `texture_match`, `energy_match`, `deep_source`, `confirmed`, `rejected`, and `almost`. Genre can be metadata, but it is not the organizing principle.

## Tiny Safety Note

`.env`, Spotify tokens, the local DB, and generated output are ignored by git. Do not commit secrets. Vibes are not a security model.
