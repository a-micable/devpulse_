// devpulse/web/js/views/sessions.js
// Sessions view.
// Shows paginated session list, active session status card,
// daily coding time bar chart, tag filter.

import {
    Component, get, post, patch, toast,
    formatDate, formatRelative, formatDuration, _escapeHtml,
} from '../app.js';
import { renderBarChart, ChartContainer } from '../components/chart.js';
import { DataTable } from '../components/table.js';

export class SessionsView extends Component {
    constructor(props = {}) {
        super(props);
        this.state = {
            loading: true,
            error: null,
            sessions: [],
            total: 0,
            offset: 0,
            limit: 20,
            activeSession: null,
            daily: [],
            tagFilter: '',
            selectedId: null,
            selected: null,
            addingNote: '',
            addingTag: '',
        };
        this._chart = null;
        this._table = null;
    }

    onMount() { this._load(); }

    onUnmount() {
        if (this._chart) this._chart.unmount();
        if (this._table) this._table.unmount();
    }

    async _load() {
        this.setState({ loading: true });
        try {
            const [listRes, activeRes, dailyRes] = await Promise.all([
                get('/api/sessions', { limit: this.state.limit, offset: this.state.offset, days: 60 }),
                get('/api/sessions/active'),
                get('/api/sessions/daily', { days: 30 }),
            ]);

            this.setState({
                loading: false,
                sessions: listRes?.data || [],
                total: listRes?.meta?.total || 0,
                activeSession: activeRes?.data || null,
                daily: dailyRes?.data || [],
            });

            if (this.props.store) {
                this.props.store.set('sessions', {
                    loading: false,
                    list: listRes?.data || [],
                    active: activeRes?.data || null,
                    total: listRes?.meta?.total || 0,
                });
            }

            this._mountSubComponents();
        } catch (err) {
            this.setState({ loading: false, error: err.message });
            toast.error('Failed to load sessions.');
        }
    }

    async _loadSession(id) {
        try {
            const res = await get(`/api/sessions/${id}`);
            this.setState({ selected: res?.data || null });
        } catch {
            toast.error('Failed to load session detail.');
        }
    }

    async _addNote() {
        const { selectedId, addingNote } = this.state;
        if (!selectedId || !addingNote.trim()) return;
        try {
            await patch(`/api/sessions/${selectedId}`, { notes: addingNote });
            toast.success('Note saved.');
            this.setState({ addingNote: '' });
            this._loadSession(selectedId);
        } catch {
            toast.error('Failed to save note.');
        }
    }

    async _addTag() {
        const { selectedId, addingTag } = this.state;
        if (!selectedId || !addingTag.trim()) return;
        try {
            await post(`/api/sessions/${selectedId}/tags`, { name: addingTag.trim() });
            toast.success('Tag added.');
            this.setState({ addingTag: '' });
            this._loadSession(selectedId);
        } catch {
            toast.error('Failed to add tag.');
        }
    }

    _mountSubComponents() {
        // Daily bar chart
        const chartEl = this.$('.sessions__daily-chart');
        if (chartEl && this.state.daily.length) {
            if (this._chart) this._chart.unmount();
            this._chart = new ChartContainer(renderBarChart, {
                data: this.state.daily,
                valueKey: 'total_seconds',
                labelKey: 'date',
                title: 'Coding time per day',
            });
            this._chart.mount(chartEl);
        }

        // Sessions table
        const tableEl = this.$('.sessions__table');
        if (tableEl) {
            if (this._table) this._table.unmount();
            this._table = new DataTable({
                columns: [
                    { key: 'repo_name', label: 'Repo', sortable: true },
                    {
                        key: 'started_at', label: 'Started', sortable: true,
                        render: v => _escapeHtml(formatRelative(v))
                    },
                    { key: 'duration_human', label: 'Duration', sortable: false },
                    {
                        key: 'focus_score', label: 'Focus', sortable: true,
                        render: v => {
                            const pct = Math.round((v || 0) * 100);
                            const cls = pct >= 70 ? 'badge--green' : pct >= 40 ? 'badge--yellow' : 'badge--muted';
                            return `<span class="badge ${cls}">${pct}%</span>`;
                        },
                    },
                    {
                        key: 'tags', label: 'Tags', sortable: false,
                        render: (v) => {
                            if (!Array.isArray(v) || !v.length) return '—';
                            return v.map(t => `<span class="tag">${_escapeHtml(t)}</span>`).join(' ');
                        },
                    },
                ],
                rows: this._filteredSessions(),
                pageSize: this.state.limit,
                onRowClick: (row) => {
                    this.setState({ selectedId: row.id, selected: null });
                    this._loadSession(row.id);
                },
                emptyMessage: 'No sessions found.',
            });
            this._table.mount(tableEl);
        }
    }

