/**
 * Temporal AIS Time-Scrubber UI and Kinematic Track Interpolator for SAR Passes.
 * Allows interactive scrubbing across time [-60m, +60m] around the satellite overpass,
 * rendering animated vessel positions, covariance ellipses, route corridors, and SAR detection overlays.
 */

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const temporalScrubberState = {
    folderName: null,
    scanTimestampMs: null,
    windowSeconds: 3600, // +/- 60 minutes
    currentDeltaSec: 0,   // offset from scan pass in seconds
    isPlaying: false,
    playbackSpeed: 5,     // 1x, 5x, 15x, 60x
    playbackInterval: null,
    vessels: [],
    detections: [],
    mapInstance: null,
    trackLayerGroup: null,
    vesselMarkers: new Map(),
    detectionMarkers: [],
    bufferCorridorLayers: [],
    showTracks: true,
    showUncertainty: true,
    showRouteBuffer: true,
    routeBufferMeters: 500,
    taggedCount: 0,
};

function updateTaggedBadge(count) {
    const badge = document.getElementById('scrubberTaggedBadge');
    if (!badge) return;
    if (count > 0) {
        badge.innerText = `${count} Tagged`;
        badge.style.display = 'inline-block';
    } else {
        badge.innerText = '0 Tagged';
        badge.style.display = 'none';
    }
}

/**
 * Initialize and open the Temporal AIS Scrubber for a SAR pass.
 */
async function openTemporalAisScrubber(folderName) {
    if (!folderName) {
        if (typeof showNotification === 'function') {
            showNotification('No active SAR scan specified for temporal AIS scrubbing', 'warning');
        }
        return;
    }

    const scrubberDock = document.getElementById('temporalScrubberDock');
    if (!scrubberDock) return;

    scrubberDock.style.display = 'flex';
    temporalScrubberState.folderName = folderName;
    document.getElementById('scrubberScanName').innerText = folderName;
    document.getElementById('scrubberStatusMsg').innerText = 'Fetching kinematic AIS tracks and corridor tagging...';

    // Read user-defined route buffer settings if present
    const bufferInput = document.getElementById('scrubberRouteBufferMeters');
    if (bufferInput) {
        const val = parseFloat(bufferInput.value);
        if (!isNaN(val) && val > 0) {
            temporalScrubberState.routeBufferMeters = val;
        }
    }
    const bufferToggle = document.getElementById('scrubberRouteBufferToggle');
    if (bufferToggle) {
        temporalScrubberState.showRouteBuffer = bufferToggle.checked;
    }

    // Ensure map instance and layer group
    if (typeof map !== 'undefined' && map) {
        temporalScrubberState.mapInstance = map;
        if (!temporalScrubberState.trackLayerGroup) {
            temporalScrubberState.trackLayerGroup = L.layerGroup().addTo(map);
        } else if (!map.hasLayer(temporalScrubberState.trackLayerGroup)) {
            temporalScrubberState.trackLayerGroup.addTo(map);
        }
    }

    try {
        const url = `/api/scan/${encodeURIComponent(folderName)}/ais_tracks?window_hours=2.0&buffer_meters=${encodeURIComponent(temporalScrubberState.routeBufferMeters)}`;
        const res = await fetch(url);
        if (!res.ok) {
            throw new Error(`HTTP error ${res.status}`);
        }
        const data = await res.json();

        const scanDt = new Date(data.scan_timestamp);
        temporalScrubberState.scanTimestampMs = scanDt.getTime();
        temporalScrubberState.vessels = data.vessels || [];
        temporalScrubberState.detections = data.detections || [];
        temporalScrubberState.taggedCount = data.tagged_detections_count || 0;
        temporalScrubberState.currentDeltaSec = 0; // default to exact SAR pass epoch

        document.getElementById('scrubberStatusMsg').innerText = 
            `${temporalScrubberState.vessels.length} vessel tracks • ${temporalScrubberState.detections.length} SAR detections • ${temporalScrubberState.taggedCount} tagged to route`;
        updateTaggedBadge(temporalScrubberState.taggedCount);

        // Reset scrubber slider to center (0 delta)
        const slider = document.getElementById('temporalScrubberSlider');
        if (slider) {
            slider.min = -temporalScrubberState.windowSeconds;
            slider.max = temporalScrubberState.windowSeconds;
            slider.value = 0;
        }

        renderSarDetectionsOnMap();
        renderRouteCorridors();
        updateTemporalScrubberUI();
        renderInterpolatedVesselsAtCurrentTime();

        if (typeof showNotification === 'function') {
            const tagMsg = temporalScrubberState.taggedCount > 0 ? ` (${temporalScrubberState.taggedCount} detections tagged to route)` : '';
            showNotification(`Loaded ${temporalScrubberState.vessels.length} temporal AIS tracks${tagMsg}`, 'info');
        }
    } catch (err) {
        console.error('Failed to load temporal AIS tracks:', err);
        document.getElementById('scrubberStatusMsg').innerText = 'Failed to load AIS tracks.';
        if (typeof showNotification === 'function') {
            showNotification('Failed to load temporal AIS tracks for this pass', 'error');
        }
    }
}

