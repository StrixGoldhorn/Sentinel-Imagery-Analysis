/**
 * Autonomous Post-Pass Imagery Ingestion Pipeline Dashboard
 */

let allPostPassJobs = [];
let filteredPostPassJobs = [];
let availableAois = [];
let autoRefreshTimer = null;
let isAutoRefreshActive = true;
let liveTickerTimer = null;

function formatDuration(totalSeconds, includeSeconds = true) {
    if (isNaN(totalSeconds) || totalSeconds === null) return '-';
    const absSec = Math.abs(Math.round(totalSeconds));

    const hours = Math.floor(absSec / 3600);
    const minutes = Math.floor((absSec % 3600) / 60);
    const seconds = absSec % 60;

    let parts = [];
    if (hours > 0) {
        parts.push(`${hours}h`);
        if (minutes > 0 || !includeSeconds) parts.push(`${minutes}m`);
        if (includeSeconds && hours < 2) parts.push(`${seconds}s`);
    } else if (minutes > 0) {
        parts.push(`${minutes}m`);
        if (includeSeconds) parts.push(`${seconds}s`);
    } else {
        parts.push(`${seconds}s`);
    }

    return parts.join(' ') || '0s';
}

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

function getCanonicalPostPassStatus(job) {
    return String(job && (job.status || job.effective_status) || '').trim().toUpperCase();
}

function getPostPassDisplayStatus(job, now = new Date()) {
    const canonicalStatus = getCanonicalPostPassStatus(job);
    if (canonicalStatus !== 'POLLING_CATALOG') return canonicalStatus;
    const nextPollAt = parseUtcDate(job.next_poll_at);
    return nextPollAt && nextPollAt > now ? 'WAITING_FOR_POLL' : 'DUE_FOR_POLL';
}

document.addEventListener('DOMContentLoaded', () => {
    initPostPassDashboard();
});

async function initPostPassDashboard() {
    await loadAois();
    await loadPostPassJobs();
    setupAutoRefresh();
    setupLiveTicker();
}

function setupLiveTicker() {
    if (liveTickerTimer) clearInterval(liveTickerTimer);
    liveTickerTimer = setInterval(() => {
        updateLiveCountdowns();
    }, 1000);
}

function updateLiveCountdowns() {
    const nowMs = Date.now();

    // 1. Next poll countdowns
    document.querySelectorAll('.live-poll-countdown').forEach(el => {
        const targetMs = parseInt(el.getAttribute('data-timestamp'), 10);
        if (!isNaN(targetMs)) {
            const diffSec = Math.round((targetMs - nowMs) / 1000);
            if (diffSec > 0) {
                el.textContent = `In ${formatDuration(diffSec, true)}`;
            } else {
                const cell = el.closest('.polling-cell');
                if (cell && !cell.classList.contains('is-due')) {
                    cell.classList.add('is-due');
                    const row = cell.closest('tr');
                    const statusBadge = row ? row.querySelector('.post-pass-status-badge .badge') : null;
                    if (statusBadge) {
                        statusBadge.textContent = 'Ready for Catalog Check';
                        statusBadge.style.background = '#dbeafe';
                        statusBadge.style.color = '#1d4ed8';
                    }
                    const attempts = el.getAttribute('data-attempts') || '1';
                    const nextLine = cell.querySelector('.next-poll-line');
                    if (nextLine) {
                        nextLine.innerHTML = `
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <span class="badge" style="background: #e0f2fe; color: #0369a1; animation: pulse 1.5s infinite; font-size: 0.78rem; padding: 3px 6px;">
                                    🔄 Due, waiting for worker
                                </span>
                                <span style="font-size: 0.78rem; color: #64748b;">Polled #${attempts}</span>
                            </div>
                        `;
                    }
                }
            }
        }
    });

    // Pending jobs use an authoritative backend schedule but must not be
    // presented as POLLING_CATALOG until the backend transitions them.
    document.querySelectorAll('.live-pending-poll-countdown').forEach(el => {
        const targetMs = parseInt(el.getAttribute('data-timestamp'), 10);
        if (!isNaN(targetMs)) {
            const diffSec = Math.round((targetMs - nowMs) / 1000);
            el.textContent = diffSec > 0 ? `In ${formatDuration(diffSec, true)}` : 'Due; awaiting state transition';
        }
    });

    // 2. Flypast countdowns
    document.querySelectorAll('.live-pass-countdown').forEach(el => {
        const targetMs = parseInt(el.getAttribute('data-timestamp'), 10);
        if (!isNaN(targetMs)) {
            const diffSec = Math.round((targetMs - nowMs) / 1000);
            if (diffSec > 0) {
                el.textContent = `In ${formatDuration(diffSec, true)}`;
            } else if (diffSec >= -300) {
                el.textContent = `Active now (${formatDuration(300 + diffSec, true)} left)`;
            } else {
                el.textContent = `${formatDuration(-diffSec, false)} ago`;
            }
        }
    });

    // 3. Invalid / timeout countdowns
    document.querySelectorAll('.live-invalid-countdown').forEach(el => {
        const targetMs = parseInt(el.getAttribute('data-timestamp'), 10);
        if (!isNaN(targetMs)) {
            const diffSec = Math.round((targetMs - nowMs) / 1000);
            if (diffSec > 0) {
                el.textContent = `${formatDuration(diffSec, false)} left`;
            } else {
                el.textContent = 'Expired';
                el.style.color = '#dc2626';
                if (!el.dataset.expiredHandled) {
                    el.dataset.expiredHandled = 'true';
                    setTimeout(() => loadPostPassJobs(true), 1000);
                }
            }
        }
    });
}

function setupAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    if (isAutoRefreshActive) {
        autoRefreshTimer = setInterval(() => {
            loadPostPassJobs(true);
        }, 15000);
    }
}

function toggleAutoRefresh(enabled) {
    isAutoRefreshActive = enabled;
    if (enabled) {
        setupAutoRefresh();
        showToast('Auto-refresh enabled (15s interval)', 'info');
    } else {
        if (autoRefreshTimer) clearInterval(autoRefreshTimer);
        showToast('Auto-refresh paused', 'info');
    }
}

async function loadAois() {
    try {
        const response = await fetch('/api/aoi');
        const data = await response.json();
        if (Array.isArray(data)) {
            availableAois = data;
            availableAois.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base', numeric: true }));
            populateAoiDropdowns();
        } else if (data && data.status === 'success') {
            availableAois = data.aois || [];
            availableAois.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base', numeric: true }));
            populateAoiDropdowns();
        }
    } catch (err) {
        console.error('Error fetching AOIs:', err);
    }
}

function populateAoiDropdowns() {
    const filterSelect = document.getElementById('filterAoiSelect');
    const modalSelect = document.getElementById('customJobAoiSelect');

    if (filterSelect) {
        const curVal = filterSelect.value;
        filterSelect.innerHTML = '<option value="">All Areas of Interest</option>';
        availableAois.forEach(aoi => {
            const opt = document.createElement('option');
            opt.value = aoi.id;
            opt.textContent = `${aoi.name || 'AOI #' + aoi.id} (${aoi.satellite || 'Sentinel-1'})`;
            filterSelect.appendChild(opt);
        });
        if (curVal) filterSelect.value = curVal;
    }

    if (modalSelect) {
        modalSelect.innerHTML = '<option value="">-- Select an Area of Interest --</option>';
        availableAois.forEach(aoi => {
            const opt = document.createElement('option');
            opt.value = aoi.id;
            opt.textContent = `${aoi.name || 'AOI #' + aoi.id} (${aoi.satellite || 'Sentinel-1'})`;
            modalSelect.appendChild(opt);
        });
    }
}

