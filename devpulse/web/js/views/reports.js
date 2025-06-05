// devpulse/web/js/views/reports.js
// Reports view.
// Export sessions as CSV/JSON/Markdown.
// Shows weekly summary stats.
// Per-repo analysis trigger (calls existing analysis data from API).

import {
    Component, get, toast, formatDate, formatDuration,
    formatNumber, _escapeHtml,
} from '../app.js';
import { renderBarChart, renderHorizontalBarChart, ChartContainer } from '../components/chart.js';

export class ReportsView extends Component {
    constructor(props = {}) {
        super(props);
        this.state = {
            loading: false,
            weeklySessions: [],
            weeklyTotal: 0,
            avgFocus: 0,
            repos: [],
            selectedRepoId: '',
            exportFormat: 'csv',
            exportDays: 30,
            leaderboard: [],
            weekdayDist: [],
            hourlyDist: [],
        };
        this._charts = [];
    }

    onMount() { this._load(); }

    onUnmount() {
        this._charts.forEach(c => c.unmount());
        this._charts = [];
    }

    async _load() {
        this.setState({ loading: true });
        try {
            const [sessionsRes, reposRes, leaderRes] = await Promise.all([
                get('/api/sessions', { days: 7, limit: 100 }),
                get('/api/repos'),
                get('/api/activity/leaderboard', { days: 30 }),
            ]);

            const sessions = sessionsRes?.data || [];
            const totalS = sessions.reduce((s, r) => s + (r.duration_s || 0), 0);
            const avgFocus = sessions.length
                ? sessions.reduce((s, r) => s + (r.focus_score || 0), 0) / sessions.length
                : 0;

            this.setState({
                loading: false,
                weeklySessions: sessions,
                weeklyTotal: totalS,
                avgFocus,
                repos: reposRes?.data || [],
                leaderboard: leaderRes?.data || [],
            });

            this._loadRepoCharts();
        } catch (err) {
            this.setState({ loading: false });
            toast.error('Failed to load report data.');
        }
    }

    async _loadRepoCharts() {
        const id = this.state.selectedRepoId;
        if (!id) return;

        try {
            const [weekdayRes, hourlyRes] = await Promise.all([
                get(`/api/repos/${id}/commits/weekday`),
                get(`/api/repos/${id}/commits/hourly`),
            ]);
            this.setState({
                weekdayDist: weekdayRes?.data || [],
                hourlyDist: hourlyRes?.data || [],
            });
            this._mountRepoCharts();
        } catch {
            toast.error('Failed to load repo commit distribution.');
        }
    }

    _mountRepoCharts() {
        this._charts.forEach(c => c.unmount());
        this._charts = [];

        const weekdayEl = this.$('.reports__weekday-chart');
        if (weekdayEl && this.state.weekdayDist.length) {
            const c = new ChartContainer(renderBarChart, {
                data: this.state.weekdayDist, valueKey: 'count', labelKey: 'name',
                title: 'Commits by weekday',
            });
            c.mount(weekdayEl);
            this._charts.push(c);
        }

        const hourlyEl = this.$('.reports__hourly-chart');
        if (hourlyEl && this.state.hourlyDist.length) {
            const c = new ChartContainer(renderBarChart, {
                data: this.state.hourlyDist, valueKey: 'count', labelKey: 'hour',
                title: 'Commits by hour of day',
            });
            c.mount(hourlyEl);
            this._charts.push(c);
        }

        const leaderEl = this.$('.reports__leaderboard-chart');
        if (leaderEl && this.state.leaderboard.length) {
            const c = new ChartContainer(renderHorizontalBarChart, {
                data: this.state.leaderboard, valueKey: 'commit_count', labelKey: 'author',
                title: 'Top contributors (30 days)',
            });
            c.mount(leaderEl);
            this._charts.push(c);
        }
    }

