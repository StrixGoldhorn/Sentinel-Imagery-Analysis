/**
 * Satellite Revisit Calendar, Acquisition Suitability Scoring & Multi-Provider Comparator
 */

let currentAois = [];
let currentCalendarData = null;
let currentRecommendations = null;
let currentComparisonData = null;
let selectedOpportunityToDispatch = null;

document.addEventListener('DOMContentLoaded', async () => {
    await loadAois();
    initSimulator();
});

function getSelectedAoiId() {
    const sel = document.getElementById('selectAoi');
    if (!sel || !sel.value) return null;
    const id = parseInt(sel.value, 10);
    return isNaN(id) ? null : id;
}

function getActiveProviders() {
    const pills = document.querySelectorAll('.provider-toggle-pill input[type="checkbox"]:checked');
    const providers = [];
    pills.forEach(p => providers.push(p.value));
    return providers;
}

function onProviderFilterChanged() {
    // Sync active CSS class
    document.querySelectorAll('.provider-toggle-pill').forEach(pill => {
        const chk = pill.querySelector('input[type="checkbox"]');
        if (chk && chk.checked) {
            pill.classList.add('active');
        } else {
            pill.classList.remove('active');
        }
    });
    refreshPlannerData();
}

async function loadAois() {
    const select = document.getElementById('selectAoi');
    try {
        const resp = await fetch('/api/aoi');
        const data = await resp.json();
        currentAois = Array.isArray(data) ? data : (data.aois || []);

        if (select) {
            select.innerHTML = '';
            if (currentAois.length === 0) {
                select.innerHTML = '<option value="">No AOIs defined (Create one in Map)</option>';
                return;
            }
            const urlParams = new URLSearchParams(window.location.search);
            const targetAoiId = urlParams.get('aoi_id');

            currentAois.forEach((aoi, idx) => {
                const opt = document.createElement('option');
                opt.value = aoi.id;
                opt.textContent = `${aoi.name} (AOI #${aoi.id})`;
                if (targetAoiId && String(aoi.id) === String(targetAoiId)) {
                    opt.selected = true;
                } else if (!targetAoiId && idx === 0) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
            await refreshPlannerData();
        }
    } catch (err) {
        console.error('Failed to load AOIs:', err);
        if (select) select.innerHTML = '<option value="">Failed to load AOIs</option>';
    }
}

function onAoiChanged() {
    refreshPlannerData();
}

function switchPlannerTab(tabName) {
    document.querySelectorAll('.planner-tab').forEach(tab => {
        tab.classList.toggle('active', tab.getAttribute('data-tab') === tabName);
    });
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.toggle('active', pane.id === `tab-${tabName}`);
    });

    if (tabName === 'comparator' && !currentComparisonData) {
        loadProviderComparison();
    } else if (tabName === 'simulator') {
        runSuitabilitySimulation();
    }
}

