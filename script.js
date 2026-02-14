const queryEl = document.getElementById('query');
const modeEl = document.getElementById('mode');
const searchBtn = document.getElementById('searchBtn');
const statusEl = document.getElementById('status');
const spinnerEl = document.getElementById('spinner');
const historyBoxEl = document.getElementById('historyBox');
const historyToggleEl = document.getElementById('historyToggle');
const historyListEl = document.getElementById('historyList');
const viewTitleEl = document.getElementById('viewTitle');
const resultsEl = document.getElementById('results');
const videoEl = document.getElementById('video');
const videoMetaEl = document.getElementById('videoMeta');
const seasonTabEl = document.getElementById('seasonTab');
const seasonPosterEl = document.getElementById('seasonPoster');
const seasonTitleEl = document.getElementById('seasonTitle');
const seasonCountEl = document.getElementById('seasonCount');
const seasonEpisodesEl = document.getElementById('seasonEpisodes');
const seasonDownloadEl = document.getElementById('seasonDownload');
const seasonCloseEl = document.getElementById('seasonClose');
const playerModalEl = document.getElementById('playerModal');
const modalCloseEl = document.getElementById('modalClose');
let selectedSeason = null;

function esc(s) {
  return (s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
function setStatus(msg) { statusEl.textContent = msg; }
function setLoading(on, msg = '') {
  if (msg) setStatus(msg);
  spinnerEl.classList.toggle('show', Boolean(on));
}

function renderHistory(items) {
  historyListEl.innerHTML = '';
  if (!items.length) {
    historyListEl.innerHTML = '<li>No history yet.</li>';
    return;
  }
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item.summary || 'unknown event';
    historyListEl.appendChild(li);
  }
}

async function post(path, payload) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || 'request failed');
  return data;
}

function openPopupPlayer(mediaUrl, metaText) {
  videoEl.src = mediaUrl;
  videoMetaEl.textContent = metaText;
  playerModalEl.classList.add('open');
  videoEl.play().catch(() => {});
}

function closePopupPlayer() {
  playerModalEl.classList.remove('open');
  videoEl.pause();
}

function buildSeasonTab(item, opts = {}) {
  const isLibrary = Boolean(opts.library);
  const downloadedEpisodes = new Set(opts.downloadedEpisodes || []);
  const filesByEpisode = opts.filesByEpisode || {};
  selectedSeason = item;
  const title = item.name || item.title;
  const totalEpisodes = item.episodes || item.total_episodes;
  const poster = item.image_url || item.poster_url || 'https://placehold.co/600x900?text=No+Poster';
  seasonPosterEl.src = poster;
  seasonTitleEl.textContent = isLibrary ? title : `#${item.index} ${title}`;
  if (isLibrary) {
    seasonCountEl.textContent = `${downloadedEpisodes.size}/${totalEpisodes} downloaded`;
    seasonDownloadEl.style.display = '';
  } else {
    seasonCountEl.textContent = `${totalEpisodes} episodes`;
    seasonDownloadEl.style.display = '';
  }
  seasonEpisodesEl.innerHTML = '';
  for (let ep = 1; ep <= totalEpisodes; ep += 1) {
    const btn = document.createElement('button');
    btn.className = 'ep-btn';
    btn.textContent = String(ep);
    if (isLibrary && !downloadedEpisodes.has(ep)) {
      btn.classList.add('missing');
      btn.disabled = true;
      btn.title = 'Not downloaded';
    } else if (isLibrary && downloadedEpisodes.has(ep)) {
      btn.classList.add('downloaded');
      btn.onclick = () => {
        const fileInfo = filesByEpisode[String(ep)];
        if (!fileInfo) return;
        openPopupPlayer(fileInfo.media_url, `${title} - Episode ${ep} (${fileInfo.filename})`);
        setStatus(`Playing downloaded ${title} episode ${ep}`);
        post('/api/history_event', {
          event: 'play_downloaded_file',
          anime: title,
          filename: fileInfo.filename,
          query: queryEl.value.trim(),
        }).then(() => loadHistory()).catch(() => {});
      };
    } else {
      btn.onclick = async () => {
        try {
          setLoading(true, `Downloading ${title} episode ${ep}...`);
          const res = await post('/api/play_episode', {
            query: queryEl.value.trim(),
            mode: modeEl.value,
            index: item.index,
            episode: ep,
            anime: title,
            image_url: item.image_url || '',
          });
          openPopupPlayer(res.media_url, `${title} - Episode ${ep} (${res.filename})`);
          setStatus(`Now playing ${title} episode ${ep}`);
          loadHistory();
          loadLibrary();
        } catch (err) {
          setStatus(`Error: ${err.message}`);
        } finally {
          setLoading(false);
        }
      };
    }
    seasonEpisodesEl.appendChild(btn);
  }
  seasonTabEl.classList.add('open');
}

