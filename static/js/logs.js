/**
 * AIS Scraper Execution Logs Dashboard JavaScript
 */

let currentLogs = [];
let currentOffset = 0;
let currentLimit = 50;
let autoRefreshInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    // Check if plugin is passed in URL query param
    const urlParams = new URLSearchParams(window.location.search);
    const pluginParam = urlParams.get('plugin');
    if (pluginParam) {
        const pluginSelect = document.getElementById('filterPlugin');
        if (pluginSelect) {
            pluginSelect.value = pluginParam;
        }
    }

    loadLogsData();
    setupAutoRefresh(10);
});

/**
 * Fetch logs and summary metrics from /api/logs
 */
async function loadLogsData() {
    const plugin = document.getElementById('filterPlugin')?.value || '';
    const status = document.getElementById('filterStatus')?.value || '';
    const limit = parseInt(document.getElementById('filterLimit')?.value || '50', 10);
    currentLimit = limit;

    const queryParams = new URLSearchParams({
        limit: limit.toString(),
        offset: currentOffset.toString(),
    });
    if (plugin) queryParams.set('plugin', plugin);
    if (status) queryParams.set('status', status);

    try {
        const response = await fetch(`/api/logs?${queryParams.toString()}`);
        const data = await response.json();

        if (data.status === 'success') {
            currentLogs = data.logs || [];
            updateSummaryMetrics(data.metrics || {});
            renderLogsTable(currentLogs);
            updatePagination(data.count || 0);
        } else {
            renderErrorRow(data.error || 'Failed to fetch scraper logs');
        }
    } catch (err) {
        console.error('Error fetching logs:', err);
        renderErrorRow('Network error fetching scraper logs.');
    }
}

/**
 * Update top summary pills
 */
function updateSummaryMetrics(metrics) {
    const totalEl = document.getElementById('summaryTotalRuns');
    const rateEl = document.getElementById('summarySuccessRate');
    const recordsEl = document.getElementById('summaryTotalRecords');

    if (totalEl) totalEl.textContent = (metrics.total_runs || 0).toLocaleString();
    if (rateEl) rateEl.textContent = `${metrics.overall_success_rate || 100}%`;
    if (recordsEl) recordsEl.textContent = (metrics.total_records || 0).toLocaleString();
}

/**
 * Render log rows into the table
 */
function renderLogsTable(logs) {
    const tbody = document.getElementById('logsTableBody');
    if (!tbody) return;

    if (logs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 40px; color: #64748b;">
                    No execution logs match the selected filters.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = logs.map(log => {
        const isSuccess = log.status === 'SUCCESS';
        const isCooldown = log.status === 'COOLDOWN_SKIPPED';
        let statusBadge = '';
        if (isSuccess) {
            statusBadge = `<span class="status-pill status-success">SUCCESS</span>`;
        } else if (isCooldown) {
            statusBadge = `<span class="status-pill status-cooldown">COOLDOWN</span>`;
        } else {
            statusBadge = `<span class="status-pill status-failed">${escapeHtml(log.status || 'FAILED')}</span>`;
        }

        const timeFormatted = formatUtcTime(log.timestamp);
        const recordBadge = log.records_inserted > 0
            ? `<span class="records-badge">+${log.records_inserted}</span>`
            : `<span class="records-badge zero">0</span>`;

        let detailsHtml = '';
        if (isCooldown) {
            detailsHtml = `<span class="cooldown-note">${escapeHtml(log.error_message || 'Skipped due to active cooldown backoff')}</span>`;
        } else if (log.error_message) {
            detailsHtml = `<div class="error-snippet">${escapeHtml(log.error_message)}</div>`;
        } else if (isSuccess) {
            detailsHtml = `<span class="success-note">Successfully harvested ${log.records_inserted} vessel positions</span>`;
        } else {
            detailsHtml = `<span class="success-note" style="color: #64748b;">No records collected</span>`;
        }

        return `
            <tr>
                <td>${statusBadge}</td>
                <td style="font-size: 0.82rem; color: #475569;">${timeFormatted}</td>
                <td><span class="plugin-code">${escapeHtml(log.plugin_name)}</span></td>
                <td>${recordBadge}</td>
                <td>${detailsHtml}</td>
            </tr>
        `;
    }).join('');
}

/**
 * Filter rows locally via search input
 */
function filterLocalRows() {
    const q = (document.getElementById('filterSearch')?.value || '').toLowerCase().trim();
    if (!q) {
        renderLogsTable(currentLogs);
        return;
    }
    const filtered = currentLogs.filter(log => {
        return (log.plugin_name || '').toLowerCase().includes(q) ||
               (log.status || '').toLowerCase().includes(q) ||
               (log.error_message || '').toLowerCase().includes(q) ||
               (log.timestamp || '').toLowerCase().includes(q);
    });
    renderLogsTable(filtered);
}

/**
 * Handle filter change
 */
function applyFilters() {
    currentOffset = 0;
    loadLogsData();
}

/**
 * Pagination handlers
 */
function updatePagination(count) {
    const infoEl = document.getElementById('paginationInfo');
    const prevBtn = document.getElementById('btnPrevPage');
    const nextBtn = document.getElementById('btnNextPage');

    const start = currentOffset + 1;
    const end = currentOffset + count;

    if (infoEl) {
        infoEl.textContent = count > 0 ? `Showing ${start} - ${end} logs` : 'Showing 0 logs';
    }
    if (prevBtn) {
        prevBtn.disabled = currentOffset === 0;
    }
    if (nextBtn) {
        nextBtn.disabled = count < currentLimit;
    }
}

function prevPage() {
    if (currentOffset >= currentLimit) {
        currentOffset -= currentLimit;
        loadLogsData();
    }
}

function nextPage() {
    currentOffset += currentLimit;
    loadLogsData();
}

/**
 * Auto-refresh configuration
 */
function changeAutoRefresh(secondsStr) {
    const seconds = parseInt(secondsStr, 10);
    setupAutoRefresh(seconds);
}

function setupAutoRefresh(seconds) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    if (seconds > 0) {
        autoRefreshInterval = setInterval(() => {
            loadLogsData();
        }, seconds * 1000);
    }
}

/**
 * Export current logs as JSON file
 */
function exportLogsJson() {
    if (!currentLogs || currentLogs.length === 0) {
        alert('No logs to export.');
        return;
    }
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(currentLogs, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `scraper_logs_${new Date().toISOString().slice(0,10)}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
}

function formatUtcTime(isoStr) {
    if (!isoStr) return '-';
    try {
        const date = new Date(isoStr);
        return date.toISOString().replace('T', ' ').replace('Z', ' UTC');
    } catch {
        return isoStr;
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

function renderErrorRow(msg) {
    const tbody = document.getElementById('logsTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 40px; color: #dc2626;">
                    <strong>Error:</strong> ${escapeHtml(msg)}
                </td>
            </tr>
        `;
    }
}