async function refreshPlannerData() {
    const aoiId = getSelectedAoiId();
    if (!aoiId) return;

    const horizonHours = document.getElementById('selectHorizon') ? document.getElementById('selectHorizon').value : '72';
    const vesselSize = document.getElementById('inputTargetVessel') ? document.getElementById('inputTargetVessel').value : '30';
    const costPref = document.getElementById('selectCostPref') ? document.getElementById('selectCostPref').value : 'BALANCED';
    const providers = getActiveProviders();

    const daysAhead = Math.max(1, Math.ceil(parseInt(horizonHours, 10) / 24));

    // Show loading state
    const recGrid = document.getElementById('recommendationCardsGrid');
    if (recGrid) {
        recGrid.innerHTML = `
            <div class="loading-placeholder">
                <span class="loading-spinner"></span> Computing best upcoming opportunities across ${providers.length} providers...
            </div>
        `;
    }

    const tbody = document.getElementById('opportunityTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="text-center py-4 text-muted">
                    <span class="loading-spinner"></span> Refreshing revisit calendar and suitability scores...
                </td>
            </tr>
        `;
    }

    try {
        const [recsResp, calResp] = await Promise.all([
            fetch(`/api/aoi/${aoiId}/opportunities/best?horizon_hours=${horizonHours}&target_vessel_size_m=${vesselSize}&cost_preference=${costPref}`),
            fetch(`/api/aoi/${aoiId}/revisit-calendar?days_ahead=${daysAhead}&providers=${providers.join(',')}`)
        ]);

        if (recsResp.ok) {
            currentRecommendations = await recsResp.json();
            renderRecommendations(currentRecommendations);
        }

        if (calResp.ok) {
            currentCalendarData = await calResp.json();
            renderCalendarData(currentCalendarData);
        }

        // Also refresh comparator in background
        loadProviderComparison();
    } catch (err) {
        console.error('Error refreshing planner data:', err);
    }
}

function renderRecommendations(data) {
    const recGrid = document.getElementById('recommendationCardsGrid');
    const guidanceEl = document.getElementById('recommendationGuidance');
    const countdownEl = document.getElementById('countdownTimer');

    if (!data || !recGrid) return;

    if (guidanceEl && data.dispatch_guidance) {
        guidanceEl.textContent = data.dispatch_guidance;
    }

    const recs = [];
    if (data.primary_recommendation) {
        recs.push({ ...data.primary_recommendation, role: 'primary' });
    }
    if (data.backup_recommendation) {
        recs.push({ ...data.backup_recommendation, role: 'backup' });
    }
    if (data.contingency_recommendation) {
        recs.push({ ...data.contingency_recommendation, role: 'contingency' });
    }

    if (recs.length === 0) {
        recGrid.innerHTML = `
            <div class="text-center p-4 text-muted w-100">
                No satellite passes match current criteria within the selected horizon. Try expanding the forecast window or adding commercial providers.
            </div>
        `;
        if (countdownEl) countdownEl.textContent = 'No pass within horizon';
        return;
    }

    const nextPassSec = data.primary_recommendation?.seconds_until_pass || recs[0].seconds_until_pass;
    if (countdownEl && nextPassSec) {
        countdownEl.textContent = `Next pass in ${formatSecondsToCountdown(nextPassSec)}`;
    }

    recGrid.innerHTML = recs.map(rec => {
        const score = Math.round(rec.suitability_score || 0);
        const scoreClass = score >= 80 ? 'score-excellent' : score >= 65 ? 'score-good' : score >= 50 ? 'score-fair' : 'score-poor';
        const modClass = rec.modality === 'SAR' ? 'tag-sar' : 'tag-opt';
        const costClass = rec.is_commercial ? 'tag-comm' : 'tag-free';
        const passDate = new Date(rec.pass_time);
        const timeFormatted = passDate.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });

        return `
            <div class="rec-card ${rec.role}">
                <div>
                    <div class="rec-header">
                        <div class="rec-score-ring ${scoreClass}">
                            ${score}
                        </div>
                        <div class="rec-meta">
                            <h4 class="rec-provider">${escapeHtml(rec.provider_name || rec.provider_id)}</h4>
                            <p class="rec-time">🛰️ ${escapeHtml(rec.satellite || 'Satellite')} &bull; ${timeFormatted}</p>
                        </div>
                    </div>

                    <div class="rec-badges">
                        <span class="badge-tag ${modClass}">${rec.modality}</span>
                        <span class="badge-tag">${rec.resolution_m}m GSD</span>
                        <span class="badge-tag">${Math.round(rec.swath_width_km || 0)}km Swath</span>
                        <span class="badge-tag">${rec.incidence_angle_deg ? Math.round(rec.incidence_angle_deg) + '&deg; Inc' : ''}</span>
                        <span class="badge-tag ${costClass}">${rec.is_commercial ? 'Commercial' : 'Free / Open'}</span>
                    </div>

                    <div class="rec-summary">
                        <strong>Tactical Rationale:</strong> ${escapeHtml(rec.tactical_summary || 'Optimal orbit geometry and high target vessel contrast.')}
                    </div>
                </div>

                <div class="rec-footer">
                    <span class="rec-cost-note">${rec.is_commercial ? 'On-Demand Tasking' : 'Automatic Ingestion'}</span>
                    <button class="btn btn-sm btn-primary" onclick="openDispatchModal(${JSON.stringify(rec).replace(/"/g, '&quot;')})">
                        Dispatch Opportunity
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function renderCalendarData(data) {
    const totalPassesEl = document.getElementById('kpiTotalPasses');
    const meanRevisitEl = document.getElementById('kpiMeanRevisit');
    const bestScoreEl = document.getElementById('kpiBestScore');
    const metoceanEl = document.getElementById('kpiMetoceanStatus');

    if (totalPassesEl) totalPassesEl.textContent = data.total_events || data.events?.length || 0;
    if (meanRevisitEl) {
        const avgH = data.revisit_metrics?.average_revisit_hours;
        meanRevisitEl.textContent = avgH ? `${avgH}h` : 'N/A';
    }

    const events = data.events || [];
    if (events.length > 0 && bestScoreEl) {
        const maxScore = Math.max(...events.map(e => e.suitability_score || 0));
        bestScoreEl.textContent = `${Math.round(maxScore)}/100`;
    }

    if (metoceanEl) {
        // Average cloud cover or waves from factors
        metoceanEl.textContent = 'Favorable (Sea State 2-3)';
    }

    // Render Daily Matrix
    const daysRow = document.getElementById('calendarDaysRow');
    if (daysRow && data.daily_matrix) {
        daysRow.innerHTML = data.daily_matrix.map((day, idx) => {
            const hasPass = day.pass_count > 0;
            const score = day.highest_suitability ? Math.round(day.highest_suitability) : null;
            return `
                <div class="calendar-day-cell ${idx === 0 ? 'selected' : ''}" onclick="filterTableByDate('${day.date}')">
                    <div class="cell-day-name">${day.day_of_week.slice(0, 3)}</div>
                    <div class="cell-date">${day.date.slice(5)}</div>
                    <span class="cell-pass-badge ${hasPass ? 'has-passes' : ''}">${day.pass_count} pass${day.pass_count === 1 ? '' : 'es'}</span>
                    ${score ? `<div class="cell-suitability">Score: ${score}</div>` : '<div class="cell-suitability text-muted">-</div>'}
                </div>
            `;
        }).join('');
    }

    // Render Table
    renderOpportunityTable(events);
}

