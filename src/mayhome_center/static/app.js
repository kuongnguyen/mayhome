'use strict';
const clock = document.querySelector('#clock');
function tick() { if (clock) clock.textContent = new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date()) + ' · UTC+07'; }
tick(); setInterval(tick, 1000);
const refresh = document.querySelector('#refresh');
if (refresh) refresh.addEventListener('click', () => location.reload());
let delay = 30000;
let lastStamp = null;
let refreshFailed = false;
function markUnavailable() {
  refreshFailed = true;
  const region = document.querySelector('.freshness');
  if (region) {
    const badge = region.querySelector('.status');
    if (badge) { badge.textContent = 'UNKNOWN'; badge.className = 'status UNKNOWN'; }
    region.querySelector('span:nth-child(2)').textContent = 'Không thể cập nhật dữ liệu — đang thử kết nối lại';
  }
  const tunnel = document.querySelector('#tunnel-status');
  if (tunnel) { tunnel.textContent = 'UNKNOWN'; tunnel.className = 'status UNKNOWN'; }
  document.querySelectorAll('[data-tunnel-status]').forEach(badge => { badge.textContent = 'UNKNOWN'; badge.className = 'status UNKNOWN'; });
  const note = document.querySelector('#tunnel-note');
  if (note) note.textContent = 'Không thể cập nhật kết nối Tunnel. Trạng thái hiện tại chưa xác minh; đang thử kết nối lại.';
}
async function poll() {
  if (!navigator.onLine) { markUnavailable(); setTimeout(poll, 60000); return; }
  try {
    const response = await fetch('/api/v1/overview', { signal: AbortSignal.timeout(5000) });
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('unavailable');
    const body = await response.json();
    // Fetch a complete server-rendered page only when telemetry changes; do not interrupt details/keyboard focus.
    const stamp = body.meta.collected_at + ':' + body.meta.freshness;
    if ((refreshFailed || (lastStamp !== null && lastStamp !== stamp)) && !document.hidden && document.activeElement === document.body && !document.querySelector('details[open]')) location.reload();
    lastStamp = stamp;
    delay = 30000;
  } catch (_) {
    delay = Math.min(delay * 2, 120000);
    markUnavailable();
  }
  setTimeout(poll, document.hidden ? Math.max(delay, 90000) : delay);
}
window.addEventListener('offline', markUnavailable);
setTimeout(poll, 30000);
