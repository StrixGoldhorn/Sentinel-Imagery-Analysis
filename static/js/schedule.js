/**
 * Planned Satellite Scrapes and Pass Schedule Dashboard
 */

let allEvents = [];
let filteredEvents = [];
let aoisMap = {};
let countdownInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    loadAoisDropdown();
    loadSchedule();
    loadSchedulerStatus();
    loadPostPassJobs();
    loadLogs();

    // Live countdown updater every second
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(updateLiveCountdowns, 1000);

    // Auto-refresh schedule every 30 seconds
    setInterval(() => {
        loadSchedulerStatus();
        loadPostPassJobs();
        loadLogs();
    }, 30000);
});

async function loadAoisDropdown() {
    try {
        const response = await fetch('/api/aoi');
        const aois = await response.json();
        const select = document.getElementById('filterAoiSelect');
        if (!select) return;

        select.innerHTML = '<option value="">All Areas of Interest</option>';
        aois.forEach(aoi => {
            aoisMap[aoi.id] = aoi;
            const opt = document.createElement('option');
            opt.value = aoi.id;
            opt.textContent = `${aoi.name || `AOI #${aoi.id}`} (${aoi.auto_capture_enabled ? 'Auto' : 'Manual'})`;
            select.appendChild(opt);
        });
    } catch (err) {
        console.error('Failed to load AOIs for filter dropdown:', err);
    }
}

async function loadSchedule() {
    const loadingState = document.getElementById('loadingState');
    const scheduleList = document.getElementById('scheduleList');
    const emptyState = document.getElementById('emptyState');
    const daysAhead = document.getElementById('filterDaysAhead') ? document.getElementById('filterDaysAhead').value : '14';

    if (loadingState) loadingState.style.display = 'block';
    if (scheduleList) scheduleList.style.display = 'none';
    if (emptyState) emptyState.style.display = 'none';

    try {
        const response = await fetch(`/api/schedule/upcoming?days_ahead=${daysAhead}`);
        const data = await response.json();

        if (loadingState) loadingState.style.display = 'none';

        if (data.status !== 'success') {
            showToast(data.error || 'Failed to load upcoming scrapes', 'error');
            if (emptyState) emptyState.style.display = 'block';
            return;
        }

        allEvents = data.events || [];
        updateMetrics(data.metrics || {});
        applyFilters();
    } catch (err) {
        console.error('Error fetching planned schedule:', err);
        if (loadingState) loadingState.style.display = 'none';
        if (emptyState) emptyState.style.display = 'block';
        showToast('Connection error while fetching scrape schedule', 'error');
    }
}

function updateMetrics(metrics) {
    const el24h = document.getElementById('metricUpcoming24h');
    const el7d = document.getElementById('metricUpcoming7d');
    const elActive = document.getElementById('metricActiveFlypasts');
    const elAuto = document.getElementById('metricAutoCaptureAois');
    const iconActive = document.getElementById('metricActiveFlypastsIcon');

    if (el24h) el24h.textContent = metrics.upcoming_24h_count ?? '-';
    if (el7d) el7d.textContent = metrics.upcoming_7d_count ?? '-';
    if (elActive) elActive.textContent = metrics.active_flypasts_count ?? '0';
    if (elAuto) elAuto.textContent = `${metrics.auto_capture_count ?? 0} / ${metrics.total_aois ?? 0}`;

    if (iconActive) {
        if ((metrics.active_flypasts_count || 0) > 0) {
            iconActive.className = 'metric-icon icon-pulse';
        } else {
            iconActive.className = 'metric-icon icon-amber';
        }
    }
}

