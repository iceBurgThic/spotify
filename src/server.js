import http from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const publicDir = path.join(root, 'public');
const tokenPath = path.join(root, '.spotify-token.json');
const runFile = promisify(execFile);

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
  'playlist-read-private',
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
      return await spotifyProxy(res, 'https://api.spotify.com/v1/me');
    }
    if (req.method === 'GET' && url.pathname === '/api/search') {
      const query = url.searchParams.get('q')?.trim();
      if (!query) return json(res, 400, { error: 'Missing search query.' });

      const searchUrl = new URL('https://api.spotify.com/v1/search');
      searchUrl.searchParams.set('type', 'track');
      searchUrl.searchParams.set('limit', '8');
      searchUrl.searchParams.set('q', query);
      return await spotifyProxy(res, searchUrl);
    }
    if (req.method === 'POST' && url.pathname === '/api/playlists') {
      return await createPlaylist(req, res);
    }
    if (req.method === 'POST' && url.pathname === '/api/discover') {
      return await discover(req, res);
    }
    if (req.method === 'POST' && url.pathname === '/api/discover/playlist') {
      return await createDiscoveryPlaylist(req, res);
    }

    return json(res, 404, { error: 'Not found.' });
  } catch (error) {
    console.error(error);
    const status = Number.isInteger(error.status) ? error.status : 500;
    return json(res, status, { error: error.message || 'Unexpected error.' });
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

  const playlist = await spotifyFetch('https://api.spotify.com/v1/me/playlists', {
    method: 'POST',
    body: JSON.stringify({ name, description, public: isPublic }),
  });

  for (let i = 0; i < uris.length; i += 100) {
    await spotifyFetch(`https://api.spotify.com/v1/playlists/${playlist.id}/items`, {
      method: 'POST',
      body: JSON.stringify({ uris: uris.slice(i, i + 100) }),
    });
  }

  return json(res, 201, playlist);
}

async function discover(req, res) {
  const body = await readJson(req);
  const input = String(body.query || '').trim();
  const length = Math.max(5, Math.min(Number(body.length || 30), 60));
  const mode = body.mode === 'bridge' ? 'bridge' : 'distill';
  if (!input) return json(res, 400, { error: 'Missing discovery query.' });

  const parsed = parseDiscoveryInput(input);
  if (parsed.seeds.length < 2 && parsed.urls.length === 0 && !parsed.text) {
    return json(res, 400, { error: 'Use at least two seeds, URLs, or pasted tracklist text.' });
  }

  const dbPath = path.join('/tmp', `qrator-gui-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.sqlite`);
  const seedArgs = parsed.seeds.length >= 2 ? parsed.seeds : ['source blend', 'manual sources'];
  const bridgeArgs = [
    '-m',
    'music_harvester.main',
    '--db',
    dbPath,
    'bridge-discover',
    parsed.seedType,
    ...seedArgs,
    '--search-limit',
    mode === 'bridge' && parsed.seeds.length >= 2 ? '10' : '0',
  ];
  for (const sourceUrl of parsed.urls) bridgeArgs.push('--source-url', sourceUrl);
  if (parsed.text) bridgeArgs.push('--text', parsed.text);

  const generateArgs = [
    '-m',
    'music_harvester.main',
    '--db',
    dbPath,
    'generate',
    '--mode',
    mode === 'bridge' && parsed.seeds.length >= 2 ? 'bridge_discovery' : 'balanced_discovery',
    '--length',
    String(length),
  ];

  const bridge = await runPython(bridgeArgs);
  const generated = await runPython(generateArgs);

  const [playlist, sources, unresolved] = await Promise.all([
    readOptional(path.join(root, 'output', 'final_playlist.md')),
    readOptional(path.join(root, 'output', 'bridge_sources.md')),
    readOptional(path.join(root, 'output', 'unresolved_interesting.md')),
  ]);
  const seeds = await readPlaylistSeeds(path.join(root, 'output', 'final_playlist.json'));

  const response = {
    bridge_stdout: bridge.stdout,
    generate_stdout: generated.stdout,
    playlist,
    sources,
    unresolved,
    seeds,
    mode,
  };
  try {
    rmSync(dbPath, { force: true });
  } catch {}
  return json(res, 200, response);
}

