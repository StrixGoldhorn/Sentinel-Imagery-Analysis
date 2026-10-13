/**
 * Tactical Geospatial & Maritime Measurement Tools:
 * - Distance & True North Bearing
 * - Polyline cumulative tracking
 * - Spherical polygon enclosed area
 * - Navigational corridor buffer generation
 * - Vessel waterline dimension & classification tool
 */
(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.TacticalMeasurements = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let map = null; // Leaflet map reference
    let activeTool = null; // 'distance' | 'polyline' | 'area' | 'corridor' | 'vessel'
    let currentScanId = null;
    let clickedPoints = [];
    let drawLayers = [];
    let corridorWidthNM = 2.0;

    function initToolbar() {
        let bar = document.getElementById('tactical-measurement-toolbar');
        if (bar) return bar;

        bar = document.createElement('div');
        bar.id = 'tactical-measurement-toolbar';
        bar.className = 'fixed left-16 top-20 z-40 bg-slate-900/95 border border-cyan-800/60 backdrop-blur-md rounded-xl shadow-2xl p-2.5 flex flex-col gap-2 text-slate-100 w-72';
        bar.innerHTML = `
            <!-- Header -->
            <div class="flex items-center justify-between border-b border-slate-700/80 pb-2 px-1">
                <div class="flex items-center gap-2">
                    <span class="text-base">📐</span>
                    <h3 class="font-bold text-xs uppercase tracking-wider text-cyan-400">Tactical Measurements</h3>
                </div>
                <button id="meas-close-btn" class="p-1 text-slate-400 hover:text-white rounded transition">✕</button>
            </div>

            <!-- Tool Selector Buttons -->
            <div class="grid grid-cols-2 gap-1.5 text-xs">
                <button id="meas-tool-distance" class="meas-btn p-1.5 rounded bg-slate-800 hover:bg-cyan-950 border border-slate-700 hover:border-cyan-500 text-left flex items-center gap-1.5 transition">
                    <span>📏</span>
                    <span class="font-medium text-[11px]">Distance / Bearing</span>
                </button>
                <button id="meas-tool-polyline" class="meas-btn p-1.5 rounded bg-slate-800 hover:bg-cyan-950 border border-slate-700 hover:border-cyan-500 text-left flex items-center gap-1.5 transition">
                    <span>➰</span>
                    <span class="font-medium text-[11px]">Route Track</span>
                </button>
                <button id="meas-tool-area" class="meas-btn p-1.5 rounded bg-slate-800 hover:bg-cyan-950 border border-slate-700 hover:border-cyan-500 text-left flex items-center gap-1.5 transition">
                    <span>⬡</span>
                    <span class="font-medium text-[11px]">Geodesic Area</span>
                </button>
                <button id="meas-tool-corridor" class="meas-btn p-1.5 rounded bg-slate-800 hover:bg-cyan-950 border border-slate-700 hover:border-cyan-500 text-left flex items-center gap-1.5 transition">
                    <span>🛣️</span>
                    <span class="font-medium text-[11px]">Buffer Corridor</span>
                </button>
                <button id="meas-tool-vessel" class="meas-btn p-1.5 rounded bg-slate-800 hover:bg-cyan-950 border border-slate-700 hover:border-cyan-500 text-left flex items-center gap-1.5 transition col-span-2">
                    <span>🚢</span>
                    <span class="font-medium text-[11px]">Vessel Hull Dimension (SAR)</span>
                </button>
            </div>

            <!-- Corridor Width Configuration -->
            <div id="meas-corridor-cfg" class="p-2 bg-slate-950/80 border border-slate-800 rounded hidden space-y-1 text-xs">
                <div class="flex justify-between items-center text-slate-300">
                    <label>Corridor Width (NM)</label>
                    <input id="meas-corridor-width-input" type="number" min="0.1" max="50" step="0.5" value="2.0"
                           class="w-16 bg-slate-900 border border-slate-700 text-cyan-300 font-mono text-center rounded px-1 py-0.5">
                </div>
            </div>

            <!-- Dynamic Readout Result Card -->
            <div id="meas-result-card" class="p-2.5 bg-slate-950/90 border border-cyan-900/60 rounded-lg text-xs space-y-1.5 min-h-[58px]">
                <div class="text-[11px] text-slate-400 italic">Select a tool above and click on the map to measure.</div>
            </div>

            <!-- Action Controls -->
            <div class="flex items-center gap-2 pt-1 border-t border-slate-800">
                <button id="meas-finish-btn" class="flex-1 py-1 bg-cyan-700 hover:bg-cyan-600 text-white rounded font-medium text-xs transition">
                    Complete / Finish
                </button>
                <button id="meas-clear-btn" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition">
                    Clear
                </button>
            </div>
        `;

        document.body.appendChild(bar);
        bindEvents(bar);
        return bar;
    }

    function bindEvents(bar) {
        bar.querySelector('#meas-close-btn').addEventListener('click', () => {
            deactivateCurrentTool();
            bar.classList.add('hidden');
        });

        const toolBtns = {
            distance: bar.querySelector('#meas-tool-distance'),
            polyline: bar.querySelector('#meas-tool-polyline'),
            area: bar.querySelector('#meas-tool-area'),
            corridor: bar.querySelector('#meas-tool-corridor'),
            vessel: bar.querySelector('#meas-tool-vessel'),
        };

        for (const [toolKey, btn] of Object.entries(toolBtns)) {
            if (btn) {
                btn.addEventListener('click', () => {
                    setTool(toolKey);
                });
            }
        }

        const widthInput = bar.querySelector('#meas-corridor-width-input');
        if (widthInput) {
            widthInput.addEventListener('change', (e) => {
                corridorWidthNM = Math.max(0.1, parseFloat(e.target.value) || 2.0);
                if (activeTool === 'corridor' && clickedPoints.length >= 2) {
                    computeCorridor();
                }
            });
        }

        const finishBtn = bar.querySelector('#meas-finish-btn');
        if (finishBtn) {
            finishBtn.addEventListener('click', () => {
                finalizeCurrentMeasurement();
            });
        }

        const clearBtn = bar.querySelector('#meas-clear-btn');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                clearDrawings();
            });
        }
    }

    function setTool(toolKey) {
        activeTool = toolKey;
        clickedPoints = [];
        clearDrawings();

        // Update button active states
        document.querySelectorAll('.meas-btn').forEach((b) => {
            b.classList.remove('bg-cyan-900/80', 'border-cyan-400', 'text-white');
            b.classList.add('bg-slate-800', 'border-slate-700', 'text-slate-300');
        });
        const activeBtn = document.getElementById(`meas-tool-${toolKey}`);
        if (activeBtn) {
            activeBtn.classList.add('bg-cyan-900/80', 'border-cyan-400', 'text-white');
            activeBtn.classList.remove('bg-slate-800', 'border-slate-700', 'text-slate-300');
        }

        const corridorCfg = document.getElementById('meas-corridor-cfg');
        if (corridorCfg) {
            if (toolKey === 'corridor') {
                corridorCfg.classList.remove('hidden');
            } else {
                corridorCfg.classList.add('hidden');
            }
        }

        const resultCard = document.getElementById('meas-result-card');
        if (resultCard) {
            let prompt = 'Click points on the map to measure.';
            if (toolKey === 'distance') prompt = 'Click start point, then end point.';
            else if (toolKey === 'polyline') prompt = 'Click consecutive waypoints; click Finish when done.';
            else if (toolKey === 'area') prompt = 'Click 3 or more vertices to enclose an area.';
            else if (toolKey === 'corridor') prompt = 'Click centerline route waypoints to build corridor buffer.';
            else if (toolKey === 'vessel') prompt = 'Click bow (front) and stern (rear) of vessel on imagery.';

            resultCard.innerHTML = `<div class="text-[11px] text-cyan-300 font-medium">${prompt}</div>`;
        }
    }

    function deactivateCurrentTool() {
        activeTool = null;
        clickedPoints = [];
        clearDrawings();
    }

    function clearDrawings() {
        clickedPoints = [];
        if (map && typeof map.removeLayer === 'function') {
            drawLayers.forEach((layer) => {
                try {
                    map.removeLayer(layer);
                } catch (e) {}
            });
        }
        drawLayers = [];
        const resultCard = document.getElementById('meas-result-card');
        if (resultCard) {
            resultCard.innerHTML = `<div class="text-[11px] text-slate-400 italic">Measurements cleared. Click on map to start.</div>`;
        }
    }

    function handleMapClick(e) {
        if (!activeTool) return;

        const lat = e.latlng.lat;
        const lon = e.latlng.lng;
        clickedPoints.push({ lat, lon });

        // Add visual marker
        if (map && window.L) {
            const marker = window.L.circleMarker([lat, lon], {
                radius: 4,
                color: '#06b6d4',
                fillColor: '#22d3ee',
                fillOpacity: 0.9,
                weight: 2,
            }).addTo(map);
            drawLayers.push(marker);
        }

        if (activeTool === 'distance' && clickedPoints.length === 2) {
            computeDistanceBearing();
        } else if (activeTool === 'polyline' && clickedPoints.length >= 2) {
            drawCurrentPolyline();
            computePolyline();
        } else if (activeTool === 'area' && clickedPoints.length >= 3) {
            drawCurrentPolygon();
            computeArea();
        } else if (activeTool === 'corridor' && clickedPoints.length >= 2) {
            drawCurrentPolyline();
            computeCorridor();
        } else if (activeTool === 'vessel' && clickedPoints.length === 2) {
            computeVesselDimension();
        }
    }

    function drawCurrentPolyline() {
        if (!map || !window.L || clickedPoints.length < 2) return;
        const latlngs = clickedPoints.map((p) => [p.lat, p.lon]);
        const line = window.L.polyline(latlngs, {
            color: '#06b6d4',
            weight: 3,
            dashArray: '4, 4',
        }).addTo(map);
        drawLayers.push(line);
    }

    function drawCurrentPolygon() {
        if (!map || !window.L || clickedPoints.length < 3) return;
        const latlngs = clickedPoints.map((p) => [p.lat, p.lon]);
        const poly = window.L.polygon(latlngs, {
            color: '#38bdf8',
            fillColor: '#0284c7',
            fillOpacity: 0.25,
            weight: 2,
        }).addTo(map);
        drawLayers.push(poly);
    }

    async function computeDistanceBearing() {
        const p1 = clickedPoints[0];
        const p2 = clickedPoints[1];

        // Draw connecting line
        if (map && window.L) {
            const line = window.L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
                color: '#38bdf8',
                weight: 3,
            }).addTo(map);
            drawLayers.push(line);
        }

        try {
            const res = await fetch('/api/measurements/distance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    lat1: p1.lat,
                    lon1: p1.lon,
                    lat2: p2.lat,
                    lon2: p2.lon,
                }),
            });
            const data = await res.json();
            if (data.success && data.result) {
                renderDistanceResult(data.result);
            }
        } catch (err) {
            console.error('Distance calculation error:', err);
        }
    }

    function renderDistanceResult(r) {
        const resultCard = document.getElementById('meas-result-card');
        if (!resultCard) return;

        resultCard.innerHTML = `
            <div class="space-y-1">
                <div class="flex justify-between items-center text-cyan-400 font-bold border-b border-slate-800 pb-1">
                    <span>Geodesic Distance</span>
                    <span class="text-sm font-mono">${r.distance.nautical_miles} NM</span>
                </div>
                <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                    <div>Kilometers: <span class="font-mono text-white">${r.distance.kilometers} km</span></div>
                    <div>Meters: <span class="font-mono text-white">${r.distance.meters} m</span></div>
                    <div>Bearing (Fwd): <span class="font-mono text-amber-300">${r.bearing.initial_bearing_deg}° T</span></div>
                    <div>Heading: <span class="font-bold text-emerald-400">${r.bearing.compass_cardinal}</span></div>
                    <div class="col-span-2 text-slate-400 text-[10px]">Back Bearing: ${r.bearing.final_bearing_deg}° True</div>
                </div>
            </div>
        `;
    }

    async function computePolyline() {
        try {
            const res = await fetch('/api/measurements/distance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ points: clickedPoints }),
            });
            const data = await res.json();
            if (data.success && data.result) {
                renderPolylineResult(data.result);
            }
        } catch (err) {
            console.error('Polyline calculation error:', err);
        }
    }

    function renderPolylineResult(r) {
        const resultCard = document.getElementById('meas-result-card');
        if (!resultCard) return;

        resultCard.innerHTML = `
            <div class="space-y-1">
                <div class="flex justify-between items-center text-cyan-400 font-bold border-b border-slate-800 pb-1">
                    <span>Route Track Distance</span>
                    <span class="text-sm font-mono">${r.total_distance.nautical_miles} NM</span>
                </div>
                <div class="flex justify-between text-[11px] text-slate-300">
                    <span>Segments: <b class="text-white">${r.segment_count}</b></span>
                    <span>Total: <b class="text-white">${r.total_distance.kilometers} km</b></span>
                </div>
            </div>
        `;
    }

    async function computeArea() {
        try {
            const res = await fetch('/api/measurements/area', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ vertices: clickedPoints }),
            });
            const data = await res.json();
            if (data.success && data.result) {
                renderAreaResult(data.result);
            }
        } catch (err) {
            console.error('Area calculation error:', err);
        }
    }

    function renderAreaResult(r) {
        const resultCard = document.getElementById('meas-result-card');
        if (!resultCard) return;

        resultCard.innerHTML = `
            <div class="space-y-1">
                <div class="flex justify-between items-center text-cyan-400 font-bold border-b border-slate-800 pb-1">
                    <span>Enclosed Geodesic Area</span>
                    <span class="text-sm font-mono">${r.area.sq_nautical_miles} NM²</span>
                </div>
                <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                    <div>Sq Km: <span class="font-mono text-white">${r.area.sq_kilometers} km²</span></div>
                    <div>Hectares: <span class="font-mono text-white">${r.area.hectares} ha</span></div>
                    <div class="col-span-2">Perimeter: <span class="font-mono text-white">${r.perimeter.nautical_miles} NM</span> (${r.perimeter.meters} m)</div>
                </div>
            </div>
        `;
    }

    async function computeCorridor() {
        try {
            const res = await fetch('/api/measurements/corridor', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    centerline: clickedPoints,
                    width: corridorWidthNM,
                    unit: 'NM',
                }),
            });
            const data = await res.json();
            if (data.success && data.result) {
                renderCorridorResult(data.result);

                // Draw corridor polygon on map
                if (map && window.L && data.result.geometry) {
                    const geojsonLayer = window.L.geoJSON(data.result.geometry, {
                        style: {
                            color: '#10b981',
                            fillColor: '#059669',
                            fillOpacity: 0.2,
                            weight: 2,
                            dashArray: '5, 5',
                        },
                    }).addTo(map);
                    drawLayers.push(geojsonLayer);
                }
            }
        } catch (err) {
            console.error('Corridor calculation error:', err);
        }
    }

    function renderCorridorResult(r) {
        const resultCard = document.getElementById('meas-result-card');
        if (!resultCard) return;

        resultCard.innerHTML = `
            <div class="space-y-1">
                <div class="flex justify-between items-center text-emerald-400 font-bold border-b border-slate-800 pb-1">
                    <span>Corridor Envelope</span>
                    <span class="text-sm font-mono">${r.corridor_area.sq_nautical_miles} NM²</span>
                </div>
                <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                    <div>Centerline: <span class="font-mono text-white">${r.centerline_distance.nautical_miles} NM</span></div>
                    <div>Width: <span class="font-mono text-amber-300">${r.width.nautical_miles} NM</span></div>
                    <div class="col-span-2">Area: <span class="font-mono text-white">${r.corridor_area.sq_kilometers} km²</span></div>
                </div>
            </div>
        `;
    }

    async function computeVesselDimension() {
        const p1 = clickedPoints[0];
        const p2 = clickedPoints[1];

        // Draw hull centerline
        if (map && window.L) {
            const line = window.L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
                color: '#f59e0b',
                weight: 4,
            }).addTo(map);
            drawLayers.push(line);
        }

        // Compute direct meters using geodesic endpoints or pixel mapping
        const latMid = (p1.lat + p2.lat) / 2.0;
        const metersPerDegLat = 111132.95;
        const metersPerDegLon = 111412.84 * Math.cos((latMid * Math.PI) / 180.0);

        const dyMeters = (p2.lat - p1.lat) * metersPerDegLat;
        const dxMeters = (p2.lon - p1.lon) * metersPerDegLon;
        const totalLenMeters = Math.hypot(dxMeters, dyMeters);

        // Assume Sentinel-1 10m/px default scale
        const pxLen = totalLenMeters / 10.0;

        try {
            const res = await fetch('/api/measurements/vessel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    x1: 0,
                    y1: 0,
                    x2: dxMeters / 10.0,
                    y2: -dyMeters / 10.0,
                    scan_id: currentScanId,
                    meters_per_pixel: 10.0,
                }),
            });
            const data = await res.json();
            if (data.success && data.result) {
                renderVesselResult(data.result);
            }
        } catch (err) {
            console.error('Vessel dimension calculation error:', err);
        }
    }

    function renderVesselResult(r) {
        const resultCard = document.getElementById('meas-result-card');
        if (!resultCard) return;

        resultCard.innerHTML = `
            <div class="space-y-1">
                <div class="flex justify-between items-center text-amber-400 font-bold border-b border-slate-800 pb-1">
                    <span>Vessel Waterline Dimensions</span>
                    <span class="text-sm font-mono">${r.dimensions.length_m} m</span>
                </div>
                <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                    <div>Length: <span class="font-bold text-white">${r.dimensions.length_m} m</span></div>
                    <div>Beam (Est): <span class="font-mono text-white">${r.dimensions.beam_m} m</span></div>
                    <div>Aspect Ratio: <span class="font-mono text-cyan-300">1 : ${r.dimensions.aspect_ratio}</span></div>
                    <div>Heading: <span class="font-mono text-amber-300">${r.dimensions.heading_deg}° True</span></div>
                </div>
                <div class="p-1.5 bg-slate-900 rounded border border-amber-900/60 mt-1">
                    <div class="text-[10px] uppercase tracking-wider font-bold text-amber-400">${r.classification.estimated_class}</div>
                    <div class="text-[10px] text-slate-400 leading-tight">${r.classification.tactical_assessment}</div>
                </div>
            </div>
        `;
    }

    function finalizeCurrentMeasurement() {
        if (activeTool === 'polyline') {
            computePolyline();
        } else if (activeTool === 'area') {
            computeArea();
        } else if (activeTool === 'corridor') {
            computeCorridor();
        }
    }

    return {
        init: function (leafletMap, scanId) {
            map = leafletMap;
            currentScanId = scanId;
            const bar = initToolbar();
            bar.classList.remove('hidden');

            if (map && typeof map.on === 'function') {
                map.on('click', handleMapClick);
            }
        },
        open: function (leafletMap, scanId) {
            this.init(leafletMap, scanId);
        },
        close: function () {
            deactivateCurrentTool();
            const bar = document.getElementById('tactical-measurement-toolbar');
            if (bar) bar.classList.add('hidden');
        },
        setTool: function (toolKey) {
            setTool(toolKey);
        },
        clear: function () {
            clearDrawings();
        },
    };
}));
