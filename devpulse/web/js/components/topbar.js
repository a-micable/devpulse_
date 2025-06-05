// devpulse/web/js/components/topbar.js
// Top navigation bar component.
// Shows current page title, breadcrumb, search input, streak badge.

import { Component, _escapeHtml, get, toast } from '../app.js';

const ROUTE_TITLES = {
    '/': 'Dashboard',
    '/activity': 'Activity',
    '/repos': 'Repositories',
    '/sessions': 'Sessions',
    '/reports': 'Reports',
    '/goals': 'Goals',
};

export class Topbar extends Component {
    /**
     * Props:
     *   router: Router instance
     *   store:  Store instance
     */

    constructor(props = {}) {
        super(props);
        this.state = {
            title: 'Dashboard',
            searchQuery: '',
            streak: { current_streak: 0, longest_streak: 0 },
            searchFocused: false,
        };
        this._routeUnsub = null;
        this._storeUnsub = null;
        this._searchTimer = null;
    }

    onMount() {
        if (this.props.router) {
            this._routeUnsub = this.props.router.onChange(route => {
                const path = route.path;
                const base = '/' + path.split('/')[1];
                const title = ROUTE_TITLES[base] || ROUTE_TITLES[path] || 'devpulse';
                this.setState({ title });
            });
            const cur = this.props.router.current();
            if (cur) {
                const base = '/' + cur.path.split('/')[1];
                this.setState({
                    title: ROUTE_TITLES[base] || ROUTE_TITLES[cur.path] || 'devpulse'
                });
            }
        }

        if (this.props.store) {
            this._storeUnsub = this.props.store.subscribe(
                'metrics', (metrics) => {
                    if (metrics.streak) {
                        this.setState({ streak: metrics.streak });
                    }
                }
            );
            const metrics = this.props.store.get('metrics');
            if (metrics?.streak) {
                this.setState({ streak: metrics.streak });
            }
        }

        this._loadStreak();
    }

    onUnmount() {
        if (this._routeUnsub) this._routeUnsub();
        if (this._storeUnsub) this._storeUnsub();
        if (this._searchTimer) clearTimeout(this._searchTimer);
    }

    async _loadStreak() {
        try {
            const res = await get('/api/activity/streak');
            if (res?.data) {
                this.setState({ streak: res.data });
                if (this.props.store) {
                    this.props.store.update('metrics', 'streak', res.data);
                }
            }
        } catch {
            // Streak is non-critical — silently ignore
        }
    }

    events() {
        return [
            {
                selector: '.topbar__search-input',
                event: 'input',
                handler(e, target) {
                    const q = target.value;
                    this.setState({ searchQuery: q });
                    clearTimeout(this._searchTimer);
                    this._searchTimer = setTimeout(() => {
                        if (this.props.store) {
                            this.props.store.update('ui', 'searchQuery', q);
                        }
                    }, 300);
                },
            },
            {
                selector: '.topbar__search-input',
                event: 'focus',
                handler() {
                    this.setState({ searchFocused: true });
                },
            },
            {
                selector: '.topbar__search-input',
                event: 'blur',
                handler() {
                    this.setState({ searchFocused: false });
                },
            },
            {
                selector: '.topbar__search-clear',
                event: 'click',
                handler() {
                    this.setState({ searchQuery: '' });
                    if (this.props.store) {
                        this.props.store.update('ui', 'searchQuery', '');
                    }
                    const input = this.$('.topbar__search-input');
                    if (input) { input.value = ''; input.focus(); }
                },
            },
            {
                selector: '.topbar__theme-toggle',
                event: 'click',
                handler() {
                    const body = document.body;
                    const isDark = body.classList.toggle('theme--light');
                    if (this.props.store) {
                        this.props.store.update(
                            'ui', 'theme', isDark ? 'light' : 'dark'
                        );
                    }
                },
            },
        ];
    }

    _renderStreak() {
        const { current_streak, longest_streak } = this.state.streak;
        if (!current_streak) return '';

        const flame = current_streak >= 7 ? '🔥'
            : current_streak >= 3 ? '✦'
                : '◆';

        return `
      <div class="topbar__streak" title="Longest: ${longest_streak} days">
        <span class="topbar__streak-icon">${flame}</span>
        <span class="topbar__streak-count">${current_streak}</span>
        <span class="topbar__streak-label">day streak</span>
      </div>`;
    }

    _renderSearch() {
        const focused = this.state.searchFocused;
        const hasVal = this.state.searchQuery.length > 0;
        const cls = focused ? 'topbar__search topbar__search--focused' : 'topbar__search';

        return `
      <div class="${cls}">
        <span class="topbar__search-icon">⌕</span>
        <input
          type="text"
          class="topbar__search-input"
          placeholder="Search repos, sessions…"
          value="${_escapeHtml(this.state.searchQuery)}"
          aria-label="Search"
        />
        ${hasVal
                ? `<button class="topbar__search-clear" aria-label="Clear">×</button>`
                : ''}
      </div>`;
    }

    render() {
        return `
      <header class="topbar">
        <div class="topbar__left">
          <h1 class="topbar__title">
            ${_escapeHtml(this.state.title)}
          </h1>
        </div>

        <div class="topbar__center">
          ${this._renderSearch()}
        </div>

        <div class="topbar__right">
          ${this._renderStreak()}
          <button
            class="topbar__theme-toggle"
            aria-label="Toggle theme"
            title="Toggle light/dark mode"
          >◑</button>
        </div>
      </header>`;
    }
}