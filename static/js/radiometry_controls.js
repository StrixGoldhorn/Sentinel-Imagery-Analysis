/**
 * Interactive SAR Radiometry, Histogram, Contrast, Gamma, and Despeckling Controls.
 */
(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SarRadiometry = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let currentScanId = null;
    let targetLayer = null; // Leaflet ImageOverlay or callback
    let histogramData = null;
    let debounceTimer = null;

    let state = {
        contrastMin: 0,
        contrastMax: 255,
        gamma: 1.0,
        logStretch: false,
        despeckle: 'none',
        kernelSize: 5,
        polarization: 'VV',
    };

    function initPanel() {
        let panel = document.getElementById('sar-radiometry-panel');
        if (panel) return panel;

        panel = document.createElement('div');
        panel.id = 'sar-radiometry-panel';
        panel.className = 'fixed right-4 top-20 z-40 w-80 bg-slate-900/95 border border-slate-700/80 backdrop-blur-md rounded-xl shadow-2xl flex flex-col text-slate-100 hidden';
        panel.innerHTML = `
            <!-- Header -->
            <div class="px-4 py-2.5 bg-slate-800/90 border-b border-slate-700/80 flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <span class="text-base">🎚️</span>
                    <h3 class="font-bold text-sm text-white">SAR Radiometry & Despeckling</h3>
                </div>
                <button id="radiometry-panel-close" class="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>

            <!-- Body -->
            <div class="p-4 space-y-4 max-h-[80vh] overflow-y-auto text-xs">
                <!-- Polarization & Presets -->
                <div class="space-y-1.5">
                    <div class="flex items-center justify-between">
                        <label class="text-slate-400 font-semibold">Polarization</label>
                        <select id="rad-pol-select" class="bg-slate-950 border border-slate-700 text-cyan-300 rounded px-2 py-0.5 text-xs">
                            <option value="VV">VV (Co-Pol)</option>
                            <option value="VH">VH (Cross-Pol)</option>
                        </select>
                    </div>
                    <div class="flex items-center justify-between">
                        <label class="text-slate-400 font-semibold">Quick Preset</label>
                        <select id="rad-preset-select" class="bg-slate-950 border border-slate-700 text-slate-200 rounded px-2 py-0.5 text-xs">
                            <option value="DEFAULT">Default (Raw)</option>
                            <option value="HIGH_CONTRAST">High Contrast</option>
                            <option value="LEE_DESPECKLE">Lee Filter Despeckle</option>
                            <option value="DARK_VESSEL_BOOST">Dark Target Boost</option>
                            <option value="OCEAN_CLUTTER_SUPPRESS">Clutter Suppress</option>
                        </select>
                    </div>
                </div>

                <!-- Histogram Canvas -->
                <div class="space-y-1 bg-slate-950 p-2 rounded-lg border border-slate-800">
                    <div class="flex items-center justify-between text-[11px] text-slate-400">
                        <span>Intensity Histogram (256 bins)</span>
                        <span id="rad-stats-readout" class="font-mono text-cyan-400">μ: -- | σ: --</span>
                    </div>
                    <canvas id="rad-histogram-canvas" width="280" height="90" class="w-full h-[90px] rounded bg-slate-900"></canvas>
                    <div class="flex items-center justify-between text-[10px] text-slate-500 font-mono">
                        <span>0</span>
                        <span id="rad-hist-min-label">Min: 0</span>
                        <span id="rad-hist-max-label">Max: 255</span>
                        <span>255</span>
                    </div>
                </div>

                <!-- Contrast Stretching -->
                <div class="space-y-2">
                    <div class="flex items-center justify-between">
                        <label class="text-slate-300 font-medium">Contrast Window (Min / Max)</label>
                        <span id="rad-contrast-readout" class="font-mono text-slate-400 text-[11px]">0 - 255</span>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <span class="text-[10px] text-slate-500">Black Point:</span>
                            <input type="range" id="rad-contrast-min" min="0" max="250" value="0" class="w-full h-1 accent-cyan-500">
                        </div>
                        <div>
                            <span class="text-[10px] text-slate-500">White Point:</span>
                            <input type="range" id="rad-contrast-max" min="5" max="255" value="255" class="w-full h-1 accent-cyan-500">
                        </div>
                    </div>
                </div>

                <!-- Gamma Correction -->
                <div class="space-y-1">
                    <div class="flex items-center justify-between">
                        <label class="text-slate-300 font-medium">Gamma Correction</label>
                        <span id="rad-gamma-readout" class="font-mono text-cyan-400 text-[11px]">1.00</span>
                    </div>
                    <input type="range" id="rad-gamma" min="20" max="300" value="100" class="w-full h-1 accent-cyan-500">
                    <div class="flex justify-between text-[10px] text-slate-500">
                        <span>0.2 (Brighten)</span>
                        <span>1.0 (Linear)</span>
                        <span>3.0 (Darken)</span>
                    </div>
                </div>

                <!-- Log Stretch Checkbox -->
                <div class="flex items-center justify-between">
                    <label class="text-slate-300 font-medium cursor-pointer flex items-center gap-2">
                        <input type="checkbox" id="rad-log-stretch" class="rounded border-slate-700 text-cyan-600 focus:ring-0">
                        <span>Logarithmic Dynamic Range</span>
                    </label>
                    <span class="text-[10px] text-slate-500 font-mono">log(1+9x)</span>
                </div>

                <!-- Despeckle Filtering -->
                <div class="space-y-2 pt-2 border-t border-slate-800">
                    <div class="flex items-center justify-between">
                        <label class="text-slate-300 font-medium">Despeckle Filter</label>
                        <select id="rad-despeckle-select" class="bg-slate-950 border border-slate-700 text-slate-200 rounded px-2 py-0.5 text-xs">
                            <option value="none">None (Raw Speckle)</option>
                            <option value="lee">Lee Adaptive Filter</option>
                            <option value="frost">Frost Damped Filter</option>
                            <option value="median">Median Rank Filter</option>
                            <option value="boxcar">Boxcar Mean Averaging</option>
                        </select>
                    </div>

                    <div class="flex items-center justify-between">
                        <label class="text-slate-400">Kernel Window</label>
                        <select id="rad-kernel-select" class="bg-slate-950 border border-slate-700 text-slate-200 rounded px-2 py-0.5 text-xs">
                            <option value="3">3 x 3 Window</option>
                            <option value="5" selected>5 x 5 Window</option>
                            <option value="7">7 x 7 Window</option>
                            <option value="9">9 x 9 Window</option>
                        </select>
                    </div>
                </div>

                <!-- Reset Button -->
                <div class="pt-2 flex justify-end">
                    <button id="rad-reset-btn" class="px-3 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 rounded text-xs transition">
                        Reset Defaults
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(panel);

        // Bind Events
        document.getElementById('radiometry-panel-close').onclick = hidePanel;
        document.getElementById('rad-reset-btn').onclick = resetDefaults;

        document.getElementById('rad-pol-select').onchange = (e) => {
            state.polarization = e.target.value;
            fetchHistogram();
            scheduleUpdate();
        };

        document.getElementById('rad-preset-select').onchange = (e) => {
            applyPreset(e.target.value);
        };

        document.getElementById('rad-contrast-min').oninput = (e) => {
            state.contrastMin = parseFloat(e.target.value);
            if (state.contrastMin >= state.contrastMax) {
                state.contrastMin = state.contrastMax - 1;
                e.target.value = state.contrastMin;
            }
            updateContrastReadout();
            drawHistogram();
            scheduleUpdate();
        };

        document.getElementById('rad-contrast-max').oninput = (e) => {
            state.contrastMax = parseFloat(e.target.value);
            if (state.contrastMax <= state.contrastMin) {
                state.contrastMax = state.contrastMin + 1;
                e.target.value = state.contrastMax;
            }
            updateContrastReadout();
            drawHistogram();
            scheduleUpdate();
        };

        document.getElementById('rad-gamma').oninput = (e) => {
            state.gamma = parseFloat(e.target.value) / 100.0;
            document.getElementById('rad-gamma-readout').textContent = state.gamma.toFixed(2);
            scheduleUpdate();
        };

        document.getElementById('rad-log-stretch').onchange = (e) => {
            state.logStretch = e.target.checked;
            scheduleUpdate();
        };

        document.getElementById('rad-despeckle-select').onchange = (e) => {
            state.despeckle = e.target.value;
            scheduleUpdate();
        };

        document.getElementById('rad-kernel-select').onchange = (e) => {
            state.kernelSize = parseInt(e.target.value, 10);
            scheduleUpdate();
        };

        return panel;
    }

    function updateContrastReadout() {
        document.getElementById('rad-contrast-readout').textContent = `${state.contrastMin.toFixed(0)} - ${state.contrastMax.toFixed(0)}`;
    }

    function applyPreset(name) {
        if (name === 'HIGH_CONTRAST') {
            state.contrastMin = 20;
            state.contrastMax = 220;
            state.gamma = 0.85;
            state.logStretch = false;
            state.despeckle = 'none';
        } else if (name === 'LEE_DESPECKLE') {
            state.contrastMin = 10;
            state.contrastMax = 245;
            state.gamma = 1.0;
            state.logStretch = false;
            state.despeckle = 'lee';
            state.kernelSize = 5;
        } else if (name === 'DARK_VESSEL_BOOST') {
            state.contrastMin = 15;
            state.contrastMax = 190;
            state.gamma = 1.35;
            state.logStretch = true;
            state.despeckle = 'median';
            state.kernelSize = 3;
        } else if (name === 'OCEAN_CLUTTER_SUPPRESS') {
            state.contrastMin = 40;
            state.contrastMax = 250;
            state.gamma = 0.75;
            state.logStretch = false;
            state.despeckle = 'frost';
            state.kernelSize = 5;
        } else {
            // DEFAULT
            state.contrastMin = 0;
            state.contrastMax = 255;
            state.gamma = 1.0;
            state.logStretch = false;
            state.despeckle = 'none';
            state.kernelSize = 5;
        }

        // Sync inputs
        document.getElementById('rad-contrast-min').value = state.contrastMin;
        document.getElementById('rad-contrast-max').value = state.contrastMax;
        document.getElementById('rad-gamma').value = Math.round(state.gamma * 100);
        document.getElementById('rad-gamma-readout').textContent = state.gamma.toFixed(2);
        document.getElementById('rad-log-stretch').checked = state.logStretch;
        document.getElementById('rad-despeckle-select').value = state.despeckle;
        document.getElementById('rad-kernel-select').value = state.kernelSize;

        updateContrastReadout();
        drawHistogram();
        scheduleUpdate();
    }

    function resetDefaults() {
        document.getElementById('rad-preset-select').value = 'DEFAULT';
        applyPreset('DEFAULT');
    }

    async function fetchHistogram() {
        if (!currentScanId) return;
        try {
            const url = `/api/scans/${encodeURIComponent(currentScanId)}/histogram?polarization=${encodeURIComponent(state.polarization)}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const data = await res.json();
            histogramData = data.histogram;
            document.getElementById('rad-stats-readout').textContent = `μ: ${histogramData.mean} | σ: ${histogramData.std_dev}`;
            document.getElementById('rad-hist-min-label').textContent = `Min: ${histogramData.min_val}`;
            document.getElementById('rad-hist-max-label').textContent = `Max: ${histogramData.max_val}`;
            drawHistogram();
        } catch (err) {
            console.error('Failed fetching histogram:', err);
        }
    }

    function drawHistogram() {
        const canvas = document.getElementById('rad-histogram-canvas');
        if (!canvas || !histogramData) return;
        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);

        const counts = histogramData.counts;
        const maxCount = Math.max(...counts.slice(1, 255), 1); // Ignore pure black 0 spike for scaling

        // Draw bins area
        ctx.fillStyle = '#0284c7'; // cyan-600
        for (let i = 0; i < 256; i++) {
            const x = (i / 255.0) * w;
            const barW = Math.max(1, w / 256.0);
            const normH = Math.min(1.0, counts[i] / maxCount);
            const barH = normH * (h - 10);
            ctx.fillRect(x, h - barH, barW, barH);
        }

        // Draw contrast cutoffs
        const minX = (state.contrastMin / 255.0) * w;
        const maxX = (state.contrastMax / 255.0) * w;

        // Dim excluded regions
        ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
        ctx.fillRect(0, 0, minX, h);
        ctx.fillRect(maxX, 0, w - maxX, h);

        // Black point line
        ctx.strokeStyle = '#ef4444'; // red-500
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(minX, 0);
        ctx.lineTo(minX, h);
        ctx.stroke();

        // White point line
        ctx.strokeStyle = '#22c55e'; // green-500
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(maxX, 0);
        ctx.lineTo(maxX, h);
        ctx.stroke();
    }

    function scheduleUpdate() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            applyEnhancement();
        }, 150);
    }

    function buildEnhancedImageUrl() {
        const params = new URLSearchParams({
            contrast_min: state.contrastMin,
            contrast_max: state.contrastMax,
            gamma: state.gamma,
            log_stretch: state.logStretch ? '1' : '0',
            despeckle: state.despeckle,
            kernel_size: state.kernelSize,
            polarization: state.polarization,
            _t: Date.now(),
        });
        return `/api/scans/${encodeURIComponent(currentScanId)}/enhanced-image.png?${params.toString()}`;
    }

    function applyEnhancement() {
        if (!currentScanId) return;
        const newUrl = buildEnhancedImageUrl();

        if (targetLayer && targetLayer.setUrl) {
            targetLayer.setUrl(newUrl);
        } else if (typeof targetLayer === 'function') {
            targetLayer(newUrl, state);
        }
    }

    function showPanel(scanId, layerOrCallback) {
        currentScanId = scanId;
        targetLayer = layerOrCallback;
        const panel = initPanel();
        panel.classList.remove('hidden');
        fetchHistogram();
    }

    function hidePanel() {
        const panel = document.getElementById('sar-radiometry-panel');
        if (panel) panel.classList.add('hidden');
    }

    return {
        show: showPanel,
        hide: hidePanel,
        applyPreset: applyPreset,
        buildUrl: buildEnhancedImageUrl,
        getState: () => ({ ...state }),
    };
}));