async function createDiscoveryPlaylist(req, res) {
  const body = await readJson(req);
  const name = body.name?.trim() || `qrator ${new Date().toISOString().slice(0, 10)}`;
  const isPublic = Boolean(body.public);
  const finalPath = path.join(root, 'output', 'final_playlist.json');
  if (!existsSync(finalPath)) return json(res, 400, { error: 'No discovery shortlist found yet.' });

  const items = JSON.parse(await readFile(finalPath, 'utf8'));
  const uris = items.map((item) => item.spotify_uri).filter(Boolean);
  if (uris.length === 0) {
    return json(res, 400, { error: 'No Spotify-resolved tracks found in the current shortlist.' });
  }

  const playlist = await spotifyFetch('https://api.spotify.com/v1/me/playlists', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: 'Created by qrator from a discovery shortlist.',
      public: isPublic,
    }),
  });

  for (let i = 0; i < uris.length; i += 100) {
    await spotifyFetch(`https://api.spotify.com/v1/playlists/${playlist.id}/items`, {
      method: 'POST',
      body: JSON.stringify({ uris: uris.slice(i, i + 100) }),
    });
  }

  return json(res, 201, {
    playlist,
    added: uris.length,
    unresolved: items.length - uris.length,
  });
}

function parseDiscoveryInput(input) {
  const lines = input.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const urls = [];
  const seeds = [];
  const textLines = [];

  for (const line of lines) {
    if (/^https?:\/\//i.test(line)) {
      urls.push(line);
      continue;
    }
    textLines.push(line);
    if (looksLikeTrack(line)) seeds.push(line);
  }

  if (seeds.length >= 2) {
    return { seedType: '--tracks', seeds: seeds.slice(0, 4), urls, text: textLines.join('\n') };
  }

  const artistSeeds = lines
    .filter((line) => !/^https?:\/\//i.test(line))
    .flatMap((line) => line.split(/\s+[+&]\s+|,\s*/))
    .map((line) => line.trim())
    .filter(Boolean);

  return {
    seedType: '--artists',
    seeds: artistSeeds.slice(0, 4),
    urls,
    text: textLines.join('\n'),
  };
}

function looksLikeTrack(line) {
  return /\s[-–—:]\s/.test(line) || /\sby\s/i.test(line);
}

async function runPython(args) {
  try {
    return await runFile('python', args, {
      cwd: root,
      timeout: 180_000,
      maxBuffer: 1024 * 1024 * 8,
    });
  } catch (error) {
    const detail = [error.stdout, error.stderr, error.message].filter(Boolean).join('\n');
    throw new Error(detail || 'Discovery command failed.');
  }
}

async function readOptional(filePath) {
  try {
    return await readFile(filePath, 'utf8');
  } catch {
    return '';
  }
}

async function readPlaylistSeeds(filePath) {
  try {
    const items = JSON.parse(await readFile(filePath, 'utf8'));
    if (!Array.isArray(items)) return [];
    return items
      .map((item) => `${item.artist} - ${item.title}`.trim())
      .filter((item) => item && !item.startsWith(' - '))
      .slice(0, 12);
  } catch {
    return [];
  }
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
  if (!response.ok) throw spotifyError(response, data, url, options);
  return data;
}

function spotifyError(response, data, url, options = {}) {
  const message = data.error?.message || data.error_description || `Spotify API failed with ${response.status}`;
  const hint = response.status === 403
    ? 'Spotify returned 403 Forbidden. Reconnect Spotify from /login so the token has playlist-write scopes; if it still fails, make sure your Spotify app is not blocking this account in development mode.'
    : '';
  const method = options.method || 'GET';
  const detail = data.error ? `Spotify error: ${JSON.stringify(data.error)}` : '';
  const error = new Error([message, hint, `${method} ${url}`, detail].filter(Boolean).join(' '));
  error.status = response.status;
  return error;
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
