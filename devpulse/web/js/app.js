// Mini component framework core.
// Hand-written — no React, no Vue, no Svelte.
// Provides:
//   - Component base class with render/mount/unmount/update lifecycle
//   - Virtual DOM diffing for targeted DOM updates
//   - Global event bus
//   - Fetch wrapper with auth header injection
//   - Toast notification system
//   - Loading spinner
//   - App bootstrap

import { Router } from './router.js';
import { Store } from './store.js';

// ---------------------------------------------------------------------------
// Event bus — simple pub/sub for cross-component communication
// ---------------------------------------------------------------------------

class EventBus {
    constructor() {
        this._listeners = {};
    }

    on(event, fn) {
        if (!this._listeners[event]) {
            this._listeners[event] = [];
        }
        this._listeners[event].push(fn);
        return () => this.off(event, fn);
    }

    off(event, fn) {
        if (!this._listeners[event]) return;
        this._listeners[event] = this._listeners[event].filter(f => f !== fn);
    }

    emit(event, ...args) {
        (this._listeners[event] || []).forEach(fn => {
            try { fn(...args); }
            catch (e) { console.error(`EventBus error on "${event}":`, e); }
        });
    }

    once(event, fn) {
        const wrapper = (...args) => {
            fn(...args);
            this.off(event, wrapper);
        };
        this.on(event, wrapper);
    }
}

export const bus = new EventBus();

// ---------------------------------------------------------------------------
// Virtual DOM differ
// Compares two HTML strings rendered into temp containers
// and patches only changed nodes into the real DOM.
// Not a full vdom — works at the element attribute and text level.
// ---------------------------------------------------------------------------

function diffAndPatch(container, newHtml) {
    const temp = document.createElement('div');
    temp.innerHTML = newHtml;

    _patchChildren(container, temp);
}

function _patchChildren(real, virtual) {
    const realKids = Array.from(real.childNodes);
    const virtualKids = Array.from(virtual.childNodes);
    const maxLen = Math.max(realKids.length, virtualKids.length);

    for (let i = 0; i < maxLen; i++) {
        const r = realKids[i];
        const v = virtualKids[i];

        if (!r && v) {
            real.appendChild(v.cloneNode(true));
            continue;
        }
        if (r && !v) {
            real.removeChild(r);
            continue;
        }
        if (!r || !v) continue;

        // Different node type or tag — replace entirely
        if (r.nodeType !== v.nodeType ||
            (r.nodeType === 1 && r.tagName !== v.tagName)) {
            real.replaceChild(v.cloneNode(true), r);
            continue;
        }

        // Text node
        if (r.nodeType === 3) {
            if (r.textContent !== v.textContent) {
                r.textContent = v.textContent;
            }
            continue;
        }

        // Element node — patch attributes
        if (r.nodeType === 1) {
            _patchAttributes(r, v);
            // Recurse into children unless it's a leaf we should replace
            if (r.innerHTML !== v.innerHTML) {
                // If small leaf node, just set innerHTML
                if (v.childNodes.length === 0 ||
                    (v.childNodes.length === 1 &&
                        v.childNodes[0].nodeType === 3)) {
                    r.innerHTML = v.innerHTML;
                } else {
                    _patchChildren(r, v);
                }
            }
        }
    }
}

function _patchAttributes(real, virtual) {
    // Remove attributes not in virtual
    Array.from(real.attributes).forEach(attr => {
        if (!virtual.hasAttribute(attr.name)) {
            real.removeAttribute(attr.name);
        }
    });
    // Set/update attributes from virtual
    Array.from(virtual.attributes).forEach(attr => {
        if (real.getAttribute(attr.name) !== attr.value) {
            real.setAttribute(attr.name, attr.value);
        }
    });
}

// ---------------------------------------------------------------------------
// Component base class
// ---------------------------------------------------------------------------

export class Component {
    /**
     * Base class for all UI components.
     *
     * Subclasses must implement render() returning an HTML string.
     * Lifecycle: constructor → mount(el) → update(newProps) → unmount()
     *
     * Event listeners declared in events() are automatically
     * attached after mount and removed on unmount.
     *
     * Usage:
     *   class MyWidget extends Component {
     *     render() {
     *       return `<div class="widget">${this.props.title}</div>`;
     *     }
     *   }
     *   const w = new MyWidget({ title: "Hello" });
     *   w.mount(document.getElementById('root'));
     */

    constructor(props = {}) {
        this.props = props;
        this.state = {};
        this.el = null;       // The mounted DOM element
        this._mounted = false;
        this._listeners = [];       // [{el, event, fn}] for cleanup
        this._children = [];       // Child component instances
    }

    // Override in subclass — must return HTML string
    render() {
        return '<div></div>';
    }

    // Override to declare delegated event listeners
    // Returns array of {selector, event, handler}
    events() {
        return [];
    }

    // Lifecycle hooks — override in subclass
    onMount() { }
    onUpdate() { }
    onUnmount() { }

