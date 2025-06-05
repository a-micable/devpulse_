// devpulse/web/js/store.js
// Reactive state store with subscriber notification.
// Hand-written — no Redux, no MobX, no Zustand.
// Supports middleware for logging and side effects.
// Slice-based: each feature area has its own slice of state.

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export class Store {
    /**
     * Global state container.
     *
     * Usage:
     *   const store = new Store({
     *     repos:    { list: [], loading: false },
     *     sessions: { list: [], active: null },
     *   });
     *
     *   // Subscribe to changes
     *   store.subscribe('repos', (state) => console.log(state));
     *
     *   // Update state
     *   store.set('repos', { list: [...repos], loading: false });
     *
     *   // Read state
     *   const { list } = store.get('repos');
     */

    constructor(initialState = {}, middleware = []) {
        this._state = this._deepClone(initialState);
        this._listeners = {};   // { sliceName: Set<fn> }
        this._globalListeners = new Set();
        this._middleware = middleware;
        this._history = [];   // For time-travel debugging
        this._maxHistory = 20;
    }

    // ------------------------------------------------------------------
    // Read
    // ------------------------------------------------------------------

    get(slice) {
        if (slice === undefined) return this._deepClone(this._state);
        return this._deepClone(this._state[slice]);
    }

    getAll() {
        return this._deepClone(this._state);
    }

    // ------------------------------------------------------------------
    // Write
    // ------------------------------------------------------------------

    set(slice, partial) {
        const prev = this._state[slice];
        const next = (typeof partial === 'object' && partial !== null &&
            !Array.isArray(partial))
            ? { ...(prev || {}), ...partial }
            : partial;

        // Run through middleware
        let finalNext = next;
        for (const mw of this._middleware) {
            try {
                finalNext = mw(slice, prev, finalNext) ?? finalNext;
            } catch (e) {
                console.error('Store middleware error:', e);
            }
        }

        // Record history
        if (this._history.length >= this._maxHistory) {
            this._history.shift();
        }
        this._history.push({
            slice,
            prev: this._deepClone(prev),
            next: this._deepClone(finalNext),
            ts: Date.now(),
        });

        this._state[slice] = finalNext;
        this._notify(slice, finalNext, prev);
    }

    // Merge multiple slices at once — only one notification per slice
    setMany(updates) {
        Object.entries(updates).forEach(([slice, value]) => {
            this.set(slice, value);
        });
    }

    // Update a nested field within a slice
    // e.g. store.update('repos', 'loading', true)
    update(slice, key, value) {
        const current = this._state[slice] || {};
        this.set(slice, { ...current, [key]: value });
    }

    // Reset a slice to a given value (or empty object)
    reset(slice, value = {}) {
        this.set(slice, value);
    }

    // ------------------------------------------------------------------
    // Subscribe
    // ------------------------------------------------------------------

    subscribe(slice, fn) {
        if (!this._listeners[slice]) {
            this._listeners[slice] = new Set();
        }
        this._listeners[slice].add(fn);

        // Return unsubscribe function
        return () => {
            if (this._listeners[slice]) {
                this._listeners[slice].delete(fn);
            }
        };
    }

    subscribeAll(fn) {
        this._globalListeners.add(fn);
        return () => this._globalListeners.delete(fn);
    }

    // ------------------------------------------------------------------
    // Derived state (computed values)
    // ------------------------------------------------------------------

    derive(slices, computeFn) {
        /**
         * Create a derived value that recomputes when any listed slice changes.
         * Returns a getter function.
         *
         * Usage:
         *   const totalCommits = store.derive(
         *     ['repos'],
         *     ({ repos }) => repos.list.reduce((s, r) => s + r.commit_count, 0)
         *   );
         *   totalCommits(); // returns computed value
         */
        let cached = null;
        let dirty = true;

        const recompute = () => {
            const inputs = {};
            slices.forEach(s => { inputs[s] = this.get(s); });
            cached = computeFn(inputs);
            dirty = false;
        };

        slices.forEach(slice => {
            this.subscribe(slice, () => { dirty = true; });
        });

        return () => {
            if (dirty) recompute();
            return cached;
        };
    }

    // ------------------------------------------------------------------
    // Internal
    // ------------------------------------------------------------------

    _notify(slice, next, prev) {
        if (this._listeners[slice]) {
            this._listeners[slice].forEach(fn => {
                try { fn(next, prev); }
                catch (e) { console.error(`Store subscriber error [${slice}]:`, e); }
            });
        }
        this._globalListeners.forEach(fn => {
            try { fn(slice, next, prev); }
            catch (e) { console.error('Store global subscriber error:', e); }
        });
    }

    _deepClone(obj) {
        if (obj === null || obj === undefined) return obj;
        if (typeof obj !== 'object') return obj;
        // Fast path for plain objects and arrays — JSON round-trip
        try {
            return JSON.parse(JSON.stringify(obj));
        } catch {
            return obj;
        }
    }

    // ------------------------------------------------------------------
    // Debug helpers
    // ------------------------------------------------------------------

    history() {
        return [...this._history];
    }

    snapshot() {
        return this._deepClone(this._state);
    }
}

