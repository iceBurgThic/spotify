import http from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const publicDir = path.join(root, 'public');
const tokenPath = path.join(root, '.spotify-token.json');

loadEnv(path.join(root, '.env'));

const config = {
  clientId: process.env.SPOTIFY_CLIENT_ID,
  clientSecret: process.env.SPOTIFY_CLIENT_SECRET,
  redirectUri: process.env.SPOTIFY_REDIRECT_URI || 'http://127.0.0.1:8787/callback',
  port: Number(process.env.PORT || 8787),
};

const scopes = [
  'playlist-modify-private',
  'playlist-modify-public',
  'user-read-private',
].join(' ');

const pendingStates = new Set();

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (req.method === 'GET' && url.pathname === '/') {
      return sendFile(res, path.join(publicDir, 'index.html'), 'text/html; charset=utf-8');
    }
    if (req.method === 'GET' && url.pathname === '/app.js') {
      return sendFile(res, path.join(publicDir, 'app.js'), 'text/javascript; charset=utf-8');
    }
    if (req.method === 'GET' && url.pathname === '/styles.css') {
      return sendFile(res, path.join(publicDir, 'styles.css'), 'text/css; charset=utf-8');
    }
    if (req.method === 'GET' && url.pathname === '/login') {
      return startLogin(res);
    }
    if (req.method === 'GET' && url.pathname === '/callback') {
      return handleCallback(url, res);
    }
    if (req.method === 'GET' && url.pathname === '/api/status') {
      return json(res, 200, {
        configured: Boolean(config.clientId && config.clientSecret),
        authenticated: existsSync(tokenPath),
        redirectUri: config.redirectUri,
      });
    }
    if (req.method === 'GET' && url.pathname === '/api/me') {
      return spotifyProxy(res, 'https://api.spotify.com/v1/me');
    }
    if (req.method === 'GET' && url.pathname === '/api/search') {
      const query = url.searchParams.get('q')?.trim();
      if (!query) return json(res, 400, { error: 'Missing search query.' });

      const searchUrl = new URL('https://api.spotify.com/v1/search');
      searchUrl.searchParams.set('type', 'track');
      searchUrl.searchParams.set('limit', '8');
      searchUrl.searchParams.set('q', query);
      return spotifyProxy(res, searchUrl);
    }
    if (req.method === 'POST' && url.pathname === '/api/playlists') {
      return createPlaylist(req, res);
    }

    return json(res, 404, { error: 'Not found.' });
  } catch (error) {
    console.error(error);
    return json(res, 500, { error: error.message || 'Unexpected error.' });
  }
});

server.listen(config.port, '127.0.0.1', () => {
  console.log(`Spotify playlist maker running at http://127.0.0.1:${config.port}`);
  if (!config.clientId || !config.clientSecret) {
    console.log('Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env before logging in.');
  }
});

