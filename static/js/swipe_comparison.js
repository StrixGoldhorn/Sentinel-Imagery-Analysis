/**
 * Interactive Before/After Repeat SAR Pass Swipe Comparison
 */
(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SwipeComparison = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let currentScanId = null;
    let currentBaselineId = null;
    let swipeData = null;
    let isDragging = false;

    function initSwipeModal() {
        let modal = document.getElementById('sar-swipe-modal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.id = 'sar-swipe-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm hidden';
        modal.innerHTML = `
            <div class="relative w-11/12 max-w-6xl h-[88vh] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col overflow-hidden text-slate-100">
                <!-- Header -->
                <div class="px-5 py-3.5 bg-slate-800/90 border-b border-slate-700/80 flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <span class="text-xl">🛰️</span>
                        <div>
                            <h3 class="font-bold text-base text-white flex items-center gap-2">
                                Repeat SAR Pass Swipe Comparison
                                <span id="swipe-delta-badge" class="px-2 py-0.5 text-xs font-semibold rounded bg-cyan-900/60 text-cyan-300 border border-cyan-700/50">--</span>
                                <span id="swipe-overlap-badge" class="px-2 py-0.5 text-xs font-medium rounded bg-indigo-900/60 text-indigo-300 border border-indigo-700/50">--% Overlap</span>
                            </h3>
                            <p class="text-xs text-slate-400" id="swipe-subtitle">Comparing target pass with historical repeat acquisition</p>
                        </div>
                    </div>

                    <!-- Baseline Selector & Controls -->
                    <div class="flex items-center gap-3">
                        <div class="flex items-center gap-1.5 text-xs">
                            <label class="text-slate-400">Baseline Pass:</label>
                            <select id="swipe-baseline-select" class="bg-slate-950 border border-slate-700 text-slate-200 text-xs rounded px-2.5 py-1 focus:ring-1 focus:ring-cyan-500">
                                <option value="">Auto-detected Baseline</option>
                            </select>
                        </div>
                        <button id="swipe-modal-close" class="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Metrics & Legend Bar -->
                <div class="px-5 py-2 bg-slate-950/70 border-b border-slate-800 flex items-center justify-between text-xs">
                    <div class="flex items-center gap-4">
                        <div class="flex items-center gap-1.5">
                            <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-emerald-500/30"></span>
                            <span class="text-slate-300">Arrived Vessels:</span>
                            <span id="swipe-arrived-count" class="font-bold text-emerald-400">0</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <span class="w-2.5 h-2.5 rounded-full bg-rose-500 ring-2 ring-rose-500/30"></span>
                            <span class="text-slate-300">Departed Vessels:</span>
                            <span id="swipe-departed-count" class="font-bold text-rose-400">0</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <span class="w-2.5 h-2.5 rounded-full bg-amber-500 ring-2 ring-amber-500/30"></span>
                            <span class="text-slate-300">Persistent Targets:</span>
                            <span id="swipe-persistent-count" class="font-bold text-amber-400">0</span>
                        </div>
                    </div>
                    <div class="flex items-center gap-3">
                        <label class="flex items-center gap-1.5 cursor-pointer text-slate-300 select-none">
                            <input type="checkbox" id="swipe-toggle-markers" checked class="rounded border-slate-700 bg-slate-900 text-cyan-600 focus:ring-0">
                            <span>Highlight Change Vectors</span>
                        </label>
                        <div class="h-3 w-px bg-slate-700"></div>
                        <span class="text-slate-400">Drag vertical divider to compare</span>
                    </div>
                </div>

                <!-- Main Swipe Comparison Viewport -->
                <div class="relative flex-1 bg-black overflow-hidden select-none" id="swipe-viewport">
                    <!-- Baseline Image (Background / Left) -->
                    <div class="absolute inset-0 overflow-hidden" id="swipe-baseline-pane">
                        <img id="swipe-baseline-img" class="absolute w-full h-full object-contain pointer-events-none" src="" alt="Baseline Pass">
                        <div class="absolute top-4 left-4 z-10 px-2.5 py-1 rounded bg-black/70 border border-slate-700/80 text-xs font-medium text-slate-300 backdrop-blur-sm">
                            ⏮️ Baseline (<span id="swipe-baseline-label">Prior Pass</span>)
                        </div>
                    </div>

                    <!-- Target Image (Foreground / Right with clip-path) -->
                    <div class="absolute inset-0 overflow-hidden" id="swipe-target-pane" style="clip-path: polygon(50% 0, 100% 0, 100% 100%, 50% 100%);">
                        <img id="swipe-target-img" class="absolute w-full h-full object-contain pointer-events-none" src="" alt="Target Pass">
                        <div class="absolute top-4 right-4 z-10 px-2.5 py-1 rounded bg-cyan-950/80 border border-cyan-700/80 text-xs font-medium text-cyan-200 backdrop-blur-sm">
                            Target Pass (<span>Current</span>) ⏭️
                        </div>
                    </div>

                    <!-- Target Change Markers Overlay -->
                    <div id="swipe-markers-layer" class="absolute inset-0 pointer-events-none"></div>

                    <!-- Interactive Vertical Divider Slider -->
                    <div id="swipe-divider" class="absolute top-0 bottom-0 w-1 bg-cyan-400 shadow-[0_0_12px_rgba(6,182,212,0.8)] cursor-ew-resize z-20 flex items-center justify-center" style="left: 50%;">
                        <div class="w-8 h-8 rounded-full bg-cyan-500 border-2 border-slate-900 flex items-center justify-center text-slate-950 font-bold text-xs shadow-lg">
                            ⇆
                        </div>
                    </div>
                </div>

                <!-- Footer / Status Bar -->
                <div class="px-5 py-2.5 bg-slate-900 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
                    <div id="swipe-status-text">Ready</div>
                    <div class="flex items-center gap-2">
                        <span id="swipe-pos-indicator">Slider: 50%</span>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Event listeners
        const closeBtn = modal.querySelector('#swipe-modal-close');
        closeBtn.addEventListener('click', () => closeSwipeModal());

        const select = modal.querySelector('#swipe-baseline-select');
        select.addEventListener('change', (e) => {
            if (e.target.value) {
                openSwipeModal(currentScanId, e.target.value);
            }
        });

        const toggleMarkers = modal.querySelector('#swipe-toggle-markers');
        toggleMarkers.addEventListener('change', (e) => {
            const layer = modal.querySelector('#swipe-markers-layer');
            if (layer) layer.style.display = e.target.checked ? 'block' : 'none';
        });

        // Dragging logic
        const viewport = modal.querySelector('#swipe-viewport');
        const divider = modal.querySelector('#swipe-divider');
        const targetPane = modal.querySelector('#swipe-target-pane');
        const posIndicator = modal.querySelector('#swipe-pos-indicator');

        function updateSlider(clientX) {
            const rect = viewport.getBoundingClientRect();
            let x = clientX - rect.left;
            x = Math.max(0, Math.min(x, rect.width));
            const pct = (x / rect.width) * 100;

            divider.style.left = `${pct}%`;
            targetPane.style.clipPath = `polygon(${pct}% 0, 100% 0, 100% 100%, ${pct}% 100%)`;
            posIndicator.textContent = `Slider: ${Math.round(pct)}%`;
        }

        divider.addEventListener('mousedown', (e) => {
            isDragging = true;
            e.preventDefault();
        });

        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            updateSlider(e.clientX);
        });

        window.addEventListener('mouseup', () => {
            isDragging = false;
        });

        // Touch support
        divider.addEventListener('touchstart', (e) => {
            isDragging = true;
            e.preventDefault();
        });

        window.addEventListener('touchmove', (e) => {
            if (!isDragging || !e.touches[0]) return;
            updateSlider(e.touches[0].clientX);
        });

        window.addEventListener('touchend', () => {
            isDragging = false;
        });

        return modal;
    }

    async function openSwipeModal(scanId, baselineId = null) {
        currentScanId = scanId;
        currentBaselineId = baselineId;
        const modal = initSwipeModal();
        modal.classList.remove('hidden');

        const statusText = modal.querySelector('#swipe-status-text');
        statusText.textContent = `Loading repeat passes for ${scanId}...`;

        try {
            let url = `/api/scans/${encodeURIComponent(scanId)}/swipe-comparison`;
            if (baselineId) {
                url += `?baseline_id=${encodeURIComponent(baselineId)}`;
            }

            const resp = await fetch(url);
            if (!resp.ok) {
                throw new Error(`Failed to load swipe comparison (${resp.status})`);
            }

            swipeData = await resp.json();
            renderSwipeData(modal, swipeData);
            statusText.textContent = `Loaded swipe comparison against baseline pass: ${swipeData.baseline_scan_id}`;
        } catch (err) {
            statusText.textContent = `Error: ${err.message}`;
            console.error('[SwipeComparison]', err);
        }
    }

    function renderSwipeData(modal, data) {
        // Badges
        const deltaBadge = modal.querySelector('#swipe-delta-badge');
        const overlapBadge = modal.querySelector('#swipe-overlap-badge');
        deltaBadge.textContent = data.temporal_delta?.label || '--';
        overlapBadge.textContent = `${data.spatial_overlap?.overlap_pct || 0}% Overlap`;

        // Counts
        modal.querySelector('#swipe-arrived-count').textContent = data.diff_summary?.arrived_count ?? 0;
        modal.querySelector('#swipe-departed-count').textContent = data.diff_summary?.departed_count ?? 0;
        modal.querySelector('#swipe-persistent-count').textContent = data.diff_summary?.persistent_count ?? 0;

        // Labels
        const baseDate = data.baseline_acquisition ? new Date(data.baseline_acquisition).toLocaleDateString() : 'Baseline';
        modal.querySelector('#swipe-baseline-label').textContent = `${data.baseline_scan_id} (${baseDate})`;

        // Populate baseline selector
        const select = modal.querySelector('#swipe-baseline-select');
        select.innerHTML = '';
        if (data.candidates && data.candidates.length > 0) {
            data.candidates.forEach((c) => {
                const opt = document.createElement('option');
                opt.value = c.scan_id;
                opt.textContent = `${c.scan_id} (${Math.round(c.days_prior)}d ago, ${c.overlap_pct}%)`;
                if (c.scan_id === data.baseline_scan_id) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
        } else {
            const opt = document.createElement('option');
            opt.value = data.baseline_scan_id;
            opt.textContent = data.baseline_scan_id;
            opt.selected = true;
            select.appendChild(opt);
        }

        // Images
        const baseImg = modal.querySelector('#swipe-baseline-img');
        const targetImg = modal.querySelector('#swipe-target-img');
        baseImg.src = data.imagery?.baseline_image_url || '';
        targetImg.src = data.imagery?.target_image_url || '';

        // Reset divider to 50%
        const divider = modal.querySelector('#swipe-divider');
        const targetPane = modal.querySelector('#swipe-target-pane');
        divider.style.left = '50%';
        targetPane.style.clipPath = 'polygon(50% 0, 100% 0, 100% 100%, 50% 100%)';
    }

    function closeSwipeModal() {
        const modal = document.getElementById('sar-swipe-modal');
        if (modal) {
            modal.classList.add('hidden');
        }
    }

    return {
        open: openSwipeModal,
        close: closeSwipeModal,
    };
}));
