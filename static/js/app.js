/**
 * Main application initialization and event dispatching.
 */

let abortController = null;

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
    ais: { title: 'Latest AIS Reports', badge: 'AIS', pageId: 'ais-page' },
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

let currentTacticalFilter = 'all';
window.currentTacticalFilter = currentTacticalFilter;

function setTacticalMapFilter(filterType) {
    currentTacticalFilter = filterType || 'all';
    window.currentTacticalFilter = currentTacticalFilter;

    // Update tactical filter pills active class
    const pills = document.querySelectorAll('.tactical-filter-pill');
    pills.forEach(pill => {
        if (pill.getAttribute('data-filter') === currentTacticalFilter) {
            pill.classList.add('active');
        } else {
            pill.classList.remove('active');
        }
    });

    // Sync modal filter select if present
    const modalSelect = document.getElementById('sarDetectionsFilterSelect');
    if (modalSelect && modalSelect.value !== currentTacticalFilter) {
        modalSelect.value = currentTacticalFilter;
    }

    // Apply filter to map layers
    if (typeof window.applyTacticalMapFilter === 'function') {
        window.applyTacticalMapFilter(currentTacticalFilter);
    }

    // Re-render sidebar contacts list
    renderSarDetectionsInSidebar();

    // Re-render modal if open
    if (typeof renderSarDetectionsList === 'function' && typeof currentModalLayerId !== 'undefined' && currentModalLayerId) {
        renderSarDetectionsList();
    }
}
window.setTacticalMapFilter = setTacticalMapFilter;

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

    const totalDetectionsCount = allItems.length;
    if (currentTacticalFilter && currentTacticalFilter !== 'all' && typeof matchesTacticalFilter === 'function') {
        allItems = allItems.filter(({ item }) => matchesTacticalFilter(item, currentTacticalFilter));
    }

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
        } else if (totalDetectionsCount > 0) {
            container.innerHTML = `
                <div class="sar-detections-empty-state">
                    <div style="font-size: 2.2rem; margin-bottom: 8px;">🎯</div>
                    <h4 style="margin: 0 0 6px 0; color: #1e293b; font-size: 0.95rem;">No Matching Contacts</h4>
                    <p style="font-size: 0.8rem; color: #64748b; margin: 0 0 12px 0; line-height: 1.4;">
                        No targets match tactical filter (<strong>${escapeHtml(currentTacticalFilter)}</strong>) out of ${totalDetectionsCount} detections.
                    </p>
                    <button type="button" class="btn btn-sm btn-outline-secondary" onclick="setTacticalMapFilter('all')">
                        Show All Contacts
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

        let lat = item.latitude !== undefined && item.latitude !== null ? item.latitude : (item.lat !== undefined && item.lat !== null ? item.lat : item.center_lat);
        let lng = item.longitude !== undefined && item.longitude !== null ? item.longitude : (item.lng !== undefined && item.lng !== null ? item.lng : (item.lon !== undefined && item.lon !== null ? item.lon : item.center_lng));

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

        const latVal = (lat !== undefined && lat !== null && !isNaN(lat)) ? Number(lat) : null;
        const lngVal = (lng !== undefined && lng !== null && !isNaN(lng)) ? Number(lng) : null;
        const latStr = latVal !== null ? `${Math.abs(latVal).toFixed(4)}° ${latVal >= 0 ? 'N' : 'S'}` : 'N/A';
        const lngStr = lngVal !== null ? `${Math.abs(lngVal).toFixed(4)}° ${lngVal >= 0 ? 'E' : 'W'}` : 'N/A';

        const lengthStr = item.length_meters ? `${item.length_meters.toFixed(1)} m` : (item.length ? `${item.length} m` : 'N/A');
        const beamStr = item.width_meters ? `${item.width_meters.toFixed(1)} m` : (item.beam ? `${item.beam} m` : 'N/A');
        const hdgStr = item.angle !== undefined ? `${Number(item.angle).toFixed(0)}°` : (item.heading ? `${item.heading}°` : '---°');

        const coords = item.coords || item.pixel_bbox || item.bbox || [item.x, item.y, item.width, item.height];
        const bboxStr = Array.isArray(coords) ? coords.join(',') : coords;
        const cropUrl = `/api/scan/${layer.folder}/crop?raw=1&bbox=${encodeURIComponent(bboxStr)}`;

        const isDark = Boolean(item.is_dark || item.is_dark_vessel || !item.is_correlated || item.correlation_status === 'no_ais');
        const lengthM = Number(item.length || item.estimated_length || item.length_meters || 0);

        let threatBadgesHtml = '';
        if (item.is_solas_suspect || (isDark && lengthM >= 45.0)) {
            threatBadgesHtml += `<span class="sar-badge-tag solas" title="SOLAS Chapter V non-compliance">⚠️ SOLAS Suspect</span>`;
        }
        if (item.is_speed_spoofed || item.is_course_spoofed || item.spoofing_warning) {
            threatBadgesHtml += `<span class="sar-badge-tag spoofed" title="Radar vs AIS kinematic discrepancy">🚨 AIS Spoofed</span>`;
        }
        if (item.transshipment_suspect || (item.transshipment_events && item.transshipment_events.length > 0) || item.is_transshipment_suspect) {
            threatBadgesHtml += `<span class="sar-badge-tag sts" title="Ship-to-ship rendezvous proximity">⚓ STS Risk</span>`;
        }
        if (item.optical_status === 'CONFIRMED_VESSEL' || item.optical_confirmed) {
            threatBadgesHtml += `<span class="sar-badge-tag optical" title="Confirmed by Sentinel-2 MSI">🛰️ S2 Optical</span>`;
        }
        if (item.temporal_change_type === 'NEW_TARGET') {
            threatBadgesHtml += `<span class="sar-badge-tag temporal" style="background:rgba(239, 68, 68, 0.15); color:#ef4444; border:1px solid #ef4444;">🆕 New Target</span>`;
        } else if (item.temporal_change_type === 'PERSISTENT') {
            threatBadgesHtml += `<span class="sar-badge-tag temporal" style="background:rgba(100, 116, 139, 0.15); color:#475569; border:1px solid #cbd5e1;">⚓ Persistent</span>`;
        }

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
                ${threatBadgesHtml ? `<div class="sar-badge-tags" style="padding: 0 10px 6px 10px;">${threatBadgesHtml}</div>` : ''}

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
    let lat = item.latitude !== undefined && item.latitude !== null ? item.latitude : (item.lat !== undefined ? item.lat : item.center_lat);
    let lng = item.longitude !== undefined && item.longitude !== null ? item.longitude : (item.lng !== undefined ? item.lng : item.center_lng);

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
                  (vessel.sog !== undefined && vessel.sog !== null && !isNaN(vessel.sog)) ? Number(vessel.sog) : null;
    if (statusTag) {
        statusTag.textContent = speed === null ? 'Movement Unknown' : (speed > 0.5 ? 'Moving' : 'Stationary or Unknown');
        statusTag.style.color = speed === null ? '#64748b' : (speed > 0.5 ? '#10b981' : '#f59e0b');
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

    if (speedEl) {
        speedEl.innerHTML = speed === null
            ? 'N/A'
            : `${speed.toFixed(1)} <small>kn</small>`;
    }

    const heading = (vessel.heading !== undefined && vessel.heading !== null && !isNaN(vessel.heading) && Number(vessel.heading) <= 360) ? Number(vessel.heading) :
                    (vessel.cog !== undefined && vessel.cog !== null && !isNaN(vessel.cog) && Number(vessel.cog) <= 360) ? Number(vessel.cog) : null;
    if (headingEl) {
        headingEl.textContent = heading !== null ? `${heading.toFixed(1)}°` : 'N/A';
    }

    if (coordsEl) {
        const latitude = Number(vessel.latitude);
        const longitude = Number(vessel.longitude);
        coordsEl.textContent = Number.isFinite(latitude) && Number.isFinite(longitude)
            ? `${Math.abs(latitude).toFixed(5)}° ${latitude >= 0 ? 'N' : 'S'}, ${Math.abs(longitude).toFixed(5)}° ${longitude >= 0 ? 'E' : 'W'}`
            : 'N/A';
    }

    if (timeEl) {
        if (vessel.timestamp) {
            const parsedTime = parseUtcDate(vessel.timestamp);
            const zulu = parsedTime ? SentinelTime.formatZulu(parsedTime) : String(vessel.timestamp);
            const local = parsedTime ? `${SentinelTime.formatLocal(parsedTime)} LOCAL` : '';
            timeEl.textContent = local ? `${local} (Zulu: ${zulu})` : zulu;
        } else {
            timeEl.textContent = 'Unknown';
        }
    }

    if (sourceEl) {
        sourceEl.textContent = vessel.source_plugin || 'AIS Ingestion';
    }

    if (sarCard) sarCard.style.display = 'none';
    const alertsCard = document.getElementById('shipSidebarAlertsCard');
    if (alertsCard) alertsCard.style.display = 'none';
    const multiSensorCard = document.getElementById('shipSidebarMultiSensorCard');
    if (multiSensorCard) multiSensorCard.style.display = 'none';
    const uncertaintyCard = document.getElementById('shipSidebarUncertaintyCard');
    if (uncertaintyCard) uncertaintyCard.style.display = 'none';
    const exportRow = document.getElementById('shipSidebarExportRow');
    if (exportRow) exportRow.style.display = 'none';

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
    const lat = item.latitude !== undefined && item.latitude !== null ? item.latitude : (item.lat !== undefined ? item.lat : item.center_lat);
    const lng = item.longitude !== undefined && item.longitude !== null ? item.longitude : (item.lng !== undefined ? item.lng : item.center_lng);

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

    const correlatedAis = item.correlated_ais;
    const isCorrelated = Boolean(item.is_correlated && correlatedAis);

    if (nameEl) {
        if (isCorrelated && correlatedAis.name) {
            nameEl.textContent = correlatedAis.name;
        } else {
            nameEl.textContent = `SAR Target #${item.id || item.index || '1'}`;
        }
    }
    if (subEl) {
        if (isCorrelated) {
            const statusLabel = item.correlation_status === 'inside_box'
                ? 'Inside Detection Bounding Box'
                : `Buffer Match (+${Math.round(correlatedAis.distance_to_box_meters || 0)}m)`;
            subEl.textContent = `Correlated AIS Vessel • ${statusLabel}`;
        } else {
            subEl.textContent = `Scan: ${folderName}`;
        }
    }
    if (iconEl) iconEl.textContent = isCorrelated ? '🚢' : '🛰️';

    if (typeBadge) {
        if (isCorrelated && correlatedAis.type) {
            typeBadge.textContent = correlatedAis.type;
            typeBadge.style.backgroundColor = item.correlation_status === 'inside_box'
                ? (CONFIG.COLOR_INSIDE_BOX_DETECTION || '#10b981')
                : (CONFIG.COLOR_OUTSIDE_BOX_DETECTION || '#06b6d4');
        } else {
            typeBadge.textContent = 'SAR Contact';
            typeBadge.style.backgroundColor = '#9333ea';
        }
    }

    if (statusTag) {
        if (isCorrelated) {
            statusTag.textContent = item.correlation_status === 'inside_box'
                ? 'Cooperative Target (AIS In-Box)'
                : `Cooperative Target (AIS Buffer Match +${Math.round(correlatedAis.distance_to_box_meters || 0)}m)`;
            statusTag.style.color = item.correlation_status === 'inside_box'
                ? (CONFIG.COLOR_INSIDE_BOX_DETECTION || '#10b981')
                : (CONFIG.COLOR_OUTSIDE_BOX_DETECTION || '#06b6d4');
        } else {
            statusTag.textContent = 'Radar Non-Cooperative Target (Dark / Uncorrelated)';
            statusTag.style.color = '#ef4444';
        }
    }

    if (mmsiEl) mmsiEl.textContent = (isCorrelated && correlatedAis.mmsi) ? correlatedAis.mmsi : 'N/A (Dark / Uncorrelated)';
    if (imoEl) imoEl.textContent = (isCorrelated && correlatedAis.imo) ? correlatedAis.imo : 'N/A';
    if (callsignEl) callsignEl.textContent = (isCorrelated && correlatedAis.callsign) ? correlatedAis.callsign : 'N/A';
    if (flagEl) flagEl.textContent = (isCorrelated && correlatedAis.flag) ? correlatedAis.flag : (isCorrelated ? 'Verified Vessel' : 'Unregistered Target');

    if (speedEl) {
        if (isCorrelated && correlatedAis.speed !== undefined && correlatedAis.speed !== null) {
            speedEl.innerHTML = `${correlatedAis.speed} <small>kn</small>`;
        } else {
            speedEl.innerHTML = `--- <small>kn</small>`;
        }
    }
    if (headingEl) {
        if (isCorrelated && correlatedAis.heading !== undefined && correlatedAis.heading !== null) {
            headingEl.textContent = `${correlatedAis.heading}° (AIS) / ${item.angle !== undefined ? `${item.angle.toFixed(1)}° (SAR)` : 'N/A'}`;
        } else {
            headingEl.textContent = item.angle !== undefined ? `${item.angle.toFixed(1)}°` : (item.heading ? `${item.heading}°` : 'N/A');
        }
    }

    if (coordsEl && lat !== undefined && lng !== undefined) {
        const numLat = Number(lat);
        const numLng = Number(lng);
        coordsEl.textContent = Number.isFinite(numLat) && Number.isFinite(numLng)
            ? `${Math.abs(numLat).toFixed(5)}° ${numLat >= 0 ? 'N' : 'S'}, ${Math.abs(numLng).toFixed(5)}° ${numLng >= 0 ? 'E' : 'W'}`
            : 'N/A';
    }

    if (timeEl) timeEl.textContent = isCorrelated && correlatedAis.timestamp ? `AIS Ping: ${correlatedAis.timestamp}` : 'Satellite Radar Flyby';
    if (sourceEl) sourceEl.textContent = isCorrelated ? `Sentinel-1 SAR CV + AIS (${correlatedAis.mmsi})` : 'Sentinel-1 SAR CV';

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
        const coords = item.coords || item.pixel_bbox || item.bbox || (item.x !== undefined ? [item.x, item.y, item.width, item.height] : null);
        if (cropPreview && cropImg && coords && folderName) {
            const bboxStr = Array.isArray(coords) ? coords.join(',') : coords;
            cropImg.src = `/api/scan/${folderName}/crop?raw=1&bbox=${encodeURIComponent(bboxStr)}`;
            cropPreview.style.display = 'block';
        } else if (cropPreview) {
            cropPreview.style.display = 'none';
        }
    }

    // Compliance & Operational Threat Alerts Card
    const alertsCard = document.getElementById('shipSidebarAlertsCard');
    const alertsList = document.getElementById('shipSidebarAlertsList');
    if (alertsCard && alertsList) {
        let alertsHtml = '';
        const isDark = Boolean(item.is_dark || item.is_dark_vessel || !item.is_correlated || item.correlation_status === 'no_ais');
        const lengthM = Number(item.length || item.estimated_length || item.length_meters || 0);

        if (item.is_solas_suspect || (isDark && lengthM >= 45.0)) {
            alertsHtml += `
                <div class="c2-alert-badge solas">
                    ⚠️ <strong>SOLAS Chapter V Breach:</strong> Vessel length est. ${lengthM > 0 ? `${lengthM.toFixed(0)}m` : '>45m'} navigating without broadcast AIS transponder.
                </div>
            `;
        }
        if (item.is_speed_spoofed || item.is_course_spoofed || item.spoofing_warning) {
            alertsHtml += `
                <div class="c2-alert-badge spoof">
                    🚨 <strong>AIS Spoofing / Telemetry Conflict:</strong> ${escapeHtml(item.spoofing_warning || 'Radar kinematics conflict with broadcast AIS parameters.')}
                </div>
            `;
        }
        if (item.transshipment_suspect || (item.transshipment_events && item.transshipment_events.length > 0) || item.is_transshipment_suspect) {
            alertsHtml += `
                <div class="c2-alert-badge sts">
                    ⚓ <strong>STS Transshipment Alert:</strong> Slow-speed rendezvous proximity or tandem cargo transfer signature detected.
                </div>
            `;
        }
        if (item.mpa_breach || item.geofence_warning) {
            alertsHtml += `
                <div class="c2-alert-badge mpa">
                    🛡️ <strong>Marine Protected Area Geofence:</strong> Contact located within restricted ecological or maritime boundary.
                </div>
            `;
        }

        if (!alertsHtml) {
            if (isCorrelated) {
                alertsHtml = `
                    <div class="c2-alert-badge normal">
                        ✅ <strong>Compliant Contact:</strong> Broadcast AIS position, speed, and heading match radar signature.
                    </div>
                `;
            } else {
                alertsHtml = `
                    <div class="c2-alert-badge normal" style="background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;">
                        ℹ️ <strong>Radar Contact:</strong> Non-correlated radar detection (est. ${lengthM > 0 ? `${lengthM.toFixed(0)}m` : 'N/A'}).
                    </div>
                `;
            }
        }
        alertsList.innerHTML = alertsHtml;
        alertsCard.style.display = 'block';
    }

    // Tactical Explainability & Rationales Card
    const expCard = document.getElementById('shipSidebarExplainabilityCard');
    const expBadge = document.getElementById('explainabilityBadge');
    const expThreat = document.getElementById('explainabilityThreat');
    const expSummary = document.getElementById('explainabilitySummary');
    const expEvidence = document.getElementById('explainabilityEvidenceList');

    if (expCard) {
        let classification = 'COOPERATIVE_VESSEL';
        let threatLevel = 'INFO';
        let summary = 'Vessel telemetry is compliant and consistent with radar observations.';
        let evidence = [];

        const isDark = Boolean(item.is_dark || item.is_dark_vessel || !item.is_correlated || item.correlation_status === 'no_ais');
        const lengthM = Number(item.length || item.estimated_length || item.length_meters || 0);

        if (item.is_infrastructure || item.offshore_infrastructure) {
            classification = 'OFFSHORE_INFRASTRUCTURE';
            threatLevel = 'LOW';
            summary = 'Contact is co-located with charted offshore infrastructure (platform or wind turbine), suppressing false vessel detection.';
            evidence.push({ title: 'Infrastructure Chart Match', desc: 'Co-located within charted platform perimeter.', weight: 'HIGH' });
        } else if (item.is_speed_spoofed || item.is_course_spoofed || item.spoofing_warning) {
            classification = 'SPOOFED_AIS';
            threatLevel = 'CRITICAL';
            summary = item.spoofing_warning || 'Discrepancy detected between broadcast AIS kinematics and radar-derived Doppler/wake vectors.';
            evidence.push({ title: 'Kinematic Vector Divergence', desc: 'AIS speed/course diverges from SAR wake analysis.', weight: 'HIGH' });
            if (item.wake_speed_knots !== undefined) evidence.push({ title: 'SAR Wake Speed', desc: `${Number(item.wake_speed_knots).toFixed(1)} kn estimated from radar wake.`, weight: 'MEDIUM' });
        } else if (item.transshipment_suspect || item.is_transshipment_suspect || (item.transshipment_events && item.transshipment_events.length > 0)) {
            classification = 'TRANSSHIPMENT_SUSPECT';
            threatLevel = 'HIGH';
            summary = 'Contact engaged in close-proximity slow-speed encounter (<500m) with secondary vessel outside authorized anchorage.';
            evidence.push({ title: 'Proximity Threshold (<500m)', desc: 'Two vessels operating in close lateral separation.', weight: 'HIGH' });
            evidence.push({ title: 'Unsanctioned Water', desc: 'Encounter located outside designated anchorage polygon.', weight: 'MEDIUM' });
        } else if (item.is_solAS_suspect || item.is_solas_suspect || (isDark && lengthM >= 45.0)) {
            classification = 'SOLAS_SUSPECT';
            threatLevel = 'HIGH';
            summary = `Non-broadcasting radar contact with estimated length of ${lengthM > 0 ? `${lengthM.toFixed(0)}m` : '>45m'} exceeds international SOLAS AIS carriage mandate.`;
            evidence.push({ title: 'Mandatory SOLAS Threshold', desc: `Estimated length (${lengthM > 0 ? `${lengthM.toFixed(0)}m` : '>45m'}) exceeds 300 GT / 45m carriage threshold.`, weight: 'HIGH' });
            evidence.push({ title: 'Transponder Silence', desc: 'No correlated AIS broadcast within spatial search buffer.', weight: 'HIGH' });
        } else if (isDark) {
            classification = 'DARK_VESSEL';
            threatLevel = 'MEDIUM';
            summary = 'Uncorrelated radar target with strong metallic backscatter and zero AIS transmission in local area.';
            evidence.push({ title: 'Unassociated Radar Return', desc: 'Definite SAR backscatter cluster with no nearby AIS candidates.', weight: 'HIGH' });
            if (lengthM > 0) evidence.push({ title: 'Dimension Profile', desc: `Est. length ${lengthM.toFixed(0)}m, beam ${Number(item.width_meters || item.beam || 0).toFixed(0)}m.`, weight: 'LOW' });
        } else {
            classification = 'COOPERATIVE_VESSEL';
            threatLevel = 'LOW';
            summary = 'Contact correlated with valid AIS broadcast within confidence ellipse.';
            evidence.push({ title: 'Correlated AIS Beacon', desc: `MMSI ${item.correlated_ais?.mmsi || 'verified'} matched position and kinematic track.`, weight: 'HIGH' });
        }

        let badgeBg = '#10b981';
        let threatColor = '#10b981';
        if (classification === 'DARK_VESSEL') { badgeBg = '#f59e0b'; threatColor = '#f59e0b'; }
        else if (classification === 'SOLAS_SUSPECT') { badgeBg = '#ef4444'; threatColor = '#ef4444'; }
        else if (classification === 'SPOOFED_AIS') { badgeBg = '#dc2626'; threatColor = '#dc2626'; }
        else if (classification === 'TRANSSHIPMENT_SUSPECT') { badgeBg = '#8b5cf6'; threatColor = '#8b5cf6'; }
        else if (classification === 'OFFSHORE_INFRASTRUCTURE') { badgeBg = '#64748b'; threatColor = '#64748b'; }

        if (expBadge) {
            expBadge.textContent = classification.replace(/_/g, ' ');
            expBadge.style.background = badgeBg;
            expBadge.style.color = '#ffffff';
        }
        if (expThreat) {
            expThreat.textContent = `Threat: ${threatLevel}`;
            expThreat.style.color = threatColor;
        }
        if (expSummary) {
            expSummary.textContent = summary;
        }
        if (expEvidence) {
            expEvidence.innerHTML = evidence.map(ev => `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; background: #fff; padding: 4px 6px; border-radius: 4px; border: 1px solid #e2e8f0; font-size: 0.72rem;">
                    <div>
                        <strong style="color: #1e293b;">${escapeHtml(ev.title || '')}:</strong>
                        <span style="color: #475569;"> ${escapeHtml(ev.desc || '')}</span>
                    </div>
                    <span style="font-size: 0.65rem; font-weight: 600; padding: 1px 4px; border-radius: 3px; background: ${ev.weight === 'HIGH' ? '#fee2e2; color: #991b1b;' : '#f1f5f9; color: #475569;'}">${ev.weight || 'EVID'}</span>
                </div>
            `).join('');
        }

        expCard.style.display = 'block';
    }

    // Multi-Sensor & Temporal Analytics Card
    const multiSensorCard = document.getElementById('shipSidebarMultiSensorCard');
    const opticalStatusEl = document.getElementById('shipSidebarOpticalStatus');
    const temporalStatusEl = document.getElementById('shipSidebarTemporalStatus');
    const wakeRowEl = document.getElementById('shipSidebarWakeRow');
    const wakeSpeedEl = document.getElementById('shipSidebarWakeSpeed');
    const wakeHdgEl = document.getElementById('shipSidebarWakeHeading');

    if (multiSensorCard) {
        multiSensorCard.style.display = 'block';

        if (opticalStatusEl) {
            if (item.optical_status === 'CONFIRMED_VESSEL' || item.optical_confirmed) {
                opticalStatusEl.innerHTML = '<span style="color: #10b981; font-weight: 600;">✅ Confirmed Visual</span>';
            } else if (item.optical_status === 'CLOUD_OBSCURED') {
                opticalStatusEl.innerHTML = '<span style="color: #f59e0b; font-weight: 500;">☁️ Cloud Obscured</span>';
            } else if (item.optical_status === 'NO_CORRELATION') {
                opticalStatusEl.innerHTML = '<span style="color: #64748b;">No Visual Match</span>';
            } else {
                opticalStatusEl.innerHTML = '<span style="color: #64748b;">Not Evaluated</span>';
            }
        }

        if (temporalStatusEl) {
            if (item.temporal_change_type === 'NEW_TARGET') {
                temporalStatusEl.innerHTML = '<span style="color: #ef4444; font-weight: 600;">🆕 New Contact</span>';
            } else if (item.temporal_change_type === 'PERSISTENT') {
                temporalStatusEl.innerHTML = '<span style="color: #64748b; font-weight: 500;">⚓ Persistent Structure</span>';
            } else if (item.temporal_change_type === 'DEPARTED') {
                temporalStatusEl.innerHTML = '<span style="color: #f59e0b; font-weight: 500;">Departed Position</span>';
            } else if (item.temporal_change_type) {
                temporalStatusEl.innerHTML = `<span>${escapeHtml(item.temporal_change_type)}</span>`;
            } else {
                temporalStatusEl.innerHTML = '<span style="color: #64748b;">Baseline Pass</span>';
            }
        }

        if (wakeRowEl && wakeSpeedEl && wakeHdgEl) {
            const hasWakeSpeed = item.wake_speed_knots !== undefined && item.wake_speed_knots !== null;
            const hasWakeHdg = item.wake_heading_deg !== undefined && item.wake_heading_deg !== null;
            if (hasWakeSpeed || hasWakeHdg) {
                wakeRowEl.style.display = 'grid';
                wakeSpeedEl.textContent = hasWakeSpeed ? `${Number(item.wake_speed_knots).toFixed(1)} kn` : 'N/A';
                wakeHdgEl.textContent = hasWakeHdg ? `${Number(item.wake_heading_deg).toFixed(0)}°` : 'N/A';
            } else {
                wakeRowEl.style.display = 'none';
            }
        }
    }

    // Calibrated Uncertainty & Surveillance Codes Card in Sidebar
    const uncertaintyCard = document.getElementById('shipSidebarUncertaintyCard');
    const spatialCepEl = document.getElementById('shipSidebarSpatialCep');
    const assocLhEl = document.getElementById('shipSidebarAssocLikelihood');
    const dimBoundsEl = document.getElementById('shipSidebarDimBounds');
    const headingUncEl = document.getElementById('shipSidebarHeadingUnc');
    const rcRow = document.getElementById('shipSidebarReasonCodesRow');
    const rcList = document.getElementById('shipSidebarReasonCodesList');

    if (uncertaintyCard) {
        const spUnc = item.spatial_uncertainty || (item.raw_detection && item.raw_detection.spatial_uncertainty) || {};
        const cep = spUnc.cep_meters !== undefined ? spUnc.cep_meters : item.cep_meters;
        const assocLh = item.association_likelihood !== undefined ? item.association_likelihood :
                       (item.raw_detection && item.raw_detection.association_likelihood);
        const dimUnc = item.dimension_uncertainty || (item.raw_detection && item.raw_detection.dimension_uncertainty) || {};
        const lenUnc = dimUnc.length_uncertainty_m;
        const beamUnc = dimUnc.beam_uncertainty_m;
        const hdgUnc = dimUnc.heading_uncertainty_deg;
        const rCodes = item.reason_codes || (item.raw_detection && item.raw_detection.reason_codes) || [];

        if (spatialCepEl) {
            spatialCepEl.textContent = cep !== undefined && cep !== null ? `±${Number(cep).toFixed(1)} m` : 'Uncalibrated';
        }
        if (assocLhEl) {
            if (assocLh !== undefined && assocLh !== null) {
                assocLhEl.textContent = `${(Number(assocLh) * 100).toFixed(0)}%`;
                assocLhEl.style.color = assocLh >= 0.7 ? '#10b981' : (assocLh >= 0.4 ? '#f59e0b' : '#ef4444');
            } else {
                assocLhEl.textContent = 'N/A';
                assocLhEl.style.color = '';
            }
        }
        if (dimBoundsEl) {
            if (lenUnc !== undefined && beamUnc !== undefined) {
                dimBoundsEl.textContent = `±${Number(lenUnc).toFixed(0)}m × ±${Number(beamUnc).toFixed(0)}m`;
            } else {
                dimBoundsEl.textContent = 'Default Bounds';
            }
        }
        if (headingUncEl) {
            headingUncEl.textContent = hdgUnc !== undefined && hdgUnc !== null ? `±${Number(hdgUnc).toFixed(1)}°` : 'N/A';
        }

        if (rcRow && rcList) {
            if (Array.isArray(rCodes) && rCodes.length > 0) {
                rcList.innerHTML = rCodes.map(code => {
                    let bg = '#64748b';
                    if (code.includes('AIS_') || code.includes('CONFIRMED')) bg = '#10b981';
                    else if (code.includes('DARK_') || code.includes('SOLAS_') || code.includes('SPOOF')) bg = '#ef4444';
                    else if (code.includes('RADAR_STRONG')) bg = '#0ea5e9';
                    return `<span class="badge" style="background: ${bg}; color: #ffffff; font-size: 0.68rem; padding: 2px 6px; border-radius: 4px; font-weight: 500;">${escapeHtml(code)}</span>`;
                }).join('');
                rcRow.style.display = 'block';
            } else {
                rcRow.style.display = 'none';
            }
        }

        uncertaintyCard.style.display = 'block';
    }

    // Export Row in Sidebar
    const exportRow = document.getElementById('shipSidebarExportRow');
    if (exportRow) {
        exportRow.style.display = 'flex';
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

    const alertsCard = document.getElementById('shipSidebarAlertsCard');
    if (alertsCard) alertsCard.style.display = 'none';
    const multiSensorCard = document.getElementById('shipSidebarMultiSensorCard');
    if (multiSensorCard) multiSensorCard.style.display = 'none';
    const exportRow = document.getElementById('shipSidebarExportRow');
    if (exportRow) exportRow.style.display = 'none';

    setTimeout(() => {
        if (window.map) map.invalidateSize({ pan: false });
    }, 280);
}

