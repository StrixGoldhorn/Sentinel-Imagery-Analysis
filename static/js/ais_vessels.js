/**
 * AIS Vessel Visualization on Leaflet Map
 * Features:
 * - Directional ship icons rotated according to true heading
 * - Maritime-standard color coding by vessel type (Cargo, Tanker, Passenger, Tug, Fishing, etc.)
 * - Projected speed vectors based on Speed Over Ground (SOG)
 * - Rich tooltips & interactive inspector popups
 * - Date Range Slider control and temporal scrubbing
 * - Timeline presets (Live, 24h, 3d, 7d, Custom Range)
 * - Automatic radar timeline playback animation & 1h stepping
 * - Synchronized floating timeline control on map
 */

let aisVesselLayer = null;
let aisVesselsData = [];
let aisLayerEnabled = true;
let activeTypeFilters = new Set(['Cargo', 'Tanker', 'Passenger', 'Tug', 'Fishing', 'Military', 'Pleasure', 'Other']);

// Timeline state
const aisTimelineState = {
    isLive: true,
    minTime: Date.now() - 7 * 24 * 3600 * 1000,
    maxTime: Date.now(),
    selectedTime: Date.now(),
    customStart: null,
    customEnd: null,
    preset: 'live',
    isPlaying: false,
    playbackSpeed: 1,
    playbackInterval: null,
    windowHours: 6,
    isMinimized: false
};

const VESSEL_TYPE_COLORS = {
    'Cargo': '#28a745',
    'Tanker': '#dc3545',
    'Passenger': '#007bff',
    'Tug': '#17a2b8',
    'Fishing': '#fd7e14',
    'Military': '#6f42c1',
    'Pleasure': '#20c997',
    'HighSpeed': '#e83e8c',
    'Other': '#6c757d'
};

function classifyVesselType(typeStr) {
    if (!typeStr || typeof typeStr !== 'string') return 'Other';
    const lower = typeStr.toLowerCase();

    if (lower.includes('cargo') || lower.includes('container') || lower.includes('bulk') || lower.includes('carrier')) {
        return 'Cargo';
    }
    if (lower.includes('tanker') || lower.includes('oil') || lower.includes('chemical') || lower.includes('lpg') || lower.includes('lng')) {
        return 'Tanker';
    }
    if (lower.includes('passenger') || lower.includes('ferry') || lower.includes('cruise')) {
        return 'Passenger';
    }
    if (lower.includes('tug') || lower.includes('towing') || lower.includes('pilot') || lower.includes('dredger') || lower.includes('supply')) {
        return 'Tug';
    }
    if (lower.includes('fish') || lower.includes('trawler')) {
        return 'Fishing';
    }
    if (lower.includes('military') || lower.includes('law') || lower.includes('police') || lower.includes('guard') || lower.includes('patrol') || lower.includes('sar')) {
        return 'Military';
    }
    if (lower.includes('pleasure') || lower.includes('yacht') || lower.includes('sail')) {
        return 'Pleasure';
    }
    if (lower.includes('high speed') || lower.includes('hsc')) {
        return 'HighSpeed';
    }
    return 'Other';
}

function getVesselColor(typeStr) {
    const category = classifyVesselType(typeStr);
    return VESSEL_TYPE_COLORS[category] || VESSEL_TYPE_COLORS['Other'];
}

