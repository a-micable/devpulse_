// devpulse/web/js/components/chart.js
// SVG chart renderer — hand-written, no Chart.js, no D3.
// Produces inline SVG strings for:
//   - Bar chart
//   - Line chart
//   - Heatmap (GitHub-style activity grid)
//   - Donut chart (language breakdown)
// All charts are purely functional: given data → return SVG string.
// Tooltip support via CSS + data attributes (no JS mousemove math).

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const SVG_NS = 'http://www.w3.org/2000/svg';

function _esc(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _round(n, d = 2) {
    return Math.round(n * Math.pow(10, d)) / Math.pow(10, d);
}

function _maxVal(data, key) {
    return Math.max(...data.map(d => d[key] || 0), 1);
}

function _niceMax(val) {
    if (val <= 0) return 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(val)));
    const normalized = val / magnitude;
    let nice;
    if (normalized <= 1) nice = 1;
    else if (normalized <= 2) nice = 2;
    else if (normalized <= 5) nice = 5;
    else nice = 10;
    return nice * magnitude;
}

function _yLabels(maxVal, steps = 4) {
    const nice = _niceMax(maxVal);
    const labels = [];
    for (let i = 0; i <= steps; i++) {
        labels.push(_round((nice / steps) * i, 0));
    }
    return labels;
}

// Color palette — CSS custom properties with fallbacks
const PALETTE = [
    'var(--color-accent,  #58a6ff)',
    'var(--color-green,   #3fb950)',
    'var(--color-purple,  #bc8cff)',
    'var(--color-orange,  #f78166)',
    'var(--color-yellow,  #e3b341)',
    'var(--color-cyan,    #39d353)',
    'var(--color-pink,    #ff7b72)',
    'var(--color-teal,    #56d364)',
];

// ---------------------------------------------------------------------------
// Bar chart
// ---------------------------------------------------------------------------

