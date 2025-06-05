// devpulse/web/js/components/sidebar.js
// Sidebar navigation component.
// Renders nav links, active state, collapse toggle.
// Integrates with Router for active route detection.

import { Component, _escapeHtml, formatRelative } from '../app.js';

const NAV_ITEMS = [
    {
        section: 'Overview',
        items: [
            { path: '/', icon: '◈', label: 'Dashboard' },
            { path: '/activity', icon: '⬡', label: 'Activity' },
        ],
    },
    {
        section: 'Work',
        items: [
            { path: '/repos', icon: '⬢', label: 'Repos' },
            { path: '/sessions', icon: '◉', label: 'Sessions' },
        ],
    },
    {
        section: 'Insights',
        items: [
            { path: '/reports', icon: '▤', label: 'Reports' },
            { path: '/goals', icon: '◎', label: 'Goals' },
        ],
    },
];

export class Sidebar extends Component {
    /**
     * Props:
     *   router:    Router instance
     *   store:     Store instance
     *   collapsed: bool (initial)
     */

    constructor(props = {}) {
        super(props);
        this.state = {
            currentPath: props.router?.current()?.path || '/',
            collapsed: props.collapsed || false,
            activeSession: null,
        };
        this._routeUnsub = null;
        this._storeUnsub = null;
    }

    onMount() {
        // Track route changes for active highlighting
        if (this.props.router) {
            this._routeUnsub = this.props.router.onChange(route => {
                this.setState({ currentPath: route.path });
            });
        }

        // Track active session from store
        if (this.props.store) {
            this._storeUnsub = this.props.store.subscribe(
                'sessions', (sessions) => {
                    this.setState({ activeSession: sessions.active });
                }
            );
            const sessions = this.props.store.get('sessions');
            if (sessions?.active) {
                this.setState({ activeSession: sessions.active });
            }
        }
    }

    onUnmount() {
        if (this._routeUnsub) this._routeUnsub();
        if (this._storeUnsub) this._storeUnsub();
    }

    events() {
        return [
            {
                selector: '.sidebar__toggle',
                event: 'click',
                handler() {
                    this.setState({ collapsed: !this.state.collapsed });
                    if (this.props.store) {
                        this.props.store.update(
                            'ui', 'sidebarCollapsed', !this.state.collapsed
                        );
                    }
                },
            },
            {
                selector: '.sidebar__nav-link',
                event: 'click',
                handler(e, target) {
                    e.preventDefault();
                    const path = target.dataset.path;
                    if (path && this.props.router) {
                        this.props.router.navigate(path);
                    }
                },
            },
        ];
    }

    _isActive(path) {
        const current = this.state.currentPath;
        if (path === '/') return current === '/';
        return current === path || current.startsWith(path + '/');
    }

    _renderActiveSessionBadge() {
        const session = this.state.activeSession;
        if (!session) return '';
        if (this.state.collapsed) {
            return `<div class="sidebar__session-dot" title="Session active"></div>`;
        }
        return `
      <div class="sidebar__session-badge">
        <span class="sidebar__session-dot"></span>
        <span class="sidebar__session-text">
          Session active
          <small>${formatRelative(session.started_at)}</small>
        </span>
      </div>`;
    }

    _renderNavSection(section) {
        if (this.state.collapsed) {
            const icons = section.items.map(item => {
                const active = this._isActive(item.path) ? ' sidebar__nav-link--active' : '';
                return `
          
            href="#${item.path}"
            class="sidebar__nav-link sidebar__nav-link--icon${active}"
            data-path="${item.path}"
            title="${_escapeHtml(item.label)}"
          >
            <span class="sidebar__nav-icon">${item.icon}</span>
          </a>`;
            }).join('');
            return `<div class="sidebar__section">${icons}</div>`;
        }

        const links = section.items.map(item => {
            const active = this._isActive(item.path) ? ' sidebar__nav-link--active' : '';
            return `
        
          href="#${item.path}"
          class="sidebar__nav-link${active}"
          data-path="${item.path}"
        >
          <span class="sidebar__nav-icon">${item.icon}</span>
          <span class="sidebar__nav-label">
            ${_escapeHtml(item.label)}
          </span>
        </a>`;
        }).join('');

        return `
      <div class="sidebar__section">
        <div class="sidebar__section-title">
          ${_escapeHtml(section.section)}
        </div>
        ${links}
      </div>`;
    }

    render() {
        const collapsed = this.state.collapsed;
        const cls = collapsed ? 'sidebar sidebar--collapsed' : 'sidebar';

        const sections = NAV_ITEMS.map(s =>
            this._renderNavSection(s)
        ).join('');

        const toggleIcon = collapsed ? '›' : '‹';
        const logoText = collapsed ? 'dp' : 'devpulse';

        return `
      <aside class="${cls}">
        <div class="sidebar__header">
          <a href="#/" class="sidebar__logo">
            <span class="sidebar__logo-icon">◈</span>
            <span class="sidebar__logo-text">${logoText}</span>
          </a>
          <button
            class="sidebar__toggle"
            aria-label="${collapsed ? 'Expand' : 'Collapse'} sidebar"
          >${toggleIcon}</button>
        </div>

        <nav class="sidebar__nav">
          ${sections}
        </nav>

        <div class="sidebar__footer">
          ${this._renderActiveSessionBadge()}
        </div>
      </aside>`;
    }
}