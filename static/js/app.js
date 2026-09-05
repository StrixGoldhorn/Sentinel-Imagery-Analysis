/**
 * Main application initialization and event dispatching.
 */

let abortController = null;

function initSearch() {
    const searchInput = document.getElementById('locationSearch');
    const resultsBox = document.getElementById('searchResults');
    if (!searchInput || !resultsBox) return;

    const fetchSuggestions = debounce(async (query) => {
        if (query.length < CONFIG.SEARCH_MIN_CHARS) {
            resultsBox.style.display = 'none';
            return;
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();

        try {
            const response = await fetch(`${CONFIG.NOMINATIM_SEARCH_URL}?format=json&q=${encodeURIComponent(query)}&limit=${CONFIG.SEARCH_RESULT_LIMIT}&accept-language=en`, { signal: abortController.signal });
            const data = await response.json();
            
            resultsBox.innerHTML = '';
            if (data.length > 0) {
                data.forEach(item => {
                    const div = document.createElement('div');
                    div.textContent = item.display_name;
                    div.onclick = () => {
                        map.setView([parseFloat(item.lat), parseFloat(item.lon)], CONFIG.SEARCH_ZOOM_LEVEL);
                        searchInput.value = item.display_name;
                        resultsBox.style.display = 'none';
                    };
                    resultsBox.appendChild(div);
                });
                resultsBox.style.display = 'block';
            } else {
                resultsBox.style.display = 'none';
            }
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Suggestion error:', error);
                showNotification("Failed to fetch location suggestions.", "error");
            }
        }
    });

    searchInput.addEventListener('input', (e) => fetchSuggestions(e.target.value));

    document.addEventListener('click', (e) => {
        if (!document.querySelector('.search-group').contains(e.target)) {
            resultsBox.style.display = 'none';
        }
    });
}

function searchLocation() {
    const searchInput = document.getElementById('locationSearch');
    const resultsBox = document.getElementById('searchResults');
    if (!searchInput) return;
    const query = searchInput.value;
    if (!query) return;
    if (resultsBox) resultsBox.style.display = 'none';
}

/**
 * C2 (Command & Control) Tab & Drawer Management
 */
const C2_PANELS = {
    scan: { title: 'SAR Area Scan', badge: 'SAR', pageId: 'scan-page' },
    layers: { title: 'Layers & Nautical Charts', badge: 'LAYERS', pageId: 'layers-page' },
    ais: { title: 'Live AIS Vessels', badge: 'AIS', pageId: 'ais-page' },
    aoi: { title: 'Areas of Interest (AOIs)', badge: 'AOI', pageId: 'aoi-page' }
};

let currentC2Tab = 'scan';