function loadEnv(filePath) {
  if (!existsSync(filePath)) return;
  const text = readFileSync(filePath, 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const equals = trimmed.indexOf('=');
    if (equals === -1) continue;
    const key = trimmed.slice(0, equals).trim();
    const value = trimmed.slice(equals + 1).trim().replace(/^["']|["']$/g, '');
    if (!process.env[key]) process.env[key] = value;
  }
}

function startLogin(res) {
  if (!config.clientId || !config.clientSecret) {
    return html(res, 500, page('Missing credentials', [
      '<p>Create a <code>.env</code> file from <code>.env.example</code>, then restart this app.</p>',
    ].join('')));
  }

  const state = crypto.randomBytes(16).toString('hex');
  pendingStates.add(state);
  const authUrl = new URL('https://accounts.spotify.com/authorize');
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('client_id', config.clientId);
  authUrl.searchParams.set('scope', scopes);
  authUrl.searchParams.set('redirect_uri', config.redirectUri);
  authUrl.searchParams.set('state', state);

  res.writeHead(302, { Location: authUrl.toString() });
  res.end();
}

async function handleCallback(url, res) {
  const error = url.searchParams.get('error');
  if (error) return html(res, 400, page('Spotify denied access', `<p>${escapeHtml(error)}</p>`));

  const state = url.searchParams.get('state');
  const code = url.searchParams.get('code');
  if (!state || !pendingStates.has(state) || !code) {
    return html(res, 400, page('Invalid callback', '<p>The OAuth state or code was missing.</p>'));
  }
  pendingStates.delete(state);

  const token = await requestToken({
    grant_type: 'authorization_code',
    code,
    redirect_uri: config.redirectUri,
  });

  await saveToken(token);
  return html(res, 200, page('Connected', '<p>You can close this tab and return to the app.</p><p><a href="/">Open app</a></p>'));
}

async function createPlaylist(req, res) {
  const body = await readJson(req);
  const name = body.name?.trim();
  const description = body.description?.trim() || 'Created with a local Spotify playlist helper.';
  const isPublic = Boolean(body.public);
  const uris = Array.isArray(body.uris) ? body.uris.filter(Boolean) : [];

  if (!name) return json(res, 400, { error: 'Playlist name is required.' });
  if (uris.length === 0) return json(res, 400, { error: 'Add at least one track.' });

  const user = await spotifyFetch('https://api.spotify.com/v1/me');
  const playlist = await spotifyFetch(`https://api.spotify.com/v1/users/${encodeURIComponent(user.id)}/playlists`, {
    method: 'POST',
    body: JSON.stringify({ name, description, public: isPublic }),
  });

  for (let i = 0; i < uris.length; i += 100) {
    await spotifyFetch(`https://api.spotify.com/v1/playlists/${playlist.id}/tracks`, {
      method: 'POST',
      body: JSON.stringify({ uris: uris.slice(i, i + 100) }),
    });
  }

  return json(res, 201, playlist);
}

async function spotifyProxy(res, url) {
  const data = await spotifyFetch(url);
  return json(res, 200, data);
}

async function spotifyFetch(url, options = {}) {
  const token = await getValidToken();
  const response = await fetch(url, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token.access_token}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.error?.message || data.error_description || `Spotify API failed with ${response.status}`);
  }
  return data;
}

async function getValidToken() {
  if (!existsSync(tokenPath)) {
    throw new Error('Not logged in. Open /login first.');
  }

  const token = JSON.parse(await readFile(tokenPath, 'utf8'));
  if (Date.now() < token.expires_at - 60_000) return token;
  if (!token.refresh_token) throw new Error('Token expired and no refresh token was stored.');

  const refreshed = await requestToken({
    grant_type: 'refresh_token',
    refresh_token: token.refresh_token,
  });
  const merged = { ...token, ...refreshed, refresh_token: refreshed.refresh_token || token.refresh_token };
  await saveToken(merged);
  return merged;
}

async function requestToken(params) {
  const response = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: {
      'Authorization': `Basic ${Buffer.from(`${config.clientId}:${config.clientSecret}`).toString('base64')}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams(params),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error_description || data.error || `Spotify token request failed with ${response.status}`);
  }
  return data;
}

async function saveToken(token) {
  const normalized = {
    ...token,
    expires_at: Date.now() + Number(token.expires_in || 3600) * 1000,
  };
  await writeFile(tokenPath, JSON.stringify(normalized, null, 2), { mode: 0o600 });
}

async function readJson(req) {
  let text = '';
  for await (const chunk of req) text += chunk;
  return text ? JSON.parse(text) : {};
}

async function sendFile(res, filePath, contentType) {
  const body = await readFile(filePath);
  res.writeHead(200, { 'Content-Type': contentType });
  res.end(body);
}

function json(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

function html(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(body);
}

function page(title, body) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><link rel="stylesheet" href="/styles.css"></head><body><main class="shell">${body}</main></body></html>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
