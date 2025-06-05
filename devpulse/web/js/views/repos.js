// devpulse/web/js/views/repos.js
// Repos list view and per-repo detail view (toggled via route param).
// List: shows all tracked repos with summary stats.
// Detail: shows commit history, author breakdown, hotspots, language chart.

import {
    Component, get, post, del, toast,
    formatDate, formatRelative, formatNumber, _escapeHtml,
} from '../app.js';
import {
    renderBarChart, renderHorizontalBarChart,
    renderDonutChart, ChartContainer,
} from '../components/chart.js';
import { DataTable } from '../components/table.js';

// ---------------------------------------------------------------------------
// ReposListView
// ---------------------------------------------------------------------------

export class ReposListView extends Component {
    constructor(props = {}) {
        super(props);
        this.state = {
            loading: true,
            error: null,
            repos: [],
            adding: false,
            addPath: '',
        };
    }

    onMount() { this._load(); }

    async _load() {
        this.setState({ loading: true, error: null });
        try {
            const res = await get('/api/repos');
            this.setState({ loading: false, repos: res?.data || [] });
        } catch (err) {
            this.setState({ loading: false, error: err.message });
            toast.error('Failed to load repos.');
        }
    }

    async _addRepo() {
        const path = this.state.addPath.trim();
        if (!path) { toast.warning('Enter a repo path.'); return; }
        try {
            await post('/api/repos', { path });
            toast.success('Repo added.');
            this.setState({ adding: false, addPath: '' });
            this._load();
        } catch (err) {
            toast.error(err.message || 'Failed to add repo.');
        }
    }

    async _deleteRepo(id) {
        if (!confirm('Remove this repo from devpulse?')) return;
        try {
            await del(`/api/repos/${id}`);
            toast.success('Repo removed.');
            this._load();
        } catch (err) {
            toast.error('Failed to remove repo.');
        }
    }

    events() {
        return [
            {
                selector: '.repos__add-btn',
                event: 'click',
                handler() { this.setState({ adding: !this.state.adding }); },
            },
            {
                selector: '.repos__add-path',
                event: 'input',
                handler(e, target) { this.setState({ addPath: target.value }); },
            },
            {
                selector: '.repos__add-submit',
                event: 'click',
                handler() { this._addRepo(); },
            },
            {
                selector: '.repos__delete-btn',
                event: 'click',
                handler(e, target) {
                    e.stopPropagation();
                    const id = parseInt(target.dataset.id, 10);
                    if (!isNaN(id)) this._deleteRepo(id);
                },
            },
            {
                selector: '.repo-card',
                event: 'click',
                handler(e, target) {
                    const id = target.dataset.id;
                    if (id && this.props.router) {
                        this.props.router.navigate(`/repos/${id}`);
                    }
                },
            },
        ];
    }

    _renderAddForm() {
        if (!this.state.adding) return '';
        return `
      <div class="repos__add-form">
        <input
          type="text"
          class="input repos__add-path"
          placeholder="/home/user/projects/myapp"
          value="${_escapeHtml(this.state.addPath)}"
        />
        <button class="btn btn--primary repos__add-submit">Add</button>
      </div>`;
    }

    _renderRepo(repo) {
        const loc = formatNumber(repo.total_loc || 0);
        const cmts = formatNumber(repo.commit_count || 0);
        const lang = _escapeHtml(repo.primary_language || '');
        return `
      <div class="repo-card" data-id="${repo.id}" role="button" tabindex="0">
        <div class="repo-card__header">
          <span class="repo-card__name">${_escapeHtml(repo.name || repo.path)}</span>
          <button
            class="btn btn--ghost btn--xs repos__delete-btn"
            data-id="${repo.id}"
            title="Remove repo"
          >✕</button>
        </div>
        <div class="repo-card__path">${_escapeHtml(repo.path || '')}</div>
        <div class="repo-card__stats">
          <span title="Lines of code">⬡ ${loc} LOC</span>
          <span title="Commits">◆ ${cmts} commits</span>
          ${lang ? `<span>${lang}</span>` : ''}
        </div>
        <div class="repo-card__meta">
          ${repo.last_commit_date
                ? `Last commit: ${_escapeHtml(formatRelative(repo.last_commit_date))}`
                : 'No commits cached'}
        </div>
      </div>`;
    }