    setState(partial) {
        this.state = { ...this.state, ...partial };
        if (this._mounted) {
            this._patch();
        }
    }

    setProps(newProps) {
        this.props = { ...this.props, ...newProps };
        if (this._mounted) {
            this._patch();
            this.onUpdate();
        }
    }

    mount(container) {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) {
            console.error(`Component.mount: container not found`);
            return this;
        }

        container.innerHTML = this.render();
        this.el = container.firstElementChild || container;

        this._attachEvents(container);
        this._mounted = true;
        this.onMount();
        return this;
    }

    unmount() {
        if (!this._mounted) return;
        this._detachEvents();
        this._children.forEach(c => c.unmount());
        this._children = [];
        this.onUnmount();
        this._mounted = false;
        if (this.el && this.el.parentNode) {
            this.el.parentNode.innerHTML = '';
        }
    }

    _patch() {
        if (!this.el || !this.el.parentNode) return;
        const container = this.el.parentNode;
        const newHtml = this.render();
        diffAndPatch(container, newHtml);
        this.el = container.firstElementChild || container;
        this._detachEvents();
        this._attachEvents(container);
    }

    _attachEvents(container) {
        this.events().forEach(({ selector, event, handler }) => {
            const bound = handler.bind(this);
            if (selector === 'self' || !selector) {
                container.addEventListener(event, bound);
                this._listeners.push({ el: container, event, fn: bound });
            } else {
                // Event delegation
                const delegated = (e) => {
                    const target = e.target.closest(selector);
                    if (target && container.contains(target)) {
                        handler.call(this, e, target);
                    }
                };
                container.addEventListener(event, delegated);
                this._listeners.push({ el: container, event, fn: delegated });
            }
        });
    }

    _detachEvents() {
        this._listeners.forEach(({ el, event, fn }) => {
            el.removeEventListener(event, fn);
        });
        this._listeners = [];
    }

    // Helper: find element within this component's container
    $(selector) {
        if (!this.el) return null;
        const parent = this.el.parentNode || this.el;
        return parent.querySelector(selector);
    }

    $$(selector) {
        if (!this.el) return [];
        const parent = this.el.parentNode || this.el;
        return Array.from(parent.querySelectorAll(selector));
    }

    // Mount a child component into a selector within this component
    mountChild(ComponentClass, selector, props = {}) {
        const container = this.$(selector);
        if (!container) return null;
        const child = new ComponentClass(props);
        child.mount(container);
        this._children.push(child);
        return child;
    }
}

// ---------------------------------------------------------------------------
// Fetch wrapper
// ---------------------------------------------------------------------------

const _API_BASE = '';  // Same origin — server serves both API and frontend

let _authToken = null;

export function setAuthToken(token) {
    _authToken = token;
    if (token) {
        localStorage.setItem('devpulse_token', token);
    } else {
        localStorage.removeItem('devpulse_token');
    }
}

export function loadAuthToken() {
    _authToken = localStorage.getItem('devpulse_token') || null;
    return _authToken;
}

export async function api(path, options = {}) {
    const url = _API_BASE + path;

    const headers = {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
    };

    if (_authToken) {
        headers['Authorization'] = `Bearer ${_authToken}`;
    }

    const config = {
        method: options.method || 'GET',
        headers,
    };

    if (options.body !== undefined) {
        config.body = typeof options.body === 'string'
            ? options.body
            : JSON.stringify(options.body);
    }

    let response;
    try {
        response = await fetch(url, config);
    } catch (err) {
        throw new ApiError(0, 'Network error', err.message);
    }

    let data;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        try {
            data = await response.json();
        } catch {
            data = null;
        }
    } else {
        data = await response.text();
    }

    if (!response.ok) {
        const message = (data && data.error) || response.statusText;
        throw new ApiError(response.status, message, data);
    }

    return data;
}

export class ApiError extends Error {
    constructor(status, message, detail = null) {
        super(message);
        this.status = status;
        this.detail = detail;
        this.name = 'ApiError';
    }
}

// Convenience wrappers
export const get = (path, params = {}) => {
    const qs = Object.keys(params).length
        ? '?' + new URLSearchParams(params).toString()
        : '';
    return api(path + qs);
};
export const post = (path, body) => api(path, { method: 'POST', body });
export const put = (path, body) => api(path, { method: 'PUT', body });
export const patch = (path, body) => api(path, { method: 'PATCH', body });
export const del = (path) => api(path, { method: 'DELETE' });

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------

const _TOAST_DURATION_MS = 3500;