/**
 * Close the Temporal AIS Scrubber and clear temporal markers.
 */
function closeTemporalAisScrubber() {
    stopTemporalPlayback();
    const scrubberDock = document.getElementById('temporalScrubberDock');
    if (scrubberDock) {
        scrubberDock.style.display = 'none';
    }
    clearTemporalMapLayers();
    temporalScrubberState.folderName = null;
}

/**
 * Clear Leaflet layers created by the temporal scrubber.
 */
function clearTemporalMapLayers() {
    if (temporalScrubberState.trackLayerGroup) {
        temporalScrubberState.trackLayerGroup.clearLayers();
    }
    temporalScrubberState.vesselMarkers.clear();
    temporalScrubberState.detectionMarkers = [];
    temporalScrubberState.bufferCorridorLayers = [];
}

/**
 * Render fixed SAR detection centroids on the map, with route tagging indicators.
 */
function renderSarDetectionsOnMap() {
    if (!temporalScrubberState.trackLayerGroup || !temporalScrubberState.detections) return;

    temporalScrubberState.detectionMarkers.forEach(m => {
        if (temporalScrubberState.trackLayerGroup.hasLayer(m)) {
            temporalScrubberState.trackLayerGroup.removeLayer(m);
        }
    });
    temporalScrubberState.detectionMarkers = [];

    temporalScrubberState.detections.forEach((det, idx) => {
        const lat = det.lat !== undefined ? det.lat : det.latitude;
        const lon = det.lon !== undefined ? det.lon : det.longitude;
        if (lat === undefined || lon === undefined) return;

        const isDark = det.is_dark_vessel || det.correlation_status === 'uncorrelated';
        const isTagged = Boolean(det.tagged_to_vessel && det.route_tagged_vessel);
        const tag = det.route_tagged_vessel;

        let color = isDark ? '#ef4444' : '#10b981';
        let fillColor = color;
        let radius = 7;
        let weight = 2;
        let dashArray = '2, 3';

        if (isTagged) {
            color = '#0284c7';
            fillColor = '#38bdf8';
            radius = 8;
            weight = 3;
            dashArray = null;
        }

        const marker = L.circleMarker([lat, lon], {
            radius: radius,
            color: color,
            weight: weight,
            fillColor: fillColor,
            fillOpacity: isTagged ? 0.65 : 0.35,
            dashArray: dashArray,
        });

        let taggedHtml = '';
        if (isTagged && tag) {
            taggedHtml = `
                <div style="margin-top: 6px; padding: 6px 8px; background: #f0fdf4; border: 1px solid #86efac; border-radius: 4px; font-size: 0.76rem; color: #166534;">
                    <div style="font-weight: 700; display: flex; align-items: center; gap: 4px; margin-bottom: 2px;">
                        🏷️ Tagged to Vessel Route
                    </div>
                    <div><strong>Vessel:</strong> ${escapeHtml(tag.vessel_name)} (${escapeHtml(tag.mmsi || 'N/A')})</div>
                    <div><strong>Type:</strong> ${escapeHtml(tag.vessel_type || 'Vessel')}</div>
                    <div><strong>Route Offset:</strong> ${tag.distance_to_route_m} m (Buffer: ${tag.buffer_meters} m)</div>
                    <div style="margin-top: 2px; font-size: 0.72rem; color: #15803d;">
                        ${tag.is_predicted_segment ? '🔮 On Predicted Trajectory Corridor' : '⏱️ On Traced AIS Track Corridor'}
                    </div>
                </div>
            `;
        }

        const label = `
            <div style="font-family: sans-serif; font-size: 0.82rem; min-width: 175px;">
                <div style="font-weight: 700; color: ${color}; margin-bottom: 4px;">
                    🛰️ SAR Detection #${idx + 1}
                </div>
                <div><strong>Status:</strong> ${det.correlation_status || (isDark ? 'Dark Vessel' : 'Correlated')}</div>
                <div><strong>Length:</strong> ${det.length ? det.length + ' m' : 'N/A'}</div>
                <div><strong>Confidence:</strong> ${(det.confidence ? (det.confidence * 100).toFixed(1) : '90.0')}%</div>
                ${taggedHtml}
                <div style="margin-top: 6px; font-size: 0.74rem; color: #64748b;">
                    Fixed at SAR Epoch (T=0)
                </div>
            </div>
        `;
        marker.bindPopup(label);
        marker.addTo(temporalScrubberState.trackLayerGroup);
        temporalScrubberState.detectionMarkers.push(marker);
    });
}


