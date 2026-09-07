/**
 * Areas of Interest (AOI) management, automated capture scheduling, and pass predictions.
 */

let aoiMapLayers = [];

function parseUtcDate(val) {
    if (!val) return null;
    if (val instanceof Date) return val;
    let s = String(val).trim();
    if (!s) return null;
    if (!s.endsWith('Z') && !s.includes('+') && !s.includes('-', 10)) {
        s = s.replace(' ', 'T') + 'Z';
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
}

function getDefaultAoiDateRange() {
    const now = new Date();
    const fifteenDaysAgo = new Date(now.getTime() - 15 * 24 * 60 * 60 * 1000);
    const formatDateTime = (d) => {
        const year = d.getUTCFullYear();
        const month = String(d.getUTCMonth() + 1).padStart(2, '0');
        const day = String(d.getUTCDate()).padStart(2, '0');
        const hours = String(d.getUTCHours()).padStart(2, '0');
        const minutes = String(d.getUTCMinutes()).padStart(2, '0');
        return `${year}-${month}-${day}T${hours}:${minutes}`;
    };
    const formatDate = (d) => {
        const year = d.getUTCFullYear();
        const month = String(d.getUTCMonth() + 1).padStart(2, '0');
        const day = String(d.getUTCDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    };
    return {
        startDateTime: formatDateTime(fifteenDaysAgo),
        endDateTime: formatDateTime(now),
        startDate: formatDate(fifteenDaysAgo),
        endDate: formatDate(now)
    };
}
window.getDefaultAoiDateRange = getDefaultAoiDateRange;

function toggleAoiLayer(uiId) {
    const layerObj = aoiMapLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;

    const cb = document.querySelector(`#${uiId} > .layer-info-header input[type="checkbox"]`);
    if (cb && cb.checked) {
        map.addLayer(layerObj.leafletLayer);
    } else {
        map.removeLayer(layerObj.leafletLayer);
    }
    if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
}

function teleportToAoi(aoiId) {
    const item = aoiMapLayers.find(l => l.id === aoiId);
    if (item && item.bounds) {
        if (!map.hasLayer(item.leafletLayer)) {
            map.addLayer(item.leafletLayer);
            const cb = document.querySelector(`#${item.uiId} > .layer-info-header input[type="checkbox"]`);
            if (cb) cb.checked = true;
        }
        map.fitBounds(item.bounds, { padding: [60, 60], maxZoom: 14 });
        if (typeof showNotification === 'function') {
            showNotification(`Teleported to AOI: ${item.name || 'Selected Area'}`, 'info');
        }
    }
}

async function loadAOIs() {
    try {
        const response = await fetch(CONFIG.API_AOI);
        let aois = await response.json();
        aois.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base', numeric: true }));
        const aoiList = document.getElementById('aoiList');
        
        // Clear UI list
        if (aoiList) {
            aoiList.innerHTML = '';
        }
        
        // Remove existing AOI layers and controls
        aoiMapLayers.forEach(l => {
            if (l.leafletLayer && map.hasLayer(l.leafletLayer)) map.removeLayer(l.leafletLayer);
            if (l.tabMarker && map && map.hasLayer(l.tabMarker)) map.removeLayer(l.tabMarker);
            const el = document.getElementById(l.uiId);
            if (el) el.remove();
        });
        aoiMapLayers = [];

        const aoiLayersList = document.getElementById('aoiLayersList');
        if (aoiLayersList) aoiLayersList.innerHTML = '';
        const aoiHeading = document.getElementById('aoiLayersHeading');

        if (aois.length === 0) {
            if (aoiList) {
                aoiList.innerHTML = '<p style="color: #666; font-size: 0.9em;">No Areas of Interest saved yet.</p>';
            }
            if (aoiHeading) aoiHeading.style.display = 'none';
            return;
        }

        if (aoiHeading) aoiHeading.style.display = 'block';

        aois.forEach(aoi => {
            const safeAoiName = escapeHtml(aoi.name || `AOI #${aoi.id}`);
            const defaultDates = getDefaultAoiDateRange();
            if (aoiList) {
                const div = document.createElement('div');
                div.style.cssText = 'border: 1px solid #ddd; padding: 10px; border-radius: 5px; background: #fafafa;';
                
                let nextScanText = '<span style="color: #666; font-size: 0.8em;">Not predicted yet</span>';
                if (aoi.next_scan) {
                    const scanDate = parseUtcDate(aoi.next_scan);
                    const scanStr = scanDate ? scanDate.toLocaleString() : escapeHtml(aoi.next_scan);
                    nextScanText = `<span style="background: #28a745; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em;" title="Next Scan: ${scanStr}">${scanStr}</span>`;
                }

                const isAuto = aoi.auto_capture_enabled ? 'checked' : '';

                div.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                        <strong>${safeAoiName}</strong>
                        ${nextScanText}
                    </div>
                    <div style="margin-top: 5px; margin-bottom: 6px; padding: 6px 8px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; font-size: 0.75rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <span style="font-weight: 600; color: #475569;">SAR Range (UTC):</span>
                            <span style="color: #94a3b8; font-size: 0.7rem;">Default: Last 15d</span>
                        </div>
                        <div class="aoi-range-container">
                            <div class="aoi-range-row">
                                <label for="aoi-drawer-start-${aoi.id}">From:</label>
                                <input type="datetime-local" id="aoi-drawer-start-${aoi.id}" value="${defaultDates.startDateTime}" title="Start date and time in UTC (default 15 days ago)">
                            </div>
                            <div class="aoi-range-row">
                                <label for="aoi-drawer-end-${aoi.id}">To:</label>
                                <input type="datetime-local" id="aoi-drawer-end-${aoi.id}" value="${defaultDates.endDateTime}" title="End date and time in UTC (default current time)">
                            </div>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; gap: 4px; flex-wrap: wrap;">
                        <small style="color: #666; font-size: 0.7em;">BBox: [${aoi.bbox.map(n => n.toFixed(2)).join(', ')}]</small>
                        <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                            <button class="btn btn-sm btn-outline-success" style="padding: 2px 6px; font-size: 0.75em;" onclick="triggerAoiScan(${aoi.id})" title="Initiate SAR imagery scan for ${safeAoiName}">🛰️ Scan</button>
                            <button class="btn btn-sm btn-outline-primary" style="padding: 2px 6px; font-size: 0.75em;" onclick="teleportToAoi(${aoi.id})" title="Center map on AOI">⌖ Teleport</button>
                            <button class="btn" style="padding: 3px 6px; font-size: 0.75em;" onclick="predictAOI(${aoi.id})">Predict</button>
                            <button class="btn btn-sm btn-outline-danger" style="padding: 2px 6px; font-size: 0.75em; border: 1px solid #dc3545; color: #dc3545; background: transparent;" onclick="deleteAOI(${aoi.id}, '${safeAoiName}')" title="Delete Area of Interest ${safeAoiName}">🗑️ Delete</button>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px; font-size: 0.8em; color: #444;">
                        <input type="checkbox" id="auto-cap-${aoi.id}" ${isAuto} onchange="toggleAutoCapture(${aoi.id}, this.checked)">
                        <label for="auto-cap-${aoi.id}" style="cursor: pointer;">Auto-Capture on Pass</label>
                    </div>
                `;
                aoiList.appendChild(div);
            }

            const lBounds = L.latLngBounds([[aoi.bbox[1], aoi.bbox[0]], [aoi.bbox[3], aoi.bbox[2]]]);
            const aoiRect = L.rectangle(lBounds, { color: CONFIG.COLOR_AOI_OUTLINE, weight: 2, fill: false, interactive: true })
                .bindPopup(() => getAoiTabPopupContent(aoi.id), { className: 'sar-tab-popup aoi-tab-popup', minWidth: 270 });
            const tabMarker = L.circleMarker(lBounds.getNorthWest(), { radius: 6, opacity: 0, fillOpacity: 0, interactive: true })
                .bindTooltip(`<strong>${safeAoiName}</strong><br><span style="font-size:0.75rem;color:#86efac;font-weight:600;">Type: Area of Interest</span>`, { permanent: true, className: 'folder-tab-tooltip folder-tab-aoi', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET })
                .bindPopup(() => getAoiTabPopupContent(aoi.id), { className: 'sar-tab-popup aoi-tab-popup', offset: [15, -5], minWidth: 270 });

            tabMarker.on('click', function() {
                this.openPopup();
            });

            const attachTooltipPointer = () => {
                const tt = tabMarker.getTooltip();
                if (tt && tt._container) {
                    tt._container.style.cursor = 'pointer';
                    tt._container.onclick = (ev) => {
                        ev.stopPropagation();
                        tabMarker.openPopup();
                    };
                }
            };
            tabMarker.on('tooltipopen', attachTooltipPointer);
            tabMarker.on('add', () => setTimeout(attachTooltipPointer, 0));
            
            const aoiGroup = L.featureGroup([aoiRect]).addTo(map);
            const layerId = `aoi-layer-${aoi.id}`;
            const parsedScan = parseUtcDate(aoi.next_scan);
            const zuluTime = parsedScan ? parsedScan.toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z' : 'Not predicted yet';
            
            const controlHtml = `
                <div id="${layerId}" class="layer-info aoi-layer-card" style="border-left: 4px solid ${CONFIG.COLOR_AOI_OUTLINE};">
                    <div class="layer-info-header">
                        <input type="checkbox" checked onchange="toggleAoiLayer('${layerId}')" title="Toggle Visibility" style="margin: 0; cursor: pointer;">
                        <span class="aoi-badge">AOI</span>
                        <strong class="aoi-name" title="${safeAoiName}">${safeAoiName}</strong>
                        <button type="button" class="btn-teleport-aoi" onclick="teleportToAoi(${aoi.id})" title="Teleport to ${safeAoiName} on map">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"></circle>
                                <line x1="22" y1="12" x2="18" y2="12"></line>
                                <line x1="6" y1="12" x2="2" y2="12"></line>
                                <line x1="12" y1="6" x2="12" y2="2"></line>
                                <line x1="12" y1="22" x2="12" y2="18"></line>
                            </svg>
                            Teleport
                        </button>
                    </div>
                    <details>
                        <summary>Layer Controls & Info</summary>
                        <div class="aoi-drawer-content">
                            <div><strong>AOI Name:</strong> ${safeAoiName}</div>
                            <div><strong>Next Scan:</strong> ${zuluTime}</div>
                            <div style="margin-top: 6px; margin-bottom: 6px; padding: 6px 8px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; font-size: 0.75rem;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <span style="font-weight: 600; color: #475569;">SAR Range (UTC):</span>
                                    <span style="color: #94a3b8; font-size: 0.7rem;">Default: Last 15d</span>
                                </div>
                                <div class="aoi-range-container">
                                    <div class="aoi-range-row">
                                        <label for="aoi-layer-start-${aoi.id}">From:</label>
                                        <input type="datetime-local" id="aoi-layer-start-${aoi.id}" value="${defaultDates.startDateTime}" title="Start date and time in UTC (default 15 days ago)">
                                    </div>
                                    <div class="aoi-range-row">
                                        <label for="aoi-layer-end-${aoi.id}">To:</label>
                                        <input type="datetime-local" id="aoi-layer-end-${aoi.id}" value="${defaultDates.endDateTime}" title="End date and time in UTC (default current time)">
                                    </div>
                                </div>
                            </div>
                            <button type="button" class="btn btn-sm btn-success" onclick="triggerAoiScan(${aoi.id})" style="background: #16a34a; border-color: #15803d; color: #ffffff;">
                                🛰️ Initiate SAR Scan
                            </button>
                            <button type="button" class="btn btn-sm btn-warning" onclick="forceScanAOI(${aoi.id}, this)" style="background: #f59e0b; border-color: #d97706; color: #ffffff;">
                                ⚡ Force AIS Scan
                            </button>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="predictAOI(${aoi.id})" style="background: #f8fafc; border: 1px solid #cbd5e1; color: #334155;">
                                ⚡ Predict Next Pass
                            </button>
                            <button type="button" class="btn btn-sm btn-danger" onclick="deleteAOI(${aoi.id}, '${safeAoiName}')" style="background: #dc2626; border-color: #b91c1c; color: #ffffff;">
                                🗑️ Delete AOI
                            </button>
                        </div>
                    </details>
                </div>
            `;
            const aoiContainer = document.getElementById('aoiLayersList') || document.getElementById('layersList');
            if (aoiContainer) aoiContainer.insertAdjacentHTML('beforeend', controlHtml);
            aoiMapLayers.push({ leafletLayer: aoiGroup, tabMarker: tabMarker, bounds: lBounds, uiId: layerId, id: aoi.id, name: safeAoiName, aoi: aoi });
        });
        
        if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
    } catch (error) {
        console.error("Error loading AOIs:", error);
        showNotification("Failed to load Areas of Interest from server.", "error");
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
            showNotification(`Auto-capture ${enabled ? 'enabled' : 'disabled'} for AOI`, 'success');
        } else {
            showNotification('Failed to update auto-capture setting', 'error');
        }
    } catch (err) {
        showNotification('Connection error while updating auto-capture', 'error');
    }
}

async function predictAOI(aoiId) {
    try {
        const response = await fetch(`${CONFIG.API_AOI}/${aoiId}/predict`, {
            method: 'POST'
        });
        const result = await response.json();
        
        if (response.ok && result.status === 'success') {
            loadAOIs();
            let msg = "Scan predicted successfully!";
            const firstPred = (result.predictions && result.predictions.length > 0) ? result.predictions[0] : null;
            if (firstPred) {
                const sat = firstPred.satellite || "Sentinel-1";
                const dir = firstPred.orbit_direction ? ` (${firstPred.orbit_direction})` : '';
                const conf = firstPred.confidence_score ? ` [${Math.round(firstPred.confidence_score * 100)}% Conf]` : '';
                const src = firstPred.source ? ` [Source: ${firstPred.source}]` : '';
                const predDate = parseUtcDate(firstPred.time);
                const predStr = predDate ? predDate.toLocaleString() : escapeHtml(firstPred.time);
                msg = `Next Pass: ${sat}${dir}${conf}${src} at ${predStr}`;
            }
            if (result.mission_analysis && result.mission_analysis.total_acquisitions > 0) {
                msg += ` | Hist: ${result.mission_analysis.total_acquisitions} passes (Avg ~${result.mission_analysis.average_revisit_days}d)`;
            }
            showNotification(msg, "success");
        } else {
            showNotification('Error predicting scan: ' + (result.error || 'No upcoming scans found'), "error");
        }
    } catch (error) {
        console.error("Error predicting AOI:", error);
        showNotification("Failed to trigger scan prediction.", "error");
    }
}

function initAoiHandlers() {
    const saveAoiBtn = document.getElementById('saveAoiBtn');
    if (!saveAoiBtn) return;

    saveAoiBtn.addEventListener('click', async () => {
        const nameInput = document.getElementById('aoiName');
        const name = nameInput.value.trim();
        
        if (!name) {
            showNotification("Please enter a name for the Area of Interest.", "warning");
            return;
        }

        if (!currentBbox) {
            showNotification("Please draw a rectangle on the map first.", "warning");
            return;
        }

        const bbox = currentBbox;

        try {
            const response = await fetch(CONFIG.API_AOI, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, bbox: bbox })
            });
            
            const result = await response.json();
            if (result.status === 'success') {
                nameInput.value = '';
                drawnItems.clearLayers();
                currentBbox = null;
                document.getElementById('saveAoiBtn').disabled = true;
                document.getElementById('scanBtn').disabled = true;
                document.getElementById('aoiStatus').innerText = "Draw a rectangle to begin.";
                document.getElementById('status').innerText = "Draw a rectangle to begin.";
                loadAOIs();
                showNotification("Area of Interest saved successfully!", "success");
            } else {
                showNotification('Error saving AOI: ' + (result.error || 'Unknown error'), "error");
            }
        } catch (error) {
            console.error("Error saving AOI:", error);
            showNotification("Failed to save Area of Interest.", "error");
        }
    });
}

async function forceScanAOI(aoiId, btnElement = null) {
    let startPopup = null;
    let originalBtnHtml = null;
    if (btnElement) {
        originalBtnHtml = btnElement.innerHTML;
        btnElement.disabled = true;
        btnElement.innerHTML = '<span class="loading-spinner" style="display:inline-block;width:12px;height:12px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;"></span> Scanning...';
    }

    const layerObj = aoiMapLayers.find(l => l.id === aoiId);
    const safeAoiName = layerObj && (layerObj.name || (layerObj.aoi && layerObj.aoi.name)) ? escapeHtml(layerObj.name || layerObj.aoi.name) : `AOI #${aoiId}`;

    try {
        startPopup = showNotification(`Initiating immediate AIS vessel scan for ${safeAoiName}...`, "info", {
            autoClose: false,
            closable: true,
            title: "⚡ Force AIS Scan Started"
        });

        const res = await fetch(`/api/aoi/${aoiId}/force_ais_scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true })
        });
        const data = await res.json();

        if (startPopup && typeof startPopup.close === 'function') {
            startPopup.close();
        }

        if (res.ok && data.status === 'success') {
            const count = (data.results && data.results.total_inserted) || 0;
            showNotification(`Force AIS scan complete for ${safeAoiName}: ${count} vessel records ingested into database.`, "success", {
                autoClose: false,
                closable: true,
                showAckButton: true,
                ackText: "Dismiss",
                title: "✅ Force Scan Results"
            });
            if (typeof refreshAISVessels === 'function' && typeof map !== 'undefined' && map) {
                refreshAISVessels(map);
            }
        } else {
            showNotification(`Force AIS scan failed for ${safeAoiName}: ${data.error || 'Unknown error'}`, "error", {
                autoClose: false,
                closable: true,
                showAckButton: true,
                ackText: "Dismiss",
                title: "❌ Force Scan Failed"
            });
        }
    } catch (err) {
        console.error("Error running force AIS scan:", err);
        if (startPopup && typeof startPopup.close === 'function') {
            startPopup.close();
        }
        showNotification(`Failed to trigger force AIS scan for ${safeAoiName}: ${err.message || 'Connection error'}`, "error", {
            autoClose: false,
            closable: true,
            showAckButton: true,
            ackText: "Dismiss",
            title: "❌ Force Scan Error"
        });
    } finally {
        if (btnElement && originalBtnHtml !== null) {
            btnElement.disabled = false;
            btnElement.innerHTML = originalBtnHtml;
        }
    }
}
window.forceScanAOI = forceScanAOI;
window.forceScanAOIAIS = forceScanAOI;

function getAoiTabPopupContent(aoiId) {
    const layerObj = aoiMapLayers.find(l => l.id === aoiId);
    if (!layerObj) return '<div style="padding:10px;">AOI not found</div>';
    const aoi = layerObj.aoi || {};
    const safeAoiName = escapeHtml(aoi.name || layerObj.name);
    const parsedScan = parseUtcDate(aoi.next_scan);
    const zuluTime = parsedScan ? parsedScan.toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z' : 'Not predicted yet';
    const bboxStr = aoi.bbox ? aoi.bbox.map(n => Number(n).toFixed(3)).join(', ') : '';
    const defaultDates = getDefaultAoiDateRange();

    return `
        <div class="sar-tab-popup-content aoi-tab-popup-content">
            <div class="sar-tab-popup-header" style="border-left: 4px solid #16a34a; padding-left: 8px; margin-bottom: 6px;">
                <div style="font-size: 0.72rem; font-weight: 700; color: #16a34a; text-transform: uppercase; letter-spacing: 0.5px;">Area of Interest</div>
                <h4 style="margin: 2px 0 0 0; font-size: 0.95rem; font-weight: 700; color: #0f172a;">${safeAoiName}</h4>
            </div>
            <div class="sar-tab-popup-meta" style="margin-bottom: 8px;">
                <div class="meta-row"><span class="meta-lbl">BBox:</span><span class="meta-val">[${bboxStr}]</span></div>
                <div class="meta-row"><span class="meta-lbl">Next Scan:</span><span class="meta-val">${zuluTime}</span></div>
            </div>
            <div style="margin-bottom: 8px; padding: 6px 8px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; font-size: 0.75rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <span style="font-weight: 600; color: #475569;">SAR Range (UTC):</span>
                    <span style="color: #94a3b8; font-size: 0.7rem;">Default: Last 15d</span>
                </div>
                <div class="aoi-range-container">
                    <div class="aoi-range-row">
                        <label for="aoi-popup-start-${aoiId}">From:</label>
                        <input type="datetime-local" id="aoi-popup-start-${aoiId}" value="${defaultDates.startDateTime}" title="Start date and time in UTC (default 15 days ago)">
                    </div>
                    <div class="aoi-range-row">
                        <label for="aoi-popup-end-${aoiId}">To:</label>
                        <input type="datetime-local" id="aoi-popup-end-${aoiId}" value="${defaultDates.endDateTime}" title="End date and time in UTC (default current time)">
                    </div>
                </div>
            </div>
            <div class="sar-tab-popup-actions">
                <button type="button" class="sar-tab-popup-btn-primary" style="background: #16a34a; border-color: #15803d; color: #ffffff;" onclick="triggerAoiScan(${aoiId})">
                    🛰️ Initiate SAR Scan
                </button>
                <button type="button" class="sar-tab-popup-btn-warning" onclick="forceScanAOI(${aoiId}, this)">
                    ⚡ Force AIS Scan
                </button>
                <button type="button" class="sar-tab-popup-btn-secondary" onclick="predictAOI(${aoiId})">
                    ⚡ Predict Next Pass
                </button>
                <button type="button" class="sar-tab-popup-btn-danger" style="background: #dc2626; border-color: #b91c1c; color: #ffffff;" onclick="deleteAOI(${aoiId}, '${safeAoiName}')">
                    🗑️ Delete AOI
                </button>
            </div>
        </div>
    `;
}

async function triggerAoiScan(aoiId, startDate, endDate) {
    if (typeof isScanning !== 'undefined' && isScanning) {
        showNotification("A scan is already in progress.", "warning");
        return;
    }

    const defaultRange = getDefaultAoiDateRange();
    if (!startDate) {
        const popupStart = document.getElementById(`aoi-popup-start-${aoiId}`)?.value;
        const layerStart = document.getElementById(`aoi-layer-start-${aoiId}`)?.value;
        const drawerStart = document.getElementById(`aoi-drawer-start-${aoiId}`)?.value;
        const cardStart = document.getElementById(`aoi-start-date-${aoiId}`)?.value;
        startDate = popupStart || layerStart || drawerStart || cardStart || defaultRange.startDateTime || defaultRange.startDate;
    }
    if (!endDate) {
        const popupEnd = document.getElementById(`aoi-popup-end-${aoiId}`)?.value;
        const layerEnd = document.getElementById(`aoi-layer-end-${aoiId}`)?.value;
        const drawerEnd = document.getElementById(`aoi-drawer-end-${aoiId}`)?.value;
        const cardEnd = document.getElementById(`aoi-end-date-${aoiId}`)?.value;
        endDate = popupEnd || layerEnd || drawerEnd || cardEnd || defaultRange.endDateTime || defaultRange.endDate;
    }

    if (startDate && endDate && startDate > endDate) {
        showNotification("Start date/time cannot be after end date/time.", "warning");
        return;
    }

    const layerObj = aoiMapLayers.find(l => l.id === aoiId);
    const aoi = layerObj ? layerObj.aoi : null;
    const aoiName = aoi ? aoi.name : `AOI #${aoiId}`;
    const bbox = aoi ? aoi.bbox : null;

    if (map) map.closePopup();

    const displayStart = startDate.replace('T', ' ');
    const displayEnd = endDate.replace('T', ' ');
    showNotification(`Initiating SAR scan for ${aoiName} (${displayStart} to ${displayEnd} UTC)...`, "info");

    const statusText = document.getElementById('status');
    const scanBtn = document.getElementById('scanBtn');
    if (statusText) statusText.innerText = `Dispatching SAR acquisition for ${aoiName} (${displayStart} to ${displayEnd} UTC)...`;
    if (scanBtn) scanBtn.disabled = true;
    if (typeof isScanning !== 'undefined') isScanning = true;

    try {
        const urlParams = new URLSearchParams({
            async: 'true',
            start_date: startDate,
            end_date: endDate
        });
        const asyncRes = await fetch(`/api/aoi/${aoiId}/scan?${urlParams.toString()}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                async: true,
                start_date: startDate,
                end_date: endDate
            })
        });

        if (asyncRes.ok) {
            const taskData = await asyncRes.json();
            const taskId = taskData.task_id;
            if (statusText) statusText.innerText = `Processing SAR imagery for ${aoiName} in background...`;
            if (typeof pollScanTask === 'function') {
                pollScanTask(taskId, bbox);
            }
            return;
        }

        const errorData = await asyncRes.json().catch(() => null);
        if (errorData && errorData.error) {
            if (statusText) statusText.innerText = "Draw a rectangle to begin.";
            if (scanBtn) scanBtn.disabled = false;
            if (typeof isScanning !== 'undefined') isScanning = false;
            showNotification(`Scan Error: ${errorData.error}`, "error");
            return;
        }

        // Fallback to synchronous endpoint
        const syncRes = await fetch(`/api/aoi/${aoiId}/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_date: startDate,
                end_date: endDate
            })
        });
        const result = await syncRes.json().catch(() => ({}));
        if (typeof handleScanCompletion === 'function') {
            handleScanCompletion(result);
        }
    } catch (err) {
        console.error("Error triggering AOI scan:", err);
        if (statusText) statusText.innerText = "Draw a rectangle to begin.";
        if (scanBtn) scanBtn.disabled = false;
        if (typeof isScanning !== 'undefined') isScanning = false;
        showNotification(`Connection failed while fetching SAR imagery for ${aoiName}.`, "error");
    }
}

async function deleteAOI(aoiId, aoiName) {
    const displayName = aoiName || `AOI #${aoiId}`;
    if (!confirm(`Are you sure you want to delete Area of Interest "${displayName}"?\nThis will remove the AOI and any associated flypast forecasts and scheduled jobs.`)) {
        return;
    }

    if (map) map.closePopup();

    try {
        const response = await fetch(`${CONFIG.API_AOI}/${aoiId}`, { method: 'DELETE' });
        const data = await response.json().catch(() => ({}));

        if (response.ok && data.status === 'success') {
            // Remove Leaflet layers and tab marker from map
            const idx = aoiMapLayers.findIndex(l => l.id === aoiId);
            if (idx !== -1) {
                const layerObj = aoiMapLayers[idx];
                if (map) {
                    if (layerObj.leafletLayer && map.hasLayer(layerObj.leafletLayer)) {
                        map.removeLayer(layerObj.leafletLayer);
                    }
                    if (layerObj.tabMarker && map.hasLayer(layerObj.tabMarker)) {
                        map.removeLayer(layerObj.tabMarker);
                    }
                }
                aoiMapLayers.splice(idx, 1);
            }

            // Remove layer card from sidebar
            const layerEl = document.getElementById(`aoi-layer-${aoiId}`);
            if (layerEl) layerEl.remove();

            // Refresh the AOIs list
            loadAOIs();

            // Refresh schedule dropdown if available
            if (typeof loadAoisDropdown === 'function') {
                loadAoisDropdown();
            }

            if (typeof updateTabsVisibility === 'function') {
                updateTabsVisibility();
            }

            showNotification(`Area of Interest "${displayName}" deleted successfully.`, "success");
        } else {
            showNotification(data.error || 'Failed to delete Area of Interest.', "error");
        }
    } catch (err) {
        console.error("Error deleting AOI:", err);
        showNotification("Connection error while deleting Area of Interest.", "error");
    }
}
window.deleteAOI = deleteAOI;