export const toast = {
    _container: null,

    _getContainer() {
        if (!this._container) {
            this._container = document.getElementById('toast-container');
        }
        return this._container;
    },

    _show(message, type = 'info') {
        const container = this._getContainer();
        if (!container) return;

        const el = document.createElement('div');
        el.className = `toast toast--${type}`;

        const icons = {
            success: '✓',
            error: '✗',
            warning: '!',
            info: 'i',
        };

        el.innerHTML = `
      <span class="toast__icon">${icons[type] || 'i'}</span>
      <span class="toast__message">${_escapeHtml(message)}</span>
      <button class="toast__close" aria-label="Close">×</button>
    `;

        const closeBtn = el.querySelector('.toast__close');
        closeBtn.addEventListener('click', () => this._remove(el));

        container.appendChild(el);

        // Trigger animation
        requestAnimationFrame(() => el.classList.add('toast--visible'));

        // Auto-remove
        setTimeout(() => this._remove(el), _TOAST_DURATION_MS);
    },

    _remove(el) {
        el.classList.remove('toast--visible');
        el.classList.add('toast--hiding');
        setTimeout(() => {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 300);
    },

    success(msg) { this._show(msg, 'success'); },
    error(msg) { this._show(msg, 'error'); },
    warning(msg) { this._show(msg, 'warning'); },
    info(msg) { this._show(msg, 'info'); },
};

// ---------------------------------------------------------------------------
// Loading spinner
// ---------------------------------------------------------------------------

export const spinner = {
    _el: null,
    _count: 0,

    show() {
        this._count++;
        if (!this._el) {
            this._el = document.createElement('div');
            this._el.id = 'global-spinner';
            this._el.innerHTML = `
        <div class="spinner__overlay">
          <div class="spinner__ring">
            <div></div><div></div><div></div><div></div>
          </div>
        </div>
      `;
            document.body.appendChild(this._el);
        }
        this._el.style.display = 'flex';
    },

    hide() {
        this._count = Math.max(0, this._count - 1);
        if (this._count === 0 && this._el) {
            this._el.style.display = 'none';
        }
    },

    async wrap(promise) {
        this.show();
        try {
            return await promise;
        } finally {
            this.hide();
        }
    },
};

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

export function _escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function debounce(fn, ms = 300) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), ms);
    };
}

export function throttle(fn, ms = 100) {
    let last = 0;
    return function (...args) {
        const now = Date.now();
        if (now - last >= ms) {
            last = now;
            fn.apply(this, args);
        }
    };
}

export function formatDuration(seconds) {
    if (!seconds || seconds <= 0) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const parts = [];
    if (h) parts.push(`${h}h`);
    if (m) parts.push(`${m}m`);
    if (s && !h) parts.push(`${s}s`);
    return parts.join(' ') || '0s';
}

export function formatDate(isoStr, includeTime = false) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (isNaN(d)) return isoStr;
    const opts = { month: 'short', day: 'numeric', year: 'numeric' };
    if (includeTime) {
        opts.hour = '2-digit';
        opts.minute = '2-digit';
    }
    return d.toLocaleDateString('en-US', opts);
}

export function formatRelative(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);

    if (diff < 30) return 'just now';
    if (diff < 90) return 'a minute ago';
    if (diff < 3600) return `${Math.floor(diff / 60)} minutes ago`;
    if (diff < 7200) return 'an hour ago';
    if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`;
    if (diff < 172800) return 'yesterday';
    if (diff < 604800) return `${Math.floor(diff / 86400)} days ago`;
    if (diff < 2592000) return `${Math.floor(diff / 604800)} weeks ago`;
    return formatDate(isoStr);
}

export function formatNumber(n) {
    if (n === null || n === undefined) return '0';
    return Number(n).toLocaleString('en-US');
}

export function clamp(val, min, max) {
    return Math.max(min, Math.min(max, val));
}

// ---------------------------------------------------------------------------
// App bootstrap
// ---------------------------------------------------------------------------

export class App {
    constructor({ router, store }) {
        this.router = router;
        this.store = store;
        this._currentView = null;
    }

    async init() {
        // Load auth token from storage
        loadAuthToken();

        // Wire router to view rendering
        this.router.onChange((route) => {
            this._renderView(route);
        });

        // Listen for API errors globally
        bus.on('api:error', ({ status, message }) => {
            if (status === 401) {
                toast.warning('Session expired. Please re-authenticate.');
            } else if (status >= 500) {
                toast.error(`Server error: ${message}`);
            }
        });

        // Start routing
        this.router.start();
    }

    _renderView(route) {
        const viewRoot = document.getElementById('view-root');
        if (!viewRoot) return;

        // Unmount previous view
        if (this._currentView && typeof this._currentView.unmount === 'function') {
            this._currentView.unmount();
        }

        viewRoot.innerHTML = '';

        const ViewClass = route.component;
        if (!ViewClass) {
            viewRoot.innerHTML = `
        <div class="view-error">
          <h2>404</h2>
          <p>Page not found: <code>${_escapeHtml(route.path)}</code></p>
        </div>
      `;
            return;
        }

        this._currentView = new ViewClass({
            params: route.params,
            query: route.query,
            store: this.store,
        });
        this._currentView.mount(viewRoot);
    }
}