/**
 * Interpolate all vessels at current temporal scrubber delta time and update markers.
 */
function renderInterpolatedVesselsAtCurrentTime() {
    if (!temporalScrubberState.trackLayerGroup || !temporalScrubberState.scanTimestampMs) return;

    const currentEpochMs = temporalScrubberState.scanTimestampMs + (temporalScrubberState.currentDeltaSec * 1000);

    temporalScrubberState.vessels.forEach(vessel => {
        const pts = vessel.points || [];
        if (pts.length === 0) return;

        // Kinematic interpolation along track
        const interpolated = interpolateTrackAtEpoch(pts, currentEpochMs);
        if (!interpolated) {
            // Vessel has no coverage within window; remove marker if exists
            if (temporalScrubberState.vesselMarkers.has(vessel.mmsi)) {
                const existing = temporalScrubberState.vesselMarkers.get(vessel.mmsi);
                temporalScrubberState.trackLayerGroup.removeLayer(existing.marker);
                if (existing.ellipse) temporalScrubberState.trackLayerGroup.removeLayer(existing.ellipse);
                if (existing.trail) temporalScrubberState.trackLayerGroup.removeLayer(existing.trail);
                temporalScrubberState.vesselMarkers.delete(vessel.mmsi);
            }
            return;
        }

        const { lat, lon, heading, speed, uncertaintyMeters, nearestFixDeltaSec } = interpolated;

        let entry = temporalScrubberState.vesselMarkers.get(vessel.mmsi);
        if (!entry) {
            // Create custom SVG vessel marker
            const iconHtml = createVesselSvgIcon(heading || 0, vessel.ship_type);
            const icon = L.divIcon({
                html: iconHtml,
                className: 'temporal-vessel-icon-wrapper',
                iconSize: [26, 26],
                iconAnchor: [13, 13],
            });

            const marker = L.marker([lat, lon], { icon: icon });

            // Uncertainty covariance circle
            const ellipse = L.circle([lat, lon], {
                radius: uncertaintyMeters,
                color: '#3b82f6',
                weight: 1,
                fillColor: '#60a5fa',
                fillOpacity: 0.15,
                dashArray: '3, 4',
            });

            // Historic track trail polyline
            const trailCoords = pts.map(p => [p.latitude, p.longitude]);
            const trail = L.polyline(trailCoords, {
                color: '#0284c7',
                weight: 1.5,
                opacity: 0.45,
                dashArray: '4, 4',
            });

            if (temporalScrubberState.showTracks) trail.addTo(temporalScrubberState.trackLayerGroup);
            if (temporalScrubberState.showUncertainty) ellipse.addTo(temporalScrubberState.trackLayerGroup);
            marker.addTo(temporalScrubberState.trackLayerGroup);

            entry = { marker, ellipse, trail, iconEl: null };
            temporalScrubberState.vesselMarkers.set(vessel.mmsi, entry);
        } else {
            // Update marker position and heading rotation
            entry.marker.setLatLng([lat, lon]);
            const iconEl = entry.marker.getElement();
            if (iconEl) {
                const svg = iconEl.querySelector('svg');
                if (svg) {
                    svg.style.transform = `rotate(${heading || 0}deg)`;
                }
            }

            // Update uncertainty ellipse
            if (entry.ellipse) {
                entry.ellipse.setLatLng([lat, lon]);
                entry.ellipse.setRadius(uncertaintyMeters);
                // Adjust opacity based on uncertainty
                const opacity = Math.max(0.08, Math.min(0.28, 0.35 - (nearestFixDeltaSec / 7200)));
                entry.ellipse.setStyle({ fillOpacity: opacity });
            }
        }

        // Popup with live kinematics
        const fixDeltaMin = (nearestFixDeltaSec / 60).toFixed(1);
        const popupHtml = `
            <div style="font-family: sans-serif; font-size: 0.82rem; min-width: 175px;">
                <div style="font-weight: 700; color: #1e40af; margin-bottom: 3px;">
                    🚢 ${escapeHtml(vessel.name || 'MMSI: ' + vessel.mmsi)}
                </div>
                <div><strong>MMSI:</strong> ${vessel.mmsi}</div>
                <div><strong>Type:</strong> ${escapeHtml(vessel.ship_type || 'Vessel')}</div>
                <div><strong>Speed:</strong> ${speed.toFixed(1)} kts</div>
                <div><strong>Heading:</strong> ${Math.round(heading)}°</div>
                <div><strong>Pos. Uncertainty:</strong> ±${Math.round(uncertaintyMeters)} m</div>
                <div style="font-size: 0.74rem; color: #64748b; margin-top: 4px;">
                    Nearest fix: ${fixDeltaMin}m away
                </div>
            </div>
        `;
        entry.marker.bindPopup(popupHtml);
    });
}