function formatAisDateTime(ts) {
    if (!ts) return '-';
    const d = new Date(ts);
    const year = d.getUTCFullYear();
    const month = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    const hours = String(d.getUTCHours()).padStart(2, '0');
    const minutes = String(d.getUTCMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes} UTC`;
}

function formatAisShortDate(ts) {
    if (!ts) return '-';
    const d = new Date(ts);
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${monthNames[d.getUTCMonth()]} ${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

async function initAisTimelineBounds() {
    try {
        if (!CONFIG.API_AIS_TIMELINE) return;
        const res = await fetch(CONFIG.API_AIS_TIMELINE);
        const data = await res.json();
        if (res.ok && data.status === 'success' && data.min_timestamp && data.max_timestamp) {
            const minMs = new Date(data.min_timestamp.replace('Z', '+00:00')).getTime();
            const maxMs = new Date(data.max_timestamp.replace('Z', '+00:00')).getTime();
            if (!isNaN(minMs) && !isNaN(maxMs) && minMs < maxMs) {
                // Keep at least a 24h span
                aisTimelineState.minTime = Math.min(minMs, Date.now() - 7 * 24 * 3600 * 1000);
                aisTimelineState.maxTime = Math.max(maxMs, Date.now());
            }
        }
    } catch (e) {
        console.warn('Unable to load AIS timeline bounds:', e);
    }
    updateSliderBoundsDisplay();
}

function updateSliderBoundsDisplay() {
    const minLabel = document.getElementById('aisSliderMinLabel');
    const maxLabel = document.getElementById('aisSliderMaxLabel');
    if (minLabel) {
        minLabel.textContent = formatAisShortDate(aisTimelineState.minTime);
    }
    if (maxLabel) {
        maxLabel.textContent = aisTimelineState.isLive ? 'Now (Live)' : formatAisShortDate(aisTimelineState.maxTime);
    }
}

function initAISVessels(mapInstance) {
    if (!mapInstance) return;

    aisVesselLayer = L.featureGroup();

    const savedState = localStorage.getItem('ais_vessels_enabled');
    if (savedState === 'false') {
        aisLayerEnabled = false;
    } else {
        aisLayerEnabled = true;
        aisVesselLayer.addTo(mapInstance);
    }

    const toggleCheckbox = document.getElementById('aisVesselToggle');
    if (toggleCheckbox) {
        toggleCheckbox.checked = aisLayerEnabled;
        toggleCheckbox.addEventListener('change', (e) => {
            toggleAISVessels(mapInstance, e.target.checked);
        });
    }

    const floatingTimeline = document.getElementById('aisFloatingTimeline');
    if (floatingTimeline) {
        floatingTimeline.style.display = aisLayerEnabled ? 'flex' : 'none';
    }

    initAisTimelineBounds();

    // Initial fetch of vessels
    loadAISVessels(mapInstance);

    // Refresh when map stops moving
    if (typeof debounce === 'function') {
        mapInstance.on('moveend', debounce(() => {
            if (aisLayerEnabled) {
                loadAISVessels(mapInstance);
            }
        }, 1000));
    }
}

function toggleAISVessels(mapInstance, enable) {
    if (!aisVesselLayer || !mapInstance) return;
    const floatingTimeline = document.getElementById('aisFloatingTimeline');

    if (enable) {
        if (!mapInstance.hasLayer(aisVesselLayer)) {
            aisVesselLayer.addTo(mapInstance);
        }
        aisLayerEnabled = true;
        localStorage.setItem('ais_vessels_enabled', 'true');
        if (floatingTimeline) floatingTimeline.style.display = 'flex';
        loadAISVessels(mapInstance);
        if (typeof showNotification === 'function') {
            showNotification('AIS Vessel overlay enabled', 'success');
        }
    } else {
        if (mapInstance.hasLayer(aisVesselLayer)) {
            mapInstance.removeLayer(aisVesselLayer);
        }
        aisLayerEnabled = false;
        localStorage.setItem('ais_vessels_enabled', 'false');
        if (floatingTimeline) floatingTimeline.style.display = 'none';
        stopAisPlayback();
    }
}

function syncSliderElements(val) {
    const slider1 = document.getElementById('aisDateSlider');
    const slider2 = document.getElementById('floatingDateSlider');
    if (slider1 && slider1.value !== String(val)) slider1.value = val;
    if (slider2 && slider2.value !== String(val)) slider2.value = val;
}

function setSliderValue(val) {
    syncSliderElements(val);
}

function updateTimelineLabels(targetTs) {
    const isLive = aisTimelineState.isLive;
    const displayStr = isLive 
        ? 'Live Positions (Current)' 
        : `Historical: ${formatAisDateTime(targetTs)}`;

    const sidebarDisplay = document.getElementById('aisSelectedDateDisplay');
    const floatingDisplay = document.getElementById('floatingTimelineTimeDisplay');
    const sidebarBadge = document.getElementById('aisTimelineModeBadge');
    const floatingBadge = document.getElementById('floatingTimelineBadge');

    if (sidebarDisplay) {
        sidebarDisplay.textContent = displayStr;
        sidebarDisplay.style.color = isLive ? '#28a745' : '#007bff';
        sidebarDisplay.style.borderColor = isLive ? '#28a745' : '#007bff';
    }
    if (floatingDisplay) {
        floatingDisplay.textContent = isLive ? 'Live Positions' : formatAisDateTime(targetTs);
    }
    if (sidebarBadge) {
        sidebarBadge.textContent = isLive ? 'LIVE' : 'HISTORY';
        sidebarBadge.className = isLive ? 'badge badge-success' : 'badge badge-warning';
        sidebarBadge.style.backgroundColor = isLive ? '#28a745' : '#fd7e14';
    }
    if (floatingBadge) {
        floatingBadge.textContent = isLive ? 'LIVE' : 'HISTORY';
        floatingBadge.className = isLive ? 'badge badge-success' : 'badge badge-warning';
        floatingBadge.style.backgroundColor = isLive ? '#28a745' : '#fd7e14';
    }
}

function highlightPresetButton(preset) {
    const buttons = document.querySelectorAll('.btn-preset');
    buttons.forEach(btn => {
        if (btn.getAttribute('data-preset') === preset) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

function onAisSliderInput(val) {
    const percent = parseFloat(val) / 100;
    const targetTs = aisTimelineState.minTime + (aisTimelineState.maxTime - aisTimelineState.minTime) * percent;
    aisTimelineState.selectedTime = targetTs;
    aisTimelineState.isLive = (parseFloat(val) >= 99.5);

    syncSliderElements(val);
    updateTimelineLabels(targetTs);
}

let aisSliderDebounceTimer = null;
function onAisSliderChange(val) {
    onAisSliderInput(val);
    if (aisTimelineState.isLive) {
        aisTimelineState.preset = 'live';
        highlightPresetButton('live');
    } else {
        aisTimelineState.preset = 'slider';
        highlightPresetButton(null);
    }
    
    if (aisSliderDebounceTimer) clearTimeout(aisSliderDebounceTimer);
    aisSliderDebounceTimer = setTimeout(() => {
        if (typeof map !== 'undefined' && map) {
            loadAISVessels(map);
        }
    }, 100);
}

function setAisTimelinePreset(preset) {
    stopAisPlayback();
    aisTimelineState.preset = preset;
    highlightPresetButton(preset);

    const now = Date.now();
    aisTimelineState.maxTime = now;

    if (preset === 'live') {
        aisTimelineState.isLive = true;
        aisTimelineState.selectedTime = now;
        setSliderValue(100);
        updateTimelineLabels(now);
    } else if (preset === '24h') {
        aisTimelineState.isLive = false;
        aisTimelineState.minTime = now - 24 * 3600 * 1000;
        aisTimelineState.selectedTime = now;
        setSliderValue(100);
        updateTimelineLabels(now);
    } else if (preset === '3d') {
        aisTimelineState.isLive = false;
        aisTimelineState.minTime = now - 3 * 24 * 3600 * 1000;
        aisTimelineState.selectedTime = now;
        setSliderValue(100);
        updateTimelineLabels(now);
    } else if (preset === '7d') {
        aisTimelineState.isLive = false;
        aisTimelineState.minTime = now - 7 * 24 * 3600 * 1000;
        aisTimelineState.selectedTime = now;
        setSliderValue(100);
        updateTimelineLabels(now);
    }

    updateSliderBoundsDisplay();
    if (typeof map !== 'undefined' && map) {
        loadAISVessels(map);
    }
}

function toggleAisCustomDateInputs() {
    const panel = document.getElementById('aisCustomDatePanel');
    if (!panel) return;
    const isHidden = panel.style.display === 'none' || panel.style.display === '';
    panel.style.display = isHidden ? 'block' : 'none';

    if (isHidden) {
        const startInput = document.getElementById('aisCustomStartInput');
        const endInput = document.getElementById('aisCustomEndInput');
        if (startInput && !startInput.value) {
            const defaultStart = new Date(Date.now() - 24 * 3600 * 1000);
            startInput.value = defaultStart.toISOString().slice(0, 16);
        }
        if (endInput && !endInput.value) {
            const defaultEnd = new Date();
            endInput.value = defaultEnd.toISOString().slice(0, 16);
        }
    }
}

function applyAisCustomDateRange() {
    const startInput = document.getElementById('aisCustomStartInput');
    const endInput = document.getElementById('aisCustomEndInput');
    if (!startInput || !endInput || !startInput.value || !endInput.value) {
        if (typeof showNotification === 'function') {
            showNotification('Please provide valid start and end dates', 'warning');
        }
        return;
    }

    const startMs = new Date(startInput.value + ':00Z').getTime();
    const endMs = new Date(endInput.value + ':00Z').getTime();

    if (isNaN(startMs) || isNaN(endMs) || startMs >= endMs) {
        if (typeof showNotification === 'function') {
            showNotification('Start date must be before end date', 'error');
        }
        return;
    }

    stopAisPlayback();
    aisTimelineState.preset = 'custom';
    highlightPresetButton('custom');
    aisTimelineState.isLive = false;
    aisTimelineState.minTime = startMs;
    aisTimelineState.maxTime = endMs;
    aisTimelineState.selectedTime = endMs;
    aisTimelineState.customStart = new Date(startMs).toISOString();
    aisTimelineState.customEnd = new Date(endMs).toISOString();

    setSliderValue(100);
    updateSliderBoundsDisplay();
    updateTimelineLabels(endMs);

    if (typeof map !== 'undefined' && map) {
        loadAISVessels(map);
    }
    if (typeof showNotification === 'function') {
        showNotification(`Applied custom date range: ${formatAisShortDate(startMs)} — ${formatAisShortDate(endMs)}`, 'info');
    }
}

function toggleAisTimelinePlayback() {
    if (aisTimelineState.isPlaying) {
        stopAisPlayback();
    } else {
        startAisPlayback();
    }
}

function startAisPlayback() {
    aisTimelineState.isPlaying = true;
    aisTimelineState.isLive = false;
    updatePlayButtonUI(true);

    const currentVal = parseFloat(document.getElementById('aisDateSlider')?.value || 100);
    if (currentVal >= 99) {
        setSliderValue(0);
        onAisSliderInput(0);
    }

    if (aisTimelineState.playbackInterval) clearInterval(aisTimelineState.playbackInterval);

    aisTimelineState.playbackInterval = setInterval(() => {
        let val = parseFloat(document.getElementById('aisDateSlider')?.value || 0);
        const step = 1.2 * aisTimelineState.playbackSpeed;
        val += step;

        if (val >= 100) {
            val = 100;
            setSliderValue(100);
            onAisSliderInput(100);
            stopAisPlayback();
            if (typeof map !== 'undefined' && map) loadAISVessels(map);
            return;
        }

        setSliderValue(val);
        onAisSliderInput(val);
        if (typeof map !== 'undefined' && map) {
            loadAISVessels(map);
        }
    }, 1000);
}

function stopAisPlayback() {
    aisTimelineState.isPlaying = false;
    if (aisTimelineState.playbackInterval) {
        clearInterval(aisTimelineState.playbackInterval);
        aisTimelineState.playbackInterval = null;
    }
    updatePlayButtonUI(false);
}

function updatePlayButtonUI(playing) {
    const sidebarBtn = document.getElementById('aisPlayPauseBtn');
    const floatingBtn = document.getElementById('floatingPlayBtn');

    if (sidebarBtn) {
        sidebarBtn.textContent = playing ? '⏸ Pause' : '▶ Play Timeline';
        sidebarBtn.style.backgroundColor = playing ? '#dc3545' : '#28a745';
    }
    if (floatingBtn) {
        floatingBtn.textContent = playing ? '⏸' : '▶';
        floatingBtn.style.backgroundColor = playing ? '#dc3545' : 'rgba(255, 255, 255, 0.15)';
    }
}

function stepAisTimeline(hours) {
    stopAisPlayback();
    const range = aisTimelineState.maxTime - aisTimelineState.minTime;
    if (range <= 0) return;
    const msDelta = hours * 3600 * 1000;
    let targetTs = aisTimelineState.selectedTime + msDelta;
    targetTs = Math.max(aisTimelineState.minTime, Math.min(aisTimelineState.maxTime, targetTs));
    aisTimelineState.selectedTime = targetTs;
    aisTimelineState.isLive = (targetTs >= aisTimelineState.maxTime - 60000);

    const pct = ((targetTs - aisTimelineState.minTime) / range) * 100;
    setSliderValue(pct);
    updateTimelineLabels(targetTs);

    if (typeof map !== 'undefined' && map) {
        loadAISVessels(map);
    }
}

function setPlaybackSpeed(speed) {
    aisTimelineState.playbackSpeed = parseFloat(speed) || 1;
}

function toggleFloatingTimelineMin() {
    const controls = document.getElementById('floatingTimelineControls');
    const minBtn = document.getElementById('floatingMinBtn');
    if (!controls) return;
    aisTimelineState.isMinimized = !aisTimelineState.isMinimized;
    controls.style.display = aisTimelineState.isMinimized ? 'none' : 'flex';
    if (minBtn) minBtn.textContent = aisTimelineState.isMinimized ? '+' : '_';
}

function resetAisToLive() {
    setAisTimelinePreset('live');
}

async function loadAISVessels(mapInstance, bbox = null) {
    if (!mapInstance) return;

    try {
        let url = CONFIG.API_AIS_VESSELS + '?latest_only=true&limit=1000';
        if (bbox) {
            url += `&bbox=${bbox.join(',')}`;
        }

        if (!aisTimelineState.isLive) {
            if (aisTimelineState.preset === 'custom' && aisTimelineState.customStart && aisTimelineState.customEnd) {
                url += `&start=${encodeURIComponent(aisTimelineState.customStart)}&end=${encodeURIComponent(aisTimelineState.customEnd)}`;
            } else {
                // Window of windowHours up to selectedTime
                const end = new Date(aisTimelineState.selectedTime).toISOString();
                const start = new Date(aisTimelineState.selectedTime - aisTimelineState.windowHours * 3600 * 1000).toISOString();
                url += `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
            }
        }

        const res = await fetch(url);
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            const rawVessels = data.vessels || [];
            const latestByMmsi = new Map();
            for (const v of rawVessels) {
                if (!v || !v.mmsi) continue;
                const existing = latestByMmsi.get(v.mmsi);
                if (!existing) {
                    latestByMmsi.set(v.mmsi, v);
                } else {
                    const existingTs = existing.timestamp ? new Date(existing.timestamp).getTime() : 0;
                    const newTs = v.timestamp ? new Date(v.timestamp).getTime() : 0;
                    if (newTs >= existingTs) {
                        latestByMmsi.set(v.mmsi, v);
                    }
                }
            }
            aisVesselsData = Array.from(latestByMmsi.values());
            renderVesselsOnMap(mapInstance);
            updateVesselCountBadge(aisVesselsData.length);
        }
    } catch (err) {
        console.error('Error loading AIS vessels:', err);
    }
}