function applyFilters() {
    const aoiSelect = document.getElementById('filterAoiSelect');
    const autoOnly = document.getElementById('filterAutoOnly') ? document.getElementById('filterAutoOnly').checked : false;
    const searchInput = document.getElementById('filterSearchInput');
    const sourceSelect = document.getElementById('filterSourceSelect');

    const selectedAoiId = aoiSelect && aoiSelect.value ? parseInt(aoiSelect.value, 10) : null;
    const selectedSource = sourceSelect ? sourceSelect.value : '';
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';

    filteredEvents = allEvents.filter(event => {
        if (selectedAoiId !== null && event.aoi_id !== selectedAoiId) {
            return false;
        }
        if (autoOnly && !event.auto_capture_enabled) {
            return false;
        }
        if (selectedSource) {
            const contrib = event.contribution || (event.source === 'COMBINED' ? 'both' : (event.source === 'HISTORICAL_MISSION' ? 'historical' : 'n2yo'));
            if (contrib !== selectedSource) {
                return false;
            }
        }
        if (query) {
            const aoiMatch = (event.aoi_name || '').toLowerCase().includes(query);
            const satMatch = (event.satellite || '').toLowerCase().includes(query);
            const statusMatch = (event.status || '').toLowerCase().includes(query);
            const contribMatch = (event.contribution_label || '').toLowerCase().includes(query) || (event.contribution_detail || '').toLowerCase().includes(query);
            if (!aoiMatch && !satMatch && !statusMatch && !contribMatch) {
                return false;
            }
        }
        return true;
    });

    renderScheduleList(filteredEvents);
}