/**
 * Interpolates vessel track at a given epoch timestamp (ms).
 */
function interpolateTrackAtEpoch(points, epochMs) {
    if (!points || points.length === 0) return null;

    // Convert timestamps to ms
    const parsedPts = points.map(p => ({
        ...p,
        timeMs: new Date(p.timestamp).getTime(),
    })).filter(p => !isNaN(p.timeMs));

    if (parsedPts.length === 0) return null;
    parsedPts.sort((a, b) => a.timeMs - b.timeMs);

    // If out of range by more than 2 hours, do not extrapolate
    const maxExtrapolateMs = 7200 * 1000;
    const firstTime = parsedPts[0].timeMs;
    const lastTime = parsedPts[parsedPts.length - 1].timeMs;

    if (epochMs < firstTime - maxExtrapolateMs || epochMs > lastTime + maxExtrapolateMs) {
        return null;
    }

    // Find bounding segments
    let p1 = null;
    let p2 = null;

    if (epochMs <= firstTime) {
        p1 = parsedPts[0];
        p2 = parsedPts.length > 1 ? parsedPts[1] : parsedPts[0];
    } else if (epochMs >= lastTime) {
        p1 = parsedPts.length > 1 ? parsedPts[parsedPts.length - 2] : parsedPts[0];
        p2 = parsedPts[parsedPts.length - 1];
    } else {
        for (let i = 0; i < parsedPts.length - 1; i++) {
            if (parsedPts[i].timeMs <= epochMs && epochMs <= parsedPts[i + 1].timeMs) {
                p1 = parsedPts[i];
                p2 = parsedPts[i + 1];
                break;
            }
        }
    }

    if (!p1 || !p2) return null;

    const dt = (p2.timeMs - p1.timeMs) / 1000;
    const tRatio = dt > 0 ? (epochMs - p1.timeMs) / (p2.timeMs - p1.timeMs) : 0;
    const clampedRatio = Math.max(-0.5, Math.min(1.5, tRatio));

    // Linear/Hermite positional interpolation
    const lat = p1.latitude + (p2.latitude - p1.latitude) * clampedRatio;
    const lon = p1.longitude + (p2.longitude - p1.longitude) * clampedRatio;

    // Heading interpolation handling 360 wrap
    let h1 = p1.heading || 0;
    let h2 = p2.heading || h1;
    let diff = ((h2 - h1 + 540) % 360) - 180;
    let heading = (h1 + diff * Math.max(0, Math.min(1, clampedRatio))) % 360;
    if (heading < 0) heading += 360;

    const speed = p1.speed + ((p2.speed || p1.speed) - p1.speed) * Math.max(0, Math.min(1, clampedRatio));

    // Covariance uncertainty growth
    const nearestFixDeltaSec = Math.min(
        Math.abs(epochMs - p1.timeMs) / 1000,
        Math.abs(epochMs - p2.timeMs) / 1000
    );
    const speedMs = Math.max(1.0, speed * 0.5144);
    // Uncertainty grows non-linearly with time delta from nearest fix
    const uncertaintyMeters = Math.min(1500, Math.max(25, 15 + (speedMs * nearestFixDeltaSec * 0.08) + Math.pow(nearestFixDeltaSec / 60, 1.4)));

    return {
        lat,
        lon,
        heading,
        speed,
        uncertaintyMeters,
        nearestFixDeltaSec,
    };
}

