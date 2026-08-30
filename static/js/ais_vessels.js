/**
 * AIS Vessel Visualization on Leaflet Map
 * Features:
 * - Directional ship icons rotated according to true heading
 * - Maritime-standard color coding by vessel type (Cargo, Tanker, Passenger, Tug, Fishing, etc.)
 * - Projected speed vectors based on Speed Over Ground (SOG)
 * - Rich tooltips & interactive inspector popups
 * - Vessel type filtering and toggleable map layer
 */

let aisVesselLayer = null;
let aisVesselsData = [];
let aisLayerEnabled = true;
let activeTypeFilters = new Set(['Cargo', 'Tanker', 'Passenger', 'Tug', 'Fishing', 'Military', 'Pleasure', 'Other']);

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

    // Initial fetch of vessels
    loadAISVessels(mapInstance);

    // Refresh when map stops moving
    mapInstance.on('moveend', debounce(() => {
        if (aisLayerEnabled) {
            loadAISVessels(mapInstance);
        }
    }, 1000));
}

function toggleAISVessels(mapInstance, enable) {
    if (!aisVesselLayer || !mapInstance) return;
    if (enable) {
        if (!mapInstance.hasLayer(aisVesselLayer)) {
            aisVesselLayer.addTo(mapInstance);
        }
        aisLayerEnabled = true;
        localStorage.setItem('ais_vessels_enabled', 'true');
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
    }
}

async function loadAISVessels(mapInstance, bbox = null) {
    if (!mapInstance) return;

    try {
        let url = CONFIG.API_AIS_VESSELS + '?latest_only=true&limit=1000';
        if (bbox) {
            url += `&bbox=${bbox.join(',')}`;
        }

        const res = await fetch(url);
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            aisVesselsData = data.vessels || [];
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

    aisVesselsData.forEach(vessel => {
        const category = classifyVesselType(vessel.type);
        if (!activeTypeFilters.has(category)) return;

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
                <div class="vessel-popup-actions">
                    <button class="btn btn-sm btn-primary" style="padding: 4px 10px; font-size: 0.8rem;" onclick="map.setView([${vessel.latitude}, ${vessel.longitude}], Math.max(map.getZoom(), 14))">
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
