/**
 * JavaScript controller for Global Platform Settings.
 */

let currentSection = 'cv';
let currentSettings = {};

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
    loadSettings();
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

function populateForm(sections) {
    for (const [sectionKey, fields] of Object.entries(sections)) {
        for (const [fieldKey, fieldDef] of Object.entries(fields)) {
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

function collectFormData() {
    const payload = {
        cv: {
            coastal_buffer_pixels: parseInt(document.getElementById('input_cv_coastal_buffer_pixels').value, 10),
            morph_close_kernel: parseInt(document.getElementById('input_cv_morph_close_kernel').value, 10),
            dem_land_mask_enabled: document.getElementById('input_cv_dem_land_mask_enabled').checked,
            threshold: parseInt(document.getElementById('input_cv_threshold').value, 10),
            filter_type: document.getElementById('input_cv_filter_type').value,
            minimum_area: parseFloat(document.getElementById('input_cv_minimum_area').value),
            maximum_area: parseFloat(document.getElementById('input_cv_maximum_area').value),
            dilation_iterations: parseInt(document.getElementById('input_cv_dilation_iterations').value, 10),
            pixel_spacing_meters: parseFloat(document.getElementById('input_cv_pixel_spacing_meters').value),
        },
        imagery: {
            copernicus_username: document.getElementById('input_imagery_copernicus_username').value.trim(),
            copernicus_password: document.getElementById('input_imagery_copernicus_password').value,
            default_evalscript: document.getElementById('input_imagery_default_evalscript').value,
            search_window_days: parseInt(document.getElementById('input_imagery_search_window_days').value, 10),
            resolution_meters: parseFloat(document.getElementById('input_imagery_resolution_meters').value),
            max_image_size: parseInt(document.getElementById('input_imagery_max_image_size').value, 10),
        },
        scheduler: {
            n2yo_api_key: document.getElementById('input_scheduler_n2yo_api_key').value.trim(),
            auto_capture_default: document.getElementById('input_scheduler_auto_capture_default').checked,
            poll_interval_seconds: parseFloat(document.getElementById('input_scheduler_poll_interval_seconds').value),
            satellite_norad_ids: document.getElementById('input_scheduler_satellite_norad_ids').value.trim(),
        },
        map_ui: {
            default_lat: parseFloat(document.getElementById('input_map_ui_default_lat').value),
            default_lng: parseFloat(document.getElementById('input_map_ui_default_lng').value),
            default_zoom: parseInt(document.getElementById('input_map_ui_default_zoom').value, 10),
            sar_opacity: parseFloat(document.getElementById('input_map_ui_sar_opacity').value),
            color_cv_detection: document.getElementById('input_map_ui_color_cv_detection').value,
            color_obb_detection: document.getElementById('input_map_ui_color_obb_detection').value,
        },
        system: {
            port: parseInt(document.getElementById('input_system_port').value, 10),
            debug: document.getElementById('input_system_debug').checked,
            database_path: document.getElementById('input_system_database_path').value.trim(),
            output_root: document.getElementById('input_system_output_root').value.trim(),
            cache_root: document.getElementById('input_system_cache_root').value.trim(),
        }
    };
    return payload;
}

async function saveAllSettings() {
    const saveBtn = document.getElementById('btnSaveSettings');
    const originalText = saveBtn ? saveBtn.innerHTML : '';
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
            showToast('Settings updated successfully', true);
            await loadSettings();
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
            showToast(data.message || `Reset ${currentSection} to defaults`, true);
            await loadSettings();
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