function refreshAISVessels(mapInstance) {
    if (typeof showNotification === 'function') {
        showNotification('Refreshing AIS vessel positions...', 'info');
    }
    loadAISVessels(mapInstance);
}

function renderVesselsOnMap(mapInstance) {
    if (!aisVesselLayer) return;
    aisVesselLayer.clearLayers();

    if (!aisLayerEnabled) return;

    // Retain only the latest position per unique MMSI after category filtering
    const latestFilteredByMmsi = new Map();
    aisVesselsData.forEach(vessel => {
        if (!vessel || !vessel.mmsi) return;
        const category = classifyVesselType(vessel.type);
        if (!activeTypeFilters.has(category)) return;

        const existing = latestFilteredByMmsi.get(vessel.mmsi);
        if (!existing) {
            latestFilteredByMmsi.set(vessel.mmsi, vessel);
        } else {
            const existingTs = existing.timestamp ? new Date(existing.timestamp).getTime() : 0;
            const newTs = vessel.timestamp ? new Date(vessel.timestamp).getTime() : 0;
            if (newTs >= existingTs) {
                latestFilteredByMmsi.set(vessel.mmsi, vessel);
            }
        }
    });

    latestFilteredByMmsi.forEach(vessel => {

        const color = getVesselColor(vessel.type);
        const heading = (vessel.heading !== null && vessel.heading !== undefined && !isNaN(vessel.heading) && vessel.heading <= 360) 
            ? Number(vessel.heading) 
            : null;
        const speed = (vessel.speed !== null && vessel.speed !== undefined && !isNaN(vessel.speed)) 
            ? Number(vessel.speed) 
            : 0;

        let iconHtml;
        if (heading !== null) {
            iconHtml = `
                <div class="vessel-marker-wrapper" style="transform: rotate(${heading}deg);">
                    <svg width="22" height="22" viewBox="0 0 24 24" class="vessel-marker-svg">
                        <path d="M12 2 L19 20 L12 16 L5 20 Z" fill="${color}" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" />
                    </svg>
                </div>
            `;
        } else {
            iconHtml = `
                <div class="vessel-marker-wrapper">
                    <div class="vessel-circle-marker" style="background-color: ${color};">
                        <div class="vessel-circle-inner"></div>
                    </div>
                </div>
            `;
        }

        const customIcon = L.divIcon({
            html: iconHtml,
            className: 'vessel-div-icon',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12]
        });

        const marker = L.marker([vessel.latitude, vessel.longitude], { icon: customIcon });

        // Hover tooltip
        const safeName = escapeHtml(vessel.name || `MMSI: ${vessel.mmsi}`);
        const safeType = escapeHtml(vessel.type || 'Unspecified');
        const tooltipHtml = `
            <div class="vessel-tooltip">
                <strong>🚢 ${safeName}</strong><br>
                <span style="color: ${color}; font-weight: 600;">${safeType}</span> • 
                <span>${speed.toFixed(1)} kn</span> • 
                <span>${heading !== null ? heading.toFixed(0) + '°' : 'N/A'}</span>
            </div>
        `;
        marker.bindTooltip(tooltipHtml, { direction: 'top', offset: [0, -10] });

        // Click popup
        const zuluTime = vessel.timestamp ? new Date(vessel.timestamp).toISOString().replace('T', ' ').replace(/\..+/, '') + ' UTC' : 'Unknown';
        const localTime = vessel.timestamp ? new Date(vessel.timestamp).toLocaleTimeString() : 'Unknown';
        const popupHtml = `
            <div class="vessel-popup-card">
                <div class="vessel-popup-header" style="border-left: 4px solid ${color};">
                    <div>
                        <div class="vessel-popup-title">${safeName}</div>
                        <small style="color: #6c757d;">MMSI: ${vessel.mmsi} ${vessel.imo ? '• IMO: ' + vessel.imo : ''}</small>
                    </div>
                    <span class="badge" style="background-color: ${color}; color: #ffffff; white-space: nowrap;">${safeType}</span>
                </div>
                <div class="vessel-popup-grid">
                    <div class="vessel-field"><span class="vessel-label">Speed (SOG)</span><span class="vessel-val"><b>${speed.toFixed(1)} kn</b></span></div>
                    <div class="vessel-field"><span class="vessel-label">Heading (HDG)</span><span class="vessel-val"><b>${heading !== null ? heading.toFixed(1) + '°' : 'N/A'}</b></span></div>
                    <div class="vessel-field"><span class="vessel-label">Callsign</span><span class="vessel-val">${vessel.callsign || 'N/A'}</span></div>
                    <div class="vessel-field"><span class="vessel-label">Source</span><span class="vessel-val"><small>${escapeHtml(vessel.source_plugin)}</small></span></div>
                    <div class="vessel-field" style="grid-column: span 2;"><span class="vessel-label">Coordinates</span><span class="vessel-val">${vessel.latitude.toFixed(5)}° N, ${vessel.longitude.toFixed(5)}° E</span></div>
                    <div class="vessel-field" style="grid-column: span 2;"><span class="vessel-label">Last Reported</span><span class="vessel-val">${zuluTime} (${localTime})</span></div>
                </div>
                <div class="vessel-popup-actions" style="display: flex; gap: 6px; justify-content: flex-end;">
                    <button class="btn btn-sm btn-secondary" style="padding: 4px 10px; font-size: 0.8rem; background: #6c757d; color: white; border: none; border-radius: 4px; cursor: pointer;" onclick="openEditVesselModal(${vessel.vessel_id})">
                        ✏️ Edit Details
                    </button>
                    <button class="btn btn-sm btn-primary" style="padding: 4px 10px; font-size: 0.8rem; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;" onclick="map.setView([${vessel.latitude}, ${vessel.longitude}], Math.max(map.getZoom(), 14))">
                        Center Map
                    </button>
                </div>
            </div>
        `;
        marker.bindPopup(popupHtml, { maxWidth: 340 });

        aisVesselLayer.addLayer(marker);

        // Projected speed vector line if speed > 1 knot and heading known
        if (speed >= 1.0 && heading !== null) {
            const rad = (heading * Math.PI) / 180;
            // 6-minute distance in degrees (speed in knots * 0.1 hr * nautical miles to degrees)
            const distDeg = (speed * 0.1) / 60;
            const endLat = vessel.latitude + distDeg * Math.cos(rad);
            const endLng = vessel.longitude + (distDeg * Math.sin(rad)) / Math.cos((vessel.latitude * Math.PI) / 180);

            const speedLine = L.polyline([[vessel.latitude, vessel.longitude], [endLat, endLng]], {
                color: color,
                weight: 2,
                opacity: 0.75,
                dashArray: '3, 4'
            });
            aisVesselLayer.addLayer(speedLine);
        }
    });
}

