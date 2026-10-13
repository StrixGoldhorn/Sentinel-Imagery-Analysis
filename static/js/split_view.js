/**
 * Synchronized Split View for SAR, Optical, Detection Mask, and AIS Ground Truth.
 * Coordinates 2-pane (Dual) or 4-pane (Quad) Leaflet map viewports in lockstep.
 */
(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SynchronizedSplitView = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let currentScanId = null;
    let splitData = null;
    let maps = {}; // modality -> L.Map
    let overlays = {}; // modality -> L.ImageOverlay or L.GeoJSON
    let crosshairMarkers = {}; // modality -> L.Marker
    let isSyncing = false;
    let currentMode = 'quad'; // 'quad' or 'dual'
    let crosshairEnabled = true;

    const MODALITY_CONFIG = {
        sar: {
            title: 'Sentinel-1 SAR Amplitude',
            icon: '📡',
            badgeClass: 'bg-cyan-900/60 text-cyan-300 border-cyan-700/60',
            bg: 'bg-slate-950',
        },
        optical: {
            title: 'Sentinel-2 MSI Optical',
            icon: '🌍',
            badgeClass: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/60',
            bg: 'bg-slate-950',
        },
        mask: {
            title: 'Vessel Detection Mask',
            icon: '🎯',
            badgeClass: 'bg-amber-900/60 text-amber-300 border-amber-700/60',
            bg: 'bg-slate-950',
        },
        ais: {
            title: 'AIS Kinematic Picture',
            icon: '🚢',
            badgeClass: 'bg-blue-900/60 text-blue-300 border-blue-700/60',
            bg: 'bg-slate-950',
        },
    };

    function initSplitModal() {
        let modal = document.getElementById('synchronized-split-modal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.id = 'synchronized-split-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm hidden';
        modal.innerHTML = `
            <div class="relative w-[96vw] max-w-7xl h-[92vh] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col overflow-hidden text-slate-100">
                <!-- Header -->
                <div class="px-5 py-3 bg-slate-800/95 border-b border-slate-700/80 flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <span class="text-xl">🎛️</span>
                        <div>
                            <h3 class="font-bold text-base text-white flex items-center gap-2">
                                Synchronized Multi-Modality Split View
                                <span id="split-scan-badge" class="px-2 py-0.5 text-xs font-mono rounded bg-slate-800 text-slate-300 border border-slate-600">Scan</span>
                            </h3>
                            <p class="text-xs text-slate-400">Lockstep coordinated viewports: SAR, Sentinel-2 Optical, Detection Mask, and AIS Kinematics</p>
                        </div>
                    </div>

                    <!-- Mode & Synchrony Controls -->
                    <div class="flex items-center gap-3">
                        <div class="inline-flex rounded-lg border border-slate-700 bg-slate-950 p-0.5 text-xs">
                            <button id="split-mode-quad" class="px-2.5 py-1 rounded font-medium bg-cyan-700 text-white transition">Quad (2x2)</button>
                            <button id="split-mode-dual" class="px-2.5 py-1 rounded font-medium text-slate-400 hover:text-white transition">Dual (1x2)</button>
                        </div>

                        <label class="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                            <input type="checkbox" id="split-sync-crosshair" checked class="rounded border-slate-700 text-cyan-600 focus:ring-0">
                            <span>Sync Crosshair</span>
                        </label>

                        <button id="split-reset-view" class="px-2.5 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 transition">
                            Reset View
                        </button>

                        <button id="split-modal-close" class="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Grid Viewport Area -->
                <div id="split-grid-container" class="flex-1 grid grid-cols-2 grid-rows-2 gap-1 p-1 bg-slate-950 min-h-0">
                    <!-- SAR Pane -->
                    <div id="pane-container-sar" class="relative flex flex-col bg-slate-900 border border-slate-800 rounded overflow-hidden">
                        <div class="pane-header px-3 py-1.5 bg-slate-850 border-b border-slate-800 flex items-center justify-between text-xs z-10">
                            <div class="flex items-center gap-2">
                                <span>📡</span>
                                <span class="font-semibold text-slate-200">SAR Amplitude</span>
                                <span id="pane-badge-sar" class="px-1.5 py-0.2 rounded text-[10px] bg-cyan-900/60 text-cyan-300 border border-cyan-700/50">VV</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <label class="text-[10px] text-slate-400">Opacity</label>
                                <input type="range" id="opacity-sar" min="0" max="100" value="100" class="w-16 h-1 accent-cyan-500">
                            </div>
                        </div>
                        <div id="split-map-sar" class="flex-1 w-full h-full bg-slate-950"></div>
                    </div>

                    <!-- Optical Pane -->
                    <div id="pane-container-optical" class="relative flex flex-col bg-slate-900 border border-slate-800 rounded overflow-hidden">
                        <div class="pane-header px-3 py-1.5 bg-slate-850 border-b border-slate-800 flex items-center justify-between text-xs z-10">
                            <div class="flex items-center gap-2">
                                <span>🌍</span>
                                <span class="font-semibold text-slate-200">Sentinel-2 Optical</span>
                                <span id="pane-badge-optical" class="px-1.5 py-0.2 rounded text-[10px] bg-emerald-900/60 text-emerald-300 border border-emerald-700/50">RGB</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <label class="text-[10px] text-slate-400">Opacity</label>
                                <input type="range" id="opacity-optical" min="0" max="100" value="100" class="w-16 h-1 accent-emerald-500">
                            </div>
                        </div>
                        <div id="split-map-optical" class="flex-1 w-full h-full bg-slate-950"></div>
                        <div id="optical-unavailable-banner" class="absolute inset-0 top-8 z-20 flex flex-col items-center justify-center bg-slate-900/80 backdrop-blur-xs p-4 text-center hidden">
                            <span class="text-3xl mb-2">☁️</span>
                            <p class="text-xs font-semibold text-slate-200" id="optical-banner-text">Optical Pass Unavailable</p>
                            <p class="text-[11px] text-slate-400 mt-1 max-w-xs">No concurrent Sentinel-2 optical scene was acquired within the temporal threshold.</p>
                        </div>
                    </div>

                    <!-- Detection Mask Pane -->
                    <div id="pane-container-mask" class="relative flex flex-col bg-slate-900 border border-slate-800 rounded overflow-hidden">
                        <div class="pane-header px-3 py-1.5 bg-slate-850 border-b border-slate-800 flex items-center justify-between text-xs z-10">
                            <div class="flex items-center gap-2">
                                <span>🎯</span>
                                <span class="font-semibold text-slate-200">Detection Mask</span>
                                <span id="pane-badge-mask" class="px-1.5 py-0.2 rounded text-[10px] bg-amber-900/60 text-amber-300 border border-amber-700/50">0 Targets</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <label class="text-[10px] text-slate-400">Opacity</label>
                                <input type="range" id="opacity-mask" min="0" max="100" value="100" class="w-16 h-1 accent-amber-500">
                            </div>
                        </div>
                        <div id="split-map-mask" class="flex-1 w-full h-full bg-slate-950"></div>
                    </div>

                    <!-- AIS Kinematics Pane -->
                    <div id="pane-container-ais" class="relative flex flex-col bg-slate-900 border border-slate-800 rounded overflow-hidden">
                        <div class="pane-header px-3 py-1.5 bg-slate-850 border-b border-slate-800 flex items-center justify-between text-xs z-10">
                            <div class="flex items-center gap-2">
                                <span>🚢</span>
                                <span class="font-semibold text-slate-200">AIS Ground Truth</span>
                                <span id="pane-badge-ais" class="px-1.5 py-0.2 rounded text-[10px] bg-blue-900/60 text-blue-300 border border-blue-700/50">0 Vessels</span>
                            </div>
                            <div class="flex items-center gap-2 text-[10px] text-slate-400">
                                <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 mr-1"></span>Correlated
                                <span class="inline-block w-2 h-2 rounded-full bg-cyan-400 ml-2 mr-1"></span>Dark
                            </div>
                        </div>
                        <div id="split-map-ais" class="flex-1 w-full h-full bg-slate-950"></div>
                    </div>
                </div>

                <!-- Footer Status Bar -->
                <div class="px-4 py-2 bg-slate-950 border-t border-slate-800/90 flex items-center justify-between text-xs text-slate-400">
                    <div class="flex items-center gap-4">
                        <div id="cursor-coords-readout" class="font-mono text-slate-300">Lat: --.----°, Lon: --.----°</div>
                        <div id="cursor-scale-readout" class="text-slate-500">Zoom: --</div>
                    </div>
                    <div class="flex items-center gap-4 text-[11px]" id="split-footer-summary">
                        <span>4 synchronized viewports active</span>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Bind Controls
        document.getElementById('split-modal-close').onclick = closeModal;
        document.getElementById('split-mode-quad').onclick = () => setSplitMode('quad');
        document.getElementById('split-mode-dual').onclick = () => setSplitMode('dual');
        document.getElementById('split-reset-view').onclick = resetView;
        document.getElementById('split-sync-crosshair').onchange = (e) => {
            crosshairEnabled = e.target.checked;
            if (!crosshairEnabled) {
                removeCrosshairs();
            }
        };

        // Opacity sliders
        ['sar', 'optical', 'mask'].forEach(modality => {
            const slider = document.getElementById(`opacity-${modality}`);
            if (slider) {
                slider.oninput = (e) => {
                    const val = parseFloat(e.target.value) / 100.0;
                    if (overlays[modality] && overlays[modality].setOpacity) {
                        overlays[modality].setOpacity(val);
                    }
                };
            }
        });

        return modal;
    }

    function setSplitMode(mode) {
        currentMode = mode;
        const container = document.getElementById('split-grid-container');
        const quadBtn = document.getElementById('split-mode-quad');
        const dualBtn = document.getElementById('split-mode-dual');
        const maskPane = document.getElementById('pane-container-mask');
        const aisPane = document.getElementById('pane-container-ais');

        if (mode === 'dual') {
            container.className = 'flex-1 grid grid-cols-2 grid-rows-1 gap-1 p-1 bg-slate-950 min-h-0';
            maskPane.classList.add('hidden');
            aisPane.classList.add('hidden');
            dualBtn.className = 'px-2.5 py-1 rounded font-medium bg-cyan-700 text-white transition';
            quadBtn.className = 'px-2.5 py-1 rounded font-medium text-slate-400 hover:text-white transition';
        } else {
            container.className = 'flex-1 grid grid-cols-2 grid-rows-2 gap-1 p-1 bg-slate-950 min-h-0';
            maskPane.classList.remove('hidden');
            aisPane.classList.remove('hidden');
            quadBtn.className = 'px-2.5 py-1 rounded font-medium bg-cyan-700 text-white transition';
            dualBtn.className = 'px-2.5 py-1 rounded font-medium text-slate-400 hover:text-white transition';
        }

        // Invalidate map sizes
        setTimeout(() => {
            Object.values(maps).forEach(m => m.invalidateSize());
        }, 150);
    }

    function createCrosshairIcon() {
        return L.divIcon({
            className: 'synchronized-crosshair-icon',
            html: `
                <div style="position:relative; width:24px; height:24px; transform:translate(-12px, -12px); pointer-events:none;">
                    <div style="position:absolute; top:11px; left:0; width:24px; height:2px; background:rgba(0,255,255,0.9); box-shadow:0 0 4px #00ffff;"></div>
                    <div style="position:absolute; top:0; left:11px; width:2px; height:24px; background:rgba(0,255,255,0.9); box-shadow:0 0 4px #00ffff;"></div>
                    <div style="position:absolute; top:8px; left:8px; width:8px; height:8px; border-radius:50%; border:1.5px solid #00ffff;"></div>
                </div>
            `,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
        });
    }

    function updateCrosshair(latlng) {
        if (!crosshairEnabled) return;
        const modalities = currentMode === 'dual' ? ['sar', 'optical'] : ['sar', 'optical', 'mask', 'ais'];

        modalities.forEach(mod => {
            const map = maps[mod];
            if (!map) return;

            if (!crosshairMarkers[mod]) {
                crosshairMarkers[mod] = L.marker(latlng, {
                    icon: createCrosshairIcon(),
                    interactive: false,
                    zIndexOffset: 1000,
                }).addTo(map);
            } else {
                crosshairMarkers[mod].setLatLng(latlng);
            }
        });

        const readout = document.getElementById('cursor-coords-readout');
        if (readout) {
            readout.textContent = `Lat: ${latlng.lat.toFixed(5)}°, Lon: ${latlng.lng.toFixed(5)}°`;
        }
    }

    function removeCrosshairs() {
        Object.keys(crosshairMarkers).forEach(mod => {
            if (crosshairMarkers[mod] && maps[mod]) {
                maps[mod].removeLayer(crosshairMarkers[mod]);
                delete crosshairMarkers[mod];
            }
        });
    }

    function syncViewports(sourceModality, center, zoom) {
        if (isSyncing) return;
        isSyncing = true;

        Object.keys(maps).forEach(targetModality => {
            if (targetModality !== sourceModality && maps[targetModality]) {
                maps[targetModality].setView(center, zoom, { animate: false });
            }
        });

        const scaleReadout = document.getElementById('cursor-scale-readout');
        if (scaleReadout) {
            scaleReadout.textContent = `Zoom: ${zoom}`;
        }

        isSyncing = false;
    }

    async function openModal(scanId) {
        currentScanId = scanId;
        const modal = initSplitModal();
        modal.classList.remove('hidden');

        document.getElementById('split-scan-badge').textContent = scanId;

        try {
            const res = await fetch(`/api/scans/${encodeURIComponent(scanId)}/synchronized-views`);
            if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load split view`);
            splitData = await res.json();
            setupMaps(splitData);
        } catch (err) {
            console.error('Split view error:', err);
            alert(`Unable to load synchronized split view: ${err.message}`);
            closeModal();
        }
    }

    function setupMaps(data) {
        const vp = data.viewport;
        const bounds = L.latLngBounds(vp.bounds);
        const center = L.latLng(vp.center_lat, vp.center_lon);
        const zoom = vp.initial_zoom || 12;

        const modalities = ['sar', 'optical', 'mask', 'ais'];

        modalities.forEach(mod => {
            const mapElem = document.getElementById(`split-map-${mod}`);
            if (!mapElem) return;

            // Cleanup old map instance if present
            if (maps[mod]) {
                maps[mod].remove();
                delete maps[mod];
            }

            const map = L.map(mapElem, {
                center: center,
                zoom: zoom,
                zoomControl: false,
                attributionControl: false,
                maxBounds: bounds.pad(0.5),
            });

            // Base map tile layer (CartoDB Dark Matter)
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                maxZoom: 19,
                subdomains: 'abcd',
            }).addTo(map);

            // Bind synchrony events
            map.on('move', () => {
                if (!isSyncing) {
                    syncViewports(mod, map.getCenter(), map.getZoom());
                }
            });

            map.on('mousemove', (e) => {
                updateCrosshair(e.latlng);
            });

            maps[mod] = map;
        });

        // 1. SAR Pane
        const sarPane = data.panes.sar;
        if (sarPane && sarPane.image_url) {
            overlays.sar = L.imageOverlay(sarPane.image_url, bounds).addTo(maps.sar);
            const pols = (sarPane.metadata && sarPane.metadata.polarizations) ? sarPane.metadata.polarizations.join('/') : 'VV';
            document.getElementById('pane-badge-sar').textContent = pols;
        }

        // 2. Optical Pane
        const optPane = data.panes.optical;
        const banner = document.getElementById('optical-unavailable-banner');
        if (optPane && optPane.available && optPane.image_url) {
            overlays.optical = L.imageOverlay(optPane.image_url, bounds).addTo(maps.optical);
            banner.classList.add('hidden');
            const clouds = optPane.metadata && optPane.metadata.cloud_cover_pct !== null
                ? `${optPane.metadata.cloud_cover_pct.toFixed(0)}% Clouds`
                : 'RGB';
            document.getElementById('pane-badge-optical').textContent = clouds;
        } else {
            banner.classList.remove('hidden');
            document.getElementById('optical-banner-text').textContent = optPane.status_message || 'Optical Pass Unavailable';
            document.getElementById('pane-badge-optical').textContent = 'No Pass';
        }

        // 3. Detection Mask Pane
        const maskPane = data.panes.mask;
        if (maskPane) {
            // SAR base layer underneath mask
            if (sarPane && sarPane.image_url) {
                L.imageOverlay(sarPane.image_url, bounds, { opacity: 0.65 }).addTo(maps.mask);
            }
            // PNG Mask overlay
            if (maskPane.image_url) {
                overlays.mask = L.imageOverlay(maskPane.image_url, bounds).addTo(maps.mask);
            }
            // Interactive vector GeoJSON
            if (maskPane.vector_geojson) {
                L.geoJSON(maskPane.vector_geojson, {
                    style: (feature) => ({
                        color: feature.properties.is_dark ? '#00f0ff' : '#00ff80',
                        weight: 2,
                        fillColor: feature.properties.is_dark ? '#00f0ff' : '#00ff80',
                        fillOpacity: 0.35,
                    }),
                    onEachFeature: (feature, layer) => {
                        const p = feature.properties;
                        const status = p.is_dark ? '⚠️ Non-Broadcasting Dark Target' : '✅ AIS Correlated Target';
                        layer.bindPopup(`
                            <div class="text-xs space-y-1">
                                <div class="font-bold text-slate-100">${status}</div>
                                <div>Confidence: <span class="font-mono text-cyan-300">${(p.confidence * 100).toFixed(1)}%</span></div>
                                ${p.length ? `<div>Estimated Length: <span class="font-mono">${p.length.toFixed(1)}m</span></div>` : ''}
                                ${p.beam ? `<div>Beam: <span class="font-mono">${p.beam.toFixed(1)}m</span></div>` : ''}
                                ${p.mmsi ? `<div>MMSI: <span class="font-mono text-amber-300">${p.mmsi}</span></div>` : ''}
                            </div>
                        `);
                    },
                }).addTo(maps.mask);
            }
            const detCount = maskPane.metadata ? maskPane.metadata.total_detections : 0;
            document.getElementById('pane-badge-mask').textContent = `${detCount} Targets`;
        }

        // 4. AIS Pane
        const aisPane = data.panes.ais;
        if (aisPane && aisPane.vector_geojson) {
            overlays.ais = L.geoJSON(aisPane.vector_geojson, {
                pointToLayer: (feature, latlng) => {
                    const isCorr = feature.properties.is_correlated;
                    const color = isCorr ? '#10b981' : '#38bdf8';
                    return L.circleMarker(latlng, {
                        radius: 6,
                        fillColor: color,
                        color: '#ffffff',
                        weight: 1.5,
                        opacity: 1,
                        fillOpacity: 0.85,
                    });
                },
                onEachFeature: (feature, layer) => {
                    const p = feature.properties;
                    layer.bindPopup(`
                        <div class="text-xs space-y-1">
                            <div class="font-bold text-slate-100">${p.vessel_name || 'Vessel'}</div>
                            <div>MMSI: <span class="font-mono text-cyan-300">${p.mmsi}</span></div>
                            ${p.speed !== null && p.speed !== undefined ? `<div>Speed: <span class="font-mono">${p.speed} kts</span></div>` : ''}
                            ${p.heading !== null && p.heading !== undefined ? `<div>Heading: <span class="font-mono">${p.heading}°</span></div>` : ''}
                            <div>Type: <span class="font-medium">${p.vessel_type || 'Unknown'}</span></div>
                        </div>
                    `);
                },
            }).addTo(maps.ais);

            const contacts = aisPane.metadata ? aisPane.metadata.contact_count : 0;
            document.getElementById('pane-badge-ais').textContent = `${contacts} Vessels`;
        }

        setTimeout(() => {
            resetView();
        }, 150);
    }

    function resetView() {
        if (!splitData) return;
        const vp = splitData.viewport;
        const center = L.latLng(vp.center_lat, vp.center_lon);
        const zoom = vp.initial_zoom || 12;

        Object.values(maps).forEach(m => {
            m.invalidateSize();
            m.setView(center, zoom, { animate: false });
        });
    }

    function closeModal() {
        const modal = document.getElementById('synchronized-split-modal');
        if (modal) modal.classList.add('hidden');
        removeCrosshairs();
    }

    return {
        open: openModal,
        close: closeModal,
        setMode: setSplitMode,
        resetView: resetView,
    };
}));
