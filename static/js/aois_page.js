/**
 * Areas of Interest (AOIs) dashboard management and flypast forecasts.
 */

const aoiPredictionsCache = {};

document.addEventListener('DOMContentLoaded', () => {
    loadAOIs();
});

async function loadAOIs() {
    const grid = document.getElementById('aoiGrid');
    const emptyState = document.getElementById('emptyState');
    const loadingState = document.getElementById('loadingState');

    if (loadingState) loadingState.style.display = 'block';
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.style.display = 'none';

    try {
        const response = await fetch('/api/aoi');
        const aois = await response.json();

        if (loadingState) loadingState.style.display = 'none';

        if (!aois || aois.length === 0) {
            if (emptyState) emptyState.style.display = 'block';
            return;
        }

        aois.forEach(aoi => {
            const card = createAoiCard(aoi);
            grid.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load AOIs:', error);
        if (loadingState) loadingState.style.display = 'none';
        showToast('Error loading Areas of Interest', 'error');
    }
}

function createAoiCard(aoi) {
    const card = document.createElement('div');
    card.className = 'aoi-card';
    card.id = `aoi-card-${aoi.id}`;

    const safeName = escapeHtml(aoi.name || `AOI #${aoi.id}`);
    const bboxFormatted = aoi.bbox.map(n => Number(n).toFixed(2)).join(', ');
    const isAuto = aoi.auto_capture_enabled ? 'checked' : '';

    let nextScanBadge = '<span class="badge badge-secondary">Not predicted</span>';
    if (aoi.next_scan) {
        const d = new Date(aoi.next_scan);
        nextScanBadge = `<span class="badge badge-success" title="Next Pass: ${d.toISOString()}">Next Pass: ${d.toLocaleString()}</span>`;
    }

    card.innerHTML = `
        <div class="aoi-card-header">
            <div class="aoi-title-group">
                <h3 class="aoi-title" title="${safeName}">${safeName}</h3>
                <span class="aoi-id-badge">ID #${aoi.id}</span>
            </div>
            <div>${nextScanBadge}</div>
        </div>
        <div class="aoi-card-body">
            <div class="aoi-summary-row">
                <div class="aoi-meta-group">
                    <div class="aoi-meta-item">
                        <span class="aoi-info-label">Bounding Box:</span>
                        <span class="coords-box">[${bboxFormatted}]</span>
                    </div>
                    <div class="aoi-meta-item">
                        <label for="auto-cap-${aoi.id}" style="cursor: pointer; display: flex; align-items: center; gap: 8px; font-size: 0.88rem; color: #495057; margin: 0;">
                            <input type="checkbox" id="auto-cap-${aoi.id}" ${isAuto} onchange="toggleAutoCapture(${aoi.id}, this.checked)">
                            <span>Auto-Capture & Scrape on Pass</span>
                        </label>
                    </div>
                </div>

                <div class="aoi-actions">
                    <button class="btn btn-primary" onclick="goToMap([${aoi.bbox.join(',')}], ${aoi.id})">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon>
                            <line x1="8" y1="2" x2="8" y2="18"></line>
                            <line x1="16" y1="6" x2="16" y2="22"></line>
                        </svg>
                        View on Map
                    </button>
                    <button class="btn btn-outline-primary" id="btn-toggle-${aoi.id}" onclick="toggleFlypasts(${aoi.id})">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        Next Flypasts ▾
                    </button>
                    <button class="btn btn-warning" id="btn-force-scan-${aoi.id}" onclick="forceScanAOIAIS(${aoi.id})" title="Force immediate AIS vessel scan regardless of satellite flypass timing">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path>
                        </svg>
                        Force AIS Scan Now
                    </button>
                </div>
            </div>

            <div class="flypast-dropdown" id="flypasts-panel-${aoi.id}" style="display: none;">
                <div id="flypasts-content-${aoi.id}">
                    <div style="text-align: center; padding: 12px; color: #6c757d;">
                        <span class="loading-spinner"></span> Loading flypast predictions...
                    </div>
                </div>
            </div>
        </div>
    `;

    return card;
}


function goToMap(bbox, aoiId) {
    if (!bbox || bbox.length !== 4) return;
    const url = `/?bbox=${bbox.join(',')}&aoi_id=${aoiId}`;
    window.location.href = url;
}

async function toggleFlypasts(aoiId) {
    const panel = document.getElementById(`flypasts-panel-${aoiId}`);
    const btn = document.getElementById(`btn-toggle-${aoiId}`);
    if (!panel || !btn) return;

    if (panel.style.display === 'block') {
        panel.style.display = 'none';
        btn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            Next Flypasts ▾
        `;
        return;
    }

    panel.style.display = 'block';
    btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
        </svg>
        Hide Flypasts ▴
    `;

    // Fetch predictions if not in memory
    if (!aoiPredictionsCache[aoiId]) {
        await loadFlypastsData(aoiId, false);
    } else {
        renderFlypasts(aoiId, aoiPredictionsCache[aoiId]);
    }
}

function getPreferredCacheTtlHours() {
    const val = localStorage.getItem('aoi_forecast_cache_ttl_hours');
    return val !== null ? parseFloat(val) : 3;
}

function setPreferredCacheTtlHours(hours) {
    localStorage.setItem('aoi_forecast_cache_ttl_hours', hours.toString());
}

async function changeFlypastCacheTtl(aoiId, ttlHoursStr) {
    const hours = parseFloat(ttlHoursStr);
    setPreferredCacheTtlHours(hours);
    delete aoiPredictionsCache[aoiId];
    await loadFlypastsData(aoiId, true, hours);
}

async function loadFlypastsData(aoiId, forceRefresh = false, customTtlHours = null) {
    const contentDiv = document.getElementById(`flypasts-content-${aoiId}`);
    if (contentDiv) {
        contentDiv.innerHTML = `
            <div style="text-align: center; padding: 16px; color: #6c757d;">
                <span class="loading-spinner"></span> ${forceRefresh ? 'Refreshing satellite orbital forecasts...' : 'Loading flypast predictions...'}
            </div>
        `;
    }

    try {
        const ttlHours = customTtlHours !== null ? customTtlHours : getPreferredCacheTtlHours();
        let url = `/api/aoi/${aoiId}/predict?ttl_hours=${encodeURIComponent(ttlHours)}`;
        if (forceRefresh || ttlHours === 0) {
            url += '&refresh=true';
        }
        const response = await fetch(url, { method: 'POST' });
        const data = await response.json();

        if (response.ok && data.status === 'success') {
            aoiPredictionsCache[aoiId] = data;
            renderFlypasts(aoiId, data);
        } else {
            renderFlypastError(aoiId, data.error || 'No upcoming flypasts found.');
        }
    } catch (err) {
        console.error('Error fetching flypasts:', err);
        renderFlypastError(aoiId, 'Failed to fetch flypast predictions.');
    }
}

async function refreshFlypasts(aoiId) {
    delete aoiPredictionsCache[aoiId];
    await loadFlypastsData(aoiId, true);
}

function renderFlypasts(aoiId, data) {
    const container = document.getElementById(`flypasts-content-${aoiId}`);
    if (!container) return;

    const n2yoPredictions = data.n2yo_predictions || [];
    const histPredictions = data.historical_predictions || [];
    const combinedPredictions = (data.predictions || []).slice(0, 10);
    const missionAnalysis = data.mission_analysis;

    const currentTtl = getPreferredCacheTtlHours();

    if (n2yoPredictions.length === 0 && histPredictions.length === 0 && combinedPredictions.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; color: #6c757d; font-size: 0.85rem; padding: 8px;">
                No upcoming satellite passes predicted.
                <button class="flypast-refresh-btn" onclick="refreshFlypasts(${aoiId})" style="margin-left: 8px;">🔄 Refresh</button>
            </div>
        `;
        return;
    }

    let statsHtml = '';
    if (missionAnalysis && missionAnalysis.total_acquisitions > 0) {
        statsHtml = `
            <div style="font-size: 0.8rem; color: #495057; background: #e8f4fd; padding: 6px 10px; border-radius: 4px; margin-bottom: 8px;">
                <strong>Sentinel-1 Mission History:</strong> ${missionAnalysis.total_acquisitions} past acquisitions | Avg Revisit: ~${missionAnalysis.average_revisit_days} days
            </div>
        `;
    }

    let cacheBadge = '';
    if (data.cached && data.expires_at) {
        const expDate = new Date(data.expires_at);
        cacheBadge = `<span class="flypast-cache-pill cached" title="Cache valid until ${expDate.toISOString()}">💾 DB Cached (Expires ${expDate.toLocaleTimeString()})</span>`;
    } else {
        cacheBadge = `<span class="flypast-cache-pill live" title="Freshly generated and updated in local database">⚡ Live Forecast</span>`;
    }

    const defaultTab = combinedPredictions.length > 0 ? 'combined' : (n2yoPredictions.length > 0 ? 'n2yo' : (histPredictions.length > 0 ? 'hist' : 'combined'));

    container.innerHTML = `
        <div class="flypast-header-bar">
            <div class="flypast-cache-info">
                ${cacheBadge}
                <label class="flypast-ttl-label" for="flypast-ttl-${aoiId}">
                    <span>Cache:</span>
                    <select class="flypast-ttl-select" id="flypast-ttl-${aoiId}" onchange="changeFlypastCacheTtl(${aoiId}, this.value)" title="Change how long forecasts are cached in the database">
                        <option value="0.5" ${currentTtl === 0.5 ? 'selected' : ''}>30 mins</option>
                        <option value="1" ${currentTtl === 1 ? 'selected' : ''}>1 hour</option>
                        <option value="3" ${currentTtl === 3 ? 'selected' : ''}>3 hours (Default)</option>
                        <option value="6" ${currentTtl === 6 ? 'selected' : ''}>6 hours</option>
                        <option value="12" ${currentTtl === 12 ? 'selected' : ''}>12 hours</option>
                        <option value="24" ${currentTtl === 24 ? 'selected' : ''}>24 hours</option>
                        <option value="0" ${currentTtl === 0 ? 'selected' : ''}>No Cache (Always Live)</option>
                    </select>
                </label>
            </div>
            <button class="flypast-refresh-btn" onclick="refreshFlypasts(${aoiId})" title="Force recalculation and update local database cache">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <polyline points="1 20 1 14 7 14"></polyline>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                </svg>
                Refresh Forecast
            </button>
        </div>

        ${statsHtml}
        <div class="flypast-tabs" id="flypast-tabs-${aoiId}">
            <button class="flypast-tab-btn ${defaultTab === 'combined' ? 'active' : ''}" onclick="switchFlypastTab(${aoiId}, 'combined')">
                ✨ Both (Combined) <span class="tab-count">${combinedPredictions.length}</span>
            </button>
            <button class="flypast-tab-btn ${defaultTab === 'n2yo' ? 'active' : ''}" onclick="switchFlypastTab(${aoiId}, 'n2yo')">
                🛰️ N2YO Tracking <span class="tab-count">${n2yoPredictions.length}</span>
            </button>
            <button class="flypast-tab-btn ${defaultTab === 'hist' ? 'active' : ''}" onclick="switchFlypastTab(${aoiId}, 'hist')">
                🔁 Scan Extrapolation <span class="tab-count">${histPredictions.length}</span>
            </button>
        </div>

        <div id="flypast-tab-combined-${aoiId}" class="flypast-tab-content" style="display: ${defaultTab === 'combined' ? 'block' : 'none'};">
            <div class="flypast-section-desc">Cross-validated passes merging astronomical tracking and historical repeat cycles.</div>
            <div class="flypast-list">
                ${renderPassListHtml(aoiId, combinedPredictions, 'No combined passes available.')}
            </div>
        </div>

        <div id="flypast-tab-n2yo-${aoiId}" class="flypast-tab-content" style="display: ${defaultTab === 'n2yo' ? 'block' : 'none'};">
            <div class="flypast-section-desc">Astronomical satellite tracking passes from N2YO orbital pass predictions.</div>
            <div class="flypast-list">
                ${renderPassListHtml(aoiId, n2yoPredictions, 'No N2YO predictions found (API key required or offline).')}
            </div>
        </div>

        <div id="flypast-tab-hist-${aoiId}" class="flypast-tab-content" style="display: ${defaultTab === 'hist' ? 'block' : 'none'};">
            <div class="flypast-section-desc">Passes extrapolated from previous Sentinel-1 acquisitions and 12-day orbital repeat cycles.</div>
            <div class="flypast-list">
                ${renderPassListHtml(aoiId, histPredictions, 'No previous scans found for repeat-cycle extrapolation.')}
            </div>
        </div>
    `;
}

function switchFlypastTab(aoiId, tabKey) {
    const tabsContainer = document.getElementById(`flypast-tabs-${aoiId}`);
    if (!tabsContainer) return;

    const buttons = tabsContainer.querySelectorAll('.flypast-tab-btn');
    const keys = ['combined', 'n2yo', 'hist'];
    buttons.forEach((btn, idx) => {
        btn.classList.toggle('active', keys[idx] === tabKey);
    });

    keys.forEach(key => {
        const pane = document.getElementById(`flypast-tab-${key}-${aoiId}`);
        if (pane) {
            pane.style.display = key === tabKey ? 'block' : 'none';
        }
    });
}

function renderPassListHtml(aoiId, predictions, emptyMessage) {
    if (!predictions || predictions.length === 0) {
        return `<div style="text-align: center; color: #6c757d; font-size: 0.85rem; padding: 12px;">${escapeHtml(emptyMessage)}</div>`;
    }

    return predictions.slice(0, 10).map((pred, index) => {
        const passDate = new Date(pred.time);
        const zuluStr = passDate.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
        const localStr = passDate.toLocaleString();
        
        const sat = pred.satellite || 'Sentinel-1';
        const dir = pred.orbit_direction || null;
        const dirClass = dir === 'ASCENDING' ? 'badge-ascending' : (dir === 'DESCENDING' ? 'badge-descending' : 'badge-secondary');
        const dirArrow = dir === 'ASCENDING' ? '⬆ Ascending' : (dir === 'DESCENDING' ? '⬇ Descending' : dir);
        
        const track = pred.relative_orbit ? `Track #${pred.relative_orbit}` : '';
        const confPercent = pred.confidence_score ? `${Math.round(pred.confidence_score * 100)}% Conf` : '';
        const source = pred.source || 'N2YO';
        const contrib = pred.contribution || (source === 'COMBINED' ? 'both' : (source === 'HISTORICAL_MISSION' ? 'historical' : 'n2yo'));
        let contribBadge = '';
        if (contrib === 'both') {
            contribBadge = '<span class="badge" style="background: linear-gradient(135deg, #4f46e5, #7c3aed); color: #fff; font-weight: 600;">✨ Both (N2YO + Historical)</span>';
        } else if (contrib === 'historical') {
            contribBadge = '<span class="badge" style="background: #059669; color: #fff; font-weight: 600;">🔁 Historical Cycle</span>';
        } else {
            contribBadge = '<span class="badge" style="background: #0284c7; color: #fff; font-weight: 600;">🛰️ N2YO Tracking</span>';
        }
        const elev = pred.max_elevation ? `Max Elev: ${pred.max_elevation}°` : '';
        const matchNote = pred.contribution_detail || pred.historical_match ? escapeHtml(pred.contribution_detail || pred.historical_match) : '';

        return `
            <div class="flypast-item">
                <div class="flypast-item-main">
                    <div class="flypast-item-header">
                        <span>#${index + 1} &bull; ${localStr}</span>
                        <span style="font-size: 0.78rem; color: #6c757d;">${zuluStr}</span>
                    </div>
                    <div class="flypast-item-meta">
                        <span class="badge badge-success">${escapeHtml(sat)}</span>
                        ${dir ? `<span class="badge ${dirClass}">${escapeHtml(dirArrow)}</span>` : ''}
                        ${track ? `<span class="badge badge-info">${escapeHtml(track)}</span>` : ''}
                        ${confPercent ? `<span class="badge badge-warning">${escapeHtml(confPercent)}</span>` : ''}
                        ${contribBadge}
                        ${elev ? `<span style="font-size: 0.78rem; color: #6c757d;">${escapeHtml(elev)}</span>` : ''}
                    </div>
                    ${matchNote ? `<div class="flypast-item-detail">🔍 ${matchNote}</div>` : ''}
                </div>
                <div class="flypast-item-actions">
                    <button class="btn btn-outline-secondary btn-sm" onclick="scrapePassAIS(${aoiId}, '${pred.time}')" title="Scrape AIS data for the [-5m, +5m] window around this pass">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path>
                        </svg>
                        Scrape AIS (±5m)
                    </button>
                </div>
            </div>
        `;
    }).join('');
}


function renderFlypastError(aoiId, errorMsg) {
    const container = document.getElementById(`flypasts-content-${aoiId}`);
    if (container) {
        container.innerHTML = `
            <div style="color: #dc3545; background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 4px; padding: 8px 12px; font-size: 0.85rem; text-align: center;">
                ${escapeHtml(errorMsg)}
            </div>
        `;
    }
}

async function toggleAutoCapture(aoiId, enabled) {
    try {
        const res = await fetch(`/api/aoi/${aoiId}/auto_capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        if (res.ok) {
            showToast(`Auto-capture ${enabled ? 'enabled' : 'disabled'} for AOI #${aoiId}`, 'success');
        } else {
            showToast('Failed to update auto-capture setting', 'error');
        }
    } catch (err) {
        console.error('Error toggling auto-capture:', err);
        showToast('Connection error while updating auto-capture', 'error');
    }
}

async function scrapePassAIS(aoiId, passTime) {
    try {
        showToast('Initiating AIS pass scrape...', 'info');
        const res = await fetch(`/api/aoi/${aoiId}/scrape_ais`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pass_time: passTime })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            const total = (data.results && data.results.total_inserted) || 0;
            showToast(`Scraped and ingested ${total} AIS records for pass window!`, 'success');
        } else {
            showToast('AIS Scrape failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        console.error('Error scraping AIS:', err);
        showToast('Failed to trigger AIS scrape.', 'error');
    }
}

async function forceScanAOIAIS(aoiId) {
    const btn = document.getElementById(`btn-force-scan-${aoiId}`);
    const originalContent = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner"></span> Scanning Ships...';
    }

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
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalContent;
        }
    }
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