/**
 * Returns custom vessel SVG icon rotated to heading.
 */
function createVesselSvgIcon(heading, shipType) {
    let color = '#2563eb';
    if (shipType && shipType.toLowerCase().includes('tanker')) color = '#ea580c';
    else if (shipType && shipType.toLowerCase().includes('cargo')) color = '#16a34a';
    else if (shipType && shipType.toLowerCase().includes('fish')) color = '#0284c7';

    return `
        <svg width="26" height="26" viewBox="0 0 26 26" style="transform: rotate(${heading}deg); transform-origin: 13px 13px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.35)); cursor: pointer;">
            <polygon points="13,2 21,21 13,17 5,21" fill="${color}" stroke="#ffffff" stroke-width="1.8" />
            <circle cx="13" cy="12" r="2.2" fill="#ffffff" />
        </svg>
    `;
}

/**
 * Updates UI labels and readouts for the scrubber dock.
 */
function updateTemporalScrubberUI() {
    const deltaSec = temporalScrubberState.currentDeltaSec;
    const deltaDisplay = document.getElementById('scrubberDeltaDisplay');
    const utcDisplay = document.getElementById('scrubberUtcDisplay');

    if (deltaDisplay) {
        const sign = deltaSec > 0 ? '+' : deltaSec < 0 ? '-' : '=';
        const absSec = Math.abs(deltaSec);
        const mins = Math.floor(absSec / 60);
        const secs = absSec % 60;
        const timeStr = `T ${sign} ${mins.toString().padStart(2, '0')}m ${secs.toString().padStart(2, '0')}s`;

        if (deltaSec === 0) {
            deltaDisplay.innerHTML = `<span style="color: #10b981; font-weight: 700;">T = 00:00 [SAR PASS EPOCH]</span>`;
        } else {
            deltaDisplay.innerText = timeStr;
        }
    }

    if (utcDisplay && temporalScrubberState.scanTimestampMs) {
        const currentMs = temporalScrubberState.scanTimestampMs + (deltaSec * 1000);
        const dt = new Date(currentMs);
        utcDisplay.innerText = dt.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
    }

    // Toggle detection markers pulse effect if close to pass epoch
    const isAtPass = Math.abs(deltaSec) <= 45;
    temporalScrubberState.detectionMarkers.forEach(m => {
        if (isAtPass) {
            m.setStyle({ weight: 3, fillOpacity: 0.6 });
        } else {
            m.setStyle({ weight: 1.5, fillOpacity: 0.25 });
        }
    });
}