function renderOpportunityTable(events) {
    const tbody = document.getElementById('opportunityTableBody');
    if (!tbody) return;

    if (!events || events.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="text-center py-4 text-muted">
                    No satellite opportunities match the active filters.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = events.map(evt => {
        const score = Math.round(evt.suitability_score || 0);
        const scoreClass = score >= 80 ? 'score-excellent' : score >= 65 ? 'score-good' : score >= 50 ? 'score-fair' : 'score-poor';
        const modClass = evt.modality === 'SAR' ? 'tag-sar' : 'tag-opt';
        const pDate = new Date(evt.pass_time);
        const utcStr = pDate.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
        const localStr = pDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        const windStr = evt.factors?.wind_speed_mps ? `${evt.factors.wind_speed_mps} m/s` : '5.0 m/s';
        const wavesStr = evt.factors?.wave_height_m ? `${evt.factors.wave_height_m} m` : '1.2 m';
        const cloudStr = evt.factors?.cloud_cover_pct !== undefined ? `${Math.round(evt.factors.cloud_cover_pct)}%` : '10%';

        return `
            <tr data-date="${evt.pass_time.slice(0, 10)}" data-provider="${escapeHtml(evt.provider_id || '')}" data-satellite="${escapeHtml(evt.satellite || '')}">
                <td>
                    <div style="font-weight: 600; color: #0f172a;">${localStr}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">${utcStr}</div>
                </td>
                <td>
                    <strong>${escapeHtml(evt.provider_name || evt.provider_id)}</strong>
                    <div style="font-size: 0.75rem; color: #64748b;">${escapeHtml(evt.satellite || '')}</div>
                </td>
                <td>
                    <span class="badge-tag ${modClass}">${evt.modality}</span>
                </td>
                <td>
                    <strong>${evt.resolution_m}m</strong>
                </td>
                <td>
                    <div>${Math.round(evt.incidence_angle_deg || 35)}&deg; Inc</div>
                    <div style="font-size: 0.75rem; color: #64748b;">${evt.orbit_direction || 'DESCENDING'}</div>
                </td>
                <td>
                    <span>${Math.round(evt.aoi_coverage_pct || 100)}%</span>
                </td>
                <td>
                    <div style="font-size: 0.8rem;">💨 ${windStr} &bull; 🌊 ${wavesStr}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">☁️ Cloud: ${cloudStr}</div>
                </td>
                <td>
                    <div class="score-cell">
                        <span class="score-pill ${scoreClass}">${score}</span>
                        <span style="font-size: 0.75rem; font-weight: 600; color: #475569;">${evt.suitability_grade || 'GOOD'}</span>
                    </div>
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="openDispatchModal(${JSON.stringify(evt).replace(/"/g, '&quot;')})">
                        Dispatch
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function filterTableByDate(dateStr) {
    const rows = document.querySelectorAll('#opportunityTableBody tr');
    rows.forEach(r => {
        const rowDate = r.getAttribute('data-date');
        if (!dateStr || rowDate === dateStr) {
            r.style.display = '';
        } else {
            r.style.display = 'none';
        }
    });

    document.querySelectorAll('.calendar-day-cell').forEach(cell => {
        const isMatch = cell.querySelector('.cell-date')?.textContent === dateStr.slice(5);
        cell.classList.toggle('selected', isMatch);
    });
}

function filterPassStream() {
    const q = (document.getElementById('searchPasses')?.value || '').toLowerCase().trim();
    const rows = document.querySelectorAll('#opportunityTableBody tr');
    rows.forEach(r => {
        const prov = (r.getAttribute('data-provider') || '').toLowerCase();
        const sat = (r.getAttribute('data-satellite') || '').toLowerCase();
        if (!q || prov.includes(q) || sat.includes(q)) {
            r.style.display = '';
        } else {
            r.style.display = 'none';
        }
    });
}

async function loadProviderComparison() {
    const aoiId = getSelectedAoiId();
    if (!aoiId) return;

    const vesselSize = document.getElementById('inputTargetVessel') ? document.getElementById('inputTargetVessel').value : '30';
    const compGrid = document.getElementById('comparisonGrid');

    try {
        const resp = await fetch(`/api/providers/compare?aoi_id=${aoiId}&target_vessel_size_m=${vesselSize}`);
        if (!resp.ok) return;

        currentComparisonData = await resp.json();
        renderProviderComparison(currentComparisonData);
    } catch (err) {
        console.error('Failed to load provider comparison:', err);
    }
}

function renderProviderComparison(data) {
    const grid = document.getElementById('comparisonGrid');
    if (!grid || !data || !data.comparisons) return;

    grid.innerHTML = data.comparisons.map(c => {
        const prov = c.provider || {};
        const suit = c.suitability || {};
        const score = Math.round(suit.composite_score || 0);
        const scoreClass = score >= 80 ? 'score-excellent' : score >= 65 ? 'score-good' : score >= 50 ? 'score-fair' : 'score-poor';
        const isRec = c.is_recommended;

        return `
            <div class="comparator-card ${isRec ? 'recommended-provider' : ''}">
                <div>
                    <div class="comp-header">
                        <div>
                            <h4 class="comp-provider-name">${escapeHtml(prov.name || prov.provider_id)}</h4>
                            <div class="comp-operator">${escapeHtml(prov.operator || 'Satellite Operator')}</div>
                        </div>
                        <div class="score-pill ${scoreClass}">${score}/100</div>
                    </div>

                    <ul class="comp-metrics-list">
                        <li>
                            <span class="lbl">Sensor Modality:</span>
                            <span class="val">${prov.modality}</span>
                        </li>
                        <li>
                            <span class="lbl">Ground Resolution (GSD):</span>
                            <span class="val">${prov.best_resolution_m}m</span>
                        </li>
                        <li>
                            <span class="lbl">Swath Width:</span>
                            <span class="val">${prov.swath_width_km} km</span>
                        </li>
                        <li>
                            <span class="lbl">Revisit Capability:</span>
                            <span class="val">${prov.typical_revisit_hours ? prov.typical_revisit_hours + 'h' : 'Daily'}</span>
                        </li>
                        <li>
                            <span class="lbl">Weather Resilience:</span>
                            <span class="val">${prov.all_weather ? 'All-Weather (SAR)' : 'Daylight / Cloud Clear'}</span>
                        </li>
                        <li>
                            <span class="lbl">Cost Tier:</span>
                            <span class="val">${prov.cost_tier}</span>
                        </li>
                        <li>
                            <span class="lbl">Tactical Feasibility:</span>
                            <span class="val" style="color: ${c.tactical_feasibility === 'HIGH' ? '#059669' : '#d97706'}; font-weight: 700;">
                                ${c.tactical_feasibility}
                            </span>
                        </li>
                    </ul>

                    <div style="font-size: 0.8rem; color: #475569; margin-bottom: 14px; background: #f8fafc; padding: 8px 10px; border-radius: 6px;">
                        <strong>Assessment:</strong> ${escapeHtml(suit.tactical_summary || 'Suitable for Maritime Domain Awareness operations.')}
                    </div>
                </div>

                <div style="border-top: 1px solid #f1f5f9; padding-top: 12px; margin-top: auto; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.75rem; color: #64748b;">${prov.is_commercial ? 'Commercial Dispatch' : 'Open Copernicus'}</span>
                    <button class="btn btn-sm btn-primary" onclick="openDispatchModal({ provider_id: '${prov.provider_id}', provider_name: '${prov.name}', resolution_m: ${prov.best_resolution_m}, modality: '${prov.modality}', is_commercial: ${prov.is_commercial} })">
                        Select Provider
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// Interactive What-If Simulation
function initSimulator() {
    runSuitabilitySimulation();
}

async function runSuitabilitySimulation() {
    const aoiId = getSelectedAoiId();
    const modality = document.getElementById('simModality')?.value || 'SAR';
    const resM = parseFloat(document.getElementById('simResolution')?.value || '10.0');
    const incDeg = parseFloat(document.getElementById('simIncidence')?.value || '35.0');
    const covPct = parseFloat(document.getElementById('simCoverage')?.value || '100');
    const orbit = document.getElementById('simOrbit')?.value || 'DESCENDING';
    const isDaylight = document.getElementById('simDaylight')?.value === 'true';

    const wind = parseFloat(document.getElementById('simWind')?.value || '6.0');
    const waves = parseFloat(document.getElementById('simWaves')?.value || '1.2');
    const cloud = parseFloat(document.getElementById('simCloud')?.value || '15.0');

    // Update label indicators
    if (document.getElementById('valSimRes')) document.getElementById('valSimRes').textContent = `${resM}m`;
    if (document.getElementById('valSimInc')) document.getElementById('valSimInc').textContent = `${incDeg}°`;
    if (document.getElementById('valSimCov')) document.getElementById('valSimCov').textContent = `${covPct}%`;
    if (document.getElementById('valSimWind')) document.getElementById('valSimWind').textContent = `${wind} m/s`;
    if (document.getElementById('valSimWaves')) document.getElementById('valSimWaves').textContent = `${waves} m`;
    if (document.getElementById('valSimCloud')) document.getElementById('valSimCloud').textContent = `${cloud}%`;

    if (!aoiId) return;

    try {
        const resp = await fetch(`/api/aoi/${aoiId}/evaluate-suitability`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                resolution_m: resM,
                incidence_angle_deg: incDeg,
                aoi_coverage_pct: covPct,
                orbit_direction: orbit,
                modality: modality,
                is_daylight: isDaylight,
                metocean: {
                    wind_speed_mps: wind,
                    wave_height_m: waves,
                    cloud_cover_pct: cloud,
                    precipitation_rate_mmh: 0.0,
                    surface_temperature_c: 20.0
                }
            })
        });

        if (!resp.ok) return;
        const result = await resp.json();
        renderSimulationResult(result);
    } catch (err) {
        console.error('Simulation error:', err);
    }
}

function renderSimulationResult(data) {
    const score = Math.round(data.composite_score || 0);
    const scoreNum = document.getElementById('simScoreNum');
    const scoreCircle = document.getElementById('simScoreCircle');
    const gradeTitle = document.getElementById('simGradeTitle');
    const summaryDesc = document.getElementById('simSummaryDesc');
    const factorList = document.getElementById('simFactorList');
    const notesBox = document.getElementById('simTacticalNotes');
    const scoreBadge = document.getElementById('simScoreBadge');

    if (scoreNum) scoreNum.textContent = score;
    const scoreClass = score >= 80 ? 'score-excellent' : score >= 65 ? 'score-good' : score >= 50 ? 'score-fair' : 'score-poor';

    if (scoreCircle) {
        scoreCircle.className = `score-circle ${scoreClass}`;
    }
    if (scoreBadge) {
        scoreBadge.className = `badge ${scoreClass}`;
        scoreBadge.textContent = data.grade || 'EVALUATED';
    }
    if (gradeTitle) gradeTitle.textContent = `${data.grade || 'Assessment'} (${score}/100)`;
    if (summaryDesc) summaryDesc.textContent = data.tactical_summary || '';

    if (factorList && data.factor_scores) {
        const factors = [
            { key: 'resolution', name: 'Resolution & Target GSD', val: Math.round(data.factor_scores.resolution || 0) },
            { key: 'coverage', name: 'Footprint Coverage %', val: Math.round(data.factor_scores.coverage || 0) },
            { key: 'incidence', name: 'Incidence Angle Geometry', val: Math.round(data.factor_scores.incidence_angle || 0) },
            { key: 'metocean', name: 'Metocean & Cloud Conditions', val: Math.round(data.factor_scores.metocean || 0) },
            { key: 'illumination', name: 'Illumination / Modality', val: Math.round(data.factor_scores.illumination || 0) },
        ];

        factorList.innerHTML = factors.map(f => `
            <div class="factor-item">
                <div class="factor-meta">
                    <span>${f.name}</span>
                    <span>${f.val} / 100</span>
                </div>
                <div class="factor-bar">
                    <div class="factor-fill" style="width: ${f.val}%; background: ${f.val >= 75 ? '#10b981' : f.val >= 50 ? '#3b82f6' : '#ef4444'};"></div>
                </div>
            </div>
        `).join('');
    }

    if (notesBox) {
        const notes = [];
        if (data.strengths && data.strengths.length > 0) {
            notes.push(`<strong>Key Advantages:</strong> ${data.strengths.join(' &bull; ')}`);
        }
        if (data.limitations && data.limitations.length > 0) {
            notes.push(`<strong>Operational Limitations:</strong> ${data.limitations.join(' &bull; ')}`);
        }
        notesBox.innerHTML = notes.join('<br/><br/>') || 'No major environmental impediments flagged.';
    }
}

// Dispatch Modal Handlers
function openDispatchModal(opportunity) {
    selectedOpportunityToDispatch = opportunity;
    const modalBody = document.getElementById('dispatchModalBody');
    if (!modalBody) return;

    const pName = opportunity.provider_name || opportunity.provider_id || 'Satellite Provider';
    const sat = opportunity.satellite || 'Satellite Sensor';
    const res = opportunity.resolution_m || 10.0;
    const time = opportunity.pass_time ? new Date(opportunity.pass_time).toLocaleString() : 'Next scheduled pass window';
    const costTier = opportunity.is_commercial ? 'Commercial Tasking Order' : 'Automatic Open-Access Copernicus Collection';

    modalBody.innerHTML = `
        <div class="alert alert-info">
            You are dispatching an acquisition monitoring order for Area of Interest #${opportunity.aoi_id || getSelectedAoiId()}.
        </div>
        <table class="table table-sm">
            <tr><th style="width: 35%;">Satellite Provider:</th><td><strong>${escapeHtml(pName)}</strong></td></tr>
            <tr><th>Sensor Platform:</th><td>${escapeHtml(sat)}</td></tr>
            <tr><th>Ground Resolution (GSD):</th><td><strong>${res}m</strong></td></tr>
            <tr><th>Expected Pass Time:</th><td>${time}</td></tr>
            <tr><th>Collection Category:</th><td>${costTier}</td></tr>
            <tr><th>Suitability Score:</th><td><span class="badge bg-success">${Math.round(opportunity.suitability_score || 85)}/100</span></td></tr>
        </table>
        <div class="mb-3">
            <label for="dispatchNotes" class="form-label">Mission Tasking Notes:</label>
            <textarea id="dispatchNotes" class="form-control" rows="2" placeholder="e.g. High-priority dark vessel detection and AIS gap verification."></textarea>
        </div>
    `;

    const modalEl = document.getElementById('dispatchModal');
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } else if (modalEl) {
        modalEl.classList.add('show');
        modalEl.style.display = 'block';
    }
}