async function loadPostPassJobs(isSilent = false) {
    const refreshBtn = document.getElementById('btnRefreshPostPass');
    if (refreshBtn && !isSilent) {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<span class="loading-spinner" style="width: 14px; height: 14px;"></span> Loading...';
    }

    try {
        const response = await fetch('/api/schedule/post_pass_jobs?limit=500');
        const data = await response.json();

        if (data.status === 'success') {
            allPostPassJobs = data.jobs || [];
            updateMetrics(allPostPassJobs, data.stats);
            applyPostPassFilters();

            const lastUpdated = document.getElementById('lastUpdatedText');
            if (lastUpdated) {
                lastUpdated.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
            }
        } else {
            showToast(data.error || 'Failed to load post-pass jobs', 'error');
        }
    } catch (err) {
        console.error('Error loading post pass jobs:', err);
        if (!isSilent) {
            showToast('Connection error while fetching post-pass jobs', 'error');
        }
    } finally {
        if (refreshBtn && !isSilent) {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <polyline points="1 20 1 14 7 14"></polyline>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                </svg>
                Refresh
            `;
        }
    }
}

function updateMetrics(jobs, serverStats) {
    let polling = 0;
    let pending = 0;
    let querying = 0;
    let ingesting = 0;
    let completed = 0;
    let failed = 0;
    let timedOut = 0;

    if (serverStats && typeof serverStats.polling === 'number') {
        polling = serverStats.polling;
        pending = serverStats.pending;
        querying = serverStats.querying || 0;
        ingesting = serverStats.ingesting || 0;
        completed = serverStats.completed;
        failed = serverStats.failed;
        timedOut = serverStats.timed_out;
    } else {
        const now = new Date();
        jobs.forEach(job => {
            const effectiveStatus = getCanonicalPostPassStatus(job);

            if (effectiveStatus === 'POLLING_CATALOG') polling++;
            else if (effectiveStatus === 'PENDING_PASS') pending++;
            else if (effectiveStatus === 'QUERYING_CATALOG') querying++;
            else if (effectiveStatus === 'INGESTING') ingesting++;
            else if (effectiveStatus === 'COMPLETED') completed++;
            else if (effectiveStatus === 'TIMED_OUT' || effectiveStatus === 'WAIT_EXPIRED') {
                timedOut++;
            }
            else if (effectiveStatus === 'FAILED') failed++;
        });
    }

    const elPolling = document.getElementById('metricPollingCount');
    const elPending = document.getElementById('metricPendingCount');
    const elQuerying = document.getElementById('metricQueryingCount');
    const elIngesting = document.getElementById('metricIngestingCount');
    const elCompleted = document.getElementById('metricCompletedCount');
    const elFailed = document.getElementById('metricFailedCount');
    const elTimedOut = document.getElementById('metricTimedOutCount');

    if (elPolling) elPolling.textContent = polling;
    if (elPending) elPending.textContent = pending;
    if (elQuerying) elQuerying.textContent = querying;
    if (elIngesting) elIngesting.textContent = ingesting;
    if (elCompleted) elCompleted.textContent = completed;
    if (elFailed) elFailed.textContent = failed;
    if (elTimedOut) elTimedOut.textContent = timedOut;
}

function applyPostPassFilters() {
    const aoiSelect = document.getElementById('filterAoiSelect');
    const statusSelect = document.getElementById('filterStatusSelect');
    const searchInput = document.getElementById('filterSearchInput');

    const selectedAoiId = aoiSelect && aoiSelect.value ? parseInt(aoiSelect.value, 10) : null;
    const selectedStatus = statusSelect ? statusSelect.value.trim().toUpperCase() : '';
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    const now = new Date();

    filteredPostPassJobs = allPostPassJobs.filter(job => {
        if (selectedAoiId !== null && job.aoi_id !== selectedAoiId) {
            return false;
        }

        const expDt = parseUtcDate(job.expected_imagery_time) || parseUtcDate(job.pass_time);
        const maxWaitHours = job.max_wait_hours || 24.0;
        const invalidDt = parseUtcDate(job.expires_at) || (expDt ? new Date(expDt.getTime() + maxWaitHours * 3600 * 1000) : null);
        const effectiveStatus = getCanonicalPostPassStatus(job);
        const displayStatus = getPostPassDisplayStatus(job, now);

        if (selectedStatus) {
            if (selectedStatus === 'FAILED') {
                if (effectiveStatus !== 'FAILED' && effectiveStatus !== 'TIMED_OUT' && effectiveStatus !== 'WAIT_EXPIRED') {
                    return false;
                }
            } else if (selectedStatus === 'TIMED_OUT') {
                if (effectiveStatus !== 'TIMED_OUT' && effectiveStatus !== 'WAIT_EXPIRED') {
                    return false;
                }
            } else if (effectiveStatus !== selectedStatus) {
                return false;
            }
        }
        if (query) {
            const aoiMatch = (job.aoi_name || '').toLowerCase().includes(query);
            const satMatch = (job.satellite || '').toLowerCase().includes(query);
            const scanMatch = (job.scan_folder || '').toLowerCase().includes(query);
            const statusMatch = (job.status || '').toLowerCase().includes(query)
                || effectiveStatus.toLowerCase().includes(query)
                || displayStatus.toLowerCase().includes(query);
            const errMatch = (job.error_message || '').toLowerCase().includes(query);
            if (!aoiMatch && !satMatch && !scanMatch && !statusMatch && !errMatch) {
                return false;
            }
        }
        return true;
    });

    const countSpan = document.getElementById('displayedJobsCount');
    if (countSpan) countSpan.textContent = filteredPostPassJobs.length;

    renderPostPassTable(filteredPostPassJobs);
}

function renderPostPassTable(jobs) {
    const tbody = document.getElementById('postPassTableBody');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; color: #64748b; padding: 35px;">
                    <div style="font-size: 1.1rem; font-weight: 500; color: #334155; margin-bottom: 6px;">No Post-Pass Jobs Found</div>
                    <div style="font-size: 0.85rem; margin-bottom: 15px;">No ingestion tasks match the active filters or flypast registrations.</div>
                    <button class="btn btn-primary btn-sm" onclick="openAddPostPassModal()">+ Add Custom Job</button>
                </td>
            </tr>
        `;
        return;
    }

    const now = new Date();

    tbody.innerHTML = jobs.map(job => {
        const passDt = parseUtcDate(job.pass_time);
        const passStr = passDt ? passDt.toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
        }) : 'N/A';

        const expDt = parseUtcDate(job.expected_imagery_time) || passDt;
        const expStr = expDt ? expDt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'N/A';

        // Validity calculation from the backend-configured wait window.
        const maxWaitHours = job.max_wait_hours || 24.0;
        const invalidDt = parseUtcDate(job.expires_at) || (expDt ? new Date(expDt.getTime() + maxWaitHours * 3600 * 1000) : null);
        const invalidDiffSec = invalidDt ? Math.round((invalidDt - now) / 1000) : null;
        const canonicalStatus = getCanonicalPostPassStatus(job);
        const effectiveStatus = getPostPassDisplayStatus(job, now);

        // Relative pass timing
        let timingContent = '';
        if (passDt) {
            const passDiffSec = Math.round((passDt - now) / 1000);
            let relPassText = '';
            if (passDiffSec > 300) {
                relPassText = `<div style="font-size: 0.75rem; color: #4338ca; font-weight: 500;">Flypast in <span class="live-pass-countdown" data-timestamp="${passDt.getTime()}">${formatDuration(passDiffSec, true)}</span></div>`;
            } else if (passDiffSec >= -300) {
                relPassText = `<div style="font-size: 0.75rem; color: #059669; font-weight: 600;">⚡ Active flypast window</div>`;
            } else {
                relPassText = `<div style="font-size: 0.75rem; color: #64748b;">Passed ${formatDuration(-passDiffSec, false)} ago</div>`;
            }

            timingContent = `
                <div style="font-size: 0.85rem; font-weight: 600; color: #0369a1;">${expStr}</div>
                <div style="font-size: 0.75rem; color: #64748b;">&plusmn;1h Window</div>
                ${relPassText}
            `;
        } else {
            timingContent = '<span style="color: #94a3b8;">N/A</span>';
        }

        let statusBadge = '';
        const completionWarning = canonicalStatus === 'COMPLETED' && Boolean(job.error_message);

        switch (effectiveStatus) {
            case 'PENDING_PASS':
                statusBadge = '<span class="badge badge-secondary" style="background: #e2e8f0; color: #475569;">⏳ Queued (Flypast Pending)</span>';
                break;
            case 'POLLING_CATALOG':
                statusBadge = '<span class="badge badge-primary" style="background: #dbeafe; color: #1d4ed8; animation: pulse 2s infinite;">🔄 Polling Catalog</span>';
                break;
            case 'WAITING_FOR_POLL':
                statusBadge = '<span class="badge badge-primary" style="background: #e0f2fe; color: #0369a1;">⏱ Waiting for Next Catalog Check</span>';
                break;
            case 'DUE_FOR_POLL':
                statusBadge = '<span class="badge badge-primary" style="background: #dbeafe; color: #1d4ed8;">Ready for Catalog Check</span>';
                break;
            case 'QUERYING_CATALOG':
                statusBadge = '<span class="badge badge-primary" style="background: #dbeafe; color: #1d4ed8; animation: pulse 2s infinite;">🔎 Querying Catalog Now</span>';
                break;
            case 'INGESTING':
                statusBadge = '<span class="badge badge-warning" style="background: #fef3c7; color: #b45309;">📥 Ingesting &amp; Stitching</span>';
                break;
            case 'COMPLETED':
                statusBadge = completionWarning
                    ? '<span class="badge badge-warning" style="background: #fef3c7; color: #b45309;">⚠️ Ingested with Warning</span>'
                    : '<span class="badge badge-success" style="background: #dcfce7; color: #15803d;">✅ Ingested Successfully</span>';
                break;
            case 'TIMED_OUT':
            case 'WAIT_EXPIRED':
                statusBadge = `<span class="badge badge-danger" style="background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; font-weight: 600;">⏱ Wait Expired (${escapeHtml(String(job.max_wait_hours || 24))}h)</span>`;
                break;
            case 'FAILED':
                statusBadge = '<span class="badge badge-danger" style="background: #fee2e2; color: #b91c1c;">❌ Ingestion Failed</span>';
                break;
            default:
                statusBadge = `<span class="badge badge-secondary">${escapeHtml(effectiveStatus || 'UNKNOWN')}</span>`;
        }

        let nextPollContent = '-';
        if (canonicalStatus === 'POLLING_CATALOG') {
            let nextPollLine = '';
            if (job.next_poll_at) {
                const nextDt = parseUtcDate(job.next_poll_at);
                const diffSec = Math.round((nextDt - now) / 1000);
                if (diffSec > 0) {
                    nextPollLine = `
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span style="color: #1d4ed8; font-weight: 600;">Next scan:</span>
                            <span class="live-poll-countdown" data-timestamp="${nextDt.getTime()}" data-attempts="${job.attempts}" style="font-weight: 600; color: #2563eb;">
                                In ${formatDuration(diffSec, true)}
                            </span>
                            <span style="font-size: 0.78rem; color: #64748b;">(Check #${job.attempts + 1})</span>
                        </div>
                    `;
                } else {
                    nextPollLine = `
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span class="badge" style="background: #e0f2fe; color: #0369a1; animation: pulse 1.5s infinite; font-size: 0.78rem; padding: 3px 6px;">
                                🔄 Due, waiting for worker
                            </span>
                            <span style="font-size: 0.78rem; color: #64748b;">Polled #${job.attempts}</span>
                        </div>
                    `;
                }
            } else {
                nextPollLine = `
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span class="badge" style="background: #e0f2fe; color: #0369a1; font-size: 0.78rem; padding: 3px 6px;">
                            🔄 Due for Poll
                        </span>
                        <span style="font-size: 0.78rem; color: #64748b;">Polled #${job.attempts}</span>
                    </div>
                `;
            }

            let validityLine = '';
            if (invalidDiffSec !== null) {
                if (invalidDiffSec > 0) {
                    validityLine = `
                        <div style="font-size: 0.78rem; color: #475569; margin-top: 3px;" title="Catalog polling expires after the backend-configured ${escapeHtml(String(maxWaitHours))}-hour wait window">
                            ⏱️ <strong>Valid for:</strong> <span class="live-invalid-countdown" data-timestamp="${invalidDt.getTime()}" style="color: #0f172a; font-weight: 600;">${formatDuration(invalidDiffSec, false)} left</span>
                        </div>
                    `;
                } else {
                    validityLine = `
                        <div style="font-size: 0.78rem; color: #dc2626; margin-top: 3px;">
                            ⏱️ <strong>Wait window expired:</strong> Timed out after ${escapeHtml(String(maxWaitHours))}h
                        </div>
                    `;
                }
            }

            nextPollContent = `
                <div class="polling-cell" data-job-id="${job.id}">
                    <div class="next-poll-line">${nextPollLine}</div>
                    ${validityLine}
                </div>
            `;
        } else if (canonicalStatus === 'PENDING_PASS') {
            const nextPollDt = parseUtcDate(job.next_poll_at);
            let passLine = '';

            if (nextPollDt) {
                const secToPoll = Math.round((nextPollDt - now) / 1000);
                if (secToPoll > 0) {
                    passLine = `
                        <div style="color: #4f46e5; font-weight: 600;">
                            Catalog polling <span class="live-pending-poll-countdown" data-timestamp="${nextPollDt.getTime()}">In ${formatDuration(secToPoll, true)}</span>
                        </div>
                        <div style="font-size: 0.78rem; color: #64748b;">Backend schedule: ${nextPollDt.toLocaleString()}</div>
                    `;
                } else {
                    passLine = `
                        <div style="color: #0284c7; font-weight: 600;">
                            Catalog polling is due
                        </div>
                        <div style="font-size: 0.78rem; color: #64748b;">Waiting for the pass monitor to transition this job</div>
                    `;
                }
            } else {
                passLine = '<span style="color: #64748b;">Pending pass schedule</span>';
            }

            nextPollContent = `
                <div class="polling-cell" data-job-id="${job.id}">
                    ${passLine}
                </div>
            `;
        } else if (effectiveStatus === 'QUERYING_CATALOG') {
            nextPollContent = '<div style="color: #1d4ed8; font-weight: 600;">Checking Copernicus catalog...</div>';
        } else if (effectiveStatus === 'INGESTING') {
            nextPollContent = `
                <div style="color: #b45309; font-weight: 600; display: flex; align-items: center; gap: 5px;">
                    <span class="loading-spinner" style="width: 12px; height: 12px; border-color: #b45309; border-top-color: transparent;"></span>
                    Ingesting Imagery...
                </div>
                <div style="font-size: 0.78rem; color: #64748b;">Downloading SAR product &amp; stitching DEM</div>
            `;
        } else if (effectiveStatus === 'COMPLETED') {
            const compDt = parseUtcDate(job.completed_at);
            const timeStr = compDt ? compDt.toLocaleTimeString() : 'N/A';
            nextPollContent = `
                <div style="color: #15803d; font-weight: 600;">
                    ✓ Acquired on Check #${job.attempts || 1}
                </div>
                <div style="font-size: 0.78rem; color: #64748b;">Completed at ${timeStr}</div>
                ${job.error_message ? `<div style="font-size: 0.78rem; color: #b45309; margin-top: 3px;" title="${escapeHtml(job.error_message)}">⚠️ ${escapeHtml(job.error_message)}</div>` : ''}
            `;
        } else if (effectiveStatus === 'TIMED_OUT' || effectiveStatus === 'WAIT_EXPIRED') {
            nextPollContent = `
                <div style="color: #b91c1c; font-size: 0.8rem; line-height: 1.3;" title="${escapeHtml(job.error_message || `Exceeded maximum post-pass wait window (${maxWaitHours}h)`)}">
                    ⏱️ <strong>Wait window expired:</strong> Timed out after ${escapeHtml(String(job.max_wait_hours || 24))}h.
                </div>
            `;
        } else if (job.error_message) {
            nextPollContent = `
                <div style="color: #dc3545; font-size: 0.8rem;" title="${escapeHtml(job.error_message)}">
                    ❌ ${escapeHtml(job.error_message)}
                </div>
            `;
        }

        let scanCell = '<span style="color: #94a3b8;">None</span>';
        if (job.scan_folder) {
            scanCell = `<a href="/gallery?scan=${encodeURIComponent(job.scan_folder)}" class="btn btn-sm btn-outline-primary" style="font-size: 0.78rem; padding: 2px 8px; text-decoration: none;" target="_blank">
                📂 View Scan (${escapeHtml(job.scan_folder)})
            </a>`;
        }

        let actionBtns = '';
        const allowedActions = Array.isArray(job.allowed_actions) ? job.allowed_actions : [];
        if (allowedActions.includes('poll')) {
            actionBtns += `<button class="btn btn-outline-primary btn-sm" onclick="pollJobNow(${job.id})" title="Query Copernicus STAC catalog now" style="padding: 3px 8px; font-size: 0.78rem;">
                🔍 Poll Now
            </button>`;
        }
        if (allowedActions.includes('retry')) {
            actionBtns += `<button class="btn btn-outline-warning btn-sm" onclick="retryJob(${job.id})" title="Reset to POLLING_CATALOG and re-poll" style="padding: 3px 8px; font-size: 0.78rem;">
                🔄 Retry
            </button>`;
        }

        actionBtns += ` <button class="btn btn-outline-secondary btn-sm" onclick="viewJobHistory(${job.id})" title="View state history" style="padding: 3px 6px; font-size: 0.78rem;">📜</button>`;
        if (allowedActions.includes('delete')) {
            actionBtns += ` <button class="btn btn-outline-danger btn-sm" onclick="deleteJob(${job.id})" title="Delete job" style="padding: 3px 6px; font-size: 0.78rem;">🗑️</button>`;
        }

        return `
            <tr data-job-id="${job.id}" style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='white'">
                <td style="padding: 12px 14px;">
                    <div style="font-weight: 600; color: #1e293b;">${escapeHtml(job.aoi_name)}</div>
                    <div style="font-size: 0.8rem; color: #64748b;">${passStr}</div>
                </td>
                <td style="padding: 12px 14px;">
                    ${timingContent}
                </td>
                <td style="padding: 12px 14px;">
                    <div style="font-weight: 500; color: #334155;">${escapeHtml(job.satellite || 'Sentinel-1')}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">${escapeHtml(job.orbit_direction || 'Auto')}</div>
                </td>
                <td style="padding: 12px 14px;">
                    <div class="post-pass-status-badge">${statusBadge}</div>
                </td>
                <td style="padding: 12px 14px; font-size: 0.85rem; color: #334155;">
                    ${nextPollContent}
                </td>
                <td style="padding: 12px 14px;">
                    ${scanCell}
                </td>
                <td style="padding: 12px 14px; text-align: right; white-space: nowrap;">
                    ${actionBtns}
                </td>
            </tr>
        `;
    }).join('');
}

