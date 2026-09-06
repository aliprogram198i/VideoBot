const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);
const urlInput = $('urlInput');
const searchInput = $('searchInput');
const errorBox = $('error');
const historyKey = 'alibot-miniapp-history-v1';

function initTelegram() {
  if (!tg) return;
  tg.ready();
  tg.expand();
  tg.setHeaderColor('#080c18');
  tg.setBackgroundColor('#080c18');
  const user = tg.initDataUnsafe?.user;
  if (user) {
    $('greeting').textContent = `أهلاً ${user.first_name || 'بك'}`;
    $('avatar').textContent = (user.first_name || 'A').slice(0, 1).toUpperCase();
  }
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function clearError() { errorBox.hidden = true; }

function isValidUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch (_) { return false; }
}

function loadHistory() {
  let items = [];
  try { items = JSON.parse(localStorage.getItem(historyKey) || '[]'); } catch (_) {}
  const root = $('history');
  root.innerHTML = '';
  $('emptyHistory').hidden = items.length > 0;
  items.slice(0, 8).forEach(item => {
    const row = document.createElement('div');
    row.className = 'history-item';
    row.innerHTML = `<div class="history-icon">${item.kind === 'search' ? '🔎' : '🔗'}</div><div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.value)}</span></div>`;
    row.addEventListener('click', () => {
      if (item.kind === 'search') searchInput.value = item.value;
      else urlInput.value = item.value;
      window.scrollTo({top: 0, behavior: 'smooth'});
    });
    root.appendChild(row);
  });
}

function saveHistory(kind, value, label) {
  let items = [];
  try { items = JSON.parse(localStorage.getItem(historyKey) || '[]'); } catch (_) {}
  items = [{kind, value, label}, ...items.filter(x => !(x.kind === kind && x.value === value))].slice(0, 12);
  localStorage.setItem(historyKey, JSON.stringify(items));
  loadHistory();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
}

function send(payload) {
  if (!tg || typeof tg.sendData !== 'function') {
    showError('افتح هذه الصفحة من داخل Mini App في Telegram.');
    return false;
  }
  tg.sendData(JSON.stringify(payload));
  return true;
}

$('downloadBtn').addEventListener('click', () => {
  clearError();
  const value = urlInput.value.trim();
  if (!isValidUrl(value)) {
    showError('أدخل رابطاً صحيحاً يبدأ بـ http:// أو https://');
    return;
  }
  saveHistory('url', value, 'طلب تحميل');
  send({version: 1, action: 'download', url: value});
});

$('searchBtn').addEventListener('click', () => {
  clearError();
  const value = searchInput.value.trim();
  if (value.length < 2 || value.length > 200) {
    showError('اكتب عبارة بحث بين حرفين و200 حرف.');
    return;
  }
  saveHistory('search', value, 'بحث ذكي');
  send({version: 1, action: 'search', query: value});
});

$('pasteBtn').addEventListener('click', async () => {
  clearError();
  try {
    const value = await navigator.clipboard.readText();
    if (value) urlInput.value = value.trim();
  } catch (_) {
    showError('تعذر الوصول إلى الحافظة. الصق الرابط يدوياً.');
  }
});

$('clearHistory').addEventListener('click', () => {
  localStorage.removeItem(historyKey);
  loadHistory();
});

initTelegram();
loadHistory();