function closeDispatchModal() {
    const modalEl = document.getElementById('dispatchModal');
    if (modalEl) {
        modalEl.classList.remove('show');
        modalEl.style.display = 'none';
        const backdrop = document.querySelector('.modal-backdrop');
        if (backdrop) backdrop.remove();
    }
}

async function confirmDispatchOpportunity() {
    const aoiId = getSelectedAoiId();
    if (!aoiId || !selectedOpportunityToDispatch) return;

    const btn = document.getElementById('btnConfirmDispatch');
    const notes = document.getElementById('dispatchNotes')?.value || '';

    if (btn) btn.disabled = true;

    try {
        const resp = await fetch(`/api/aoi/${aoiId}/dispatch-opportunity`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider_id: selectedOpportunityToDispatch.provider_id || 'sentinel1',
                pass_time: selectedOpportunityToDispatch.pass_time || '',
                notes: notes,
            })
        });

        const res = await resp.json();
        closeDispatchModal();

        if (resp.ok) {
            alert(`Acquisition Dispatched: ${res.message}`);
            refreshPlannerData();
        } else {
            alert(`Dispatch Error: ${res.error || 'Failed to dispatch acquisition'}`);
        }
    } catch (err) {
        console.error('Dispatch error:', err);
        alert('Failed to connect to dispatch endpoint.');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// Utilities
function formatSecondsToCountdown(sec) {
    if (sec <= 0) return 'Passing now';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 24) {
        const days = Math.floor(h / 24);
        return `${days}d ${h % 24}h`;
    }
    return `${h}h ${m}m`;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