const monitoredPostPassTasks = new Set();

function monitorPostPassTask(taskId, actionLabel) {
    if (!taskId || monitoredPostPassTasks.has(taskId)) return;
    monitoredPostPassTasks.add(taskId);
    let consecutiveErrors = 0;

    const poll = async () => {
        try {
            const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

            consecutiveErrors = 0;
            const taskStatus = String(data.status || 'UNKNOWN').toUpperCase();
            if (taskStatus === 'COMPLETED') {
                monitoredPostPassTasks.delete(taskId);
                const processedCount = data.result && Number.isInteger(data.result.processed_count)
                    ? ` (${data.result.processed_count} job${data.result.processed_count === 1 ? '' : 's'} processed)`
                    : '';
                showToast(`${actionLabel} completed${processedCount}`, 'success');
                loadPostPassJobs(true);
                return;
            }
            if (taskStatus === 'FAILED' || taskStatus === 'CANCELLED') {
                monitoredPostPassTasks.delete(taskId);
                showToast(`${actionLabel} ${taskStatus.toLowerCase()}: ${data.error || data.message || 'Unknown error'}`, 'error');
                loadPostPassJobs(true);
                return;
            }
            setTimeout(poll, 1500);
        } catch (err) {
            consecutiveErrors += 1;
            if (consecutiveErrors >= 5) {
                monitoredPostPassTasks.delete(taskId);
                showToast(`${actionLabel} status is unavailable; completion has not been confirmed.`, 'warning');
                return;
            }
            setTimeout(poll, Math.min(15000, 1000 * (2 ** consecutiveErrors)));
        }
    };

    setTimeout(poll, 500);
}

