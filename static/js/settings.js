/**
 * JavaScript controller for Global Platform Settings.
 */

let currentSection = 'cv';
let currentSettings = {};
let savedFormSnapshot = '';

function setSaveStatus(message, isDirty = false) {
    const status = document.getElementById('settingsSaveStatus');
    if (status) {
        status.textContent = message;
        status.classList.toggle('is-dirty', isDirty);
    }
}

function formSnapshot() {
    const form = document.getElementById('settingsForm');
    if (!form) return '';
    return JSON.stringify(collectFormData());
}

function updateDirtyState() {
    validateCrossFieldConstraints();
    const dirty = formSnapshot() !== savedFormSnapshot;
    setSaveStatus(dirty ? 'Unsaved changes' : 'All changes saved', dirty);
    const saveBtn = document.getElementById('btnSaveSettings');
    if (saveBtn) saveBtn.disabled = !dirty;
}

function initFormChangeTracking() {
    const form = document.getElementById('settingsForm');
    if (!form) return;
    form.addEventListener('input', updateDirtyState);
    form.addEventListener('change', updateDirtyState);
    const maskToggle = document.getElementById('input_cv_dem_land_mask_enabled');
    if (maskToggle) maskToggle.addEventListener('change', updateDependentControls);
}

async function loadSettingsRuntimeStatus() {
    const statusEl = document.getElementById('settingsRuntimeStatus');
    if (!statusEl) return;
    const statusDot = statusEl.parentElement ? statusEl.parentElement.querySelector('.status-pulse') : null;
    try {
        const res = await fetch('/api/schedule/status');
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.error || `HTTP ${res.status}`);
        const scheduler = data.scheduler || {};
        const state = String(scheduler.operational_status || scheduler.health || 'UNKNOWN').toUpperCase();
        statusEl.textContent = `Scheduler runtime: ${state}`;
        if (statusDot) {
            statusDot.style.backgroundColor = state === 'RUNNING' ? '#10b981' : (state === 'DEGRADED' ? '#f59e0b' : '#64748b');
        }
    } catch (err) {
        statusEl.textContent = 'Scheduler runtime: UNAVAILABLE';
        if (statusDot) statusDot.style.backgroundColor = '#64748b';
    }
}

function initNavTabs() {
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const sec = btn.dataset.section || btn.getAttribute('data-section');
            if (sec) {
                switchSection(sec);
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initNavTabs();
    initFormChangeTracking();
    initColorControls();
    window.addEventListener('beforeunload', (event) => {
        if (formSnapshot() !== savedFormSnapshot) {
            event.preventDefault();
            event.returnValue = '';
        }
    });
    loadSettings();
    loadSettingsRuntimeStatus();
});

function switchSection(sectionId) {
    if (!sectionId) return;
    currentSection = sectionId;

    // Update nav tab buttons
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        const isMatch = (btn.dataset.section === sectionId || btn.getAttribute('data-section') === sectionId);
        btn.classList.toggle('active', isMatch);
    });

    // Update sections
    document.querySelectorAll('.settings-section').forEach(sec => {
        sec.classList.toggle('active', sec.id === `section-${sectionId}`);
    });
}

async function loadSettings() {
    const loading = document.getElementById('settingsLoadingIndicator');
    const form = document.getElementById('settingsForm');

    if (loading) loading.style.display = 'flex';
    if (form) form.style.opacity = '0.4';

    try {
        const res = await fetch('/api/settings?definitions=true');
        const data = await res.json();

        if (data.status === 'success' && data.settings) {
            currentSettings = data.settings;
            populateForm(data.settings);
            savedFormSnapshot = formSnapshot();
            updateDirtyState();
            updateCredentialStatus(data.runtime || {});
        } else {
            showToast(data.error || 'Failed to load settings', false);
        }
    } catch (err) {
        console.error('Failed to load settings:', err);
        showToast('Error connecting to settings API', false);
    } finally {
        if (loading) loading.style.display = 'none';
        if (form) form.style.opacity = '1';
    }
}

function updateCredentialStatus(runtime) {
    const copernicusStatus = document.getElementById('copernicusCredentialStatus');
    if (copernicusStatus) {
        copernicusStatus.textContent = runtime.copernicus_configured ? 'Configured in .env' : 'Missing from .env';
        copernicusStatus.classList.toggle('status-missing', !runtime.copernicus_configured);
    }
    const n2yoStatus = document.getElementById('n2yoCredentialStatus');
    if (n2yoStatus) {
        n2yoStatus.textContent = runtime.n2yo_configured ? 'Configured in .env' : 'Missing from .env';
        n2yoStatus.classList.toggle('status-missing', !runtime.n2yo_configured);
    }
}

