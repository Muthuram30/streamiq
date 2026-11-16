const state = { catalog: [], uploads: [], view: 'stream', playing: null, lastSaved: 0, conserveData: false, stallTimer: null };
const $ = (id) => document.getElementById(id);

function formatSize(bytes) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
}

function notify(message) {
  const toast = $('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove('show'), 3500);
}

function card(item) {
  const button = document.createElement('button');
  button.className = 'card';
  const thumb = document.createElement('div');
  thumb.className = 'thumb';
  if (item.posterUrl) {
    const poster = document.createElement('img');
    poster.src = item.posterUrl;
    poster.alt = '';
    poster.loading = 'lazy';
    thumb.append(poster);
  }
  const icon = document.createElement('span');
  icon.textContent = '▶';
  thumb.append(icon);
  const info = document.createElement('div');
  info.className = 'card-info';
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = item.name;
  const meta = document.createElement('div');
  meta.className = 'card-meta';
  meta.textContent = `${item.section === 'catalog' ? 'Streaming' : 'My Drive'} · ${formatSize(item.size)}`;
  info.append(title, meta);
  button.append(thumb, info);
  button.addEventListener('click', () => {
    openPlayer(item);
  });
  return button;
}

function chooseSource(item) {
  const selected = $('quality').value;
  const slow = ['slow-2g', '2g', '3g'].includes(navigator.connection?.effectiveType);
  return item.efficientUrl && (selected === 'efficient' || (selected === 'auto' && (slow || state.conserveData))) ? item.efficientUrl : item.url;
}

function switchSource(item, position = 0) {
  const player = $('player');
  player.src = chooseSource(item);
  player.addEventListener('loadedmetadata', () => {
    if (position > 0 && Number.isFinite(player.duration)) {
      player.currentTime = Math.min(position, Math.max(0, player.duration - 0.5));
    }
    player.play().catch(() => {});
  }, { once: true });
}

async function openPlayer(item) {
  state.playing = item;
  state.lastSaved = 0;
  state.conserveData = false;
  $('player-title').textContent = item.name;
  $('quality').value = 'auto';
  $('quality').querySelector('[value="efficient"]').disabled = !item.efficientUrl;
  $('player-dialog').showModal();
  let position = 0;
  try {
    const response = await fetch(`/api/playback?section=${item.section}&id=${encodeURIComponent(item.id)}`);
    if (response.ok) position = (await response.json()).position;
  } catch (_) {
    // Playback remains available if history cannot load.
  }
  if (state.playing === item) {
    switchSource(item, position);
  }
}

async function savePosition(started = false) {
  const item = state.playing;
  const player = $('player');
  if (!item || !Number.isFinite(player.duration) || !Number.isFinite(player.currentTime) || player.duration <= 0) return;
  try {
    await fetch('/api/playback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section: item.section, id: item.id, position: player.currentTime,
        duration: player.duration, started })
    });
  } catch (_) {
    // A network interruption should not stop playback.
  }
}

function restorePlaying() {
  clearTimeout(state.stallTimer);
  if (state.playing) {
    savePosition();
    $('player').pause();
    $('player').removeAttribute('src');
    $('player').load();
    state.playing = null;
  }
}


function render() {
  const query = $('search').value.trim().toLowerCase();
  for (const [section, items, gridId, emptyText] of [
    ['catalog', state.catalog, 'catalog-grid', 'Your streaming collection is ready for videos.'],
    ['uploads', state.uploads, 'drive-grid', 'Your Drive is empty. Upload a video to get started.']
  ]) {
    const grid = $(gridId);
    const matches = items.filter((item) => item.name.toLowerCase().includes(query));
    grid.replaceChildren(...matches.map(card));
    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      const title = document.createElement('strong');
      title.textContent = query ? 'No matching videos' : section === 'catalog' ? 'Nothing to stream yet' : 'No uploads yet';
      const detail = document.createElement('span');
      detail.textContent = query ? 'Try another search.' : emptyText;
      empty.append(title, detail);
      grid.append(empty);
    }
  }
  $('catalog-count').textContent = `${state.catalog.length} / 10 videos`;
  $('drive-count').textContent = `${state.uploads.length} / 3 videos`;
  const full = state.uploads.length >= 3;
  $('file-input').disabled = full;
  document.querySelector('.upload-button').classList.toggle('disabled', full);
}

async function refresh() {
  const response = await fetch('/api/library');
  if (!response.ok) throw new Error('Library could not be loaded');
  const library = await response.json();
  state.catalog = library.catalog;
  state.uploads = library.uploads;
  render();
}

function setView(view) {
  state.view = view;
  $('stream-view').hidden = view !== 'stream';
  $('drive-view').hidden = view !== 'drive';
  document.querySelectorAll('.nav').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  $('search').value = '';
  render();
}

document.querySelectorAll('.nav').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('.brand').addEventListener('click', () => setView('stream'));
$('hero-play').addEventListener('click', () => $('library-heading').scrollIntoView({ behavior: 'smooth' }));
$('search').addEventListener('input', render);
$('close-player').addEventListener('click', () => $('player-dialog').close());
$('player-dialog').addEventListener('close', restorePlaying);
$('quality').addEventListener('change', () => {
  if (state.playing) switchSource(state.playing, $('player').currentTime);
});
$('player').addEventListener('play', () => {
  if (!state.lastSaved) {
    state.lastSaved = Date.now();
    savePosition(true);
  }
});
$('player').addEventListener('timeupdate', () => {
  if (Date.now() - state.lastSaved > 10000) {
    state.lastSaved = Date.now();
    savePosition();
  }
});
$('player').addEventListener('pause', () => savePosition());
$('player').addEventListener('waiting', () => {
  clearTimeout(state.stallTimer);
  state.stallTimer = setTimeout(() => {
    const item = state.playing;
    if (item?.efficientUrl && $('quality').value === 'auto' && !state.conserveData && !$('player').paused) {
      state.conserveData = true;
      switchSource(item, $('player').currentTime);
      notify('Switched to Data saver after buffering.');
    }
  }, 3000);
});
$('player').addEventListener('playing', () => clearTimeout(state.stallTimer));
$('file-input').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  if (!['mp4', 'webm', 'ogg'].includes(file.name.split('.').pop().toLowerCase()) || file.size < 1 || file.size > 1073741824) {
    notify('Choose an MP4, WebM, or Ogg video smaller than 1 GB.');
    return;
  }
  $('upload-progress').hidden = false;
  $('file-input').disabled = true;
  try {
    const response = await fetch(`/api/uploads?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Upload failed');
    await refresh();
    notify('Video uploaded to My Drive.');
  } catch (error) {
    notify(error.message);
  } finally {
    $('upload-progress').hidden = true;
    $('file-input').disabled = state.uploads.length >= 3;
  }
});
refresh().catch((error) => notify(error.message));