    _triggerExport() {
        const { exportFormat, exportDays } = this.state;
        const url = `/api/export/${exportFormat}?days=${exportDays}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        toast.success(`Export started (${exportFormat.toUpperCase()}).`);
    }

    _triggerRepoExport() {
        const id = this.state.selectedRepoId;
        if (!id) { toast.warning('Select a repo first.'); return; }
        const a = document.createElement('a');
        a.href = `/api/repos/${id}/export/csv`;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        toast.success('Repo metrics CSV export started.');
    }

    events() {
        return [
            {
                selector: '.reports__format-select',
                event: 'change',
                handler(e, target) { this.setState({ exportFormat: target.value }); },
            },
            {
                selector: '.reports__days-select',
                event: 'change',
                handler(e, target) { this.setState({ exportDays: parseInt(target.value, 10) || 30 }); },
            },
            {
                selector: '.reports__export-btn',
                event: 'click',
                handler() { this._triggerExport(); },
            },
            {
                selector: '.reports__repo-select',
                event: 'change',
                handler(e, target) {
                    this.setState({ selectedRepoId: target.value, weekdayDist: [], hourlyDist: [] });
                    if (target.value) this._loadRepoCharts();
                },
            },
            {
                selector: '.reports__repo-export-btn',
                event: 'click',
                handler() { this._triggerRepoExport(); },
            },
        ];
    }

    _renderWeeklySummary() {
        const { weeklySessions, weeklyTotal, avgFocus } = this.state;
        const sessionCount = weeklySessions.length;

        return `
      <section class="reports__section">
        <h2 class="section-title">This Week</h2>
        <div class="stat-cards stat-cards--sm">
          <div class="stat-card">
            <div class="stat-card__value">${_escapeHtml(formatDuration(weeklyTotal))}</div>
            <div class="stat-card__label">Coding time</div>
          </div>
          <div class="stat-card">
            <div class="stat-card__value">${sessionCount}</div>
            <div class="stat-card__label">Sessions</div>
          </div>
          <div class="stat-card">
            <div class="stat-card__value">${Math.round(avgFocus * 100)}%</div>
            <div class="stat-card__label">Avg focus</div>
          </div>
          <div class="stat-card">
            <div class="stat-card__value">${sessionCount
                ? formatDuration(Math.round(weeklyTotal / sessionCount))
                : '—'}</div>
            <div class="stat-card__label">Avg session</div>
          </div>
        </div>
      </section>`;
    }

    _renderExportPanel() {
        const { exportFormat, exportDays } = this.state;
        const formats = ['csv', 'json', 'markdown'];
        const dayOptions = [7, 14, 30, 60, 90];

        return `
      <section class="reports__section">
        <h2 class="section-title">Export Sessions</h2>
        <div class="reports__export-row">
          <label class="label">Format
            <select class="input reports__format-select">
              ${formats.map(f => `<option value="${f}"${exportFormat === f ? ' selected' : ''}>${f.toUpperCase()}</option>`).join('')}
            </select>
          </label>
          <label class="label">Period
            <select class="input reports__days-select">
              ${dayOptions.map(d => `<option value="${d}"${exportDays === d ? ' selected' : ''}>${d} days</option>`).join('')}
            </select>
          </label>
          <button class="btn btn--primary reports__export-btn">⬇ Export</button>
        </div>
      </section>`;
    }

    _renderRepoPanel() {
        const repos = this.state.repos;
        const selectedId = this.state.selectedRepoId;

        return `
      <section class="reports__section">
        <h2 class="section-title">Repo Analysis</h2>
        <div class="reports__repo-row">
          <select class="input reports__repo-select">
            <option value="">Select a repo…</option>
            ${repos.map(r => `
              <option value="${r.id}"${selectedId == r.id ? ' selected' : ''}>
                ${_escapeHtml(r.name || r.path)}
              </option>`).join('')}
          </select>
          <button class="btn btn--ghost reports__repo-export-btn">
            ⬇ Export metrics CSV
          </button>
        </div>

        ${selectedId ? `
          <div class="reports__charts-grid">
            <div class="reports__weekday-chart"></div>
            <div class="reports__hourly-chart"></div>
          </div>` : ''}
      </section>`;
    }

    _renderLeaderboard() {
        const lb = this.state.leaderboard;
        if (!lb.length) return '';

        return `
      <section class="reports__section">
        <h2 class="section-title">Contributor Leaderboard</h2>
        <div class="reports__leaderboard-chart"></div>
      </section>`;
    }

    render() {
        if (this.state.loading) {
            return `<div class="view view--reports"><div class="view__loading"><p>Loading…</p></div></div>`;
        }

        return `
      <div class="view view--reports">
        <div class="view__header">
          <h1 class="view__title">Reports</h1>
        </div>

        ${this._renderWeeklySummary()}
        ${this._renderExportPanel()}
        ${this._renderRepoPanel()}
        ${this._renderLeaderboard()}
      </div>`;
    }
}