function initColorControls() {
    document.querySelectorAll('input[type="color"]').forEach(colorInput => {
        const hexInput = document.getElementById(colorInput.id.replace('input_', 'hex_'));
        if (!hexInput) return;
        colorInput.addEventListener('input', () => {
            hexInput.value = colorInput.value.toUpperCase();
        });
        hexInput.addEventListener('input', () => {
            const value = hexInput.value.trim();
            if (/^#[0-9a-f]{6}$/i.test(value)) colorInput.value = value;
        });
    });
}

function updateDependentControls() {
    const maskEnabled = getBool('input_cv_dem_land_mask_enabled', true);
    ['input_cv_coastal_buffer_pixels', 'sync_cv_coastal_buffer_pixels',
        'input_cv_morph_close_kernel', 'sync_cv_morph_close_kernel']
        .forEach(id => {
            const control = document.getElementById(id);
            if (control) control.disabled = !maskEnabled;
        });
}

function validateCrossFieldConstraints() {
    const minimum = document.getElementById('input_cv_minimum_area');
    const maximum = document.getElementById('input_cv_maximum_area');
    if (!minimum || !maximum) return true;
    const valid = Number(minimum.value) < Number(maximum.value);
    maximum.setCustomValidity(valid ? '' : 'Maximum vessel area must be greater than minimum vessel area.');
    return valid;
}

function populateForm(sections) {
    for (const [sectionKey, fields] of Object.entries(sections)) {
        for (const [fieldKey, fieldDef] of Object.entries(fields)) {
            if (sectionKey === 'scheduler' && fieldKey === 'enabled_satellites') {
                const rawVal = fieldDef.value;
                const checkedSats = Array.isArray(rawVal)
                    ? rawVal
                    : (typeof rawVal === 'string' ? rawVal.split(',').map(s => s.trim()) : ['Sentinel-1A', 'Sentinel-1C']);
                ['Sentinel-1A', 'Sentinel-1B', 'Sentinel-1C', 'Sentinel-1D'].forEach(sat => {
                    const chk = document.getElementById(`sat_${sat}`);
                    if (chk) chk.checked = checkedSats.includes(sat);
                });
                continue;
            }

            const inputId = `input_${sectionKey}_${fieldKey}`;
            const elem = document.getElementById(inputId);
            if (!elem) continue;

            const val = fieldDef.value;

            if (elem.type === 'checkbox') {
                elem.checked = Boolean(val);
            } else if (elem.type === 'range') {
                elem.value = val;
                const syncElem = document.getElementById(`sync_${sectionKey}_${fieldKey}`);
                if (syncElem) syncElem.value = val;
                const unit = fieldKey.includes('pixel') ? ' px' : '';
                updateRangeDisplay(`${sectionKey}_${fieldKey}`, val, unit);
            } else if (elem.type === 'color') {
                elem.value = val;
                const hexElem = document.getElementById(`hex_${sectionKey}_${fieldKey}`);
                if (hexElem) hexElem.value = val;
            } else {
                elem.value = (val !== null && val !== undefined) ? val : '';
            }
        }
    }
    updateDependentControls();
}

function updateRangeDisplay(fieldKey, value, unit) {
    const badge = document.getElementById(`badge_${fieldKey}`);
    if (badge) {
        badge.innerText = `${value}${unit || ''}`;
    }
    const sync = document.getElementById(`sync_${fieldKey}`);
    if (sync && sync.value != value) {
        sync.value = value;
    }
}

function syncSlider(fieldKey, value) {
    const slider = document.getElementById(`input_${fieldKey}`);
    if (slider) {
        slider.value = value;
    }
    const unit = fieldKey.includes('pixel') ? ' px' : '';
    const badge = document.getElementById(`badge_${fieldKey}`);
    if (badge) {
        badge.innerText = `${value}${unit}`;
    }
}

function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

function getInputValue(id, fallback = '') {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return el.value !== undefined ? el.value : fallback;
}

function getInt(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const v = parseInt(el.value, 10);
    return isNaN(v) ? fallback : v;
}

function getFloat(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const v = parseFloat(el.value);
    return isNaN(v) ? fallback : v;
}

function getString(id, fallback = '') {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return typeof el.value === 'string' ? el.value.trim() : fallback;
}

function getBool(id, fallback = false) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return Boolean(el.checked);
}