function renderScheduleList(events) {
    const scheduleList = document.getElementById('scheduleList');
    const emptyState = document.getElementById('emptyState');

    if (!scheduleList || !emptyState) return;

    if (!events || events.length === 0) {
        scheduleList.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    scheduleList.style.display = 'flex';
    scheduleList.innerHTML = '';

    events.forEach((event, index) => {
        const card = createScheduleCard(event, index);
        scheduleList.appendChild(card);
    });
}

function createScheduleCard(event, index) {
    const card = document.createElement('div');
    const passDt = new Date(event.pass_time);
    const winStart = new Date(event.window_start);
    const winEnd = new Date(event.window_end);

    const isLive = event.is_active;
    const statusClass = isLive ? 'status-active' : (event.auto_capture_enabled ? 'status-scheduled' : 'status-predicted');
    card.className = `schedule-card ${statusClass}`;
    card.id = `schedule-card-${event.aoi_id}-${index}`;
    card.dataset.passTime = event.pass_time;
    card.dataset.windowStart = event.window_start;
    card.dataset.windowEnd = event.window_end;

    // Badges
    const sat = event.satellite || 'Sentinel-1';
    const dir = event.orbit_direction;
    const dirClass = dir === 'ASCENDING' ? 'badge-ascending' : (dir === 'DESCENDING' ? 'badge-descending' : 'badge-secondary');
    const dirArrow = dir === 'ASCENDING' ? '⬆ Ascending' : (dir === 'DESCENDING' ? '⬇ Descending' : (dir || ''));
    const track = event.relative_orbit ? `Track #${event.relative_orbit}` : '';
    const conf = event.confidence_score ? `${Math.round(event.confidence_score * 100)}% Conf` : '';
    const elev = event.max_elevation ? `${Math.round(event.max_elevation)}° Elev` : '';
    const contrib = event.contribution || (event.source === 'COMBINED' ? 'both' : (event.source === 'HISTORICAL_MISSION' ? 'historical' : 'n2yo'));

    let contribBadge = '';
    if (contrib === 'both' || event.source === 'COMBINED') {
        contribBadge = `<span class="badge badge-contrib-both" title="${escapeHtml(event.contribution_detail || 'Cross-validated: N2YO tracking confirmed by Sentinel-1 repeat cycle')}">✨ Both (N2YO + Historical)</span>`;
    } else if (contrib === 'historical' || event.source === 'HISTORICAL_MISSION') {
        contribBadge = `<span class="badge badge-contrib-hist" title="${escapeHtml(event.contribution_detail || 'Historical Sentinel-1 Repeat Cycle')}">🔁 Historical Cycle</span>`;
    } else {
        contribBadge = `<span class="badge badge-contrib-n2yo" title="${escapeHtml(event.contribution_detail || 'Astronomical pass tracking via N2YO')}">🛰️ N2YO Tracking</span>`;
    }

    let statusBadge = '';
    if (isLive) {
        statusBadge = '<span class="badge badge-live">🔴 FLYPAST ACTIVE NOW</span>';
    } else if (event.auto_capture_enabled) {
        statusBadge = '<span class="badge badge-scheduled">⚡ Auto-Capture Enabled</span>';
    } else {
        statusBadge = '<span class="badge badge-predicted">Manual Scrape Only</span>';
    }

    const zuluPass = passDt.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
    const localPass = passDt.toLocaleString();
    const zuluWindow = `${winStart.toISOString().substring(11, 16)} - ${winEnd.toISOString().substring(11, 16)} UTC`;
    const bboxFormatted = (event.bbox || []).map(n => Number(n).toFixed(2)).join(', ');

    const countdownText = formatCountdown(event.pass_time, isLive);
    const isAutoChecked = event.auto_capture_enabled ? 'checked' : '';
    const contribLabel = event.contribution_label || (contrib === 'both' ? 'Both (N2YO + Historical Repeat Cycle)' : (contrib === 'historical' ? 'Historical Mission Repeat Cycle' : 'N2YO Orbit Tracking'));
    const contribDetail = event.contribution_detail || '';

    card.innerHTML = `
        <div class="card-top-row">
            <div class="event-timing-group">
                <div class="event-countdown ${isLive ? 'countdown-live' : ''}" id="countdown-${event.aoi_id}-${index}">
                    ${countdownText}
                </div>
                <div class="event-timestamps">
                    <span><strong>Pass Time:</strong> ${localPass} (${zuluPass})</span>
                </div>
            </div>
            <div class="event-badges">
                ${statusBadge}
                ${contribBadge}
                <span class="badge badge-track">${escapeHtml(sat)}</span>
                ${dir ? `<span class="badge ${dirClass}">${escapeHtml(dirArrow)}</span>` : ''}
                ${track ? `<span class="badge badge-secondary">${escapeHtml(track)}</span>` : ''}
                ${conf ? `<span class="badge badge-conf">${escapeHtml(conf)}</span>` : ''}
                ${elev ? `<span class="badge badge-elevation">${escapeHtml(elev)}</span>` : ''}
            </div>
        </div>

        <div class="card-details-row">
            <div class="aoi-info-block">
                <div class="aoi-name-tag">
                    ${escapeHtml(event.aoi_name || `AOI #${event.aoi_id}`)}
                </div>
                <span class="scrape-window-tag" title="AIS Scrape Window [UTC]">
                    Scrape Window (±5m): <strong>${zuluWindow}</strong>
                </span>
                <span style="font-size: 0.82rem; color: #6c757d; font-family: monospace;">
                    [${bboxFormatted}]
                </span>
                <div class="contrib-detail-text" title="${escapeHtml(contribDetail || contribLabel)}">
                    <strong>Source:</strong> ${escapeHtml(contribLabel)}
                    ${contribDetail ? `&bull; <span>${escapeHtml(contribDetail)}</span>` : ''}
                </div>
                <label style="cursor: pointer; display: inline-flex; align-items: center; gap: 6px; font-size: 0.82rem; color: #495057; margin: 0;">
                    <input type="checkbox" ${isAutoChecked} onchange="toggleCardAutoCapture(${event.aoi_id}, this.checked)">
                    <span>Auto-Capture</span>
                </label>
            </div>

            <div class="card-actions">
                <button class="btn btn-outline-primary btn-sm" onclick="goToMap([${event.bbox.join(',')}], ${event.aoi_id})">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon>
                        <line x1="8" y1="2" x2="8" y2="18"></line>
                        <line x1="16" y1="6" x2="16" y2="22"></line>
                    </svg>
                    Map
                </button>
                <button class="btn btn-outline-secondary btn-sm" onclick="scrapePassAIS(${event.aoi_id}, '${event.pass_time}')" title="Scrape AIS data for [-5m, +5m] window">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path>
                    </svg>
                    Scrape Window (±5m)
                </button>
                <button class="btn btn-warning btn-sm" onclick="forceScanAOIAIS(${event.aoi_id})" title="Force immediate live AIS scrape">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path>
                    </svg>
                    Force AIS Scan
                </button>
            </div>
        </div>
    `;

    return card;
}

function updateLiveCountdowns() {
    const cards = document.querySelectorAll('.schedule-card');
    const now = new Date();

    cards.forEach(card => {
        const passTimeStr = card.dataset.passTime;
        const winStartStr = card.dataset.windowStart;
        const winEndStr = card.dataset.windowEnd;
        if (!passTimeStr) return;

        const passTime = new Date(passTimeStr);
        const winStart = new Date(winStartStr);
        const winEnd = new Date(winEndStr);

        const isLive = (now >= winStart && now <= winEnd);
        const countdownEl = card.querySelector('.event-countdown');

        if (countdownEl) {
            countdownEl.textContent = formatCountdown(passTimeStr, isLive);
            if (isLive) {
                countdownEl.classList.add('countdown-live');
                card.classList.add('status-active');
            } else {
                countdownEl.classList.remove('countdown-live');
                card.classList.remove('status-active');
            }
        }
    });
}

function formatCountdown(passTimeStr, isLive) {
    if (isLive) {
        return '🔴 LIVE FLYPAST NOW';
    }

    const now = new Date();
    const target = new Date(passTimeStr);
    const diffSec = Math.floor((target - now) / 1000);

    if (diffSec < -300) {
        return 'Pass Ended';
    }

    if (diffSec < 0) {
        return '🔴 FLYPAST IN WINDOW';
    }

    const days = Math.floor(diffSec / 86400);
    const hours = Math.floor((diffSec % 86400) / 3600);
    const mins = Math.floor((diffSec % 3600) / 60);
    const secs = diffSec % 60;

    if (days > 0) {
        return `⏳ In ${days}d ${hours}h ${mins}m`;
    }
    if (hours > 0) {
        return `⏳ In ${hours}h ${mins}m ${secs}s`;
    }
    return `⚡ In ${mins}m ${secs}s`;
}

async function loadSchedulerStatus() {
    try {
        const response = await fetch('/api/schedule/status');
        const data = await response.json();
        if (data.status !== 'success') return;

        const s = data.scheduler || {};
        const badge = document.getElementById('schedulerBadge');
        const text = document.getElementById('schedulerStatusText');
        const pollRate = document.getElementById('schedulerPollRate');
        const lastRun = document.getElementById('schedulerLastRun');

        if (s.is_running && s.thread_alive) {
            if (badge) badge.className = 'scheduler-badge scheduler-running';
            if (text) text.textContent = 'Scheduler Daemon Active';
        } else {
            if (badge) badge.className = 'scheduler-badge scheduler-stopped';
            if (text) text.textContent = s.api_key_configured ? 'Scheduler Paused' : 'Scheduler Disabled (No API Key)';
        }

        if (pollRate) pollRate.textContent = `Poll interval: ${Math.round(s.poll_interval_seconds || 60)}s`;

        if (lastRun) {
            if (s.last_run_at) {
                const dt = new Date(s.last_run_at);
                lastRun.textContent = `Last check: ${dt.toLocaleTimeString()}`;
            } else {
                lastRun.textContent = 'Last check: Starting...';
            }
        }
    } catch (err) {
        console.error('Error fetching scheduler status:', err);
    }
}

async function forceSchedulerCheck() {
    const btn = document.getElementById('btnForcePoll');
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner"></span> Checking...';
    }

    try {
        showToast('Triggering scheduler pass check cycle...', 'info');
        const response = await fetch('/api/schedule/trigger_poll', { method: 'POST' });
        const data = await response.json();

        if (response.ok && data.status === 'success') {
            const count = (data.results && data.results.length) || 0;
            showToast(`Scheduler check complete across ${count} AOIs!`, 'success');
            loadSchedulerStatus();
            loadSchedule();
            loadLogs();
        } else {
            showToast('Scheduler check error: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        console.error('Error triggering scheduler poll:', err);
        showToast('Failed to trigger scheduler poll', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

async function loadLogs() {
    try {
        const response = await fetch('/api/schedule/logs?limit=25');
        const data = await response.json();
        const tbody = document.getElementById('logsTableBody');
        const badge = document.getElementById('logsCountBadge');
        if (!tbody) return;

        const logs = data.logs || [];
        if (badge) badge.textContent = `${logs.length} logs`;

        if (logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; color: #6c757d; padding: 20px;">
                        No AIS scraper execution logs recorded yet.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = logs.map(log => {
            const dt = log.timestamp ? new Date(log.timestamp.replace(' ', 'T') + 'Z') : new Date();
            const timeStr = dt.toLocaleString();
            const statusBadge = log.status === 'SUCCESS' 
                ? '<span class="badge badge-success">SUCCESS</span>' 
                : '<span class="badge badge-live">FAILED</span>';

            return `
                <tr>
                    <td><strong>${timeStr}</strong></td>
                    <td><code>${escapeHtml(log.plugin_name || 'AIS Ingestion')}</code></td>
                    <td>${statusBadge}</td>
                    <td><strong>${log.records_inserted || 0}</strong> records</td>
                    <td style="color: ${log.error_message ? '#dc3545' : '#6c757d'};">
                        ${escapeHtml(log.error_message || 'OK')}
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error('Failed to load scraper logs:', err);
    }
}

