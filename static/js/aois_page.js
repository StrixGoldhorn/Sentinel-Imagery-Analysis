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
        nextScanBadge = `<span class="badge badge-success" title="Next Pass: ${d.toISOString()}">${d.toLocaleString()}</span>`;
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
            <div class="aoi-info-row">
                <span class="aoi-info-label">Bounding Box:</span>
                <span class="coords-box">[${bboxFormatted}]</span>
            </div>
            <div class="aoi-info-row">
                <label for="auto-cap-${aoi.id}" style="cursor: pointer; display: flex; align-items: center; gap: 8px; font-size: 0.88rem; color: #495057;">
                    <input type="checkbox" id="auto-cap-${aoi.id}" ${isAuto} onchange="toggleAutoCapture(${aoi.id}, this.checked)">
                    <span>Auto-Capture & Scrape on Pass</span>
                </label>
            </div>

            <div class="aoi-actions">
                <div class="btn-row">
                    <button class="btn btn-primary" style="flex: 1;" onclick="goToMap([${aoi.bbox.join(',')}], ${aoi.id})">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon>
                            <line x1="8" y1="2" x2="8" y2="18"></line>
                            <line x1="16" y1="6" x2="16" y2="22"></line>
                        </svg>
                        View on Map
                    </button>
                    <button class="btn btn-outline-primary" style="flex: 1;" id="btn-toggle-${aoi.id}" onclick="toggleFlypasts(${aoi.id})">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        Next 5 Flypasts ▾
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
            Next 5 Flypasts ▾
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

    // Fetch predictions if not cached
    if (!aoiPredictionsCache[aoiId]) {
        try {
            const response = await fetch(`/api/aoi/${aoiId}/predict`, { method: 'POST' });
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
    } else {
        renderFlypasts(aoiId, aoiPredictionsCache[aoiId]);
    }
}

function renderFlypasts(aoiId, data) {
    const container = document.getElementById(`flypasts-content-${aoiId}`);
    if (!container) return;

    const predictions = (data.predictions || []).slice(0, 5);
    const missionAnalysis = data.mission_analysis;

    if (predictions.length === 0) {
        container.innerHTML = `<div style="text-align: center; color: #6c757d; font-size: 0.85rem; padding: 8px;">No upcoming satellite passes predicted.</div>`;
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

    let passesHtml = predictions.map((pred, index) => {
        const passDate = new Date(pred.time);
        const zuluStr = passDate.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
        const localStr = passDate.toLocaleString();
        
        const sat = pred.satellite || 'Sentinel-1';
        const dir = pred.orbit_direction || 'N/A';
        const dirClass = dir === 'ASCENDING' ? 'badge-ascending' : (dir === 'DESCENDING' ? 'badge-descending' : 'badge-secondary');
        const dirArrow = dir === 'ASCENDING' ? '⬆ Ascending' : (dir === 'DESCENDING' ? '⬇ Descending' : dir);
        
        const track = pred.relative_orbit ? `Track #${pred.relative_orbit}` : '';
        const confPercent = pred.confidence_score ? `${Math.round(pred.confidence_score * 100)}% Conf` : '';
        const source = pred.source || 'N2YO';
        const sourceClass = source === 'COMBINED' ? 'badge-combined' : (source === 'HISTORICAL_MISSION' ? 'badge-info' : 'badge-secondary');
        const elev = pred.max_elevation ? `Max Elev: ${pred.max_elevation}°` : '';

        return `
            <div class="flypast-item">
                <div class="flypast-item-header">
                    <span>#${index + 1} &bull; ${localStr}</span>
                    <span style="font-size: 0.78rem; color: #6c757d;">${zuluStr}</span>
                </div>
                <div class="flypast-item-meta">
                    <span class="badge badge-success">${escapeHtml(sat)}</span>
                    <span class="badge ${dirClass}">${escapeHtml(dirArrow)}</span>
                    ${track ? `<span class="badge badge-info">${escapeHtml(track)}</span>` : ''}
                    ${confPercent ? `<span class="badge badge-warning">${escapeHtml(confPercent)}</span>` : ''}
                    <span class="badge ${sourceClass}">[${escapeHtml(source)}]</span>
                    ${elev ? `<span style="font-size: 0.78rem; color: #6c757d;">${escapeHtml(elev)}</span>` : ''}
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

    container.innerHTML = `
        ${statsHtml}
        <div class="flypast-list">
            ${passesHtml}
        </div>
    `;
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

function escapeHtml(text) {
    if (typeof text !== 'string') return String(text ?? '');
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.cssText = 'position: fixed; bottom: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px;';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColors = {
        success: '#28a745',
        error: '#dc3545',
        info: '#17a2b8',
        warning: '#ffc107'
    };
    const textColors = {
        warning: '#212529',
        default: '#ffffff'
    };

    toast.style.cssText = `
        background: ${bgColors[type] || '#333'};
        color: ${textColors[type] || textColors.default};
        padding: 10px 16px;
        border-radius: 6px;
        font-size: 0.9rem;
        box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        opacity: 0;
        transform: translateY(10px);
        transition: all 0.3s ease;
    `;
    toast.textContent = message;
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
