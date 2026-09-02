/**
 * Toast notifications, HTML escaping, and utility helpers.
 */

function showNotification(message, type = 'info', options = {}) {
    if (typeof type === 'object' && type !== null) {
        options = type;
        type = options.type || 'info';
    }

    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.className = 'notification-container';
        document.body.appendChild(container);
    }

    const notif = document.createElement('div');
    notif.className = `notification ${type}`;

    const autoClose = options.autoClose !== undefined ? options.autoClose : (options.persistent ? false : true);
    const duration = options.duration || (typeof CONFIG !== 'undefined' && CONFIG.NOTIFICATION_DURATION_MS) || 4000;
    const showCloseBtn = options.closable !== false;
    const title = options.title || '';
    const showAckBtn = options.showAckButton === true;
    const ackText = options.ackText || 'Dismiss';

    const buildContent = (msg, msgTitle, hasAckBtn, btnText) => {
        let html = '<div class="notification-content">';
        if (msgTitle) {
            html += `<div class="notification-title">${escapeHtml(msgTitle)}</div>`;
        }
        html += `<div class="notification-message">${escapeHtml(msg)}</div>`;
        if (hasAckBtn) {
            html += `<div class="notification-actions"><button type="button" class="notification-ack-btn">${escapeHtml(btnText)}</button></div>`;
        }
        html += '</div>';
        if (showCloseBtn) {
            html += '<button type="button" class="notification-close" title="Close Notification" aria-label="Close">&times;</button>';
        }
        return html;
    };

    notif.innerHTML = buildContent(message, title, showAckBtn, ackText);

    let timeoutId = null;

    const close = () => {
        if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
        }
        if (!notif.parentNode) return;
        notif.classList.remove('show');
        setTimeout(() => {
            if (notif.parentNode) notif.remove();
        }, 260);
        if (typeof options.onClose === 'function') {
            options.onClose();
        }
    };

    const attachHandlers = () => {
        if (showCloseBtn) {
            const closeBtn = notif.querySelector('.notification-close');
            if (closeBtn) closeBtn.onclick = (e) => {
                e.stopPropagation();
                close();
            };
        }
        const ackBtn = notif.querySelector('.notification-ack-btn');
        if (ackBtn) {
            ackBtn.onclick = (e) => {
                e.stopPropagation();
                close();
            };
        }
    };

    attachHandlers();
    container.appendChild(notif);

    // Trigger reflow for animation
    void notif.offsetWidth;
    notif.classList.add('show');

    if (autoClose) {
        timeoutId = setTimeout(() => {
            close();
        }, duration);
    }

    return {
        element: notif,
        close: close,
        update: (newMessage, newType, newOptions = {}) => {
            if (timeoutId) {
                clearTimeout(timeoutId);
                timeoutId = null;
            }
            if (newType) {
                notif.className = `notification ${newType} show`;
            }
            const updatedTitle = newOptions.title !== undefined ? newOptions.title : title;
            const updatedShowAck = newOptions.showAckButton !== undefined ? newOptions.showAckButton : showAckBtn;
            const updatedAckText = newOptions.ackText || ackText;
            const updatedAutoClose = newOptions.autoClose !== undefined ? newOptions.autoClose : autoClose;

            notif.innerHTML = buildContent(newMessage, updatedTitle, updatedShowAck, updatedAckText);
            attachHandlers();

            if (updatedAutoClose) {
                timeoutId = setTimeout(() => {
                    close();
                }, newOptions.duration || duration);
            }
        }
    };
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