function toggleLogsPanel() {
    const content = document.getElementById('logsContent');
    const icon = document.getElementById('logsToggleIcon');
    if (!content) return;

    if (content.style.display === 'none') {
        content.style.display = 'block';
        if (icon) icon.textContent = '▾';
    } else {
        content.style.display = 'none';
        if (icon) icon.textContent = '▸';
    }
}

async function toggleCardAutoCapture(aoiId, enabled) {
    try {
        const res = await fetch(`/api/aoi/${aoiId}/auto_capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        if (res.ok) {
            showToast(`Auto-capture ${enabled ? 'enabled' : 'disabled'} for AOI #${aoiId}`, 'success');
            // Update local event states
            allEvents.forEach(e => {
                if (e.aoi_id === aoiId) {
                    e.auto_capture_enabled = enabled;
                    if (!e.is_active) {
                        e.status = enabled ? 'SCHEDULED' : 'PREDICTED_ONLY';
                    }
                }
            });
            applyFilters();
        } else {
            showToast('Failed to update auto-capture setting', 'error');
        }
    } catch (err) {
        console.error('Error updating auto-capture:', err);
        showToast('Connection error updating auto-capture', 'error');
    }
}

async function scrapePassAIS(aoiId, passTime) {
    try {
        showToast('Initiating AIS pass scrape for window...', 'info');
        const res = await fetch(`/api/aoi/${aoiId}/scrape_ais`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pass_time: passTime })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            const total = (data.results && data.results.total_inserted) || 0;
            showToast(`Scraped and ingested ${total} AIS records!`, 'success');
            loadLogs();
        } else {
            showToast('AIS Scrape failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        console.error('Error scraping AIS:', err);
        showToast('Failed to trigger AIS scrape', 'error');
    }
}