export function renderBarChart({
    data,
    valueKey = 'count',
    labelKey = 'label',
    width = 600,
    height = 220,
    barColor = 'var(--color-accent, #58a6ff)',
    hoverColor = 'var(--color-accent-hover, #79b8ff)',
    showYAxis = true,
    showXAxis = true,
    maxBars = 60,
    title = '',
} = {}) {
    if (!data || data.length === 0) {
        return _emptyChart(width, height, 'No data');
    }

    const items = data.slice(-maxBars);
    const padL = showYAxis ? 42 : 8;
    const padR = 8;
    const padT = title ? 28 : 10;
    const padB = showXAxis ? 36 : 8;

    const chartW = width - padL - padR;
    const chartH = height - padT - padB;

    const maxVal = _maxVal(items, valueKey);
    const niceMax = _niceMax(maxVal);
    const yLabels = _yLabels(niceMax);

    const barCount = items.length;
    const totalGap = Math.max(barCount - 1, 0) * 2;
    const barWidth = Math.max(1, (chartW - totalGap) / barCount);
    const barGap = barCount > 1 ? (chartW - barWidth * barCount) / (barCount - 1) : 0;

    let bars = '';
    let xLabels = '';

    // Determine label step to avoid crowding
    const labelStep = Math.ceil(barCount / 20);

    items.forEach((item, i) => {
        const val = item[valueKey] || 0;
        const barH = chartH * (val / niceMax);
        const x = padL + i * (barWidth + barGap);
        const y = padT + chartH - barH;
        const label = _esc(item[labelKey] || '');
        const tooltip = `${label}: ${val}`;

        bars += `
      <rect
        class="chart-bar"
        x="${_round(x)}"
        y="${_round(y)}"
        width="${_round(barWidth)}"
        height="${_round(barH)}"
        fill="${barColor}"
        rx="2"
        data-tooltip="${_esc(tooltip)}"
      >
        <title>${_esc(tooltip)}</title>
      </rect>`;

        if (showXAxis && i % labelStep === 0 && label) {
            const labelX = _round(x + barWidth / 2);
            const labelY = height - padB + 14;
            xLabels += `
        <text
          class="chart-label"
          x="${labelX}"
          y="${labelY}"
          text-anchor="middle"
          font-size="10"
        >${label.slice(0, 6)}</text>`;
        }
    });

    // Y axis labels and grid lines
    let yAxis = '';
    let gridLines = '';
    if (showYAxis) {
        yLabels.forEach((val, i) => {
            const y = _round(padT + chartH - (chartH * val / niceMax));
            gridLines += `
        <line
          class="chart-grid"
          x1="${padL}" y1="${y}"
          x2="${padL + chartW}" y2="${y}"
          stroke="var(--color-border, #30363d)"
          stroke-width="1"
          stroke-dasharray="4,3"
        />`;
            yAxis += `
        <text
          class="chart-label"
          x="${padL - 6}"
          y="${y + 4}"
          text-anchor="end"
          font-size="10"
        >${val}</text>`;
        });
    }

    const titleEl = title
        ? `<text class="chart-title" x="${width / 2}" y="16"
         text-anchor="middle" font-size="12" font-weight="600"
       >${_esc(title)}</text>`
        : '';

    return `
    <svg
      class="chart chart--bar"
      viewBox="0 0 ${width} ${height}"
      xmlns="${SVG_NS}"
      role="img"
      aria-label="${_esc(title || 'Bar chart')}"
    >
      ${titleEl}
      ${gridLines}
      ${bars}
      ${yAxis}
      ${xLabels}
      <line
        x1="${padL}" y1="${padT + chartH}"
        x2="${padL + chartW}" y2="${padT + chartH}"
        stroke="var(--color-border, #30363d)"
        stroke-width="1"
      />
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Line chart
// ---------------------------------------------------------------------------

export function renderLineChart({
    data,
    valueKey = 'count',
    labelKey = 'date',
    width = 600,
    height = 200,
    lineColor = 'var(--color-accent, #58a6ff)',
    fillColor = 'var(--color-accent-muted, rgba(88,166,255,0.15))',
    showDots = false,
    showArea = true,
    title = '',
} = {}) {
    if (!data || data.length < 2) {
        return _emptyChart(width, height, 'Not enough data');
    }

    const padL = 42;
    const padR = 12;
    const padT = title ? 28 : 10;
    const padB = 30;

    const chartW = width - padL - padR;
    const chartH = height - padT - padB;

    const maxVal = _maxVal(data, valueKey);
    const niceMax = _niceMax(maxVal);
    const yLabels = _yLabels(niceMax);

    const step = chartW / Math.max(data.length - 1, 1);

    // Build SVG polyline points
    const points = data.map((item, i) => {
        const val = item[valueKey] || 0;
        const x = _round(padL + i * step);
        const y = _round(padT + chartH - (chartH * val / niceMax));
        return `${x},${y}`;
    }).join(' ');

    // Area fill polygon — close the path along the bottom
    const firstX = _round(padL);
    const lastX = _round(padL + (data.length - 1) * step);
    const baseY = _round(padT + chartH);
    const areaPoints = `${firstX},${baseY} ${points} ${lastX},${baseY}`;

    const areaEl = showArea
        ? `<polygon points="${areaPoints}" fill="${fillColor}" />`
        : '';

    // Dots
    let dots = '';
    if (showDots) {
        data.forEach((item, i) => {
            const val = item[valueKey] || 0;
            const x = _round(padL + i * step);
            const y = _round(padT + chartH - (chartH * val / niceMax));
            const label = _esc(item[labelKey] || '');
            dots += `
        <circle
          class="chart-dot"
          cx="${x}" cy="${y}" r="3"
          fill="${lineColor}"
          data-tooltip="${label}: ${val}"
        >
          <title>${label}: ${val}</title>
        </circle>`;
        });
    }

    // Y axis
    let yAxis = '';
    let gridLines = '';
    yLabels.forEach(val => {
        const y = _round(padT + chartH - (chartH * val / niceMax));
        gridLines += `
      <line
        x1="${padL}" y1="${y}"
        x2="${padL + chartW}" y2="${y}"
        stroke="var(--color-border, #30363d)"
        stroke-width="1"
        stroke-dasharray="4,3"
      />`;
        yAxis += `
      <text
        class="chart-label"
        x="${padL - 6}" y="${y + 4}"
        text-anchor="end" font-size="10"
      >${val}</text>`;
    });

    // X axis — show ~6 labels
    let xLabels = '';
    const xStep = Math.ceil(data.length / 6);
    data.forEach((item, i) => {
        if (i % xStep !== 0 && i !== data.length - 1) return;
        const x = _round(padL + i * step);
        const label = String(item[labelKey] || '').slice(5); // strip year
        xLabels += `
      <text
        class="chart-label"
        x="${x}" y="${height - padB + 14}"
        text-anchor="middle" font-size="10"
      >${_esc(label)}</text>`;
    });

    const titleEl = title
        ? `<text class="chart-title" x="${width / 2}" y="16"
         text-anchor="middle" font-size="12" font-weight="600"
       >${_esc(title)}</text>`
        : '';

    return `
    <svg
      class="chart chart--line"
      viewBox="0 0 ${width} ${height}"
      xmlns="${SVG_NS}"
      role="img"
      aria-label="${_esc(title || 'Line chart')}"
    >
      ${titleEl}
      ${gridLines}
      ${areaEl}
      <polyline
        points="${points}"
        fill="none"
        stroke="${lineColor}"
        stroke-width="2"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
      ${dots}
      ${yAxis}
      ${xLabels}
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Activity heatmap (GitHub contribution graph style)
// ---------------------------------------------------------------------------

export function renderHeatmap({
    data,
    weeks = 52,
    cellSize = 12,
    gap = 2,
    title = 'Activity',
} = {}) {
    if (!data || data.length === 0) {
        return _emptyChart(200, 100, 'No activity data');
    }

    // Build date→level map
    const dateMap = {};
    data.forEach(d => {
        dateMap[d.date] = d.level || 0;
    });

    // Generate the grid: 52 weeks × 7 days
    // Start from 52 weeks ago, Sunday-aligned
    const today = new Date();
    const startDate = new Date(today);
    startDate.setDate(startDate.getDate() - (weeks * 7));
    // Align to Sunday
    startDate.setDate(startDate.getDate() - startDate.getDay());

    const cols = weeks;
    const rows = 7;
    const svgWidth = cols * (cellSize + gap) + 32;   // 32 for day labels
    const svgHeight = rows * (cellSize + gap) + 28;   // 28 for month labels

    const levelColors = [
        'var(--heatmap-0, #161b22)',
        'var(--heatmap-1, #0e4429)',
        'var(--heatmap-2, #006d32)',
        'var(--heatmap-3, #26a641)',
        'var(--heatmap-4, #39d353)',
    ];

    const dayLabels = ['', 'Mon', '', 'Wed', '', 'Fri', ''];

    let cells = '';
    let monthLabels = '';
    let prevMonth = -1;

    for (let week = 0; week < cols; week++) {
        for (let day = 0; day < rows; day++) {
            const date = new Date(startDate);
            date.setDate(startDate.getDate() + week * 7 + day);

            const iso = date.toISOString().slice(0, 10);
            const level = dateMap[iso] || 0;
            const color = levelColors[level] || levelColors[0];
            const x = 28 + week * (cellSize + gap);
            const y = 18 + day * (cellSize + gap);

            cells += `
        <rect
          class="heatmap-cell"
          x="${x}" y="${y}"
          width="${cellSize}" height="${cellSize}"
          fill="${color}"
          rx="2"
          data-date="${iso}"
          data-level="${level}"
        >
          <title>${iso}</title>
        </rect>`;

            // Month label at start of new month
            if (day === 0) {
                const month = date.getMonth();
                if (month !== prevMonth) {
                    const monthName = date.toLocaleString('en-US', { month: 'short' });
                    monthLabels += `
            <text
              class="chart-label"
              x="${x}" y="12"
              font-size="10"
            >${monthName}</text>`;
                    prevMonth = month;
                }
            }
        }
    }

    // Day-of-week labels on left
    let dayLabelsSvg = '';
    dayLabels.forEach((label, i) => {
        if (!label) return;
        const y = 18 + i * (cellSize + gap) + cellSize - 2;
        dayLabelsSvg += `
      <text
        class="chart-label"
        x="0" y="${y}"
        font-size="10"
        dominant-baseline="middle"
      >${label}</text>`;
    });

    return `
    <svg
      class="chart chart--heatmap"
      viewBox="0 0 ${svgWidth} ${svgHeight}"
      xmlns="${SVG_NS}"
      role="img"
      aria-label="${_esc(title)}"
    >
      ${monthLabels}
      ${dayLabelsSvg}
      ${cells}
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Donut chart (language breakdown)
// ---------------------------------------------------------------------------

export function renderDonutChart({
    data,
    valueKey = 'total_loc',
    labelKey = 'language',
    size = 200,
    thickness = 40,
    title = '',
} = {}) {
    if (!data || data.length === 0) {
        return _emptyChart(size, size, 'No data');
    }

    const total = data.reduce((s, d) => s + (d[valueKey] || 0), 0);
    if (total === 0) return _emptyChart(size, size, 'No data');

    const cx = size / 2;
    const cy = size / 2;
    const radius = (size / 2) - 10;
    const inner = radius - thickness;

    let slices = '';
    let legend = '';
    let startAngle = -Math.PI / 2;  // Start at top

    data.slice(0, 8).forEach((item, i) => {
        const val = item[valueKey] || 0;
        const pct = val / total;
        const angle = pct * 2 * Math.PI;
        const endAngle = startAngle + angle;
        const color = PALETTE[i % PALETTE.length];
        const label = _esc(item[labelKey] || '');

        // Arc path
        const x1 = _round(cx + radius * Math.cos(startAngle));
        const y1 = _round(cy + radius * Math.sin(startAngle));
        const x2 = _round(cx + radius * Math.cos(endAngle));
        const y2 = _round(cy + radius * Math.sin(endAngle));
        const ix1 = _round(cx + inner * Math.cos(endAngle));
        const iy1 = _round(cy + inner * Math.sin(endAngle));
        const ix2 = _round(cx + inner * Math.cos(startAngle));
        const iy2 = _round(cy + inner * Math.sin(startAngle));

        const largeArc = angle > Math.PI ? 1 : 0;

        const path = [
            `M ${x1} ${y1}`,
            `A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`,
            `L ${ix1} ${iy1}`,
            `A ${inner} ${inner} 0 ${largeArc} 0 ${ix2} ${iy2}`,
            'Z',
        ].join(' ');

        const pctStr = Math.round(pct * 100);
        slices += `
      <path
        class="donut-slice"
        d="${path}"
        fill="${color}"
        opacity="0.9"
        data-tooltip="${label}: ${pctStr}%"
      >
        <title>${label}: ${pctStr}% (${val.toLocaleString()} LOC)</title>
      </path>`;

        // Legend item
        const legendY = i * 20;
        legend += `
      <g transform="translate(0, ${legendY})">
        <rect width="10" height="10" rx="2" fill="${color}" y="1" />
        <text
          class="chart-label"
          x="14" y="10"
          font-size="11"
        >${label} <tspan fill="var(--color-muted)">${pctStr}%</tspan></text>
      </g>`;

        startAngle = endAngle;
    });

    // Center label
    const centerLabel = `
    <text
      x="${cx}" y="${cy - 6}"
      text-anchor="middle"
      font-size="22"
      font-weight="700"
      fill="var(--color-text, #e6edf3)"
    >${data.length}</text>
    <text
      x="${cx}" y="${cy + 14}"
      text-anchor="middle"
      font-size="11"
      fill="var(--color-muted, #8b949e)"
    >languages</text>`;

    const legendX = size + 16;
    const legendH = Math.min(data.length, 8) * 20;
    const totalW = legendX + 120;
    const totalH = Math.max(size, legendH + 20);

    return `
    <svg
      class="chart chart--donut"
      viewBox="0 0 ${totalW} ${totalH}"
      xmlns="${SVG_NS}"
      role="img"
      aria-label="${_esc(title || 'Donut chart')}"
    >
      ${slices}
      ${centerLabel}
      <g transform="translate(${legendX}, ${(totalH - legendH) / 2})">
        ${legend}
      </g>
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Horizontal bar chart (for author leaderboard, weekday distribution etc)
// ---------------------------------------------------------------------------

export function renderHorizontalBarChart({
    data,
    valueKey = 'count',
    labelKey = 'name',
    width = 400,
    barColor = 'var(--color-accent, #58a6ff)',
    maxItems = 10,
    title = '',
} = {}) {
    if (!data || data.length === 0) {
        return _emptyChart(width, 100, 'No data');
    }

    const items = data.slice(0, maxItems);
    const rowH = 28;
    const padL = 100;
    const padR = 50;
    const padT = title ? 28 : 8;
    const padB = 8;
    const chartW = width - padL - padR;
    const height = padT + items.length * rowH + padB;
    const maxVal = _maxVal(items, valueKey);

    let bars = '';
    items.forEach((item, i) => {
        const val = item[valueKey] || 0;
        const barW = _round(chartW * (val / maxVal));
        const y = padT + i * rowH;
        const label = _esc(String(item[labelKey] || '').slice(0, 14));
        const tooltip = `${label}: ${val}`;

        bars += `
      <text
        class="chart-label"
        x="${padL - 6}" y="${y + rowH / 2 + 4}"
        text-anchor="end" font-size="11"
      >${label}</text>
      <rect
        class="chart-bar"
        x="${padL}" y="${y + 6}"
        width="${barW}" height="${rowH - 12}"
        fill="${barColor}" rx="3"
        data-tooltip="${_esc(tooltip)}"
      >
        <title>${_esc(tooltip)}</title>
      </rect>
      <text
        class="chart-label"
        x="${padL + barW + 4}" y="${y + rowH / 2 + 4}"
        font-size="11"
      >${val}</text>`;
    });

    const titleEl = title
        ? `<text class="chart-title" x="${width / 2}" y="16"
         text-anchor="middle" font-size="12" font-weight="600"
       >${_esc(title)}</text>`
        : '';

    return `
    <svg
      class="chart chart--hbar"
      viewBox="0 0 ${width} ${height}"
      xmlns="${SVG_NS}"
    >
      ${titleEl}
      ${bars}
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Empty / error state chart
// ---------------------------------------------------------------------------

function _emptyChart(width, height, message = 'No data') {
    return `
    <svg
      class="chart chart--empty"
      viewBox="0 0 ${width} ${height}"
      xmlns="${SVG_NS}"
    >
      <rect
        x="1" y="1"
        width="${width - 2}" height="${height - 2}"
        fill="none"
        stroke="var(--color-border, #30363d)"
        stroke-width="1"
        rx="4"
        stroke-dasharray="6,4"
      />
      <text
        x="${width / 2}" y="${height / 2 + 5}"
        text-anchor="middle"
        font-size="13"
        fill="var(--color-muted, #8b949e)"
      >${_esc(message)}</text>
    </svg>`.trim();
}

// ---------------------------------------------------------------------------
// Chart container component wrapper
// Used to mount a chart into a DOM element with responsive sizing
// ---------------------------------------------------------------------------

export class ChartContainer {
    constructor(renderFn, props = {}) {
        this._renderFn = renderFn;
        this._props = props;
        this._el = null;
    }

    mount(container) {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) return this;
        this._el = container;
        this._draw();

        // Redraw on resize
        this._resizeObserver = new ResizeObserver(() => this._draw());
        this._resizeObserver.observe(container);
        return this;
    }

    update(newProps) {
        this._props = { ...this._props, ...newProps };
        this._draw();
    }

    _draw() {
        if (!this._el) return;
        const w = this._el.clientWidth || 600;
        const svg = this._renderFn({ ...this._props, width: w });
        this._el.innerHTML = svg;
    }

    unmount() {
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
        }
        if (this._el) {
            this._el.innerHTML = '';
        }
    }
}