/**
 * Interactive SAR Polarimetric Presets (VV, VH, VV/VH Ratio, False-Color, and Change Detection).
 */
(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SarPolarimetry = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let currentScanId = null;
    let currentPreset = 'vv';
    let currentGamma = 1.0;
    let selectedBaselineScanId = null;
    let presetsData = [];
    let baselineCandidates = [];
    let onLayerUpdateCallback = null;

    function initPanel() {
        let panel = document.getElementById('sar-polarimetry-panel');
        if (panel) return panel;

        panel = document.createElement('div');
        panel.id = 'sar-polarimetry-panel';
        panel.className = 'fixed right-4 top-20 z-40 w-84 bg-slate-900/95 border border-cyan-700/50 backdrop-blur-md rounded-xl shadow-2xl flex flex-col text-slate-100 hidden';
        panel.innerHTML = `
            <!-- Header -->
            <div class="px-4 py-2.5 bg-slate-800/90 border-b border-cyan-800/40 flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <span class="text-base">📡</span>
                    <h3 class="font-bold text-sm text-cyan-300">SAR Polarization Presets</h3>
                </div>
                <button id="polarimetry-panel-close" class="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>

            <!-- Body -->
            <div class="p-4 space-y-4 max-h-[82vh] overflow-y-auto text-xs">
                <!-- Preset Buttons Grid -->
                <div class="space-y-1.5">
                    <label class="text-slate-400 font-semibold block text-[11px] uppercase tracking-wider">Analysis Mode / Modality</label>
                    <div id="polarimetry-button-group" class="grid grid-cols-1 gap-1.5">
                        <!-- Preset buttons dynamically injected -->
                    </div>
                </div>

                <!-- Baseline Scan Selector (Visible only when Change Detection is active) -->
                <div id="polarimetry-baseline-container" class="space-y-1.5 p-2.5 bg-slate-950/80 border border-slate-800 rounded-lg hidden">
                    <label class="text-amber-400 font-semibold flex items-center gap-1.5">
                        <span>⏱️</span>
                        <span>Baseline Comparison Pass (T1)</span>
                    </label>
                    <select id="polarimetry-baseline-select" class="w-full bg-slate-900 border border-slate-700 text-slate-200 rounded px-2 py-1 text-xs">
                        <option value="">Auto-select closest repeat pass</option>
                    </select>
                </div>

                <!-- Tactical Details & Color Channel Legend -->
                <div id="polarimetry-legend-container" class="space-y-2 p-3 bg-slate-950/70 border border-slate-800/90 rounded-lg">
                    <div class="flex items-center justify-between border-b border-slate-800 pb-1">
                        <span id="polarimetry-active-title" class="font-bold text-cyan-300 text-xs">VV Co-Pol</span>
                        <span id="polarimetry-active-tag" class="px-1.5 py-0.5 rounded text-[10px] bg-cyan-900/40 text-cyan-400 border border-cyan-800/50">Surface</span>
                    </div>
                    <p id="polarimetry-active-desc" class="text-slate-300 text-[11px] leading-relaxed">
                        Highlights direct surface scattering and rough seas.
                    </p>
                    <div id="polarimetry-color-chips" class="space-y-1 pt-1">
                        <!-- Channel keys dynamically populated -->
                    </div>
                </div>

                <!-- Gamma Adjustment Slider -->
                <div class="space-y-1 bg-slate-950/60 p-2.5 rounded-lg border border-slate-800">
                    <div class="flex justify-between items-center text-slate-400">
                        <label for="polarimetry-gamma-slider">Gamma Tuning</label>
                        <span id="polarimetry-gamma-val" class="font-mono text-cyan-300 font-bold">1.0</span>
                    </div>
                    <input id="polarimetry-gamma-slider" type="range" min="0.3" max="2.5" step="0.05" value="1.0"
                           class="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-500">
                </div>

                <!-- Apply & Reset Actions -->
                <div class="flex items-center gap-2 pt-1">
                    <button id="polarimetry-apply-btn" class="flex-1 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white font-semibold rounded shadow transition flex items-center justify-center gap-1">
                        <span>Apply Preset</span>
                    </button>
                    <button id="polarimetry-reset-btn" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded transition">
                        Reset
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(panel);
        bindEvents(panel);
        return panel;
    }

    function bindEvents(panel) {
        const closeBtn = panel.querySelector('#polarimetry-panel-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                panel.classList.add('hidden');
            });
        }

        const gammaSlider = panel.querySelector('#polarimetry-gamma-slider');
        const gammaVal = panel.querySelector('#polarimetry-gamma-val');
        if (gammaSlider && gammaVal) {
            gammaSlider.addEventListener('input', (e) => {
                currentGamma = parseFloat(e.target.value);
                gammaVal.textContent = currentGamma.toFixed(2);
            });
            gammaSlider.addEventListener('change', () => {
                applyPreset();
            });
        }

        const baselineSelect = panel.querySelector('#polarimetry-baseline-select');
        if (baselineSelect) {
            baselineSelect.addEventListener('change', (e) => {
                selectedBaselineScanId = e.target.value || null;
                applyPreset();
            });
        }

        const applyBtn = panel.querySelector('#polarimetry-apply-btn');
        if (applyBtn) {
            applyBtn.addEventListener('click', () => applyPreset());
        }

        const resetBtn = panel.querySelector('#polarimetry-reset-btn');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                currentPreset = 'vv';
                currentGamma = 1.0;
                selectedBaselineScanId = null;
                if (gammaSlider) gammaSlider.value = '1.0';
                if (gammaVal) gammaVal.textContent = '1.0';
                updateUI();
                applyPreset();
            });
        }
    }

    function renderPresetButtons() {
        const container = document.getElementById('polarimetry-button-group');
        if (!container) return;

        container.innerHTML = '';
        presetsData.forEach((preset) => {
            const btn = document.createElement('button');
            const isActive = preset.preset_id === currentPreset;
            btn.className = `w-full text-left p-2 rounded border transition flex items-center justify-between ${
                isActive
                    ? 'bg-cyan-950/80 border-cyan-500 text-white font-semibold shadow-md'
                    : 'bg-slate-950/40 border-slate-800 text-slate-300 hover:bg-slate-800/60 hover:text-white'
            }`;

            let icon = '🌊';
            if (preset.preset_id === 'vh') icon = '🚢';
            else if (preset.preset_id === 'vv_vh_ratio') icon = '⚖️';
            else if (preset.preset_id === 'false_color_composite') icon = '🎨';
            else if (preset.preset_id === 'change_detection') icon = '🔄';

            btn.innerHTML = `
                <div class="flex items-center gap-2">
                    <span class="text-sm">${icon}</span>
                    <div>
                        <div class="font-medium text-xs leading-tight">${preset.name}</div>
                        <div class="text-[10px] text-slate-400 line-clamp-1">${preset.tactical_purpose}</div>
                    </div>
                </div>
                ${isActive ? '<span class="text-cyan-400 font-bold text-xs">✓</span>' : ''}
            `;

            btn.addEventListener('click', () => {
                currentPreset = preset.preset_id;
                updateUI();
                applyPreset();
            });

            container.appendChild(btn);
        });
    }

    function updateUI() {
        renderPresetButtons();

        const activePreset = presetsData.find((p) => p.preset_id === currentPreset);
        const baselineContainer = document.getElementById('polarimetry-baseline-container');
        const activeTitle = document.getElementById('polarimetry-active-title');
        const activeTag = document.getElementById('polarimetry-active-tag');
        const activeDesc = document.getElementById('polarimetry-active-desc');
        const colorChips = document.getElementById('polarimetry-color-chips');

        if (!activePreset) return;

        if (activeTitle) activeTitle.textContent = activePreset.display_title;
        if (activeDesc) activeDesc.textContent = activePreset.description;

        if (activeTag) {
            if (activePreset.preset_id === 'vv') {
                activeTag.textContent = 'Co-Pol Surface';
                activeTag.className = 'px-1.5 py-0.5 rounded text-[10px] bg-blue-900/40 text-blue-400 border border-blue-800/50';
            } else if (activePreset.preset_id === 'vh') {
                activeTag.textContent = 'Cross-Pol Target';
                activeTag.className = 'px-1.5 py-0.5 rounded text-[10px] bg-green-900/40 text-green-400 border border-green-800/50';
            } else if (activePreset.preset_id === 'vv_vh_ratio') {
                activeTag.textContent = 'Polarization Ratio';
                activeTag.className = 'px-1.5 py-0.5 rounded text-[10px] bg-purple-900/40 text-purple-400 border border-purple-800/50';
            } else if (activePreset.preset_id === 'false_color_composite') {
                activeTag.textContent = 'RGB Composite';
                activeTag.className = 'px-1.5 py-0.5 rounded text-[10px] bg-amber-900/40 text-amber-400 border border-amber-800/50';
            } else if (activePreset.preset_id === 'change_detection') {
                activeTag.textContent = 'Multi-Temporal';
                activeTag.className = 'px-1.5 py-0.5 rounded text-[10px] bg-emerald-900/40 text-emerald-400 border border-emerald-800/50';
            }
        }

        // Show/hide baseline container
        if (baselineContainer) {
            if (activePreset.supports_multitemporal) {
                baselineContainer.classList.remove('hidden');
                populateBaselineDropdown();
            } else {
                baselineContainer.classList.add('hidden');
            }
        }

        // Populate color chips
        if (colorChips && activePreset.color_mapping) {
            colorChips.innerHTML = '';
            for (const [key, desc] of Object.entries(activePreset.color_mapping)) {
                let badgeColor = 'bg-slate-700 text-slate-300';
                if (key.toLowerCase().includes('red')) badgeColor = 'bg-red-900/70 text-red-300 border border-red-700';
                else if (key.toLowerCase().includes('green')) badgeColor = 'bg-emerald-900/70 text-emerald-300 border border-emerald-700';
                else if (key.toLowerCase().includes('blue')) badgeColor = 'bg-blue-900/70 text-blue-300 border border-blue-700';
                else if (key.toLowerCase().includes('cyan')) badgeColor = 'bg-cyan-900/70 text-cyan-300 border border-cyan-700';

                const row = document.createElement('div');
                row.className = 'flex items-center gap-1.5 text-[11px]';
                row.innerHTML = `
                    <span class="px-1.5 py-0.2 rounded font-mono text-[10px] uppercase ${badgeColor}">${key}</span>
                    <span class="text-slate-400">${desc}</span>
                `;
                colorChips.appendChild(row);
            }
        }
    }

    function populateBaselineDropdown() {
        const select = document.getElementById('polarimetry-baseline-select');
        if (!select) return;

        select.innerHTML = '<option value="">Auto-select closest repeat pass</option>';
        baselineCandidates.forEach((c) => {
            const opt = document.createElement('option');
            opt.value = c.scan_id;
            opt.textContent = `${c.scan_id} (${c.days_prior > 0 ? c.days_prior + 'd prior' : 'repeat'}, ${c.overlap_pct}% overlap)`;
            if (selectedBaselineScanId === c.scan_id) {
                opt.selected = true;
            }
            select.appendChild(opt);
        });
    }

    function getPresetImageUrl(scanId, preset, gamma, baselineScanId) {
        let url = `/api/scans/${encodeURIComponent(scanId)}/polarimetry/${encodeURIComponent(preset)}.png?gamma=${gamma}`;
        if (baselineScanId) {
            url += `&baseline_scan_id=${encodeURIComponent(baselineScanId)}`;
        }
        return url;
    }

    function applyPreset() {
        if (!currentScanId) return;

        const imgUrl = getPresetImageUrl(currentScanId, currentPreset, currentGamma, selectedBaselineScanId);

        if (typeof onLayerUpdateCallback === 'function') {
            onLayerUpdateCallback(imgUrl, {
                scanId: currentScanId,
                preset: currentPreset,
                gamma: currentGamma,
                baselineScanId: selectedBaselineScanId,
            });
        }
    }

    async function loadPresets(scanId) {
        try {
            const res = await fetch(`/api/scans/${encodeURIComponent(scanId)}/polarimetry/presets`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            presetsData = data.presets || [];
            baselineCandidates = data.baseline_candidates || [];
            updateUI();
        } catch (err) {
            console.error('Failed loading polarimetric presets:', err);
        }
    }

    return {
        open: function (scanId, updateCallback) {
            currentScanId = scanId;
            onLayerUpdateCallback = updateCallback;
            const panel = initPanel();
            panel.classList.remove('hidden');
            loadPresets(scanId);
        },
        close: function () {
            const panel = document.getElementById('sar-polarimetry-panel');
            if (panel) panel.classList.add('hidden');
        },
        toggle: function (scanId, updateCallback) {
            const panel = initPanel();
            if (panel.classList.contains('hidden') || currentScanId !== scanId) {
                this.open(scanId, updateCallback);
            } else {
                this.close();
            }
        },
        getUrl: function (scanId, preset, gamma, baselineScanId) {
            return getPresetImageUrl(scanId, preset || 'vv', gamma || 1.0, baselineScanId);
        },
    };
}));