function updateVesselCountBadge(count) {
    const badge = document.getElementById('aisVesselCount');
    if (badge) {
        badge.textContent = `${count} vessel${count === 1 ? '' : 's'}`;
    }
}

function toggleAisFilterPanel() {
    const panel = document.getElementById('aisFilterPanel');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

function applyAisFilters() {
    const checkboxes = document.querySelectorAll('.ais-type-filter');
    activeTypeFilters.clear();
    checkboxes.forEach(cb => {
        if (cb.checked) {
            activeTypeFilters.add(cb.value);
        }
    });
    if (typeof map !== 'undefined' && map) {
        renderVesselsOnMap(map);
    }
}

function escapeHtml(str) {
    if (typeof str !== 'string') return String(str ?? '');
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Vessel Details Modal and Editing Functions
 */
function onVesselTypeSelectChange(value) {
    const customInput = document.getElementById('editVesselTypeCustom');
    if (!customInput) return;
    if (value === '__custom__') {
        customInput.style.display = 'block';
        customInput.focus();
    } else {
        customInput.style.display = 'none';
        customInput.value = '';
    }
}

async function openEditVesselModal(vesselId) {
    const modal = document.getElementById('editVesselModal');
    if (!modal) return;

    // Reset fields
    document.getElementById('editVesselId').value = vesselId;
    document.getElementById('editVesselMmsi').value = 'Loading...';
    document.getElementById('editVesselName').value = '';
    document.getElementById('editVesselImo').value = '';
    document.getElementById('editVesselCallsign').value = '';
    
    const typeSelect = document.getElementById('editVesselTypeSelect');
    const customTypeInput = document.getElementById('editVesselTypeCustom');
    typeSelect.value = 'Unspecified';
    customTypeInput.style.display = 'none';
    customTypeInput.value = '';

    // Show modal immediately
    modal.classList.add('active');

    // Find local vessel data first for instant population
    const localVessel = aisVesselsData.find(v => v.vessel_id === vesselId);
    if (localVessel) {
        populateVesselModalFields(localVessel);
    }

    // Fetch freshest data from API
    try {
        const res = await fetch(`/api/ais/vessels/${vesselId}`);
        if (res.ok) {
            const data = await res.json();
            if (data && data.vessel) {
                populateVesselModalFields(data.vessel);
            }
        }
    } catch (err) {
        console.warn('Could not fetch vessel details from API:', err);
    }
}

function populateVesselModalFields(vessel) {
    document.getElementById('editVesselMmsi').value = vessel.mmsi || '';
    document.getElementById('editVesselName').value = vessel.name && !vessel.name.startsWith('MMSI:') ? vessel.name : (vessel.vessel_name || '');
    document.getElementById('editVesselImo').value = vessel.imo && !vessel.imo.startsWith('UNKNOWN-') ? vessel.imo : '';
    document.getElementById('editVesselCallsign').value = vessel.callsign || '';

    const typeSelect = document.getElementById('editVesselTypeSelect');
    const customTypeInput = document.getElementById('editVesselTypeCustom');
    const vesselType = vessel.type || vessel.vessel_type || 'Unspecified';

    const standardTypes = ['Cargo', 'Tanker', 'Passenger', 'Tug', 'Fishing', 'Military', 'Pleasure', 'High Speed Craft', 'Unspecified'];
    const matchedType = standardTypes.find(t => t.toLowerCase() === vesselType.toLowerCase());

    if (matchedType) {
        typeSelect.value = matchedType;
        customTypeInput.style.display = 'none';
        customTypeInput.value = '';
    } else {
        typeSelect.value = '__custom__';
        customTypeInput.style.display = 'block';
        customTypeInput.value = vesselType;
    }
}

function closeEditVesselModal() {
    const modal = document.getElementById('editVesselModal');
    if (modal) {
        modal.classList.remove('active');
    }
}

async function saveVesselDetails(event) {
    if (event) event.preventDefault();

    const vesselId = parseInt(document.getElementById('editVesselId').value, 10);
    if (!vesselId) return;

    const name = document.getElementById('editVesselName').value.trim();
    const imo = document.getElementById('editVesselImo').value.trim();
    const callsign = document.getElementById('editVesselCallsign').value.trim();
    const typeSelectVal = document.getElementById('editVesselTypeSelect').value;
    const customType = document.getElementById('editVesselTypeCustom').value.trim();
    const vesselType = typeSelectVal === '__custom__' ? customType : typeSelectVal;

    const saveBtn = document.getElementById('saveVesselBtn');
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = 'Saving...';
    }

    try {
        const payload = {
            name: name || null,
            vessel_type: vesselType || null,
            callsign: callsign || null,
            imo: imo || null
        };

        const res = await fetch(`/api/ais/vessels/${vesselId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || 'Failed to update vessel');
        }

        const updated = data.vessel;

        // Update in-memory data
        aisVesselsData.forEach(v => {
            if (v.vessel_id === vesselId) {
                v.name = updated.name;
                v.vessel_name = updated.vessel_name;
                v.type = updated.type;
                v.vessel_type = updated.vessel_type;
                v.callsign = updated.callsign;
                v.imo = updated.imo;
            }
        });

        // Re-render map markers
        if (typeof map !== 'undefined' && map) {
            renderVesselsOnMap(map);
        }

        closeEditVesselModal();
        if (typeof showNotification === 'function') {
            showNotification(`Vessel ${updated.name || updated.mmsi} updated successfully.`, 'success');
        }
    } catch (err) {
        console.error('Error updating vessel:', err);
        if (typeof showNotification === 'function') {
            showNotification(`Error: ${err.message}`, 'error');
        } else {
            alert(`Error: ${err.message}`);
        }
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerText = 'Save Changes';
        }
    }
}