/**
 * Handle user dragging or clicking the timeline scrubber.
 */
function onTemporalScrubberInput(deltaSec) {
    temporalScrubberState.currentDeltaSec = parseInt(deltaSec, 10);
    updateTemporalScrubberUI();
    renderInterpolatedVesselsAtCurrentTime();
}

/**
 * Step temporal scrubber forward or backward.
 */
function stepTemporalScrubber(stepSec) {
    let nextVal = temporalScrubberState.currentDeltaSec + stepSec;
    nextVal = Math.max(-temporalScrubberState.windowSeconds, Math.min(temporalScrubberState.windowSeconds, nextVal));
    temporalScrubberState.currentDeltaSec = nextVal;

    const slider = document.getElementById('temporalScrubberSlider');
    if (slider) slider.value = nextVal;

    updateTemporalScrubberUI();
    renderInterpolatedVesselsAtCurrentTime();
}

/**
 * Jump scrubber directly to SAR pass epoch (T=0).
 */
function syncScrubberToSarPass() {
    temporalScrubberState.currentDeltaSec = 0;
    const slider = document.getElementById('temporalScrubberSlider');
    if (slider) slider.value = 0;

    updateTemporalScrubberUI();
    renderInterpolatedVesselsAtCurrentTime();
}

/**
 * Play / Pause temporal playback.
 */
function toggleTemporalPlayback() {
    if (temporalScrubberState.isPlaying) {
        stopTemporalPlayback();
    } else {
        startTemporalPlayback();
    }
}

function startTemporalPlayback() {
    temporalScrubberState.isPlaying = true;
    updatePlaybackButtonUI(true);

    if (temporalScrubberState.currentDeltaSec >= temporalScrubberState.windowSeconds - 10) {
        temporalScrubberState.currentDeltaSec = -temporalScrubberState.windowSeconds;
    }

    if (temporalScrubberState.playbackInterval) clearInterval(temporalScrubberState.playbackInterval);

    // Update tick every 200ms
    temporalScrubberState.playbackInterval = setInterval(() => {
        const step = 2 * temporalScrubberState.playbackSpeed;
        let nextVal = temporalScrubberState.currentDeltaSec + step;

        if (nextVal >= temporalScrubberState.windowSeconds) {
            nextVal = temporalScrubberState.windowSeconds;
            temporalScrubberState.currentDeltaSec = nextVal;
            const slider = document.getElementById('temporalScrubberSlider');
            if (slider) slider.value = nextVal;
            updateTemporalScrubberUI();
            renderInterpolatedVesselsAtCurrentTime();
            stopTemporalPlayback();
            return;
        }

        temporalScrubberState.currentDeltaSec = nextVal;
        const slider = document.getElementById('temporalScrubberSlider');
        if (slider) slider.value = nextVal;
        updateTemporalScrubberUI();
        renderInterpolatedVesselsAtCurrentTime();
    }, 200);
}

function stopTemporalPlayback() {
    temporalScrubberState.isPlaying = false;
    if (temporalScrubberState.playbackInterval) {
        clearInterval(temporalScrubberState.playbackInterval);
        temporalScrubberState.playbackInterval = null;
    }
    updatePlaybackButtonUI(false);
}

function updatePlaybackButtonUI(playing) {
    const btn = document.getElementById('scrubberPlayBtn');
    if (btn) {
        btn.innerText = playing ? '⏸' : '▶';
        btn.title = playing ? 'Pause Scrubber' : 'Play Timeline';
    }
}

function setTemporalPlaybackSpeed(speedVal) {
    temporalScrubberState.playbackSpeed = parseFloat(speedVal) || 5;
    if (temporalScrubberState.isPlaying) {
        startTemporalPlayback(); // restart interval with new speed
    }
}

