/**
 * Toast notifications, HTML escaping, and utility helpers.
 */

function showNotification(message, type = 'error') {
    const container = document.getElementById('notification-container');
    if (!container) return;
    const notif = document.createElement('div');
    notif.className = `notification ${type}`;
    notif.innerText = message;
    
    container.appendChild(notif);
    
    // Trigger reflow for animation
    void notif.offsetWidth;
    notif.classList.add('show');
    
    setTimeout(() => {
        notif.classList.remove('show');
        notif.addEventListener('transitionend', () => notif.remove());
    }, CONFIG.NOTIFICATION_DURATION_MS || 3000);
}

function escapeHtml(value) {
    const replacements = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(value ?? '').replace(/[&<>"']/g, character => replacements[character]);
}

function debounce(func, timeout = CONFIG.SEARCH_DEBOUNCE_MS || 400) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => { func.apply(this, args); }, timeout);
    };
}