    render() {
        if (this.state.loading) {
            return `
        <div class="view view--repos">
          <div class="view__loading"><p>Loading repos…</p></div>
        </div>`;
        }

        const repos = this.state.repos;
        const cards = repos.length
            ? repos.map(r => this._renderRepo(r)).join('')
            : `<p class="empty-state">No repos tracked yet. Add one below.</p>`;

        return `
      <div class="view view--repos">
        <div class="view__header">
          <h1 class="view__title">Repositories</h1>
          <button class="btn btn--primary repos__add-btn">+ Add Repo</button>
        </div>
        ${this._renderAddForm()}
        <div class="repo-grid">${cards}</div>
      </div>`;
    }
}

// ---------------------------------------------------------------------------
// RepoDetailView
// ---------------------------------------------------------------------------

export class RepoDetailView extends Component {
    constructor(props = {}) {
        super(props);
        this.state = {
            loading: true,
            error: null,
            repo: null,
            stats: null,
            commits: [],
            authors: [],
            hotspots: [],
            activeTab: 'commits',
            commitsOffset: 0,
        };
        this._charts = [];
        this._table = null;
    }

    get _repoId() {
        return this.props.params?.id;
    }

    onMount() { this._load(); }

    onUnmount() {
        this._charts.forEach(c => c.unmount());
        this._charts = [];
        if (this._table) this._table.unmount();
    }

    async _load() {
        const id = this._repoId;
        if (!id) return;
        this.setState({ loading: true, error: null });
        try {
            const [repoRes, statsRes, commitsRes, authorsRes, hotspotsRes] = await Promise.all([
                get(`/api/repos/${id}`),
                get(`/api/repos/${id}/stats`),
                get(`/api/repos/${id}/commits`, { limit: 50, offset: 0 }),
                get(`/api/repos/${id}/authors`),
                get(`/api/repos/${id}/hotspots`, { limit: 15, order: 'complexity' }),
            ]);
            this.setState({
                loading: false,
                repo: repoRes?.data || null,
                stats: statsRes?.data || null,
                commits: commitsRes?.data || [],
                authors: authorsRes?.data || [],
                hotspots: hotspotsRes?.data || [],
            });
            this._mountCharts();
        } catch (err) {
            this.setState({ loading: false, error: err.message });
            toast.error('Failed to load repo details.');
        }
    }

    _mountCharts() {
        this._charts.forEach(c => c.unmount());
        this._charts = [];

        // Commits by day bar chart
        const barEl = this.$('.repo-detail__commits-chart');
        const commitsByDay = this.state.stats?.commits_by_day || [];
        if (barEl && commitsByDay.length) {
            const c = new ChartContainer(renderBarChart, {
                data: commitsByDay, valueKey: 'count', labelKey: 'date',
                title: 'Commits per day (30 days)',
            });
            c.mount(barEl);
            this._charts.push(c);
        }

        // Language donut
        const donutEl = this.$('.repo-detail__lang-chart');
        const langs = this.state.stats?.language_breakdown || [];
        if (donutEl && langs.length) {
            const c = new ChartContainer(renderDonutChart, {
                data: langs, valueKey: 'total_loc', labelKey: 'language',
                title: 'Language breakdown',
            });
            c.mount(donutEl);
            this._charts.push(c);
        }

        // Author horizontal bar
        const authorEl = this.$('.repo-detail__authors-chart');
        const authors = this.state.authors || [];
        if (authorEl && authors.length) {
            const c = new ChartContainer(renderHorizontalBarChart, {
                data: authors, valueKey: 'commit_count', labelKey: 'author',
                title: 'Commits by author',
            });
            c.mount(authorEl);
            this._charts.push(c);
        }

        // Commits table
        const tableEl = this.$('.repo-detail__commits-table');
        if (tableEl) {
            if (this._table) this._table.unmount();
            this._table = new DataTable({
                columns: [
                    {
                        key: 'hash', label: 'Hash', sortable: false,
                        render: v => `<code>${_escapeHtml(String(v || '').slice(0, 7))}</code>`
                    },
                    { key: 'author', label: 'Author', sortable: true },
                    {
                        key: 'committed_at', label: 'Date', sortable: true,
                        render: v => _escapeHtml(formatDate(v, true))
                    },
                    {
                        key: 'message', label: 'Message', sortable: false,
                        render: v => `<span class="commit-msg">${_escapeHtml(String(v || '').slice(0, 72))}</span>`
                    },
                    {
                        key: 'lines_added', label: '+', sortable: true,
                        render: v => `<span class="lines-added">+${formatNumber(v)}</span>`
                    },
                    {
                        key: 'lines_removed', label: '−', sortable: true,
                        render: v => `<span class="lines-removed">−${formatNumber(v)}</span>`
                    },
                ],
                rows: this.state.commits,
                pageSize: 20,
            });
            this._table.mount(tableEl);
        }
    }

