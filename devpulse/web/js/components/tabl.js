// devpulse/web/js/components/table.js
// Sortable, paginated data table component.
// Fully hand-written — no DataTables, no AG Grid.
// Supports: column sorting, client-side pagination,
// column visibility toggle, row click callbacks,
// empty state, loading skeleton.

import { Component, _escapeHtml } from '../app.js';

export class DataTable extends Component {
    /**
     * Props:
     *   columns:  [{key, label, sortable, render, align, width}]
     *   rows:     array of data objects
     *   pageSize: rows per page (default 20)
     *   onRowClick: fn(row) — optional
     *   emptyMessage: string
     *   loading: bool
     *   caption: string
     */

    constructor(props = {}) {
        super(props);
        this.state = {
            sortKey: null,
            sortDir: 'asc',   // 'asc' | 'desc'
            page: 1,
            hiddenCols: new Set(),
        };
    }

    get _columns() {
        return (this.props.columns || []).filter(
            c => !this.state.hiddenCols.has(c.key)
        );
    }

    get _sorted() {
        const rows = [...(this.props.rows || [])];
        const { sortKey, sortDir } = this.state;
        if (!sortKey) return rows;

        rows.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            if (typeof av === 'number' && typeof bv === 'number') {
                return sortDir === 'asc' ? av - bv : bv - av;
            }
            const as = String(av).toLowerCase();
            const bs = String(bv).toLowerCase();
            if (as < bs) return sortDir === 'asc' ? -1 : 1;
            if (as > bs) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return rows;
    }

    get _pageSize() {
        return this.props.pageSize || 20;
    }

    get _pageRows() {
        const sorted = this._sorted;
        const start = (this.state.page - 1) * this._pageSize;
        return sorted.slice(start, start + this._pageSize);
    }

    get _totalPages() {
        return Math.max(1,
            Math.ceil((this.props.rows || []).length / this._pageSize)
        );
    }

    _handleSort(key) {
        const { sortKey, sortDir } = this.state;
        if (sortKey === key) {
            this.setState({ sortDir: sortDir === 'asc' ? 'desc' : 'asc' });
        } else {
            this.setState({ sortKey: key, sortDir: 'asc' });
        }
    }

    _handlePage(page) {
        const p = Math.max(1, Math.min(page, this._totalPages));
        this.setState({ page: p });
    }

    _toggleCol(key) {
        const hidden = new Set(this.state.hiddenCols);
        if (hidden.has(key)) hidden.delete(key);
        else hidden.add(key);
        this.setState({ hiddenCols: hidden });
    }

    events() {
        return [
            {
                selector: '.dt-th--sortable',
                event: 'click',
                handler(e, target) {
                    const key = target.dataset.key;
                    if (key) this._handleSort(key);
                },
            },
            {
                selector: '.dt-page-btn',
                event: 'click',
                handler(e, target) {
                    const page = parseInt(target.dataset.page, 10);
                    if (!isNaN(page)) this._handlePage(page);
                },
            },
            {
                selector: '.dt-col-toggle',
                event: 'change',
                handler(e, target) {
                    const key = target.dataset.key;
                    if (key) this._toggleCol(key);
                },
            },
            {
                selector: '.dt-row',
                event: 'click',
                handler(e, target) {
                    const idx = parseInt(target.dataset.idx, 10);
                    if (!isNaN(idx) && this.props.onRowClick) {
                        const row = this._pageRows[idx];
                        if (row) this.props.onRowClick(row);
                    }
                },
            },
        ];
    }

    _renderSortIcon(key) {
        const { sortKey, sortDir } = this.state;
        if (sortKey !== key) return '<span class="dt-sort-icon dt-sort-icon--none">⇅</span>';
        return sortDir === 'asc'
            ? '<span class="dt-sort-icon dt-sort-icon--asc">↑</span>'
            : '<span class="dt-sort-icon dt-sort-icon--desc">↓</span>';
    }

    _renderHeader() {
        const cols = this._columns;
        const heads = cols.map(col => {
            const sortable = col.sortable !== false;
            const cls = sortable ? 'dt-th dt-th--sortable' : 'dt-th';
            const style = col.width ? ` style="width:${col.width}"` : '';
            const align = col.align ? ` dt-th--${col.align}` : '';
            const sortIcon = sortable ? this._renderSortIcon(col.key) : '';
            return `
        <th class="${cls}${align}" data-key="${_escapeHtml(col.key)}"${style}>
          ${_escapeHtml(col.label || col.key)}${sortIcon}
        </th>`;
        }).join('');

        return `<thead><tr>${heads}</tr></thead>`;
    }

