/**
 * PharmaSight — Trial Safety Monitor
 * Polls /api/stats every 8 seconds and re-renders all panels.
 */

const POLL_INTERVAL_MS = 8_000;

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt_time(iso) {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleTimeString('en-GB', { hour12: false });
}

function severity_class(sev) {
  return `sev-${sev}`;
}

function confidence_label(c) {
  return c.charAt(0).toUpperCase() + c.slice(1);
}

// ── Clock ────────────────────────────────────────────────────────────────────

function tick_clock() {
  const el = document.getElementById('live-time');
  if (el) el.textContent = new Date().toLocaleTimeString('en-GB', { hour12: false });
}
setInterval(tick_clock, 1000);
tick_clock();

// ── KPI strip ─────────────────────────────────────────────────────────────────

function render_kpis(stats) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('kpi-events-val',  stats.total_events_today.toLocaleString());
  set('kpi-signals-val', stats.active_signals);
  set('kpi-trials-val',  stats.trials_monitored);
  set('kpi-sites-val',   stats.sites_reporting);
}

// ── Signal list ───────────────────────────────────────────────────────────────

function render_signals(signals) {
  const el = document.getElementById('signal-list');
  if (!el) return;
  if (!signals.length) {
    el.innerHTML = '<div class="empty-state">No active signals detected.</div>';
    return;
  }
  el.innerHTML = signals.map(s => {
    const rate_pct = Math.min((s.incidence_rate * 100 / 20) * 100, 100); // cap bar at 20 %
    const sev_parts = Object.entries(s.severity_distribution)
      .map(([k, v]) => `${v} ${k}`).join(' · ');
    return `
      <div class="signal-card confidence-${s.confidence}">
        <div class="signal-top">
          <span class="signal-symptom">${s.symptom_label}</span>
          <span class="confidence-badge ${s.confidence}">${confidence_label(s.confidence)}</span>
        </div>
        <div class="signal-meta">
          <span><strong>${s.trial_id}</strong>Trial</span>
          <span><strong>${s.arm}</strong>Arm</span>
          <span><strong>${(s.incidence_rate * 100).toFixed(1)} %</strong>Rate</span>
          <span><strong>${s.event_count}</strong>Events</span>
        </div>
        <div class="signal-meta" style="margin-top:0.35rem; font-size:0.65rem;">
          <span style="color:var(--slate-400)">${sev_parts}</span>
        </div>
        <div class="signal-rate-bar">
          <div class="signal-rate-fill" style="width:${rate_pct}%"></div>
        </div>
      </div>`;
  }).join('');
}

// ── Bar chart (generic) ───────────────────────────────────────────────────────

const SEVERITY_ORDER   = ['mild', 'moderate', 'severe', 'life_threatening'];
const SEVERITY_COLORS  = {
  mild:             'var(--severity-mild)',
  moderate:         'var(--severity-moderate)',
  severe:           'var(--severity-severe)',
  life_threatening: 'var(--severity-life_threatening)',
};
const SEVERITY_LABELS  = {
  mild: 'Mild', moderate: 'Moderate', severe: 'Severe', life_threatening: 'Life-threat.',
};
const ARM_COLORS = {
  treatment: 'var(--blue-400)',
  placebo:   'var(--slate-400)',
  control:   'var(--green-400)',
};

function render_bar_chart(container_id, data, label_map, color_map, order) {
  const el = document.getElementById(container_id);
  if (!el) return;
  const keys   = order || Object.keys(data);
  const max    = Math.max(...keys.map(k => data[k] || 0), 1);
  el.innerHTML = keys.map(k => {
    const count = data[k] || 0;
    const pct   = (count / max * 100).toFixed(1);
    const label = label_map[k] || k;
    const color = color_map[k] || 'var(--slate-400)';
    return `
      <div class="bar-row">
        <div class="bar-label-row">
          <span>${label}</span>
          <span class="bar-count">${count.toLocaleString()}</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${pct}%; background:${color}"></div>
        </div>
      </div>`;
  }).join('');
}

function render_severity_chart(stats) {
  render_bar_chart(
    'severity-chart',
    stats.events_by_severity,
    SEVERITY_LABELS,
    SEVERITY_COLORS,
    SEVERITY_ORDER,
  );
}

function render_arm_chart(stats) {
  render_bar_chart(
    'arm-chart',
    stats.events_by_arm,
    { treatment: 'Treatment', placebo: 'Placebo', control: 'Control' },
    ARM_COLORS,
    ['treatment', 'placebo', 'control'],
  );
}

// ── Symptom list ──────────────────────────────────────────────────────────────

function render_symptoms(symptoms) {
  const el = document.getElementById('symptom-list');
  if (!el) return;
  const max = symptoms[0]?.count || 1;
  el.innerHTML = symptoms.map(s => {
    const pct = (s.count / max * 100).toFixed(1);
    return `
      <div class="symptom-row">
        <span class="symptom-name">${s.symptom_label}</span>
        <div class="bar-track" style="height:5px">
          <div class="bar-fill" style="width:${pct}%; background:var(--blue-400)"></div>
        </div>
        <span class="symptom-count">${s.count}</span>
      </div>`;
  }).join('');
}

// ── Event feed ────────────────────────────────────────────────────────────────

function render_feed(events) {
  const el     = document.getElementById('event-feed');
  const badge  = document.getElementById('feed-badge');
  if (!el) return;
  if (!events.length) { el.innerHTML = '<div class="empty-state">No events yet.</div>'; return; }
  if (badge) badge.textContent = events.length;
  // show newest first
  const sorted = [...events].reverse();
  el.innerHTML = sorted.map(e => `
    <div class="event-row">
      <span class="ev-time">${fmt_time(e.reported_at)}</span>
      <span class="ev-trial">${e.trial_id}</span>
      <span class="ev-label">${e.symptom_label}</span>
      <span class="sev-pill ${severity_class(e.severity)}">${e.severity.replace('_', '\u00A0')}</span>
    </div>`).join('');
}

// ── Main render ───────────────────────────────────────────────────────────────

async function fetch_and_render() {
  try {
    const res  = await fetch('/api/stats');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stats = await res.json();

    render_kpis(stats);
    render_signals(stats.active_signal_alerts);
    render_severity_chart(stats);
    render_arm_chart(stats);
    render_symptoms(stats.top_symptoms);
    render_feed(stats.recent_events);
  } catch (err) {
    console.warn('Poll failed:', err);
  }
}

// ── Re-run detection button ───────────────────────────────────────────────────

document.getElementById('btn-rerun')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = 'Running…';
  try {
    const res  = await fetch('/api/detect', { method: 'POST' });
    const data = await res.json();
    btn.textContent = `${data.signals_detected} signal${data.signals_detected !== 1 ? 's' : ''} found`;
    await fetch_and_render();
  } catch (err) {
    btn.textContent = 'Error';
    console.error(err);
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Re-run Detection'; }, 2500);
  }
});

// ── Bootstrap ─────────────────────────────────────────────────────────────────

fetch_and_render();
setInterval(fetch_and_render, POLL_INTERVAL_MS);
