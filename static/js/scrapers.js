/**
 * AIS Scrapers Management & Toggle Dashboard JavaScript
 */

let allScrapers = [];
let activeCategory = 'ALL';

document.addEventListener('DOMContentLoaded', () => {
    loadScrapersData();
});

/**
 * Fetch scrapers list and metrics from the API
 */
async function loadScrapersData() {
    const grid = document.getElementById('scrapersGrid');
    grid.innerHTML = `
        <div class="loading-state">
            <div class="spinner"></div>
            <p>Loading AIS scraper configurations...</p>
        </div>
    `;

    try {
        const response = await fetch('/api/scrapers');
        const data = await response.json();

        if (data.status === 'success') {
            allScrapers = data.scrapers || [];
            updateMetrics(data.metrics || {});
            renderScrapers();
        } else {
            showError('Failed to load scrapers: ' + (data.error || 'Unknown error'));
        }
    } catch (err) {
        showError('Network error loading scraper data.');
        console.error(err);
    }
}

/**
 * Update top KPI metrics cards
 */
function updateMetrics(metrics) {
    const activeEl = document.getElementById('metricActiveScrapers');
    const runsEl = document.getElementById('metricTotalRuns');
    const recordsEl = document.getElementById('metricTotalRecords');
    const rateEl = document.getElementById('metricSuccessRate');

    if (activeEl) {
        activeEl.textContent = `${metrics.active_scrapers || 0} / ${metrics.total_scrapers || 0} Active`;
    }
    if (runsEl) {
        runsEl.textContent = (metrics.total_runs || 0).toLocaleString();
    }
    if (recordsEl) {
        recordsEl.textContent = (metrics.total_records_ingested || 0).toLocaleString();
    }
    if (rateEl) {
        rateEl.textContent = `${metrics.overall_success_rate || 100}%`;
    }
}

/**
 * Render filtered scrapers into the grid
 */
