// devpulse/web/js/views/dashboard.js
// Main dashboard view.
// Loads summary data from /api/summary, renders stat cards,
// activity heatmap, coding time line chart, recent sessions table.

import { Component, get, toast, formatDuration, formatRelative, _escapeHtml } from '../app.js';
import { renderHeatmap, renderLineChart, ChartContainer } from '../components/chart.js';
import { DataTable } from '../components/table.js';

export class DashboardView extends Component {
    constructor(props = {}) {
        super(props);
        this.state = {
            loading: true,
            error: null,
            summary: null,
            heatmap: [],
            daily: [],
        };
        this._heatmapChart = null;
        this._lineChart = null;
        this._table = null;
    }

    onMount() {
        this._load();
    }

    onUnmount() {
        if (this._heatmapChart) this._heatmapChart.unmount();
        if (this._lineChart) this._lineChart.unmount();
        if (this._table) this._table.unmount();
    }

    async _load() {
        this.setState({ loading: true, error: null });
        try {
            const [summaryRes, heatmapRes, dailyRes] = await Promise.all([
                get('/api/summary'),
                get('/api/activity/heatmap'),
                get('/api/sessions/daily', { days: 30 }),
            ]);
            this.setState({
                loading: false,
                summary: summaryRes?.data || null,
                heatmap: heatmapRes?.data || [],
                daily: dailyRes?.data || [],
            });
            if (this.props.store) {
                this.props.store.set('summary', {
                    loading: false, error: null,
                    data: summaryRes?.data, lastFetched: Date.now(),
                });
            }
            this._mountSubComponents();
        } catch (err) {
            this.setState({ loading: false, error: err.message });
            toast.error('Failed to load dashboard data.');
        }
    }

    _mountSubComponents() {
        // Heatmap
        const heatmapEl = this.$('.dashboard__heatmap-chart');
        if (heatmapEl && this.state.heatmap.length) {
            this._heatmapChart = new ChartContainer(renderHeatmap, {
                data: this.state.heatmap,
                title: 'Commit activity',
            });
            this._heatmapChart.mount(heatmapEl);
        }

        // Line chart
        const lineEl = this.$('.dashboard__line-chart');
        if (lineEl && this.state.daily.length) {
            this._lineChart = new ChartContainer(renderLineChart, {
                data: this.state.daily,
                valueKey: 'total_seconds',
                labelKey: 'date',
                title: 'Coding time (30 days)',
            });
            this._lineChart.mount(lineEl);
        }

        // Recent sessions table
        const tableEl = this.$('.dashboard__sessions-table');
        if (tableEl && this.state.summary) {
            const sessions = this.state.summary.recent_sessions || [];
            this._table = new DataTable({
                columns: [
                    { key: 'repo_name', label: 'Repo', sortable: false },
                    {
                        key: 'started_at', label: 'Started', sortable: true,
                        render: v => _escapeHtml(formatRelative(v))
                    },
                    { key: 'duration_human', label: 'Duration', sortable: false },
                    {
                        key: 'focus_score', label: 'Focus', sortable: true,
                        render: v => `<span class="badge badge--focus">${Math.round((v || 0) * 100)}%</span>`
                    },
                ],
                rows: sessions,
                pageSize: 5,
                emptyMessage: 'No recent sessions.',
            });
            this._table.mount(tableEl);
        }
    }

    _renderStatCards() {
        const s = this.state.summary;
        if (!s) return '<div class="stat-cards"></div>';

        const cards = [
            {
                label: 'Coding time',
                value: s.total_coding_human || '0s',
                sub: 'last 30 days',
                icon: '◷',
                cls: 'stat-card--blue',
            },
            {
                label: 'Sessions',
                value: (s.session_count || 0).toLocaleString(),
                sub: 'all time',
                icon: '◉',
                cls: 'stat-card--green',
            },
            {
                label: 'Repos',
                value: (s.repo_count || 0).toLocaleString(),
                sub: 'tracked',
                icon: '⬢',
                cls: 'stat-card--purple',
            },
            {
                label: 'Commits',
                value: (s.total_commits || 0).toLocaleString(),
                sub: 'all time',
                icon: '◆',
                cls: 'stat-card--orange',
            },
            {
                label: 'Focus score',
                value: `${Math.round((s.avg_focus_score || 0) * 100)}%`,
                sub: 'avg last 30 days',
                icon: '◎',
                cls: 'stat-card--yellow',
            },
            {
                label: 'Streak',
                value: `${s.current_streak || 0}d`,
                sub: `longest: ${s.longest_streak || 0}d`,
                icon: '◈',
                cls: 'stat-card--teal',
            },
        ];

        const html = cards.map(c => `
      <div class="stat-card ${c.cls}">
        <div class="stat-card__icon">${c.icon}</div>
        <div class="stat-card__body">
          <div class="stat-card__value">${_escapeHtml(String(c.value))}</div>
          <div class="stat-card__label">${_escapeHtml(c.label)}</div>
          <div class="stat-card__sub">${_escapeHtml(c.sub)}</div>
        </div>
      </div>`).join('');

        return `<div class="stat-cards">${html}</div>`;
    }

    _renderGoals() {
        const goals = this.state.summary?.active_goals || [];
        if (!goals.length) return '';

        const items = goals.slice(0, 4).map(g => `
      <div class="goal-item">
        <span class="goal-item__metric">${_escapeHtml(g.metric)}</span>
        <span class="goal-item__period badge">${_escapeHtml(g.period)}</span>
        <span class="goal-item__target">target: ${_escapeHtml(String(g.target))}</span>
      </div>`).join('');

        return `
      <section class="dashboard__section">
        <h2 class="section-title">Active Goals</h2>
        <div class="goal-list">${items}</div>
      </section>`;
    }

    render() {
        if (this.state.loading) {
            return `
        <div class="view view--dashboard">
          <div class="view__loading">
            <div class="spinner__ring"><div></div><div></div><div></div><div></div></div>
            <p>Loading dashboard…</p>
          </div>
        </div>`;
        }

        if (this.state.error) {
            return `
        <div class="view view--dashboard">
          <div class="view__error">
            <p>Failed to load: ${_escapeHtml(this.state.error)}</p>
            <button class="btn btn--primary" onclick="location.reload()">Retry</button>
          </div>
        </div>`;
        }

        return `
      <div class="view view--dashboard">
        <div class="view__header">
          <h1 class="view__title">Dashboard</h1>
          <button class="btn btn--ghost btn--sm dashboard__refresh">↻ Refresh</button>
        </div>

        ${this._renderStatCards()}

        <section class="dashboard__section">
          <h2 class="section-title">Activity</h2>
          <div class="dashboard__heatmap-chart"></div>
        </section>

        <section class="dashboard__section">
          <h2 class="section-title">Coding Time</h2>
          <div class="dashboard__line-chart"></div>
        </section>

        <section class="dashboard__section">
          <h2 class="section-title">Recent Sessions</h2>
          <div class="dashboard__sessions-table"></div>
        </section>

        ${this._renderGoals()}
      </div>`;
    }

    events() {
        return [
            {
                selector: '.dashboard__refresh',
                event: 'click',
                handler() { this._load(); },
            },
        ];
    }
}