function toggleTemporalLayerTracks(enable) {
    temporalScrubberState.showTracks = enable;
    temporalScrubberState.vesselMarkers.forEach(entry => {
        if (entry.trail && temporalScrubberState.trackLayerGroup) {
            if (enable) {
                if (!temporalScrubberState.trackLayerGroup.hasLayer(entry.trail)) {
                    entry.trail.addTo(temporalScrubberState.trackLayerGroup);
                }
            } else {
                if (temporalScrubberState.trackLayerGroup.hasLayer(entry.trail)) {
                    temporalScrubberState.trackLayerGroup.removeLayer(entry.trail);
                }
            }
        }
    });
}

function toggleTemporalLayerUncertainty(enable) {
    temporalScrubberState.showUncertainty = enable;
    temporalScrubberState.vesselMarkers.forEach(entry => {
        if (entry.ellipse && temporalScrubberState.trackLayerGroup) {
            if (enable) {
                if (!temporalScrubberState.trackLayerGroup.hasLayer(entry.ellipse)) {
                    entry.ellipse.addTo(temporalScrubberState.trackLayerGroup);
                }
            } else {
                if (temporalScrubberState.trackLayerGroup.hasLayer(entry.ellipse)) {
                    temporalScrubberState.trackLayerGroup.removeLayer(entry.ellipse);
                }
            }
        }
    });
}

/**
 * Render traced and predicted vessel route corridors with user-defined buffer zone.
 */
function renderRouteCorridors() {
    if (!temporalScrubberState.trackLayerGroup) return;

    // Clear existing corridor layers
    temporalScrubberState.bufferCorridorLayers.forEach(l => {
        if (temporalScrubberState.trackLayerGroup.hasLayer(l)) {
            temporalScrubberState.trackLayerGroup.removeLayer(l);
        }
    });
    temporalScrubberState.bufferCorridorLayers = [];

    if (!temporalScrubberState.showRouteBuffer) return;

    const bufferMeters = temporalScrubberState.routeBufferMeters;

    temporalScrubberState.vessels.forEach(vessel => {
        const route = vessel.route || vessel.points || [];
        if (!route || route.length === 0) return;

        const coords = [];
        const tracedCoords = [];
        const predCoords = [];

        route.forEach(p => {
            const lat = p.latitude !== undefined ? p.latitude : p.lat;
            const lon = p.longitude !== undefined ? p.longitude : p.lon;
            if (lat !== undefined && lon !== undefined) {
                const pt = [parseFloat(lat), parseFloat(lon)];
                coords.push(pt);
                if (p.is_predicted) {
                    predCoords.push(pt);
                } else {
                    tracedCoords.push(pt);
                }
            }
        });

        if (coords.length < 2) return;

        // Route corridor buffer band (semi-transparent corridor)
        const corridor = L.polyline(coords, {
            color: '#38bdf8',
            weight: 16,
            opacity: 0.22,
            lineCap: 'round',
            lineJoin: 'round',
            dashArray: null,
            interactive: false,
        });
        corridor.addTo(temporalScrubberState.trackLayerGroup);
        temporalScrubberState.bufferCorridorLayers.push(corridor);

        // Circular buffer zones at key waypoints
        coords.forEach((coord, i) => {
            if (i === 0 || i === coords.length - 1 || i % 3 === 0) {
                const bufCircle = L.circle(coord, {
                    radius: bufferMeters,
                    color: '#38bdf8',
                    weight: 1,
                    opacity: 0.35,
                    fillColor: '#38bdf8',
                    fillOpacity: 0.05,
                    dashArray: '3, 4',
                    interactive: false,
                });
                bufCircle.addTo(temporalScrubberState.trackLayerGroup);
                temporalScrubberState.bufferCorridorLayers.push(bufCircle);
            }
        });

        // Traced route polyline (solid line)
        if (tracedCoords.length >= 2) {
            const tracedLine = L.polyline(tracedCoords, {
                color: '#0284c7',
                weight: 2.5,
                opacity: 0.75,
            });
            tracedLine.bindTooltip(`Route: ${escapeHtml(vessel.name || vessel.mmsi)} (Traced)`, { sticky: true });
            tracedLine.addTo(temporalScrubberState.trackLayerGroup);
            temporalScrubberState.bufferCorridorLayers.push(tracedLine);
        }

        // Predicted route polyline (dashed line)
        if (predCoords.length >= 1) {
            const lastTraced = tracedCoords.length > 0 ? tracedCoords[tracedCoords.length - 1] : null;
            const predWithAnchor = lastTraced ? [lastTraced, ...predCoords] : predCoords;
            if (predWithAnchor.length >= 2) {
                const predLine = L.polyline(predWithAnchor, {
                    color: '#06b6d4',
                    weight: 2.5,
                    opacity: 0.85,
                    dashArray: '6, 6',
                });
                predLine.bindTooltip(`Route: ${escapeHtml(vessel.name || vessel.mmsi)} (Predicted)`, { sticky: true });
                predLine.addTo(temporalScrubberState.trackLayerGroup);
                temporalScrubberState.bufferCorridorLayers.push(predLine);
            }
        }

        // Dotted tie-lines to tagged detections
        const taggedDets = vessel.tagged_detections || [];
        taggedDets.forEach(td => {
            if (td.closest_point && td.lat !== undefined && td.lon !== undefined) {
                const tieLine = L.polyline([[td.lat, td.lon], td.closest_point], {
                    color: '#0284c7',
                    weight: 1.5,
                    dashArray: '2, 4',
                    opacity: 0.85,
                });
                tieLine.bindTooltip(`Tagged to ${escapeHtml(vessel.name || vessel.mmsi)}: ${td.distance_to_route_m}m to route`, { sticky: true });
                tieLine.addTo(temporalScrubberState.trackLayerGroup);
                temporalScrubberState.bufferCorridorLayers.push(tieLine);
            }
        });
    });
}