async function forceScanAOIAIS(aoiId) {
    let startToast = null;
    try {
        startToast = showToast('Initiating immediate live AIS vessel scan...', 'info', {
            autoClose: false,
            title: '⚡ Force AIS Scan Started'
        });
        const res = await fetch(`/api/aoi/${aoiId}/force_ais_scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true })
        });
        const data = await res.json();

        if (startToast && typeof startToast.close === 'function') {
            startToast.close();
        }

        if (res.ok && data.status === 'success') {
            const total = (data.results && data.results.total_inserted) || 0;
            showToast(`Force AIS scan complete: ${total} vessel records ingested!`, 'success', {
                autoClose: false,
                title: '✅ Force Scan Results'
            });
            loadLogs();
        } else {
            showToast('Force AIS scan failed: ' + (data.error || 'Unknown error'), 'error', {
                autoClose: false,
                title: '❌ Force Scan Failed'
            });
        }
    } catch (err) {
        console.error('Error in force AIS scan:', err);
        if (startToast && typeof startToast.close === 'function') {
            startToast.close();
        }
        showToast('Connection error during force AIS scan: ' + (err.message || 'Server unreachable'), 'error', {
            autoClose: false,
            title: '❌ Force Scan Error'
        });
    }
}

function goToMap(bbox, aoiId) {
    if (!bbox || bbox.length !== 4) return;
    window.location.href = `/?bbox=${bbox.join(',')}&aoi_id=${aoiId}`;
}

function escapeHtml(text) {
    if (typeof text !== 'string') return String(text ?? '');
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info', options = {}) {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.cssText = 'position: fixed; bottom: 24px; right: 24px; z-index: 99999; display: flex; flex-direction: column; gap: 12px; pointer-events: none; max-width: 420px; width: calc(100% - 48px);';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColors = {
        success: '#16a34a',
        error: '#dc2626',
        info: '#0284c7',
        warning: '#d97706'
    };
    const borders = {
        success: '#bbf7d0',
        error: '#fecaca',
        info: '#bae6fd',
        warning: '#fef3c7'
    };

    const autoClose = options.autoClose !== undefined ? options.autoClose : true;
    const title = options.title || '';

    toast.style.cssText = `
        background: ${bgColors[type] || '#1e293b'};
        color: #ffffff;
        padding: 12px 16px;
        border-radius: 8px;
        font-size: 0.92rem;
        box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4), 0 8px 10px -6px rgba(0,0,0,0.3);
        opacity: 0;
        transform: translateY(10px);
        transition: all 0.25s ease-out;
        pointer-events: auto;
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        border-left: 5px solid ${borders[type] || 'transparent'};
    `;

    toast.innerHTML = `
        <div style="flex: 1; min-width: 0;">
            ${title ? `<div style="font-weight: 700; margin-bottom: 4px; font-size: 0.95rem;">${escapeHtml(title)}</div>` : ''}
            <div style="word-break: break-word; font-size: 0.88rem;">${escapeHtml(message)}</div>
            ${!autoClose ? '<div style="margin-top: 8px;"><button type="button" class="toast-ack-btn" style="background: rgba(255,255,255,0.22); border: 1px solid rgba(255,255,255,0.45); color: #fff; padding: 3px 10px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; cursor: pointer;">Dismiss</button></div>' : ''}
        </div>
        <button type="button" class="toast-close-btn" style="background: transparent; border: none; color: rgba(255,255,255,0.85); font-size: 1.3rem; line-height: 1; cursor: pointer; padding: 2px 6px; border-radius: 4px; margin-top: -3px; margin-right: -4px;">&times;</button>
    `;

    const closeToast = () => {
        if (!toast.parentNode) return;
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => { if (toast.parentNode) toast.remove(); }, 250);
    };

    const closeBtn = toast.querySelector('.toast-close-btn');
    if (closeBtn) closeBtn.onclick = closeToast;
    const ackBtn = toast.querySelector('.toast-ack-btn');
    if (ackBtn) ackBtn.onclick = closeToast;

    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });

    let timer = null;
    if (autoClose) {
        timer = setTimeout(() => {
            closeToast();
        }, options.duration || 4000);
    }

    return {
        element: toast,
        close: () => {
            if (timer) clearTimeout(timer);
            closeToast();
        }
    };
}

async function loadPostPassJobs() {
    const tbody = document.getElementById('postPassTableBody');
    const badge = document.getElementById('postPassCountBadge');
    const statusFilter = document.getElementById('filterPostPassStatus') ? document.getElementById('filterPostPassStatus').value : '';
    if (!tbody) return;

    try {
        let url = '/api/schedule/post_pass_jobs?limit=50';
        if (statusFilter) {
            url += `&status=${encodeURIComponent(statusFilter)}`;
        }
        const response = await fetch(url);
        const data = await response.json();

        if (data.status !== 'success') {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #dc3545; padding: 20px;">Failed to load post-pass jobs: ${escapeHtml(data.error || 'Unknown error')}</td></tr>`;
            return;
        }

        const jobs = data.jobs || [];
        const activeCount = jobs.filter(j => ['POLLING_CATALOG', 'INGESTING', 'PENDING_PASS'].includes(j.status)).length;
        if (badge) {
            badge.textContent = `${activeCount} active / ${jobs.length} total`;
            badge.className = activeCount > 0 ? 'badge badge-scheduled' : 'badge badge-secondary';
        }

        if (jobs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align: center; color: #64748b; padding: 30px;">
                        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom: 8px; opacity: 0.6;">
                            <polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline>
                            <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>
                        </svg>
                        <div>No post-pass ingestion jobs matching the filter.</div>
                        <div style="font-size: 0.8rem; margin-top: 4px; color: #94a3b8;">Jobs are registered automatically whenever an auto-capture AOI completes a flypast.</div>
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = jobs.map(job => {
            const passDt = job.pass_time ? new Date(job.pass_time) : null;
            const passLocal = passDt ? passDt.toLocaleString() : 'Unknown';
            const passZulu = passDt ? (passDt.toISOString().substring(11, 16) + ' UTC') : '';

            let statusBadge = '';
            if (job.status === 'POLLING_CATALOG') {
                statusBadge = `<span class="badge badge-warning" style="background: #fef3c7; color: #92400e; border: 1px solid #fde68a;">⏳ Polling Copernicus (Attempt #${job.attempts})</span>`;
            } else if (job.status === 'INGESTING') {
                statusBadge = `<span class="badge badge-scheduled" style="background: #e0e7ff; color: #3730a3; border: 1px solid #c7d2fe;">📥 Ingesting &amp; Stitching</span>`;
            } else if (job.status === 'COMPLETED') {
                statusBadge = `<span class="badge badge-success" style="background: #dcfce7; color: #166534; border: 1px solid #bbf7d0;">✅ Ingested</span>`;
            } else if (job.status === 'TIMED_OUT') {
                statusBadge = `<span class="badge badge-secondary" style="background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;">⏱ Timed Out</span>`;
            } else {
                statusBadge = `<span class="badge badge-live" style="background: #fee2e2; color: #991b1b; border: 1px solid #fecaca;">❌ ${escapeHtml(job.status)}</span>`;
            }

            // Check / Next poll info
            let nextPollText = '-';
            if (job.status === 'POLLING_CATALOG') {
                if (job.next_poll_at) {
                    const nextDt = new Date(job.next_poll_at);
                    const diffSec = Math.round((nextDt - new Date()) / 1000);
                    if (diffSec > 0) {
                        nextPollText = `Next in ${diffSec}s (${nextDt.toLocaleTimeString()})`;
                    } else {
                        nextPollText = `Due now (Polled #${job.attempts})`;
                    }
                } else {
                    nextPollText = `Due now (Polled #${job.attempts})`;
                }
            } else if (job.status === 'COMPLETED' && job.completed_at) {
                nextPollText = `Completed at ${new Date(job.completed_at).toLocaleTimeString()}`;
            } else if (job.error_message) {
                nextPollText = `<span style="color: #dc3545; font-size: 0.8rem;" title="${escapeHtml(job.error_message)}">${escapeHtml(job.error_message.substring(0, 30))}...</span>`;
            }

            // Generated scan link
            let scanLink = '<span style="color: #94a3b8;">-</span>';
            if (job.scan_folder) {
                scanLink = `<a href="/scan/${encodeURIComponent(job.scan_folder)}" class="btn btn-outline-primary btn-sm" style="font-size: 0.8rem; padding: 3px 8px; text-decoration: none;">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                        <circle cx="12" cy="12" r="3"></circle>
                    </svg>
                    ${escapeHtml(job.scan_folder)}
                </a>`;
            }

            return `
                <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 12px 14px;">
                        <strong style="color: #1e293b;">${escapeHtml(job.aoi_name || `AOI #${job.aoi_id}`)}</strong>
                        <div style="font-size: 0.8rem; color: #64748b; margin-top: 2px;">
                            ${passLocal} <span style="font-family: monospace;">(${passZulu})</span>
                        </div>
                    </td>
                    <td style="padding: 12px 14px;">
                        <span class="badge badge-track" style="font-size: 0.8rem;">${escapeHtml(job.satellite || 'Sentinel-1')}</span>
                        ${job.orbit_direction ? `<span class="badge badge-secondary" style="font-size: 0.75rem; margin-left: 4px;">${escapeHtml(job.orbit_direction)}</span>` : ''}
                    </td>
                    <td style="padding: 12px 14px;">
                        ${statusBadge}
                    </td>
                    <td style="padding: 12px 14px; font-size: 0.85rem; color: #475569;">
                        ${nextPollText}
                    </td>
                    <td style="padding: 12px 14px;">
                        ${scanLink}
                    </td>
                    <td style="padding: 12px 14px; text-align: right;">
                        <div style="display: inline-flex; gap: 4px;">
                            ${job.status === 'POLLING_CATALOG' ? `
                                <button class="btn btn-outline-primary btn-sm" style="padding: 3px 8px; font-size: 0.8rem;" onclick="pollPostPassJob(${job.id})" title="Check Copernicus catalog now">
                                    Check Now
                                </button>
                            ` : ''}
                            ${['FAILED', 'TIMED_OUT', 'COMPLETED'].includes(job.status) ? `
                                <button class="btn btn-outline-secondary btn-sm" style="padding: 3px 8px; font-size: 0.8rem;" onclick="retryPostPassJob(${job.id})" title="Retry catalog polling">
                                    Retry
                                </button>
                            ` : ''}
                            <button class="btn btn-outline-danger btn-sm" style="padding: 3px 8px; font-size: 0.8rem;" onclick="deletePostPassJob(${job.id})" title="Delete job">
                                &times;
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error('Failed to load post-pass jobs:', err);
    }
}

async function pollPostPassJob(jobId) {
    try {
        showToast(`Checking Copernicus catalog for job #${jobId}...`, 'info');
        const res = await fetch(`/api/schedule/post_pass_jobs/${jobId}/poll`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            showToast(`Catalog check completed for job #${jobId}`, 'success');
            loadPostPassJobs();
        } else {
            showToast(`Catalog check error: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        console.error('Error polling post-pass job:', err);
        showToast('Connection error checking Copernicus catalog', 'error');
    }
}

async function retryPostPassJob(jobId) {
    try {
        showToast(`Resetting job #${jobId} to polling...`, 'info');
        const res = await fetch(`/api/schedule/post_pass_jobs/${jobId}/retry`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            showToast(`Job #${jobId} reset to polling!`, 'success');
            loadPostPassJobs();
        } else {
            showToast(`Failed to retry job: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        console.error('Error retrying job:', err);
        showToast('Connection error retrying job', 'error');
    }
}

async function deletePostPassJob(jobId) {
    if (!confirm(`Delete post-pass ingestion job #${jobId}?`)) return;
    try {
        const res = await fetch(`/api/schedule/post_pass_jobs/${jobId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            showToast(`Job #${jobId} deleted`, 'info');
            loadPostPassJobs();
        } else {
            showToast(`Failed to delete job: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        console.error('Error deleting job:', err);
        showToast('Connection error deleting job', 'error');
    }
}

