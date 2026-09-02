/**
 * AIS Scrapers Management & Toggle Dashboard JavaScript
 */

let allScrapers = [];
let activeCategory = 'ALL';
let countdownInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    loadScrapersData();
    startCountdownTimer();
});

/**
 * Fetch scrapers list and metrics from the API
 */
async function loadScrapersData() {
    const grid = document.getElementById('scrapersGrid');
    if (!grid) return;

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
        const cooling = metrics.cooling_scrapers ? ` (${metrics.cooling_scrapers} in cooldown)` : '';
        activeEl.textContent = `${metrics.active_scrapers || 0} / ${metrics.total_scrapers || 0} Active${cooling}`;
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
        if (activeCategory !== 'ALL' && s.category !== activeCategory) {
            return false;
        }
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
        const hasProxy = Boolean(s.config && s.config.proxy_url);

        return `
            <div class="${cardClass}" id="card-${s.name}">
                <div>
                    <div class="card-top">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="category-tag ${catClass}">${escapeHtml(s.category)}</span>
                            ${s.is_cooling_down ? `
                                <span class="cooldown-badge">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <circle cx="12" cy="12" r="10"></circle>
                                        <polyline points="12 6 12 12 16 14"></polyline>
                                    </svg>
                                    COOLING DOWN
                                </span>
                            ` : ''}
                            ${hasProxy ? `
                                <span class="category-tag" style="background: #e0f2fe; color: #0369a1;" title="Configured with custom proxy">
                                    PROXY
                                </span>
                            ` : ''}
                        </div>
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

                    ${s.is_cooling_down ? `
                        <div class="cooldown-alert-box">
                            <div class="cooldown-text">
                                <div>Bot protection/rate limit detected. Resumes in <strong class="cooldown-timer" data-seconds="${s.cooldown_remaining_seconds}">${formatDuration(s.cooldown_remaining_seconds)}</strong></div>
                                ${s.last_failure_reason ? `<div class="cooldown-reason">${escapeHtml(s.last_failure_reason)}</div>` : ''}
                            </div>
                            <button type="button" class="btn-reset-cooldown" onclick="resetCooldown('${s.name}')">Reset</button>
                        </div>
                    ` : ''}

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
                    <button class="btn btn-secondary btn-sm" onclick="openConfigModal('${s.name}')" title="Configure proxy, API keys, and network settings">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="3"></circle>
                            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                        </svg>
                        Config
                    </button>
                    <button class="btn btn-outline-primary btn-sm" id="testBtn-${s.name}" onclick="testScraper('${s.name}', this)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polygon points="5 3 19 12 5 21 5 3"></polygon>
                        </svg>
                        Test
                    </button>
                    <a href="/logs?plugin=${encodeURIComponent(s.name)}" class="btn btn-secondary btn-sm" style="text-decoration: none;">
                        Logs
                    </a>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Live cooldown countdown timer loop
 */
function startCountdownTimer() {
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(() => {
        const timerEls = document.querySelectorAll('.cooldown-timer');
        timerEls.forEach(el => {
            let secs = parseInt(el.getAttribute('data-seconds') || '0', 10);
            if (secs > 0) {
                secs -= 1;
                el.setAttribute('data-seconds', secs);
                el.textContent = formatDuration(secs);
            } else if (secs === 0) {
                el.textContent = 'Expired (re-checking...)';
                // Trigger refresh once cooldown reaches zero
                loadScrapersData();
            }
        });
    }, 1000);
}

function formatDuration(seconds) {
    if (seconds <= 0) return '0s';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    const parts = [];
    if (hrs > 0) parts.push(`${hrs}h`);
    if (mins > 0 || hrs > 0) parts.push(`${mins}m`);
    parts.push(`${secs}s`);
    return parts.join(' ');
}

/**
 * Handle scraper toggle switch click
 */
async function toggleScraper(pluginName, checkbox) {
    const enabled = checkbox.checked;
    const labelEl = document.getElementById(`label-${pluginName}`);
    const cardEl = document.getElementById(`card-${pluginName}`);

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
            const target = allScrapers.find(s => s.name === pluginName);
            if (target) target.enabled = enabled;
            const activeCount = allScrapers.filter(s => s.enabled).length;
            const activeEl = document.getElementById('metricActiveScrapers');
            if (activeEl) {
                activeEl.textContent = `${activeCount} / ${allScrapers.length} Active`;
            }
        } else {
            throw new Error(data.error || 'Server rejected toggle');
        }
    } catch (err) {
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
 * Open configuration modal
 */
async function openConfigModal(pluginName) {
    const scraper = allScrapers.find(s => s.name === pluginName);
    if (!scraper) return;

    const modal = document.getElementById('scraperConfigModal');
    const title = document.getElementById('modalScraperTitle');
    const hiddenName = document.getElementById('configPluginName');
    const enabledCheckbox = document.getElementById('cfgEnabled');
    const descInput = document.getElementById('cfgDescription');
    const proxyInput = document.getElementById('cfgProxyUrl');
    const apiKeyInput = document.getElementById('cfgApiKey');
    const hostInput = document.getElementById('cfgHost');
    const portInput = document.getElementById('cfgPort');
    const uaInput = document.getElementById('cfgUserAgent');
    const timeoutInput = document.getElementById('cfgTimeout');
    const zoneDelayInput = document.getElementById('cfgZoneDelay');
    const zoneSizeInput = document.getElementById('cfgZoneSize');

    const groupUdp = document.getElementById('groupUdp');
    const groupApiKey = document.getElementById('groupApiKey');
    const groupProxy = document.getElementById('groupProxy');
    const groupZonePacing = document.getElementById('groupZonePacing');

    title.textContent = `Configure ${scraper.display_name}`;
    hiddenName.value = pluginName;

    if (enabledCheckbox) enabledCheckbox.checked = Boolean(scraper.enabled);
    if (descInput) descInput.value = scraper.description || '';

    const cfg = scraper.config || {};
    proxyInput.value = cfg.proxy_url || '';
    apiKeyInput.value = cfg.api_key || '';
    hostInput.value = cfg.host || '0.0.0.0';
    portInput.value = cfg.port || 10110;
    uaInput.value = cfg.user_agent || '';
    timeoutInput.value = cfg.timeout || 30;
    if (zoneDelayInput) zoneDelayInput.value = (cfg.zone_delay_seconds !== undefined ? cfg.zone_delay_seconds : (cfg.zone_delay !== undefined ? cfg.zone_delay : 0));
    if (zoneSizeInput) zoneSizeInput.value = (cfg.zone_size_nm !== undefined ? cfg.zone_size_nm : 10.0);

    // Show/hide relevant fields based on scraper type
    if (pluginName === 'UDPListenerPlugin') {
        if (groupUdp) groupUdp.style.display = 'flex';
        if (groupProxy) groupProxy.style.display = 'none';
        if (groupApiKey) groupApiKey.style.display = 'none';
        if (groupZonePacing) groupZonePacing.style.display = 'none';
    } else {
        if (groupUdp) groupUdp.style.display = 'none';
        if (groupProxy) groupProxy.style.display = 'flex';
        if (groupApiKey) groupApiKey.style.display = (pluginName === 'AprsFiPlugin') ? 'flex' : 'none';
        if (groupZonePacing) groupZonePacing.style.display = 'flex';
    }

    modal.style.display = 'flex';

    // Fetch fresh details from individual scraper API
    try {
        const response = await fetch(`/api/scrapers/${encodeURIComponent(pluginName)}`);
        if (response.ok) {
            const data = await response.json();
            if (data.status === 'success' && data.scraper) {
                const fresh = data.scraper;
                if (enabledCheckbox) enabledCheckbox.checked = Boolean(fresh.enabled);
                if (descInput) descInput.value = fresh.description || '';
                const freshCfg = fresh.config || {};
                proxyInput.value = freshCfg.proxy_url || '';
                apiKeyInput.value = freshCfg.api_key || '';
                hostInput.value = freshCfg.host || '0.0.0.0';
                portInput.value = freshCfg.port || 10110;
                uaInput.value = freshCfg.user_agent || '';
                timeoutInput.value = freshCfg.timeout || 30;
                if (zoneDelayInput) zoneDelayInput.value = (freshCfg.zone_delay_seconds !== undefined ? freshCfg.zone_delay_seconds : (freshCfg.zone_delay !== undefined ? freshCfg.zone_delay : 0));
                if (zoneSizeInput) zoneSizeInput.value = (freshCfg.zone_size_nm !== undefined ? freshCfg.zone_size_nm : 10.0);
            }
        }
    } catch (err) {
        console.warn('Could not fetch individual scraper detail:', err);
    }
}

function closeConfigModal() {
    const modal = document.getElementById('scraperConfigModal');
    if (modal) modal.style.display = 'none';
}

/**
 * Save configuration via API
 */
async function saveScraperConfig(event) {
    event.preventDefault();
    const pluginName = document.getElementById('configPluginName').value;
    const btnSave = document.getElementById('btnSaveConfig');
    const enabledCheckbox = document.getElementById('cfgEnabled');
    const descInput = document.getElementById('cfgDescription');

    const configData = {
        proxy_url: document.getElementById('cfgProxyUrl').value.trim(),
        api_key: document.getElementById('cfgApiKey').value.trim(),
        host: document.getElementById('cfgHost').value.trim(),
        port: parseInt(document.getElementById('cfgPort').value, 10) || 10110,
        user_agent: document.getElementById('cfgUserAgent').value.trim(),
        timeout: parseInt(document.getElementById('cfgTimeout').value, 10) || 30,
        zone_delay_seconds: parseFloat(document.getElementById('cfgZoneDelay').value) || 0,
        zone_size_nm: parseFloat(document.getElementById('cfgZoneSize').value) || 10.0,
    };

    const payload = {
        enabled: enabledCheckbox ? enabledCheckbox.checked : true,
        description: descInput ? descInput.value.trim() : null,
        config: configData,
    };

    btnSave.disabled = true;
    btnSave.textContent = 'Saving...';

    try {
        const response = await fetch(`/api/scrapers/${encodeURIComponent(pluginName)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json();

        if (data.status === 'success') {
            showToast(`Scraper "${pluginName}" updated successfully.`, 'success');
            closeConfigModal();
            loadScrapersData();
        } else {
            showToast(`Error saving configuration: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        showToast(`Network error saving configuration: ${err.message}`, 'error');
    } finally {
        btnSave.disabled = false;
        btnSave.textContent = 'Save Changes';
    }
}

/**
 * Reset active cooldown for a scraper
 */
async function resetCooldown(pluginName) {
    try {
        const response = await fetch(`/api/scrapers/${encodeURIComponent(pluginName)}/reset_cooldown`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await response.json();

        if (data.status === 'success') {
            showToast(`Cooldown reset for "${pluginName}". Automated scraping will resume.`, 'success');
            loadScrapersData();
        } else {
            showToast(`Failed to reset cooldown: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
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
            const logEntry = (data.logs && data.logs[0]) || {};
            if (logEntry.status === 'FAILED') {
                showToast(`Test Failed for ${pluginName}: ${logEntry.error || 'Provider execution failed'}`, 'error');
            } else {
                showToast(`Test Succeeded: ${count} vessel position(s) captured by ${pluginName}`, 'success');
            }
            loadScrapersData();
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

