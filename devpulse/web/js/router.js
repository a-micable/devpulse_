// devpulse/web/js/router.js
// Hash-based client-side router.
// Uses window.location.hash for navigation — no server-side routing needed.
// Supports path parameters: #/repos/:id
// Supports navigation history via a manual stack.
// No dependency on History API (works in file:// too).

export class Router {
    /**
     * Client-side hash router.
     *
     * Usage:
     *   const router = new Router();
     *   router.add('/',            DashboardView);
     *   router.add('/repos',       ReposView);
     *   router.add('/repos/:id',   RepoDetailView);
     *   router.onChange(route => console.log(route));
     *   router.start();
     */

    constructor() {
        this._routes = [];
        this._handlers = [];
        this._history = [];
        this._current = null;
        this._started = false;
        this._notFound = null;

        this._onHashChange = this._onHashChange.bind(this);
    }

    // ------------------------------------------------------------------
    // Route registration
    // ------------------------------------------------------------------

    add(pattern, component, meta = {}) {
        this._routes.push({
            pattern,
            component,
            meta,
            regex: this._patternToRegex(pattern),
            params: this._extractParamNames(pattern),
        });
        return this;
    }

    notFound(component) {
        this._notFound = component;
        return this;
    }

    // ------------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------------

    start() {
        if (this._started) return;
        this._started = true;
        window.addEventListener('hashchange', this._onHashChange);
        // Handle initial route
        this._resolve(this._currentHash());
    }

    stop() {
        window.removeEventListener('hashchange', this._onHashChange);
        this._started = false;
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    navigate(path, replace = false) {
        const hash = '#' + path;
        if (replace) {
            window.location.replace(hash);
        } else {
            window.location.hash = hash;
        }
    }

    back() {
        if (this._history.length > 1) {
            this._history.pop();
            const prev = this._history[this._history.length - 1];
            this.navigate(prev, true);
        } else {
            this.navigate('/');
        }
    }

    // Programmatic navigation without hash change
    // (used internally when replacing current route)
    replace(path) {
        this.navigate(path, true);
    }

    // ------------------------------------------------------------------
    // Change listener
    // ------------------------------------------------------------------

    onChange(fn) {
        this._handlers.push(fn);
        return () => {
            this._handlers = this._handlers.filter(h => h !== fn);
        };
    }

    // ------------------------------------------------------------------
    // URL building
    // ------------------------------------------------------------------

    urlFor(name, params = {}) {
        const route = this._routes.find(r => r.meta.name === name);
        if (!route) return '#/';
        let path = route.pattern;
        Object.entries(params).forEach(([k, v]) => {
            path = path.replace(`:${k}`, encodeURIComponent(v));
        });
        return '#' + path;
    }

    // ------------------------------------------------------------------
    // Current state
    // ------------------------------------------------------------------

    current() {
        return this._current;
    }

    isActive(path) {
        if (!this._current) return false;
        return this._current.path === path ||
            this._current.path.startsWith(path + '/');
    }

    // ------------------------------------------------------------------
    // Internal
    // ------------------------------------------------------------------

    _currentHash() {
        const hash = window.location.hash;
        if (!hash || hash === '#') return '/';
        return hash.slice(1).split('?')[0] || '/';
    }

    _currentQuery() {
        const hash = window.location.hash;
        const qIndex = hash.indexOf('?');
        if (qIndex === -1) return {};
        const qs = hash.slice(qIndex + 1);
        const result = {};
        new URLSearchParams(qs).forEach((v, k) => { result[k] = v; });
        return result;
    }

    _onHashChange() {
        this._resolve(this._currentHash());
    }

    _resolve(path) {
        // Try each registered route
        for (const route of this._routes) {
            const match = route.regex.exec(path);
            if (!match) continue;

            // Extract named params
            const params = {};
            route.params.forEach((name, i) => {
                params[name] = decodeURIComponent(match[i + 1] || '');
            });

            const resolved = {
                path,
                pattern: route.pattern,
                component: route.component,
                params,
                query: this._currentQuery(),
                meta: route.meta,
            };

            this._current = resolved;
            this._history.push(path);

            // Cap history stack at 50
            if (this._history.length > 50) {
                this._history.shift();
            }

            this._emit(resolved);
            return;
        }

        // No match — 404
        const notFound = {
            path,
            pattern: null,
            component: this._notFound,
            params: {},
            query: this._currentQuery(),
            meta: { name: '404' },
        };
        this._current = notFound;
        this._emit(notFound);
    }

    _emit(route) {
        this._handlers.forEach(fn => {
            try { fn(route); }
            catch (e) { console.error('Router onChange error:', e); }
        });
    }

    _patternToRegex(pattern) {
        // Escape regex special chars except :param segments
        const escaped = pattern
            .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            .replace(/:\\([a-zA-Z_][a-zA-Z0-9_]*)/g, '([^/]+)');
        return new RegExp(`^${escaped}$`);
    }

    _extractParamNames(pattern) {
        const names = [];
        const re = /:([a-zA-Z_][a-zA-Z0-9_]*)/g;
        let m;
        while ((m = re.exec(pattern)) !== null) {
            names.push(m[1]);
        }
        return names;
    }
}

// ---------------------------------------------------------------------------
// Link helper — renders an anchor that triggers router navigation
// ---------------------------------------------------------------------------

export function routerLink(href, text, className = '') {
    const cls = className ? ` class="${className}"` : '';
    return `<a href="#${href}"${cls}>${text}</a>`;
}