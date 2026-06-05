# DevPulse

A developer activity tracker that monitors coding sessions, indexes git history, computes productivity metrics, and serves a dark-themed web dashboard with a full CLI.

## Features

- **Session tracking** — start/stop/pause coding sessions with heartbeat monitoring
- **Git analysis** — indexes commits, computes LOC, complexity, churn, and hotspots
- **Focus scoring** — rates each session 0–100% based on heartbeat regularity, save events, and idle time
- **Streaks** — tracks daily coding streaks (current and longest)
- **Goals** — set daily/weekly/monthly targets for coding time, sessions, or commits
- **Web dashboard** — dark-themed SPA with heatmap, charts, and session detail view
- **CLI** — full command-line interface for all features
- **Reports** — export sessions and commits as CSV, JSON, or Markdown
- **WebSocket** — real-time session updates pushed to the dashboard

## Tech stack

- **Backend** — Python 3.10+, aiohttp, SQLite
- **Frontend** — Vanilla JS (ES modules), SVG charts, no build step
- **CLI** — pure Python, no Click dependency
- **Testing** — pytest, pytest-asyncio

## Project structure
