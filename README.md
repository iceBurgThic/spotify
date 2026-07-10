# Spotify Playlist Maker

A local helper app for creating real Spotify playlists without sharing your Spotify password.

## Setup

1. Open the Spotify Developer Dashboard.
2. Create an app.
3. Add this redirect URI in the app settings:

   ```text
   http://127.0.0.1:8787/callback
   ```

4. Copy `.env.example` to `.env`.
5. Fill in:

   ```text
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   ```

## Run

```bash
npm start
```

Open:

```text
http://127.0.0.1:8787
```

Click **Connect Spotify**, authorize the app, then search tracks and create a playlist.

## Notes

- `.env` and `.spotify-token.json` are ignored by git.
- The app uses Spotify OAuth scopes for creating public or private playlists.
- It uses Node 18+ built-ins only, so there is no dependency install step.