    _filteredSessions() {
        const q = this.state.tagFilter.toLowerCase();
        if (!q) return this.state.sessions;
        return this.state.sessions.filter(s => {
            const tags = Array.isArray(s.tags) ? s.tags : [];
            return tags.some(t => t.toLowerCase().includes(q)) ||
                (s.repo_name || '').toLowerCase().includes(q);
        });
    }

    _renderActiveSession() {
        const s = this.state.activeSession;
        if (!s) return '';
        return `
      <div class="active-session-card">
        <div class="active-session-card__dot"></div>
        <div class="active-session-card__body">
          <strong>Session in progress</strong>
          <span>Started ${_escapeHtml(formatRelative(s.started_at))}</span>
          ${s.duration_human ? `<span>${_escapeHtml(s.duration_human)}</span>` : ''}
          ${s.repo_name ? `<span>Repo: ${_escapeHtml(s.repo_name)}</span>` : ''}
        </div>
      </div>`;
    }

    _renderDetail() {
        const s = this.state.selected;
        if (!this.state.selectedId) return '';
        if (!s) return `<div class="sessions__detail"><p>Loading…</p></div>`;

        const tags = (s.tags || []).map(t =>
            `<span class="tag">${_escapeHtml(t)}</span>`
        ).join(' ');

        return `
      <div class="sessions__detail">
        <h3>Session #${s.id}</h3>
        <dl class="def-list">
          <dt>Started</dt><dd>${_escapeHtml(formatDate(s.started_at, true))}</dd>
          <dt>Ended</dt><dd>${_escapeHtml(s.ended_at ? formatDate(s.ended_at, true) : 'In progress')}</dd>
          <dt>Duration</dt><dd>${_escapeHtml(s.duration_human || formatDuration(s.duration_s))}</dd>
          <dt>Focus</dt><dd>${Math.round((s.focus_score || 0) * 100)}%</dd>
          <dt>Heartbeats</dt><dd>${s.heartbeat_count || 0}</dd>
          <dt>Tags</dt><dd>${tags || '—'}</dd>
          <dt>Notes</dt><dd>${_escapeHtml(s.notes || '—')}</dd>
        </dl>

        <div class="sessions__detail-actions">
          <div class="input-row">
            <input type="text" class="input sessions__tag-input"
              placeholder="Add tag…"
              value="${_escapeHtml(this.state.addingTag)}" />
            <button class="btn btn--sm btn--primary sessions__tag-btn">Add tag</button>
          </div>
          <div class="input-row">
            <input type="text" class="input sessions__note-input"
              placeholder="Add note…"
              value="${_escapeHtml(this.state.addingNote)}" />
            <button class="btn btn--sm btn--ghost sessions__note-btn">Save note</button>
          </div>
        </div>
      </div>`;
    }

    events() {
        return [
            {
                selector: '.sessions__tag-filter',
                event: 'input',
                handler(e, target) {
                    this.setState({ tagFilter: target.value });
                    this._mountSubComponents();
                },
            },
            {
                selector: '.sessions__tag-input',
                event: 'input',
                handler(e, target) { this.setState({ addingTag: target.value }); },
            },
            {
                selector: '.sessions__note-input',
                event: 'input',
                handler(e, target) { this.setState({ addingNote: target.value }); },
            },
            {
                selector: '.sessions__tag-btn',
                event: 'click',
                handler() { this._addTag(); },
            },
            {
                selector: '.sessions__note-btn',
                event: 'click',
                handler() { this._addNote(); },
            },
            {
                selector: '.sessions__close-detail',
                event: 'click',
                handler() { this.setState({ selectedId: null, selected: null }); },
            },
        ];
    }

    render() {
        if (this.state.loading) {
            return `<div class="view view--sessions"><div class="view__loading"><p>Loading sessions…</p></div></div>`;
        }

        return `
      <div class="view view--sessions">
        <div class="view__header">
          <h1 class="view__title">Sessions</h1>
          <input type="text" class="input input--sm sessions__tag-filter"
            placeholder="Filter by tag or repo…"
            value="${_escapeHtml(this.state.tagFilter)}" />
        </div>

        ${this._renderActiveSession()}

        <section class="sessions__section">
          <div class="sessions__daily-chart"></div>
        </section>

        <div class="sessions__layout${this.state.selectedId ? ' sessions__layout--split' : ''}">
          <div class="sessions__table-wrap">
            <div class="sessions__table"></div>
          </div>
          ${this.state.selectedId ? `
            <div class="sessions__detail-wrap">
              <button class="btn btn--ghost btn--xs sessions__close-detail">✕ Close</button>
              ${this._renderDetail()}
            </div>` : ''}
        </div>
      </div>`;
    }
}