function switchC2Tab(tabName, forceOpen = true) {
    if (!C2_PANELS[tabName]) return;
    
    const isCollapsed = document.body.classList.contains('drawer-collapsed');
    
    // If clicking already active tab while drawer is open and forceOpen isn't mandated, toggle collapse
    if (tabName === currentC2Tab && !isCollapsed && forceOpen === false) {
        toggleC2Drawer(false);
        return;
    }

    currentC2Tab = tabName;
    const panelConfig = C2_PANELS[tabName];

    // Update rail tab active states
    document.querySelectorAll('.c2-rail-btn[data-tab]').forEach(btn => {
        if (btn.getAttribute('data-tab') === tabName) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    // Update drawer header title & badge
    const titleEl = document.getElementById('c2DrawerTitle');
    const badgeEl = document.getElementById('c2DrawerBadge');
    if (titleEl) titleEl.innerText = panelConfig.title;
    if (badgeEl) badgeEl.innerText = panelConfig.badge;

    // Switch active panel
    document.querySelectorAll('.c2-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    const targetPanel = document.getElementById(panelConfig.pageId);
    if (targetPanel) {
        targetPanel.classList.add('active');
    }

    // Ensure drawer is open if requested
    if (isCollapsed && forceOpen) {
        toggleC2Drawer(true);
    }

    // Manage Leaflet draw control visibility (enable for scan and aoi tabs)
    const isScanOrAoi = (tabName === 'scan' || tabName === 'aoi');
    if (isScanOrAoi && !drawControlVisible && typeof map !== 'undefined' && map && drawControl) {
        map.addControl(drawControl);
        drawControlVisible = true;
    } else if (!isScanOrAoi && drawControlVisible && typeof map !== 'undefined' && map && drawControl) {
        map.removeControl(drawControl);
        drawControlVisible = false;
    }

    localStorage.setItem('c2_active_tab', tabName);
}

function toggleC2Drawer(forceState) {
    const isCurrentlyCollapsed = document.body.classList.contains('drawer-collapsed');
    let shouldCollapse;
    if (typeof forceState === 'boolean') {
        shouldCollapse = !forceState;
    } else {
        shouldCollapse = !isCurrentlyCollapsed;
    }

    if (shouldCollapse) {
        document.body.classList.add('drawer-collapsed');
        localStorage.setItem('c2_drawer_collapsed', 'true');
    } else {
        document.body.classList.remove('drawer-collapsed');
        localStorage.setItem('c2_drawer_collapsed', 'false');
    }

    // Invalidate Leaflet map size after smooth animation completes
    setTimeout(() => {
        if (window.map) {
            map.invalidateSize({ pan: false });
        }
    }, 280);
}

function startDrawingBbox() {
    switchC2Tab('scan', true);
    if (window.drawControl && window.drawControl._toolbars && window.drawControl._toolbars.draw) {
        const rectHandler = window.drawControl._toolbars.draw._modes.rectangle.handler;
        if (rectHandler) rectHandler.enable();
    }
}

function resetMapView() {
    if (window.map) {
        map.setView([CONFIG.MAP_DEFAULT_LAT, CONFIG.MAP_DEFAULT_LNG], CONFIG.MAP_DEFAULT_ZOOM);
    }
}

// Backward-compatibility shim for any legacy references
function toggleAccordion(header) {
    switchC2Tab('scan', true);
}

/* ==========================================================================
   C2 Right Tactical Ship Dossier / Details & SAR Detections Sidebar Handlers
   ========================================================================== */
let currentSelectedShip = null;
let currentShipSidebarTab = 'sar';

function switchShipSidebarTab(tab) {
    currentShipSidebarTab = tab;
    const tabSar = document.getElementById('tabBtnSarDetections');
    const tabDossier = document.getElementById('tabBtnShipDossier');
    const viewSar = document.getElementById('shipSidebarSarView');
    const viewDossier = document.getElementById('shipSidebarDossierView');
    const headerTitle = document.getElementById('shipSidebarName');
    const headerSub = document.getElementById('shipSidebarSub');
    const headerIcon = document.getElementById('shipSidebarIcon');

    if (tab === 'sar') {
        if (tabSar) tabSar.classList.add('active');
        if (tabDossier) tabDossier.classList.remove('active');
        if (viewSar) viewSar.style.display = 'block';
        if (viewDossier) viewDossier.style.display = 'none';

        if (headerTitle) headerTitle.textContent = 'Maritime Intelligence';
        if (headerSub) headerSub.textContent = 'SAR Vessel Detections & Radar Metrology';
        if (headerIcon) headerIcon.textContent = '🛰️';

        renderSarDetectionsInSidebar();
    } else {
        if (tabSar) tabSar.classList.remove('active');
        if (tabDossier) tabDossier.classList.add('active');
        if (viewSar) viewSar.style.display = 'none';
        if (viewDossier) viewDossier.style.display = 'block';

        if (currentSelectedShip) {
            if (headerTitle) headerTitle.textContent = currentSelectedShip.vessel_name || (currentSelectedShip.mmsi ? `MMSI: ${currentSelectedShip.mmsi}` : 'Target Dossier');
            if (headerSub) headerSub.textContent = currentSelectedShip.isSarDetection ? `Scan: ${currentSelectedShip.folderName || 'SAR Scan'}` : `MMSI: ${currentSelectedShip.mmsi || 'N/A'}`;
            if (headerIcon) headerIcon.textContent = currentSelectedShip.isSarDetection ? '🛰️' : '🚢';
        } else {
            if (headerTitle) headerTitle.textContent = 'No Vessel Selected';
            if (headerSub) headerSub.textContent = 'Select a ship on the map or in detections';
            if (headerIcon) headerIcon.textContent = '🚢';
        }
    }
}

function toggleShipDetailsSidebar() {
    const sidebar = document.getElementById('c2ShipSidebar');
    if (!sidebar) return;

    if (sidebar.classList.contains('open')) {
        closeShipDetailsSidebar();
    } else {
        if (currentSelectedShip) {
            switchShipSidebarTab('dossier');
        } else {
            switchShipSidebarTab('sar');
        }
        sidebar.classList.add('open');
        document.body.classList.add('ship-sidebar-open');
        setTimeout(() => {
            if (window.map) map.invalidateSize({ pan: false });
        }, 280);
    }
}

function updateSarDetectionsInSidebar() {
    const layers = (typeof activeLayers !== 'undefined' && Array.isArray(activeLayers)) ? activeLayers : [];
    let totalDetections = 0;

    const select = document.getElementById('sarSidebarLayerSelect');
    if (select) {
        const currentVal = select.value;
        let optionsHtml = '<option value="__all__">All Active Scans</option>';
        layers.forEach(l => {
            const count = (l.detections && Array.isArray(l.detections)) ? l.detections.length : 0;
            const name = l.name || l.folder || 'Scan Layer';
            optionsHtml += `<option value="${l.uiId}">${escapeHtml(name)} (${count} ships)</option>`;
        });
        select.innerHTML = optionsHtml;
        if ([...select.options].some(o => o.value === currentVal)) {
            select.value = currentVal;
        }
    }

    layers.forEach(l => {
        if (l.detections && Array.isArray(l.detections)) {
            totalDetections += l.detections.length;
        }
    });

    const badge1 = document.getElementById('sarDetectionsSidebarBadge');
    const badge2 = document.getElementById('c2RightToggleBadge');
    const countLabel = document.getElementById('sarSidebarCountLabel');

    if (badge1) badge1.textContent = totalDetections;
    if (badge2) badge2.textContent = totalDetections;
    if (countLabel) countLabel.textContent = `${totalDetections} Detected Ship${totalDetections === 1 ? '' : 's'}`;

    if (currentShipSidebarTab === 'sar') {
        renderSarDetectionsInSidebar();
    }
}

function renderSarDetectionsInSidebar() {
    const container = document.getElementById('sarSidebarDetectionsList');
    if (!container) return;

    const layers = (typeof activeLayers !== 'undefined' && Array.isArray(activeLayers)) ? activeLayers : [];
    if (layers.length === 0) {
        container.innerHTML = `
            <div class="sar-detections-empty-state">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🛰️</div>
                <h4 style="margin: 0 0 6px 0; color: #1e293b; font-size: 0.95rem;">No Active SAR Imagery</h4>
                <p style="font-size: 0.8rem; color: #64748b; margin: 0; line-height: 1.4;">
                    Load a Sentinel-1 imagery pass from the <strong>SAR Scan</strong> panel or <strong>Layers</strong> tab to detect and inspect maritime vessels.
                </p>
            </div>
        `;
        return;
    }

    const select = document.getElementById('sarSidebarLayerSelect');
    const selectedFilter = select ? select.value : '__all__';

    const targetLayers = selectedFilter === '__all__' ? layers : layers.filter(l => l.uiId === selectedFilter);

    let allItems = [];
    let hasCvRun = false;

    targetLayers.forEach(l => {
        if (l.cvRun) hasCvRun = true;
        const dets = l.detections || [];
        dets.forEach((item, idx) => {
            allItems.push({
                layer: l,
                item: item,
                index: idx
            });
        });
    });

    if (allItems.length === 0) {
        if (!hasCvRun) {
            container.innerHTML = `
                <div class="sar-detections-empty-state">
                    <div style="font-size: 2.2rem; margin-bottom: 8px;">🔍</div>
                    <h4 style="margin: 0 0 6px 0; color: #1e293b; font-size: 0.95rem;">Detection Not Run Yet</h4>
                    <p style="font-size: 0.8rem; color: #64748b; margin: 0 0 12px 0; line-height: 1.4;">
                        SAR satellite imagery is loaded, but Computer Vision ship detection has not been executed yet.
                    </p>
                    <button type="button" class="btn btn-primary btn-sm" style="padding: 6px 14px; font-size: 0.82rem;" onclick="runCvForActiveLayer()">
                        ▶ Run Ship Detection Now
                    </button>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="sar-detections-empty-state">
                    <div style="font-size: 2.2rem; margin-bottom: 8px;">🌊</div>
                    <h4 style="margin: 0 0 6px 0; color: #1e293b; font-size: 0.95rem;">No Ships Detected</h4>
                    <p style="font-size: 0.8rem; color: #64748b; margin: 0; line-height: 1.4;">
                        No maritime targets were identified above the detection threshold in the selected scan.
                    </p>
                </div>
            `;
        }
        return;
    }

    let html = '';
    allItems.forEach(({ layer, item, index }) => {
        const confNum = item.confidence !== undefined ? item.confidence : 1;
        const confPct = (confNum * 100).toFixed(0);
        let confColor = '#10b981';
        if (confNum < 0.6) confColor = '#ef4444';
        else if (confNum < 0.8) confColor = '#f59e0b';

        let lat = item.lat || item.center_lat;
        let lng = item.lng || item.center_lng;

        if ((lat === undefined || lng === undefined) && layer.bounds && layer.imgWidth && layer.imgHeight) {
            const bounds = layer.bounds;
            const minLat = bounds.getSouth();
            const maxLat = bounds.getNorth();
            const minLon = bounds.getWest();
            const maxLon = bounds.getEast();
            const latScale = (maxLat - minLat) / layer.imgHeight;
            const lonScale = (maxLon - minLon) / layer.imgWidth;
            const cx = item.center_x !== undefined ? item.center_x : (item.x + item.width / 2);
            const cy = item.center_y !== undefined ? item.center_y : (item.y + item.height / 2);
            lat = maxLat - cy * latScale;
            lng = minLon + cx * lonScale;
        }

        const latStr = lat !== undefined ? `${Number(lat).toFixed(4)}° N` : 'N/A';
        const lngStr = lng !== undefined ? `${Number(lng).toFixed(4)}° E` : 'N/A';

        const lengthStr = item.length_meters ? `${item.length_meters.toFixed(1)} m` : (item.length ? `${item.length} m` : 'N/A');
        const beamStr = item.width_meters ? `${item.width_meters.toFixed(1)} m` : (item.beam ? `${item.beam} m` : 'N/A');
        const hdgStr = item.angle !== undefined ? `${Number(item.angle).toFixed(0)}°` : (item.heading ? `${item.heading}°` : '---°');

        const coords = item.coords || item.pixel_bbox || item.bbox || [item.x, item.y, item.width, item.height];
        const bboxStr = Array.isArray(coords) ? coords.join(',') : coords;
        const cropUrl = `/api/scan/${layer.folder}/crop?raw=1&bbox=${encodeURIComponent(bboxStr)}`;

        html += `
            <div class="sar-detection-card" id="sarCard_${layer.uiId}_${index}">
                <div class="sar-detection-card-header">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-size: 1.1rem;">🛰️</span>
                        <div>
                            <strong style="font-size: 0.86rem; color: #0f172a;">Target #${index + 1}</strong>
                            <span style="font-size: 0.7rem; color: #64748b; display: block;">${escapeHtml(layer.name || layer.folder)}</span>
                        </div>
                    </div>
                    <span class="sar-conf-pill" style="background: ${confColor};">${confPct}% Conf</span>
                </div>

                <div class="sar-detection-thumb-wrap">
                    <img class="sar-crop-thumb" src="${cropUrl}" alt="Radar Chip" loading="lazy" onerror="this.onerror=null; this.parentElement.innerHTML='<div style=\\'color:#94a3b8; font-size:0.75rem; text-align:center; padding:10px;\\'>Radar Chip Preview Unavailable</div>';">
                </div>

                <div class="sar-detection-grid">
                    <div class="sar-cell">
                        <span class="sar-lbl">Length</span>
                        <span class="sar-val"><b>${lengthStr}</b></span>
                    </div>
                    <div class="sar-cell">
                        <span class="sar-lbl">Beam</span>
                        <span class="sar-val"><b>${beamStr}</b></span>
                    </div>
                    <div class="sar-cell">
                        <span class="sar-lbl">Heading</span>
                        <span class="sar-val">${hdgStr}</span>
                    </div>
                    <div class="sar-cell">
                        <span class="sar-lbl">Position</span>
                        <span class="sar-val" style="font-size: 0.72rem;">${latStr}, ${lngStr}</span>
                    </div>
                </div>

                <div class="sar-detection-actions">
                    <button type="button" class="btn btn-sm btn-outline-primary" style="flex: 1; padding: 4px 8px; font-size: 0.76rem;" onclick='panToSarDetection("${layer.uiId}", ${index})'>
                        🎯 Center Map
                    </button>
                    <button type="button" class="btn btn-sm btn-primary" style="flex: 1; padding: 4px 8px; font-size: 0.76rem;" onclick='selectSarDetectionFromList("${layer.folder}", ${index}, "${layer.uiId}")'>
                        🔍 Dossier
                    </button>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function panToSarDetection(uiId, index) {
    const layers = (typeof activeLayers !== 'undefined') ? activeLayers : [];
    const layer = layers.find(l => l.uiId === uiId);
    if (!layer || !layer.detections || !layer.detections[index]) return;

    const item = layer.detections[index];
    let lat = item.lat || item.center_lat;
    let lng = item.lng || item.center_lng;

    if ((lat === undefined || lng === undefined) && layer.bounds && layer.imgWidth && layer.imgHeight) {
        const bounds = layer.bounds;
        const minLat = bounds.getSouth();
        const maxLat = bounds.getNorth();
        const minLon = bounds.getWest();
        const maxLon = bounds.getEast();
        const latScale = (maxLat - minLat) / layer.imgHeight;
        const lonScale = (maxLon - minLon) / layer.imgWidth;
        const cx = item.center_x !== undefined ? item.center_x : (item.x + item.width / 2);
        const cy = item.center_y !== undefined ? item.center_y : (item.y + item.height / 2);
        lat = maxLat - cy * latScale;
        lng = minLon + cx * lonScale;
    }

    if (lat !== undefined && lng !== undefined && window.map) {
        map.setView([lat, lng], Math.max(map.getZoom(), 15));
        if (typeof showNotification === 'function') {
            showNotification(`Centered map on SAR Detection #${index + 1}`, 'info');
        }
    }
}

function selectSarDetectionFromList(folderName, index, uiId) {
    const layers = (typeof activeLayers !== 'undefined') ? activeLayers : [];
    const layer = layers.find(l => l.uiId === uiId || l.folder === folderName);
    if (!layer || !layer.detections || !layer.detections[index]) return;

    const item = layer.detections[index];
    openSarDetectionInShipSidebar(layer.folder, item);
    switchShipSidebarTab('dossier');
}

function runCvForActiveLayer() {
    const layers = (typeof activeLayers !== 'undefined') ? activeLayers : [];
    if (layers.length === 0) return;
    const select = document.getElementById('sarSidebarLayerSelect');
    const selectedFilter = select ? select.value : '__all__';
    const targetLayer = (selectedFilter !== '__all__') ? layers.find(l => l.uiId === selectedFilter) : layers[0];

    if (targetLayer && typeof runCVDetection === 'function') {
        runCVDetection(targetLayer.folder, targetLayer.uiId);
    }
}

function openShipDetailsSidebar(vessel) {
    if (!vessel) return;
    currentSelectedShip = vessel;

    const sidebar = document.getElementById('c2ShipSidebar');
    if (!sidebar) return;

    const nameEl = document.getElementById('shipSidebarName');
    const subEl = document.getElementById('shipSidebarSub');
    const iconEl = document.getElementById('shipSidebarIcon');
    const typeBadge = document.getElementById('shipSidebarTypeBadge');
    const statusTag = document.getElementById('shipSidebarStatusTag');
    const mmsiEl = document.getElementById('shipSidebarMmsi');
    const imoEl = document.getElementById('shipSidebarImo');
    const callsignEl = document.getElementById('shipSidebarCallsign');
    const flagEl = document.getElementById('shipSidebarFlag');
    const speedEl = document.getElementById('shipSidebarSpeed');
    const headingEl = document.getElementById('shipSidebarHeading');
    const coordsEl = document.getElementById('shipSidebarCoords');
    const timeEl = document.getElementById('shipSidebarTimestamp');
    const sourceEl = document.getElementById('shipSidebarSource');
    const sarCard = document.getElementById('shipSidebarSarCard');

    const rawType = vessel.ship_type || vessel.type || 'Unspecified';
    const cleanType = typeof classifyVesselType === 'function' ? classifyVesselType(rawType) : rawType;
    const typeColor = (typeof VESSEL_TYPE_COLORS !== 'undefined' && VESSEL_TYPE_COLORS[cleanType]) ? VESSEL_TYPE_COLORS[cleanType] : '#3498db';

    const vesselName = vessel.vessel_name || vessel.name || `Vessel ${vessel.mmsi || 'Unknown'}`;
    if (nameEl) nameEl.textContent = vesselName;
    if (subEl) subEl.textContent = `MMSI: ${vessel.mmsi || 'N/A'} • ${cleanType}`;
    if (iconEl) iconEl.textContent = '🚢';

    if (typeBadge) {
        typeBadge.textContent = cleanType;
        typeBadge.style.backgroundColor = typeColor;
    }

    const speed = (vessel.speed !== undefined && vessel.speed !== null && !isNaN(vessel.speed)) ? Number(vessel.speed) :
                  (vessel.speed_knots !== undefined && vessel.speed_knots !== null && !isNaN(vessel.speed_knots)) ? Number(vessel.speed_knots) :
                  (vessel.sog !== undefined && vessel.sog !== null && !isNaN(vessel.sog)) ? Number(vessel.sog) : 0;
    if (statusTag) {
        statusTag.textContent = speed > 0.5 ? 'Underway using Engine' : 'Moored / Stationary';
        statusTag.style.color = speed > 0.5 ? '#10b981' : '#f59e0b';
    }

    if (mmsiEl) mmsiEl.textContent = vessel.mmsi || 'N/A';
    if (imoEl) imoEl.textContent = (vessel.imo && !String(vessel.imo).startsWith('UNKNOWN-')) ? vessel.imo : 'N/A';
    if (callsignEl) callsignEl.textContent = vessel.callsign || 'N/A';

    if (flagEl) {
        if (vessel.country) {
            flagEl.textContent = vessel.country;
        } else if (vessel.mmsi && String(vessel.mmsi).length >= 3) {
            flagEl.textContent = `MID ${String(vessel.mmsi).slice(0, 3)}`;
        } else {
            flagEl.textContent = 'International / Unknown';
        }
    }

    if (speedEl) speedEl.innerHTML = `${speed.toFixed(1)} <small>kn</small>`;

    const heading = (vessel.heading !== undefined && vessel.heading !== null && !isNaN(vessel.heading) && Number(vessel.heading) <= 360) ? Number(vessel.heading) :
                    (vessel.cog !== undefined && vessel.cog !== null && !isNaN(vessel.cog) && Number(vessel.cog) <= 360) ? Number(vessel.cog) : null;
    if (headingEl) {
        headingEl.textContent = heading !== null ? `${heading.toFixed(1)}°` : 'N/A';
    }

    if (coordsEl && vessel.latitude !== undefined && vessel.longitude !== undefined) {
        coordsEl.textContent = `${vessel.latitude.toFixed(5)}° N, ${vessel.longitude.toFixed(5)}° E`;
    }

    if (timeEl) {
        if (vessel.timestamp) {
            const zulu = new Date(vessel.timestamp).toISOString().replace('T', ' ').replace(/\..+/, '') + ' UTC';
            const local = new Date(vessel.timestamp).toLocaleTimeString();
            timeEl.textContent = `${zulu} (${local})`;
        } else {
            timeEl.textContent = 'Unknown';
        }
    }

    if (sourceEl) {
        sourceEl.textContent = vessel.source_plugin || 'AIS Ingestion';
    }

    if (sarCard) sarCard.style.display = 'none';

    const placeholder = document.getElementById('shipSidebarPlaceholder');
    const detailsContainer = document.getElementById('shipSidebarDetails');
    if (placeholder) placeholder.style.display = 'none';
    if (detailsContainer) detailsContainer.style.display = 'block';

    switchShipSidebarTab('dossier');

    sidebar.classList.add('open');
    document.body.classList.add('ship-sidebar-open');

    setTimeout(() => {
        if (window.map) map.invalidateSize({ pan: false });
    }, 280);
}

function openSarDetectionInShipSidebar(folderName, item) {
    if (!item) return;
    const lat = item.lat || item.center_lat;
    const lng = item.lng || item.center_lng;

    currentSelectedShip = {
        latitude: lat,
        longitude: lng,
        vessel_name: `SAR Detection #${item.id || item.index || '1'}`,
        isSarDetection: true,
        folderName: folderName,
        detectionData: item
    };

    const sidebar = document.getElementById('c2ShipSidebar');
    if (!sidebar) return;

    const nameEl = document.getElementById('shipSidebarName');
    const subEl = document.getElementById('shipSidebarSub');
    const iconEl = document.getElementById('shipSidebarIcon');
    const typeBadge = document.getElementById('shipSidebarTypeBadge');
    const statusTag = document.getElementById('shipSidebarStatusTag');
    const mmsiEl = document.getElementById('shipSidebarMmsi');
    const imoEl = document.getElementById('shipSidebarImo');
    const callsignEl = document.getElementById('shipSidebarCallsign');
    const flagEl = document.getElementById('shipSidebarFlag');
    const speedEl = document.getElementById('shipSidebarSpeed');
    const headingEl = document.getElementById('shipSidebarHeading');
    const coordsEl = document.getElementById('shipSidebarCoords');
    const timeEl = document.getElementById('shipSidebarTimestamp');
    const sourceEl = document.getElementById('shipSidebarSource');
    const sarCard = document.getElementById('shipSidebarSarCard');

    if (nameEl) nameEl.textContent = `SAR Target #${item.id || item.index || '1'}`;
    if (subEl) subEl.textContent = `Scan: ${folderName}`;
    if (iconEl) iconEl.textContent = '🛰️';

    if (typeBadge) {
        typeBadge.textContent = 'SAR Contact';
        typeBadge.style.backgroundColor = '#9333ea';
    }

    if (statusTag) {
        statusTag.textContent = 'Radar Non-Cooperative Target';
        statusTag.style.color = '#a855f7';
    }

    if (mmsiEl) mmsiEl.textContent = 'N/A (Dark / Uncorrelated)';
    if (imoEl) imoEl.textContent = 'N/A';
    if (callsignEl) callsignEl.textContent = 'N/A';
    if (flagEl) flagEl.textContent = 'Unregistered Target';

    if (speedEl) speedEl.innerHTML = `--- <small>kn</small>`;
    if (headingEl) {
        headingEl.textContent = item.angle !== undefined ? `${item.angle.toFixed(1)}°` : (item.heading ? `${item.heading}°` : 'N/A');
    }

    if (coordsEl && lat !== undefined && lng !== undefined) {
        coordsEl.textContent = `${Number(lat).toFixed(5)}° N, ${Number(lng).toFixed(5)}° E`;
    }

    if (timeEl) timeEl.textContent = 'Satellite Radar Flyby';
    if (sourceEl) sourceEl.textContent = 'Sentinel-1 SAR CV';

    if (sarCard) {
        sarCard.style.display = 'block';
        const lenEl = document.getElementById('shipSidebarSarLength');
        const beamEl = document.getElementById('shipSidebarSarBeam');
        const confEl = document.getElementById('shipSidebarSarConfidence');
        const sarHdgEl = document.getElementById('shipSidebarSarHeading');

        if (lenEl) lenEl.textContent = item.length_meters ? `${item.length_meters.toFixed(1)} m` : (item.length ? `${item.length} m` : 'N/A');
        if (beamEl) beamEl.textContent = item.width_meters ? `${item.width_meters.toFixed(1)} m` : (item.beam ? `${item.beam} m` : 'N/A');
        if (confEl) confEl.textContent = item.confidence ? `${(item.confidence * 100).toFixed(0)}%` : 'High';
        if (sarHdgEl) sarHdgEl.textContent = item.angle !== undefined ? `${item.angle.toFixed(1)}°` : 'N/A';

        const cropPreview = document.getElementById('shipSidebarCropPreview');
        const cropImg = document.getElementById('shipSidebarCropImg');
        const coords = item.coords || item.pixel_bbox || item.bbox;
        if (cropPreview && cropImg && coords && folderName) {
            const bboxStr = Array.isArray(coords) ? coords.join(',') : coords;
            cropImg.src = `/api/scan/${folderName}/crop?raw=1&bbox=${encodeURIComponent(bboxStr)}`;
            cropPreview.style.display = 'block';
        } else if (cropPreview) {
            cropPreview.style.display = 'none';
        }
    }

    const placeholder = document.getElementById('shipSidebarPlaceholder');
    const detailsContainer = document.getElementById('shipSidebarDetails');
    if (placeholder) placeholder.style.display = 'none';
    if (detailsContainer) detailsContainer.style.display = 'block';

    switchShipSidebarTab('dossier');

    sidebar.classList.add('open');
    document.body.classList.add('ship-sidebar-open');

    setTimeout(() => {
        if (window.map) map.invalidateSize({ pan: false });
    }, 280);
}

function closeShipDetailsSidebar() {
    const sidebar = document.getElementById('c2ShipSidebar');
    if (sidebar) sidebar.classList.remove('open');
    document.body.classList.remove('ship-sidebar-open');
    setTimeout(() => {
        if (window.map) map.invalidateSize({ pan: false });
    }, 280);
}

function centerOnSelectedShip() {
    if (!currentSelectedShip) return;
    const lat = currentSelectedShip.latitude;
    const lng = currentSelectedShip.longitude;
    if (lat !== undefined && lng !== undefined && window.map) {
        map.setView([lat, lng], Math.max(map.getZoom(), 14));
        if (typeof showNotification === 'function') {
            const label = currentSelectedShip.vessel_name || (currentSelectedShip.mmsi ? `MMSI: ${currentSelectedShip.mmsi}` : 'Target');
            showNotification(`Centered map on ${label}`, 'info');
        }
    }
}

function editSelectedShipDetails() {
    if (!currentSelectedShip) return;
    if (currentSelectedShip.vessel_id) {
        if (typeof openEditVesselModal === 'function') {
            openEditVesselModal(currentSelectedShip.vessel_id);
        }
    } else if (currentSelectedShip.mmsi) {
        const v = typeof aisVesselsData !== 'undefined' ? aisVesselsData.find(x => x.mmsi === currentSelectedShip.mmsi) : null;
        if (v && v.vessel_id && typeof openEditVesselModal === 'function') {
            openEditVesselModal(v.vessel_id);
        } else if (typeof showNotification === 'function') {
            showNotification('Vessel registration record not found in database.', 'warning');
        }
    } else {
        if (typeof showNotification === 'function') {
            showNotification('SAR detections cannot be edited as AIS registrations.', 'info');
        }
    }
}

function initC2Rail() {
    // Restore saved drawer collapsed state
    const isCollapsed = localStorage.getItem('c2_drawer_collapsed') === 'true';
    if (isCollapsed) {
        document.body.classList.add('drawer-collapsed');
    }

    // Restore saved active tab
    const savedTab = localStorage.getItem('c2_active_tab') || 'scan';
    switchC2Tab(savedTab, !isCollapsed);

    // Map container transition listener for immediate redraws
    const mapEl = document.getElementById('map');
    if (mapEl) {
        mapEl.addEventListener('transitionend', () => {
            if (window.map) map.invalidateSize({ pan: false });
        });
    }
}

// Global initialization on page load
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    initC2Rail();
    initNauticalChart(map);
    initAISVessels(map);
    initSearch();
    initScannerHandlers();
    initAoiHandlers();
    loadAOIs();
    updateSarDetectionsInSidebar();

    // Check for scans selected from gallery
    const params = new URLSearchParams(window.location.search);
    const loadImmediate = params.get('load');
    let selected = JSON.parse(localStorage.getItem('selected_scans') || '[]');

    if (loadImmediate && !selected.includes(loadImmediate)) {
        selected.push(loadImmediate);
        localStorage.setItem('selected_scans', JSON.stringify(selected));
    }

    // Check for AOI focus from AOIs page
    const aoiBboxParam = params.get('bbox') || params.get('aoi_bbox');
    if (aoiBboxParam) {
        const parts = aoiBboxParam.split(',').map(Number);
        if (parts.length === 4 && parts.every(n => !isNaN(n))) {
            const lBounds = L.latLngBounds([[parts[1], parts[0]], [parts[3], parts[2]]]);
            map.fitBounds(lBounds, { padding: [50, 50] });
            showNotification("Focused on Area of Interest", "info");
        }
    }

    // Check for new scan action from navbar
    if (params.get('action') === 'scan') {
        switchC2Tab('scan', true);
    }

    for (const folder of selected) {
        try {
            const res = await fetch(`${CONFIG.API_GET_SCAN}/${folder}`);
            const data = await res.json();
            if (!data.error) {
                addImageryLayer(data.imageUrl, data.bounds, data.datetime, folder, data.custom_name);
                if (folder === loadImmediate) {
                    map.fitBounds(data.bounds);
                }
            } else {
                showNotification("Failed to load scan data for: " + folder, "error");
            }
        } catch (err) {
            console.error("Failed to load persistent scan", folder);
            showNotification("Failed to load persistent scan: " + folder, "error");
        }
    }
    updateSarDetectionsInSidebar();
});