// ---------------------------------------------------------------------------
// Logging middleware
// ---------------------------------------------------------------------------

export function loggingMiddleware(slice, prev, next) {
    if (typeof console !== 'undefined' && window.__DEVPULSE_DEBUG__) {
        console.groupCollapsed(`Store: ${slice}`);
        console.log('prev:', prev);
        console.log('next:', next);
        console.groupEnd();
    }
    return next;
}

// ---------------------------------------------------------------------------
// App store factory — creates the store with all initial slices
// ---------------------------------------------------------------------------

export function createAppStore() {
    const middleware = [];

    if (typeof window !== 'undefined' && window.__DEVPULSE_DEBUG__) {
        middleware.push(loggingMiddleware);
    }

    return new Store(
        {
            // Navigation
            nav: {
                currentPath: '/',
                title: 'Dashboard',
            },

            // Summary / dashboard
            summary: {
                loading: false,
                error: null,
                data: null,
                lastFetched: null,
            },

            // Repos
            repos: {
                loading: false,
                error: null,
                list: [],
                selected: null,
                selectedStats: null,
                selectedCommits: [],
                selectedAuthors: [],
                selectedHotspots: [],
                commitsTotal: 0,
                commitsOffset: 0,
                commitsLimit: 20,
            },

            // Sessions
            sessions: {
                loading: false,
                error: null,
                list: [],
                active: null,
                total: 0,
                offset: 0,
                limit: 20,
                dailyTotals: [],
            },

            // Metrics
            metrics: {
                loading: false,
                error: null,
                global: [],
                heatmap: [],
                leaderboard: [],
                streak: { current_streak: 0, longest_streak: 0 },
            },

            // Goals
            goals: {
                loading: false,
                error: null,
                list: [],
            },

            // UI state
            ui: {
                sidebarCollapsed: false,
                theme: 'dark',
                activeTab: 'commits',
                searchQuery: '',
                dateRange: '30',
            },

            // Auth
            auth: {
                token: null,
                authenticated: false,
            },
        },
        middleware,
    );
}

// ---------------------------------------------------------------------------
// Store-aware component mixin
// ---------------------------------------------------------------------------

export class StoreComponent {
    /**
     * Mixin for components that need store access.
     * Extend both Component and use this pattern:
     *
     *   class MyView extends Component {
     *     onMount() {
     *       this._unsub = this.props.store.subscribe('repos', (state) => {
     *         this.setState({ repos: state });
     *       });
     *     }
     *     onUnmount() {
     *       if (this._unsub) this._unsub();
     *     }
     *   }
     */

    initStore(store, slices = []) {
        this._store = store;
        this._storeUnsubs = [];

        slices.forEach(slice => {
            const unsub = store.subscribe(slice, (state) => {
                this.setState({ [slice]: state });
            });
            this._storeUnsubs.push(unsub);
            // Set initial state from store
            if (this.setState) {
                this.setState({ [slice]: store.get(slice) });
            }
        });
    }

    teardownStore() {
        (this._storeUnsubs || []).forEach(fn => fn());
        this._storeUnsubs = [];
    }
}