function render(items) {
  viewTitleEl.textContent = 'Search Results';
  selectedSeason = null;
  seasonTabEl.classList.remove('open');
  resultsEl.innerHTML = '';
  if (!items.length) {
    resultsEl.innerHTML = '<div>No results.</div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'card';
    const title = esc(item.name);
    const imageUrl = item.image_url ? esc(item.image_url) : 'https://placehold.co/600x900?text=No+Poster';

    card.innerHTML = `
      <div class="poster-wrap" role="button" tabindex="0">
        <img class="poster" src="${imageUrl}" alt="${title}" />
        <div class="tap-hint">open season tab</div>
      </div>
      <div class="meta">
        <div class="title">#${item.index} ${title}</div>
        <div class="eps">${item.episodes} episodes</div>
      </div>`;

    const posterWrap = card.querySelector('.poster-wrap');
    posterWrap.onclick = () => buildSeasonTab(item);
    posterWrap.onkeydown = (evt) => {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        buildSeasonTab(item);
      }
    };

    resultsEl.appendChild(card);
  }
}

function renderLibrary(items) {
  viewTitleEl.textContent = 'Downloaded Library';
  selectedSeason = null;
  seasonTabEl.classList.remove('open');
  resultsEl.innerHTML = '';
  if (!items.length) {
    resultsEl.innerHTML = '<div>No downloads yet.</div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'card';
    const title = esc(item.title);
    const imageUrl = item.poster_url ? esc(item.poster_url) : 'https://placehold.co/600x900?text=Downloaded';
    card.innerHTML = `
      <div class="poster-wrap" role="button" tabindex="0">
        <img class="poster" src="${imageUrl}" alt="${title}" />
        <div class="tap-hint">open season tab</div>
      </div>
      <div class="meta">
        <div class="title">${title}</div>
        <div class="eps">${item.downloaded_count}/${item.total_episodes} downloaded</div>
      </div>`;

    const openLibrarySeason = () => {
      buildSeasonTab(
        item,
        {
          library: true,
          downloadedEpisodes: item.downloaded_episodes || [],
          filesByEpisode: item.files_by_episode || {},
        }
      );
      setStatus(`Opened library season: ${item.title}`);
    };

    const posterWrap = card.querySelector('.poster-wrap');
    posterWrap.onclick = openLibrarySeason;
    posterWrap.onkeydown = (evt) => {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        openLibrarySeason();
      }
    };
    resultsEl.appendChild(card);
  }
}

seasonDownloadEl.onclick = async () => {
  if (!selectedSeason) return;
  try {
    const isLibrary = !selectedSeason.index;
    const seasonTitle = selectedSeason.name || selectedSeason.title;
    setLoading(true, `Starting season download for ${seasonTitle}...`);
    let res;
    if (isLibrary) {
      res = await post('/api/download_all_by_title', {
        title: selectedSeason.title,
        mode: modeEl.value,
        image_url: selectedSeason.poster_url || '',
      });
    } else {
      res = await post('/api/download_season', {
        query: queryEl.value.trim(),
        mode: modeEl.value,
        index: selectedSeason.index,
        episodes: selectedSeason.episodes,
        anime: selectedSeason.name,
        image_url: selectedSeason.image_url || '',
      });
    }
    setStatus(res.message);
    loadHistory();
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    setLoading(false);
  }
};

seasonCloseEl.onclick = () => {
  seasonTabEl.classList.remove('open');
  selectedSeason = null;
};

modalCloseEl.onclick = closePopupPlayer;
playerModalEl.onclick = (evt) => {
  if (evt.target === playerModalEl) closePopupPlayer();
};
historyToggleEl.onclick = () => {
  const collapsed = historyBoxEl.classList.toggle('collapsed');
  historyToggleEl.textContent = collapsed ? 'Show' : 'Hide';
};

async function doSearch() {
  const q = queryEl.value.trim();
  if (!q) {
    await loadLibrary();
    await loadHistory();
    return;
  }
  setLoading(true, 'Searching...');
  resultsEl.innerHTML = '';

  try {
    const params = new URLSearchParams({q, mode: modeEl.value});
    const resp = await fetch('/api/search?' + params.toString());
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'search failed');
    render(data.results || []);
    setStatus(`Found ${(data.results || []).length} result(s). Click a poster to pick episodes.`);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

async function loadLibrary() {
  setLoading(true, 'Loading downloaded library...');
  try {
    const resp = await fetch('/api/library');
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'library failed');
    renderLibrary(data.items || []);
    setStatus(`Loaded ${(data.items || []).length} downloaded file(s).`);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

async function loadHistory() {
  try {
    const resp = await fetch('/api/history');
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'history failed');
    renderHistory(data.items || []);
  } catch {
    renderHistory([]);
  }
}

searchBtn.onclick = doSearch;
queryEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
loadLibrary();
loadHistory();