function exportCurrentSelectedKmz() {
    if (!currentSelectedShip || !currentSelectedShip.folderName) {
        if (typeof showNotification === 'function') {
            showNotification("No SAR scan associated with this contact.", "warning");
        }
        return;
    }
    window.open(`/api/scan/${encodeURIComponent(currentSelectedShip.folderName)}/export/kmz`, '_blank');
}
window.exportCurrentSelectedKmz = exportCurrentSelectedKmz;

function exportCurrentSelectedCot() {
    if (!currentSelectedShip || !currentSelectedShip.folderName) {
        if (typeof showNotification === 'function') {
            showNotification("No SAR scan associated with this contact.", "warning");
        }
        return;
    }
    window.open(`/api/scan/${encodeURIComponent(currentSelectedShip.folderName)}/export/cot`, '_blank');
}
window.exportCurrentSelectedCot = exportCurrentSelectedCot;

function exportCurrentSelectedBriefingPdf() {
    if (!currentSelectedShip || !currentSelectedShip.folderName) {
        if (typeof showNotification === 'function') {
            showNotification("No SAR scan associated with this contact.", "warning");
        }
        return;
    }
    window.open(`/api/scan/${encodeURIComponent(currentSelectedShip.folderName)}/briefing/pdf`, '_blank');
}
window.exportCurrentSelectedBriefingPdf = exportCurrentSelectedBriefingPdf;

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
                const layerId = addImageryLayer(data.imageUrl, data.bounds, data.datetime, folder, data.custom_name);
                if (data.latest_cv_results && (data.latest_cv_results.ship_count !== undefined || data.latest_cv_results.detections_json)) {
                    try {
                        const detRes = await fetch(`/api/scan/${folder}/detections`);
                        if (detRes.ok) {
                            const detData = await detRes.json();
                            if (typeof applyDetectionsToLayer === 'function') {
                                applyDetectionsToLayer(layerId, detData);
                            }
                        }
                    } catch (e) {
                        console.warn("Could not autoload detections for", folder, e);
                    }
                }
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