async function pollJobNow(jobId) {
    showToast(`Polling catalog for job #${jobId}...`, 'info');
    try {
        const response = await fetch(`/api/schedule/post_pass_jobs/${jobId}/poll`, { method: 'POST' });
        const data = await response.json();
        if (response.ok && data.status === 'accepted') {
            showToast(`Catalog check accepted for job #${jobId}`, 'info');
            monitorPostPassTask(data.task_id, `Catalog check for job #${jobId}`);
            loadPostPassJobs();
        } else {
            showToast(data.error || 'Failed to poll catalog', 'error');
        }
    } catch (err) {
        console.error('Error polling post pass job:', err);
        showToast('Error querying Copernicus catalog', 'error');
    }
}

async function retryJob(jobId) {
    showToast(`Resetting job #${jobId} to active polling...`, 'info');
    try {
        const response = await fetch(`/api/schedule/post_pass_jobs/${jobId}/retry`, { method: 'POST' });
        const data = await response.json();
        if (response.ok && data.status === 'accepted') {
            showToast(`Job #${jobId} reset; catalog check accepted`, 'info');
            monitorPostPassTask(data.task_id, `Retry for job #${jobId}`);
            loadPostPassJobs();
        } else {
            showToast(data.error || 'Failed to retry job', 'error');
        }
    } catch (err) {
        console.error('Error retrying post pass job:', err);
        showToast('Error resetting job', 'error');
    }
}