/**
 * Toggle rendering of route buffer corridors and tie-lines.
 */
function toggleTemporalRouteBuffer(enable) {
    temporalScrubberState.showRouteBuffer = Boolean(enable);
    renderRouteCorridors();
    renderSarDetectionsOnMap();
}

/**
 * Update the user-defined route buffer distance in meters and re-tag detections.
 */
async function updateTemporalRouteBufferDistance(distanceMeters) {
    const parsed = Math.max(10, Math.min(50000, parseFloat(distanceMeters) || 500));
    temporalScrubberState.routeBufferMeters = parsed;

    const input = document.getElementById('scrubberRouteBufferMeters');
    if (input) input.value = parsed;

    if (!temporalScrubberState.folderName) return;

    try {
        const res = await fetch(`/api/scan/${encodeURIComponent(temporalScrubberState.folderName)}/tag_route`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                buffer_meters: parsed,
                vessels: temporalScrubberState.vessels,
                detections: temporalScrubberState.detections,
            }),
        });

        if (res.ok) {
            const data = await res.json();
            temporalScrubberState.vessels = data.vessels || temporalScrubberState.vessels;
            temporalScrubberState.detections = data.detections || temporalScrubberState.detections;
            temporalScrubberState.taggedCount = data.tagged_detections_count || 0;
        } else {
            const fallbackRes = await fetch(`/api/scan/${encodeURIComponent(temporalScrubberState.folderName)}/ais_tracks?window_hours=2.0&buffer_meters=${parsed}`);
            if (fallbackRes.ok) {
                const data = await fallbackRes.json();
                temporalScrubberState.vessels = data.vessels || [];
                temporalScrubberState.detections = data.detections || [];
                temporalScrubberState.taggedCount = data.tagged_detections_count || 0;
            }
        }

        updateTaggedBadge(temporalScrubberState.taggedCount);
        renderSarDetectionsOnMap();
        renderRouteCorridors();

        const msg = `${temporalScrubberState.vessels.length} vessel tracks • ${temporalScrubberState.detections.length} SAR detections • ${temporalScrubberState.taggedCount} tagged to route (Buffer: ${parsed}m)`;
        const statusEl = document.getElementById('scrubberStatusMsg');
        if (statusEl) statusEl.innerText = msg;

        if (typeof showNotification === 'function') {
            showNotification(`Updated corridor buffer to ${parsed}m (${temporalScrubberState.taggedCount} detections tagged)`, 'info');
        }
    } catch (err) {
        console.error('Failed to update route buffer distance:', err);
    }
}