function collectFormData() {
    const payload = {
        cv: {
            coastal_buffer_pixels: getInt('input_cv_coastal_buffer_pixels', 81),
            morph_close_kernel: getInt('input_cv_morph_close_kernel', 27),
            dem_land_mask_enabled: getBool('input_cv_dem_land_mask_enabled', true),
            threshold: getInt('input_cv_threshold', 40),
            filter_type: getString('input_cv_filter_type', 'none'),
            minimum_area: getFloat('input_cv_minimum_area', 50.0),
            maximum_area: getFloat('input_cv_maximum_area', 5000.0),
            dilation_iterations: getInt('input_cv_dilation_iterations', 2),
            pixel_spacing_meters: getFloat('input_cv_pixel_spacing_meters', 10.0),
        },
        imagery: {
            default_evalscript: getString('input_imagery_default_evalscript', 'SAR'),
            search_window_days: getInt('input_imagery_search_window_days', 30),
            resolution_meters: getFloat('input_imagery_resolution_meters', 10.0),
            max_image_size: getInt('input_imagery_max_image_size', 2500),
        },
        scheduler: {
            auto_capture_default: getBool('input_scheduler_auto_capture_default', false),
            aoi_check_interval_seconds: getFloat('input_scheduler_aoi_check_interval_seconds', 30.0),
            satellite_norad_ids: getString('input_scheduler_satellite_norad_ids', '39634, 41456, 62232'),
            enabled_satellites: Array.from(document.querySelectorAll('input[name="satellite_selection"]:checked')).map(el => el.value),
        },
        map_ui: {
            default_lat: getFloat('input_map_ui_default_lat', 1.290270),
            default_lng: getFloat('input_map_ui_default_lng', 103.851959),
            default_zoom: getInt('input_map_ui_default_zoom', 10),
            sar_opacity: getFloat('input_map_ui_sar_opacity', 1.0),
            ais_overlay_default_enabled: getBool('input_map_ui_ais_overlay_default_enabled', true),
            nautical_chart_default_enabled: getBool('input_map_ui_nautical_chart_default_enabled', false),
            color_cv_detection: getString('input_map_ui_color_cv_detection', '#ff3333'),
            color_obb_detection: getString('input_map_ui_color_obb_detection', '#e67e22'),
        },
        notifications: {
            enabled: getBool('input_notifications_enabled', true),
            duration_seconds: getFloat('input_notifications_duration_seconds', 3.0),
            show_info: getBool('input_notifications_show_info', true),
            show_success: getBool('input_notifications_show_success', true),
            show_warning: getBool('input_notifications_show_warning', true),
            show_error: getBool('input_notifications_show_error', true),
        },
    };
    return payload;
}

async function saveAllSettings() {
    const saveBtn = document.getElementById('btnSaveSettings');
    const originalText = saveBtn ? saveBtn.innerHTML : '';
    const form = document.getElementById('settingsForm');
    if (form && (!validateCrossFieldConstraints() || !form.checkValidity())) {
        form.reportValidity();
        return;
    }
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = 'Saving...';
    }

    try {
        const payload = collectFormData();
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (data.status === 'success') {
            const errors = Array.isArray(data.apply_errors) ? data.apply_errors : [];
            const restart = Array.isArray(data.requires_restart) ? data.requires_restart : [];
            const details = [
                errors.length ? `Runtime apply errors: ${errors.join('; ')}` : '',
                restart.length ? `Restart required for: ${restart.join(', ')}` : '',
            ].filter(Boolean).join(' ');
            showToast(details || 'Settings saved and live runtime settings applied', errors.length === 0);
            await loadSettings();
            await loadSettingsRuntimeStatus();
        } else {
            showToast(data.error || 'Failed to save settings', false);
        }
    } catch (err) {
        console.error('Save error:', err);
        showToast('Error communicating with settings server', false);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalText;
        }
        updateDirtyState();
    }
}

async function resetCurrentSection() {
    if (!confirm(`Are you sure you want to reset all settings in '${currentSection.toUpperCase()}' to defaults?`)) {
        return;
    }

    const resetBtn = document.getElementById('btnResetSection');
    if (resetBtn) resetBtn.disabled = true;

    try {
        const res = await fetch('/api/settings/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ section: currentSection })
        });

        const data = await res.json();
        if (data.status === 'success') {
            const errors = Array.isArray(data.apply_errors) ? data.apply_errors : [];
            const restart = Array.isArray(data.requires_restart) ? data.requires_restart : [];
            const details = [data.message || `Reset ${currentSection} to defaults`];
            if (errors.length) details.push(`Runtime apply errors: ${errors.join('; ')}`);
            if (restart.length) details.push(`Restart required for: ${restart.join(', ')}`);
            showToast(details.join(' '), errors.length === 0);
            await loadSettings();
            await loadSettingsRuntimeStatus();
        } else {
            showToast(data.error || 'Failed to reset section', false);
        }
    } catch (err) {
        console.error('Reset error:', err);
        showToast('Error resetting section settings', false);
    } finally {
        if (resetBtn) resetBtn.disabled = false;
    }
}

function showToast(message, isSuccess = true) {
    const toast = document.getElementById('settingsToast');
    const msg = document.getElementById('toastMessage');
    if (!toast || !msg) return;

    msg.innerText = message;
    const icon = toast.querySelector('.toast-icon');
    if (icon) {
        icon.innerText = isSuccess ? '✓' : '✕';
        icon.style.background = isSuccess ? '#10b981' : '#ef4444';
    }

    toast.style.display = 'flex';
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.style.display = 'none';
    }, 3500);
}

// Expose handlers globally for inline event attributes
window.switchSection = switchSection;
window.saveAllSettings = saveAllSettings;
window.resetCurrentSection = resetCurrentSection;
window.updateRangeDisplay = updateRangeDisplay;
window.syncSlider = syncSlider;
window.togglePasswordVisibility = togglePasswordVisibility;
