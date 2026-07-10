const statusEl = document.querySelector('#status');
const resultsEl = document.querySelector('#results');
const selectedEl = document.querySelector('#selected');
const searchForm = document.querySelector('#search-form');
const playlistForm = document.querySelector('#playlist-form');
const createResult = document.querySelector('#create-result');
const clearButton = document.querySelector('#clear');
const discoverForm = document.querySelector('#discover-form');
const discoverOutput = document.querySelector('#discover-output');

const selected = new Map();

await refreshStatus();
renderSelected();

searchForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = document.querySelector('#search-query').value.trim();
  if (!query) return;

  resultsEl.textContent = 'Searching...';
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    renderResults(data.tracks.items);
  } catch (error) {
    resultsEl.textContent = error.message;
  }
});

discoverForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = document.querySelector('#discover-query').value.trim();
  const length = Number(document.querySelector('#discover-length').value || 30);
  if (!query) return;

  discoverOutput.className = 'markdown-output empty';
  discoverOutput.textContent = 'Discovering...';
  try {
    const data = await api('/api/discover', {
      method: 'POST',
      body: JSON.stringify({ query, length }),
    });
    renderDiscovery(data);
  } catch (error) {
    discoverOutput.textContent = error.message;
  }
});

playlistForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  createResult.textContent = 'Creating...';

  try {
    const playlist = await api('/api/playlists', {
      method: 'POST',
      body: JSON.stringify({
        name: document.querySelector('#playlist-name').value,
        description: document.querySelector('#playlist-description').value,
        public: document.querySelector('#playlist-public').checked,
        uris: [...selected.values()].map((track) => track.uri),
      }),
    });
    createResult.innerHTML = `<a href="${playlist.external_urls.spotify}" target="_blank" rel="noreferrer">Open playlist on Spotify</a>`;
  } catch (error) {
    createResult.textContent = error.message;
  }
});

clearButton.addEventListener('click', () => {
  selected.clear();
  renderSelected();
});

async function refreshStatus() {
  const status = await api('/api/status');
  if (!status.configured) {
    statusEl.innerHTML = `Add <code>.env</code> credentials, then restart. Redirect URI: <code>${status.redirectUri}</code>`;
    return;
  }
  statusEl.textContent = status.authenticated ? 'Connected token found.' : 'Credentials found. Connect Spotify to authorize playlists.';
}

function renderResults(tracks) {
  resultsEl.innerHTML = '';
  if (!tracks.length) {
    resultsEl.textContent = 'No tracks found.';
    return;
  }
  for (const track of tracks) {
    resultsEl.append(trackRow(track, 'Add', () => {
      selected.set(track.uri, track);
      renderSelected();
    }));
  }
}

function renderSelected() {
  selectedEl.innerHTML = '';
  if (selected.size === 0) {
    selectedEl.className = 'list empty';
    selectedEl.textContent = 'No tracks selected yet.';
    return;
  }
  selectedEl.className = 'list';
  for (const track of selected.values()) {
    selectedEl.append(trackRow(track, 'Remove', () => {
      selected.delete(track.uri);
      renderSelected();
    }));
  }
}

function renderDiscovery(data) {
  const sections = [];
  if (data.playlist) sections.push(['Shortlist', data.playlist]);
  if (data.sources) sections.push(['Bridge Sources', data.sources]);
  if (data.unresolved) sections.push(['Unresolved', data.unresolved]);
  if (data.generate_stdout) sections.push(['Run Log', data.generate_stdout]);

  discoverOutput.className = 'markdown-output';
  discoverOutput.innerHTML = sections.map(([title, text]) => {
    return `<section><h3>${escapeHtml(title)}</h3><pre>${escapeHtml(text.trim() || 'No output.')}</pre></section>`;
  }).join('');
}

function trackRow(track, action, onClick) {
  const row = document.createElement('article');
  row.className = 'track';

  const image = document.createElement('img');
  image.src = track.album.images.at(-1)?.url || '';
  image.alt = '';

  const text = document.createElement('div');
  text.className = 'track-text';
  text.innerHTML = `<strong>${escapeHtml(track.name)}</strong><span>${escapeHtml(track.artists.map((artist) => artist.name).join(', '))}</span>`;

  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = action;
  button.addEventListener('click', onClick);

  row.append(image, text, button);
  return row;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed with ${response.status}`);
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