function renderScrapers() {
    const grid = document.getElementById('scrapersGrid');
    const query = (document.getElementById('scraperSearchInput')?.value || '').toLowerCase().trim();

    const filtered = allScrapers.filter(s => {
        // Category filter
        if (activeCategory !== 'ALL' && s.category !== activeCategory) {
            return false;
        }
        // Text search
        if (query) {
            const matchName = s.name.toLowerCase().includes(query);
            const matchDisplay = (s.display_name || '').toLowerCase().includes(query);
            const matchDesc = (s.description || '').toLowerCase().includes(query);
            const matchCat = (s.category || '').toLowerCase().includes(query);
            if (!matchName && !matchDisplay && !matchDesc && !matchCat) {
                return false;
            }
        }
        return true;
    });

    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.5" style="margin-bottom: 12px;">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                <h3>No AIS Scrapers Found</h3>
                <p style="color: #64748b;">No scraper matches the current search and category filter.</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = filtered.map(s => {
        const catClass = getCategoryClass(s.category);
        const lastRunFormatted = s.last_run_at ? formatDateTime(s.last_run_at) : 'Never executed';
        const cardClass = s.enabled ? 'scraper-card' : 'scraper-card disabled';

        return `
            <div class="${cardClass}" id="card-${s.name}">
                <div>
                    <div class="card-top">
                        <span class="category-tag ${catClass}">${escapeHtml(s.category)}</span>
                        <div class="switch-container">
                            <span class="switch-label ${s.enabled ? 'on' : 'off'}" id="label-${s.name}">
                                ${s.enabled ? 'Active' : 'Disabled'}
                            </span>
                            <label class="toggle-switch">
                                <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleScraper('${s.name}', this)">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>

                    <h3 class="scraper-title">${escapeHtml(s.display_name)}</h3>
                    <span class="scraper-code-name">${escapeHtml(s.name)}</span>
                    <p class="scraper-desc">${escapeHtml(s.description)}</p>

                    <div class="scraper-stats-grid">
                        <div class="s-stat-item">
                            <span class="s-stat-label">Total Runs</span>
                            <span class="s-stat-value">${(s.total_runs || 0).toLocaleString()}</span>
                        </div>
                        <div class="s-stat-item">
                            <span class="s-stat-label">Harvested</span>
                            <span class="s-stat-value">${(s.total_records || 0).toLocaleString()}</span>
                        </div>
                        <div class="s-stat-item">
                            <span class="s-stat-label">Success Rate</span>
                            <span class="s-stat-value ${s.success_rate >= 90 ? 'success' : 'danger'}">${s.success_rate}%</span>
                        </div>
                    </div>

                    <div class="scraper-last-run">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        Last run: <strong>${lastRunFormatted}</strong>
                    </div>
                </div>

                <div class="card-actions">
                    <button class="btn btn-outline-primary btn-sm" id="testBtn-${s.name}" onclick="testScraper('${s.name}', this)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polygon points="5 3 19 12 5 21 5 3"></polygon>
                        </svg>
                        Test Scraper
                    </button>
                    <a href="/logs?plugin=${encodeURIComponent(s.name)}" class="btn btn-secondary btn-sm" style="text-decoration: none;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="8" y1="6" x2="21" y2="6"></line>
                            <line x1="8" y1="12" x2="21" y2="12"></line>
                            <line x1="8" y1="18" x2="21" y2="18"></line>
                        </svg>
                        View Logs
                    </a>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Handle scraper toggle switch click
 */
async function toggleScraper(pluginName, checkbox) {
    const enabled = checkbox.checked;
    const labelEl = document.getElementById(`label-${pluginName}`);
    const cardEl = document.getElementById(`card-${pluginName}`);

    // Optimistic UI update
    if (labelEl) {
        labelEl.textContent = enabled ? 'Active' : 'Disabled';
        labelEl.className = `switch-label ${enabled ? 'on' : 'off'}`;
    }
    if (cardEl) {
        cardEl.className = enabled ? 'scraper-card' : 'scraper-card disabled';
    }

    try {
        const response = await fetch(`/api/scrapers/${encodeURIComponent(pluginName)}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        const data = await response.json();

        if (data.status === 'success') {
            showToast(`Scraper "${pluginName}" is now ${enabled ? 'ENABLED' : 'DISABLED'}.`, 'success');
            // Update local state
            const target = allScrapers.find(s => s.name === pluginName);
            if (target) target.enabled = enabled;
            // Update metrics
            const activeCount = allScrapers.filter(s => s.enabled).length;
            const activeEl = document.getElementById('metricActiveScrapers');
            if (activeEl) {
                activeEl.textContent = `${activeCount} / ${allScrapers.length} Active`;
            }
        } else {
            throw new Error(data.error || 'Server rejected toggle');
        }
    } catch (err) {
        // Revert on error
        checkbox.checked = !enabled;
        if (labelEl) {
            labelEl.textContent = !enabled ? 'Active' : 'Disabled';
            labelEl.className = `switch-label ${!enabled ? 'on' : 'off'}`;
        }
        if (cardEl) {
            cardEl.className = !enabled ? 'scraper-card' : 'scraper-card disabled';
        }
        showToast(`Failed to update scraper: ${err.message}`, 'error');
    }
}

/**
 * Execute on-demand test scrape
 */
async function testScraper(pluginName, btn) {
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `
        <span class="spinner" style="width: 14px; height: 14px; border-width: 2px; margin: 0; display: inline-block;"></span>
        Testing...
    `;

    try {
        const response = await fetch(`/api/scrapers/${encodeURIComponent(pluginName)}/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbox: [103.8, 1.2, 103.9, 1.3] }),
        });
        const data = await response.json();

        if (data.status === 'success') {
            const count = data.total_inserted || 0;
            showToast(`Test Succeeded: ${count} vessel position(s) captured by ${pluginName}`, 'success');
            loadScrapersData(); // refresh counts
        } else {
            showToast(`Test Failed for ${pluginName}: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        showToast(`Test Execution Error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
    }
}

/**
 * Filter categories
 */
function selectCategory(category) {
    activeCategory = category;
    document.querySelectorAll('.pill').forEach(pill => {
        if (pill.dataset.category === category) {
            pill.classList.add('active');
        } else {
            pill.classList.remove('active');
        }
    });
    renderScrapers();
}

/**
 * Search filter trigger
 */
function filterScraperCards() {
    renderScrapers();
}

/**
 * Toast notifications
 */
function showToast(message, type = 'success') {
    const toast = document.getElementById('toastNotification');
    if (!toast) return;

    toast.textContent = message;
    toast.className = `toast-notification ${type}`;
    toast.style.display = 'block';

    setTimeout(() => {
        toast.style.display = 'none';
    }, 4000);
}

function getCategoryClass(cat) {
    switch (cat) {
        case 'Live Web Scraper': return 'category-live';
        case 'Community API': return 'category-api';
        case 'Radio Gateway': return 'category-radio';
        case 'Hardware / NMEA': return 'category-hardware';
        case 'Simulator': return 'category-simulator';
        default: return '';
    }
}

function formatDateTime(isoStr) {
    if (!isoStr) return '-';
    try {
        const date = new Date(isoStr);
        return date.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            timeZoneName: 'short'
        });
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

function showError(msg) {
    const grid = document.getElementById('scrapersGrid');
    if (grid) {
        grid.innerHTML = `
            <div class="empty-state" style="border-color: #fecaca; background: #fff5f5;">
                <h3 style="color: #dc2626;">Error</h3>
                <p style="color: #991b1b;">${escapeHtml(msg)}</p>
                <button class="btn btn-primary btn-sm" onclick="loadScrapersData()">Try Again</button>
            </div>
        `;
    }
}