async function deleteJob(jobId) {
    if (!confirm(`Are you sure you want to delete post-pass job #${jobId}?`)) return;

    try {
        const response = await fetch(`/api/schedule/post_pass_jobs/${jobId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.status === 'success') {
            showToast(`Job #${jobId} deleted successfully`, 'success');
            loadPostPassJobs();
        } else {
            showToast(data.error || 'Failed to delete job', 'error');
        }
    } catch (err) {
        console.error('Error deleting job:', err);
        showToast('Error deleting job', 'error');
    }
}

async function viewJobHistory(jobId) {
    try {
        const response = await fetch(`/api/schedule/post_pass_jobs/${jobId}/events`);
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.error || 'Could not load job history');
        }
        const lines = (data.events || []).map(event => {
            const when = parseUtcDate(event.created_at);
            const timestamp = when ? when.toLocaleString() : (event.created_at || 'Unknown time');
            const transition = `${event.old_status || 'NEW'} → ${event.new_status}`;
            return `${timestamp}\n${transition}: ${event.reason}${event.message ? `\n${event.message}` : ''}`;
        });
        alert(lines.length ? lines.join('\n\n') : `No state history is available for job #${jobId}.`);
    } catch (err) {
        console.error('Error loading post-pass job history:', err);
        showToast(err.message || 'Error loading job history', 'error');
    }
}