    _renderBody() {
        if (this.props.loading) {
            return this._renderSkeleton();
        }

        const rows = this._pageRows;
        if (!rows || rows.length === 0) {
            const msg = _escapeHtml(
                this.props.emptyMessage || 'No data available.'
            );
            const colSpan = this._columns.length || 1;
            return `
        <tbody>
          <tr>
            <td class="dt-empty" colspan="${colSpan}">${msg}</td>
          </tr>
        </tbody>`;
        }

        const trs = rows.map((row, idx) => {
            const clickable = this.props.onRowClick ? ' dt-row--clickable' : '';
            const tds = this._columns.map(col => {
                const raw = row[col.key];
                const cell = col.render
                    ? col.render(raw, row)
                    : _escapeHtml(raw === null || raw === undefined ? '—' : raw);
                const align = col.align ? ` dt-td--${col.align}` : '';
                return `<td class="dt-td${align}">${cell}</td>`;
            }).join('');
            return `
        <tr class="dt-row${clickable}" data-idx="${idx}">
          ${tds}
        </tr>`;
        }).join('');

        return `<tbody>${trs}</tbody>`;
    }

    _renderSkeleton() {
        const cols = this._columns.length || 4;
        const rowsNum = Math.min(this._pageSize, 8);
        const rows = Array.from({ length: rowsNum }, () => {
            const tds = Array.from({ length: cols }, () =>
                `<td class="dt-td"><div class="skeleton skeleton--text"></div></td>`
            ).join('');
            return `<tr class="dt-row">${tds}</tr>`;
        }).join('');
        return `<tbody>${rows}</tbody>`;
    }

    _renderPagination() {
        const { page } = this.state;
        const total = this._totalPages;
        const rowCount = (this.props.rows || []).length;

        if (total <= 1) return '';

        const start = (page - 1) * this._pageSize + 1;
        const end = Math.min(page * this._pageSize, rowCount);

        // Build page buttons — show at most 7 around current
        const pages = [];
        const delta = 2;
        for (let i = 1; i <= total; i++) {
            if (
                i === 1 || i === total ||
                (i >= page - delta && i <= page + delta)
            ) {
                pages.push(i);
            } else if (
                pages[pages.length - 1] !== '...'
            ) {
                pages.push('...');
            }
        }

        const btns = pages.map(p => {
            if (p === '...') {
                return `<span class="dt-page-ellipsis">…</span>`;
            }
            const active = p === page ? ' dt-page-btn--active' : '';
            return `
        <button class="dt-page-btn${active}" data-page="${p}">
          ${p}
        </button>`;
        }).join('');

        const prevDisabled = page === 1 ? ' disabled' : '';
        const nextDisabled = page === total ? ' disabled' : '';

        return `
      <div class="dt-pagination">
        <span class="dt-count">
          ${start}–${end} of ${rowCount}
        </span>
        <div class="dt-pages">
          <button class="dt-page-btn dt-page-btn--nav"
            data-page="${page - 1}"${prevDisabled}>‹</button>
          ${btns}
          <button class="dt-page-btn dt-page-btn--nav"
            data-page="${page + 1}"${nextDisabled}>›</button>
        </div>
      </div>`;
    }

    _renderColToggle() {
        if (!this.props.showColToggle) return '';
        const allCols = this.props.columns || [];
        const items = allCols.map(col => {
            const checked = !this.state.hiddenCols.has(col.key)
                ? ' checked' : '';
            return `
        <label class="dt-col-label">
          <input
            type="checkbox"
            class="dt-col-toggle"
            data-key="${_escapeHtml(col.key)}"
            ${checked}
          />
          ${_escapeHtml(col.label || col.key)}
        </label>`;
        }).join('');

        return `
      <div class="dt-col-toggle-bar">
        <span class="dt-col-toggle-label">Columns:</span>
        ${items}
      </div>`;
    }

    render() {
        const caption = this.props.caption
            ? `<caption class="dt-caption">${_escapeHtml(this.props.caption)}</caption>`
            : '';

        return `
      <div class="dt-wrapper">
        ${this._renderColToggle()}
        <div class="dt-scroll">
          <table class="dt">
            ${caption}
            ${this._renderHeader()}
            ${this._renderBody()}
          </table>
        </div>
        ${this._renderPagination()}
      </div>`;
    }
}