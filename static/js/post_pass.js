/**
 * Autonomous Post-Pass Imagery Ingestion Pipeline Dashboard
 */

let allPostPassJobs = [];
let filteredPostPassJobs = [];
let availableAois = [];
let autoRefreshTimer = null;
let isAutoRefreshActive = true;

document.addEventListener('DOMContentLoaded', () => {
    initPostPassDashboard();
});

async function initPostPassDashboard() {
    await loadAois();
    await loadPostPassJobs();
    setupAutoRefresh();
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
        if (data.status === 'success') {
            availableAois = data.aois || [];
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
        const response = await fetch('/api/schedule/post_pass_jobs');
        const data = await response.json();

        if (data.status === 'success') {
            allPostPassJobs = data.jobs || [];
            updateMetrics(allPostPassJobs);
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

function updateMetrics(jobs) {
    let polling = 0;
    let pending = 0;
    let ingesting = 0;
    let completed = 0;
    let failed = 0;
    let timedOut = 0;

    jobs.forEach(job => {
        const st = (job.status || '').toUpperCase();
        if (st === 'POLLING_CATALOG') polling++;
        else if (st === 'PENDING_PASS') pending++;
        else if (st === 'INGESTING') ingesting++;
        else if (st === 'COMPLETED') completed++;
        else if (st === 'TIMED_OUT') timedOut++;
        else if (st === 'FAILED') failed++;
    });

    const elPolling = document.getElementById('metricPollingCount');
    const elPending = document.getElementById('metricPendingCount');
    const elIngesting = document.getElementById('metricIngestingCount');
    const elCompleted = document.getElementById('metricCompletedCount');
    const elFailed = document.getElementById('metricFailedCount');
    const elTimedOut = document.getElementById('metricTimedOutCount');

    if (elPolling) elPolling.textContent = polling;
    if (elPending) elPending.textContent = pending;
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

    filteredPostPassJobs = allPostPassJobs.filter(job => {
        if (selectedAoiId !== null && job.aoi_id !== selectedAoiId) {
            return false;
        }
        if (selectedStatus && (job.status || '').toUpperCase() !== selectedStatus) {
            return false;
        }
        if (query) {
            const aoiMatch = (job.aoi_name || '').toLowerCase().includes(query);
            const satMatch = (job.satellite || '').toLowerCase().includes(query);
            const scanMatch = (job.scan_folder || '').toLowerCase().includes(query);
            const statusMatch = (job.status || '').toLowerCase().includes(query);
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
        const passDt = job.pass_time ? new Date(job.pass_time) : null;
        const passStr = passDt ? passDt.toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
        }) : 'N/A';

        const expDt = job.expected_imagery_time ? new Date(job.expected_imagery_time) : passDt;
        const expStr = expDt ? expDt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'N/A';

        let statusBadge = '';
        const isTimingMismatch = job.status === 'FAILED' && (job.error_message || '').toLowerCase().includes('more recent imagery');

        switch (job.status) {
            case 'PENDING_PASS':
                statusBadge = '<span class="badge badge-secondary" style="background: #e2e8f0; color: #475569;">⏳ Queued (Flypast Pending)</span>';
                break;
            case 'POLLING_CATALOG':
                statusBadge = '<span class="badge badge-primary" style="background: #dbeafe; color: #1d4ed8; animation: pulse 2s infinite;">🔄 Polling Catalog</span>';
                break;
            case 'INGESTING':
                statusBadge = '<span class="badge badge-warning" style="background: #fef3c7; color: #b45309;">📥 Ingesting &amp; Stitching</span>';
                break;
            case 'COMPLETED':
                statusBadge = '<span class="badge badge-success" style="background: #dcfce7; color: #15803d;">✅ Ingested Successfully</span>';
                break;
            case 'TIMED_OUT':
                statusBadge = '<span class="badge badge-danger" style="background: #f1f5f9; color: #64748b; border: 1px solid #cbd5e1;">⏱️ Wait Expired (24h)</span>';
                break;
            case 'FAILED':
                if (isTimingMismatch) {
                    statusBadge = '<span class="badge badge-danger" style="background: #fee2e2; color: #991b1b; font-weight: 600;">⚠️ Missed Pass (Newer Acq Found)</span>';
                } else {
                    statusBadge = '<span class="badge badge-danger" style="background: #fee2e2; color: #b91c1c;">❌ Ingestion Failed</span>';
                }
                break;
            default:
                statusBadge = `<span class="badge badge-secondary">${escapeHtml(job.status || 'UNKNOWN')}</span>`;
        }

        let nextPollText = '-';
        if (job.status === 'POLLING_CATALOG') {
            if (job.next_poll_at) {
                const nextDt = new Date(job.next_poll_at);
                const diffSec = Math.round((nextDt - now) / 1000);
                if (diffSec > 0) {
                    nextPollText = `Next in ${diffSec}s (Check #${job.attempts})`;
                } else {
                    nextPollText = `Due now (Polled #${job.attempts})`;
                }
            } else {
                nextPollText = `Due now (Polled #${job.attempts})`;
            }
        } else if (job.status === 'COMPLETED' && job.completed_at) {
            nextPollText = `Completed at ${new Date(job.completed_at).toLocaleTimeString()}`;
        } else if (job.status === 'TIMED_OUT') {
            nextPollText = `<div style="color: #64748b; font-size: 0.8rem; line-height: 1.3;" title="${escapeHtml(job.error_message || 'Exceeded maximum post-pass wait window')}">
                ⏱️ <strong>Wait window expired:</strong> Product not published by Copernicus within 24h.
            </div>`;
        } else if (isTimingMismatch) {
            nextPollText = `<div style="color: #b91c1c; font-size: 0.8rem; line-height: 1.3;" title="${escapeHtml(job.error_message)}">
                <strong>Missed Pass:</strong> Newer imagery detected in catalog.
            </div>`;
        } else if (job.error_message) {
            nextPollText = `<span style="color: #dc3545; font-size: 0.8rem;" title="${escapeHtml(job.error_message)}">${escapeHtml(job.error_message)}</span>`;
        }

        let scanCell = '<span style="color: #94a3b8;">None</span>';
        if (job.scan_folder) {
            scanCell = `<a href="/gallery?scan=${encodeURIComponent(job.scan_folder)}" class="btn btn-sm btn-outline-primary" style="font-size: 0.78rem; padding: 2px 8px; text-decoration: none;" target="_blank">
                📂 View Scan (${escapeHtml(job.scan_folder)})
            </a>`;
        }

        let actionBtns = '';
        if (job.status === 'POLLING_CATALOG' || job.status === 'PENDING_PASS') {
            actionBtns += `<button class="btn btn-outline-primary btn-sm" onclick="pollJobNow(${job.id})" title="Query Copernicus STAC catalog now" style="padding: 3px 8px; font-size: 0.78rem;">
                🔍 Poll Now
            </button>`;
        } else if (job.status === 'TIMED_OUT' || job.status === 'FAILED') {
            actionBtns += `<button class="btn btn-outline-warning btn-sm" onclick="retryJob(${job.id})" title="Reset to POLLING_CATALOG and re-poll" style="padding: 3px 8px; font-size: 0.78rem;">
                🔄 Retry
            </button>`;
        }

        actionBtns += ` <button class="btn btn-outline-danger btn-sm" onclick="deleteJob(${job.id})" title="Delete job" style="padding: 3px 6px; font-size: 0.78rem;">
            🗑️
        </button>`;

        return `
            <tr style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='white'">
                <td style="padding: 12px 14px;">
                    <div style="font-weight: 600; color: #1e293b;">${escapeHtml(job.aoi_name)}</div>
                    <div style="font-size: 0.8rem; color: #64748b;">${passStr}</div>
                </td>
                <td style="padding: 12px 14px;">
                    <div style="font-size: 0.85rem; font-weight: 500; color: #0369a1;">${expStr}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">&plusmn;1h Window</div>
                </td>
                <td style="padding: 12px 14px;">
                    <div style="font-weight: 500; color: #334155;">${escapeHtml(job.satellite || 'Sentinel-1')}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">${escapeHtml(job.orbit_direction || 'Auto')}</div>
                </td>
                <td style="padding: 12px 14px;">
                    ${statusBadge}
                </td>
                <td style="padding: 12px 14px; font-size: 0.85rem; color: #334155;">
                    ${nextPollText}
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

async function pollJobNow(jobId) {
    showToast(`Polling catalog for job #${jobId}...`, 'info');
    try {
        const response = await fetch(`/api/schedule/post_pass_jobs/${jobId}/poll`, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            showToast(`Catalog check finished for job #${jobId}`, 'success');
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
        if (data.status === 'success') {
            showToast(`Job #${jobId} reset to POLLING_CATALOG`, 'success');
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
        if (data.status === 'success') {
            showToast(`Checked ${data.count || 0} post-pass jobs successfully`, 'success');
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

        if (response.ok && data.status === 'success') {
            showToast(data.message || 'Custom post-pass job created successfully', 'success');
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