async function pollDueJobsNow() {
    const btn = document.getElementById('btnPollDueNow');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner" style="width: 14px; height: 14px;"></span> Checking...';
    }
    showToast('Triggering catalog check for all due jobs...', 'info');

    try {
        const response = await fetch('/api/schedule/post_pass_jobs/poll_all', { method: 'POST' });
        const data = await response.json();
        if (response.ok && data.status === 'accepted') {
            showToast('Catalog checks accepted for all currently due jobs', 'info');
            monitorPostPassTask(data.task_id, 'Due-job catalog batch');
            loadPostPassJobs();
        } else {
            showToast(data.error || 'Catalog polling failed', 'error');
        }
    } catch (err) {
        console.error('Error checking all post pass jobs:', err);
        showToast('Error checking post-pass jobs', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                </svg>
                Poll Due Jobs
            `;
        }
    }
}

function openAddPostPassModal() {
    const modal = document.getElementById('addPostPassJobModal');
    if (!modal) return;

    setCustomJobTimeToNow();
    modal.style.display = 'flex';
}

function closeAddPostPassModal() {
    const modal = document.getElementById('addPostPassJobModal');
    if (modal) modal.style.display = 'none';
}

function setCustomJobTimeToNow() {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const localIso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;

    const passInput = document.getElementById('customJobPassTime');
    const expInput = document.getElementById('customJobExpectedTime');

    if (passInput) passInput.value = localIso;
    if (expInput) expInput.value = localIso;
}

function syncExpectedTimeWithPassTime() {
    const passInput = document.getElementById('customJobPassTime');
    const expInput = document.getElementById('customJobExpectedTime');
    if (passInput && expInput && (!expInput.value || expInput.value === '')) {
        expInput.value = passInput.value;
    }
}

async function submitCustomPostPassJob(event) {
    event.preventDefault();

    const aoiSelect = document.getElementById('customJobAoiSelect');
    const passInput = document.getElementById('customJobPassTime');
    const expInput = document.getElementById('customJobExpectedTime');
    const satSelect = document.getElementById('customJobSatellite');
    const orbitSelect = document.getElementById('customJobOrbitDir');
    const pollCheck = document.getElementById('customJobPollImmediately');
    const submitBtn = document.getElementById('submitCustomJobBtn');

    if (!aoiSelect.value) {
        showToast('Please select an Area of Interest', 'warning');
        return;
    }
    if (!passInput.value) {
        showToast('Please specify the satellite pass time', 'warning');
        return;
    }
    if (!expInput.value) {
        showToast('Please specify the expected imagery acquisition time', 'warning');
        return;
    }

    const passUtc = new Date(passInput.value).toISOString();
    const expUtc = new Date(expInput.value).toISOString();

    const payload = {
        aoi_id: parseInt(aoiSelect.value, 10),
        pass_time: passUtc,
        expected_imagery_time: expUtc,
        satellite: satSelect.value || 'Sentinel-1',
        orbit_direction: orbitSelect.value || null,
        poll_immediately: pollCheck ? pollCheck.checked : false,
    };

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading-spinner" style="width: 14px; height: 14px;"></span> Queuing...';
    }

    try {
        const response = await fetch('/api/schedule/post_pass_jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json();

        if (response.ok && (data.status === 'success' || data.status === 'accepted')) {
            const isImmediatePoll = Boolean(data.task_id);
            showToast(data.message || 'Custom post-pass job created successfully', isImmediatePoll ? 'info' : 'success');
            if (isImmediatePoll) {
                monitorPostPassTask(data.task_id, `Initial catalog check for job #${data.job_id}`);
            }
            closeAddPostPassModal();
            loadPostPassJobs();
        } else {
            showToast(data.error || 'Failed to create post-pass job', 'error');
        }
    } catch (err) {
        console.error('Error submitting custom post pass job:', err);
        showToast('Network error while saving post-pass job', 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                Queue Ingestion Job
            `;
        }
    }
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

function showToast(message, type = 'info') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.position = 'fixed';
        container.style.bottom = '24px';
        container.style.right = '24px';
        container.style.zIndex = '9999';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';
        container.style.gap = '10px';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bg = type === 'error' ? '#ef4444' : (type === 'success' ? '#10b981' : (type === 'warning' ? '#f59e0b' : '#3b82f6'));
    toast.style.background = bg;
    toast.style.color = 'white';
    toast.style.padding = '12px 18px';
    toast.style.borderRadius = '8px';
    toast.style.fontSize = '0.9rem';
    toast.style.fontWeight = '500';
    toast.style.boxShadow = '0 10px 15px -3px rgba(0, 0, 0, 0.2)';
    toast.style.transition = 'all 0.3s ease';
    toast.style.maxWidth = '360px';
    toast.textContent = message;

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