    events() {
        return [
            {
                selector: '.repo-detail__tab',
                event: 'click',
                handler(e, target) {
                    const tab = target.dataset.tab;
                    if (tab) {
                        this.setState({ activeTab: tab });
                        this._mountCharts();
                    }
                },
            },
            {
                selector: '.repo-detail__back',
                event: 'click',
                handler() {
                    if (this.props.router) this.props.router.navigate('/repos');
                },
            },
        ];
    }

    _renderTabs() {
        const tabs = ['commits', 'authors', 'hotspots', 'languages'];
        return `
      <div class="tab-bar">
        ${tabs.map(t => `
          <button
            class="tab-bar__tab repo-detail__tab${this.state.activeTab === t ? ' tab-bar__tab--active' : ''}"
            data-tab="${t}"
          >${t.charAt(0).toUpperCase() + t.slice(1)}</button>`
        ).join('')}
      </div>`;
    }

    _renderTabContent() {
        const { activeTab, hotspots, authors } = this.state;
        if (activeTab === 'commits') {
            return `
        <div class="repo-detail__commits-chart"></div>
        <div class="repo-detail__commits-table"></div>`;
        }
        if (activeTab === 'authors') {
            return `<div class="repo-detail__authors-chart"></div>`;
        }
        if (activeTab === 'languages') {
            return `<div class="repo-detail__lang-chart"></div>`;
        }
        if (activeTab === 'hotspots') {
            if (!hotspots.length) return `<p class="empty-state">No hotspot data. Run an analysis first.</p>`;
            const rows = hotspots.map(h => `
        <tr>
          <td><code>${_escapeHtml(h.file_path?.split('/').pop() || '')}</code></td>
          <td>${_escapeHtml(h.language || '')}</td>
          <td>${formatNumber(h.loc)}</td>
          <td>${(h.complexity || 0).toFixed(1)}</td>
          <td>${(h.churn_score || 0).toFixed(3)}</td>
        </tr>`).join('');
            return `
        <table class="dt">
          <thead><tr>
            <th>File</th><th>Lang</th><th>LOC</th>
            <th>Complexity</th><th>Churn</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
        }
        return '';
    }

    render() {
        if (this.state.loading) {
            return `<div class="view view--repo-detail"><div class="view__loading"><p>Loading…</p></div></div>`;
        }
        if (this.state.error) {
            return `<div class="view view--repo-detail"><div class="view__error"><p>${_escapeHtml(this.state.error)}</p></div></div>`;
        }

        const repo = this.state.repo || {};
        const stats = this.state.stats || {};

        return `
      <div class="view view--repo-detail">
        <div class="view__header">
          <button class="btn btn--ghost repo-detail__back">← Repos</button>
          <h1 class="view__title">${_escapeHtml(repo.name || repo.path || '')}</h1>
        </div>

        <div class="stat-cards stat-cards--sm">
          <div class="stat-card"><div class="stat-card__value">${formatNumber(stats.commit_count)}</div><div class="stat-card__label">Commits</div></div>
          <div class="stat-card"><div class="stat-card__value">${formatNumber(stats.metrics?.total_loc)}</div><div class="stat-card__label">Lines of code</div></div>
          <div class="stat-card"><div class="stat-card__value">${formatNumber(stats.metrics?.file_count)}</div><div class="stat-card__label">Files</div></div>
          <div class="stat-card"><div class="stat-card__value">${stats.author_count || 0}</div><div class="stat-card__label">Authors</div></div>
        </div>

        ${this._renderTabs()}

        <div class="repo-detail__tab-content">
          ${this._renderTabContent()}
        </div>
      </div>`;
    }
}