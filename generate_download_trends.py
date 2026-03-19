#!/usr/bin/env python3
"""
Generate a standalone HTML page showing download trends over time
for the major cloud providers (AWS, Azure, GCP).

Reads from data/raw/providers_*.json snapshots and produces docs/downloads.html.

Usage:
    python3 generate_download_trends.py
    open docs/downloads.html
"""

import json
import glob
from pathlib import Path
from datetime import datetime

# Providers to track - grouped by cloud
PROVIDERS = {
    "hashicorp/aws":          {"label": "AWS",          "color": "#FF9900", "group": "AWS"},
    "hashicorp/awscc":        {"label": "AWS CC",       "color": "#FF6600", "group": "AWS"},
    "hashicorp/azurerm":      {"label": "Azure RM",     "color": "#0078D4", "group": "Azure"},
    "hashicorp/azuread":      {"label": "Azure AD",     "color": "#50A0E6", "group": "Azure"},
    "hashicorp/azurestack":   {"label": "Azure Stack",  "color": "#00BCF2", "group": "Azure"},
    "hashicorp/google":       {"label": "Google",       "color": "#4285F4", "group": "GCP"},
    "hashicorp/google-beta":  {"label": "Google Beta",  "color": "#34A853", "group": "GCP"},
}

METRICS = {
    "total": {
        "title": "Cumulative Downloads Over Time",
        "subtitle": "Provider-level total downloads captured in each snapshot",
    },
    "week": {
        "title": "Registry Weekly Downloads",
        "subtitle": "Terraform Registry summary metric for downloads this week",
    },
    "month": {
        "title": "Registry Monthly Downloads",
        "subtitle": "Terraform Registry summary metric for downloads this month",
    },
    "year": {
        "title": "Registry Yearly Downloads",
        "subtitle": "Terraform Registry summary metric for downloads this year",
    },
}


def load_snapshots():
    """Load raw snapshots and extract download metrics for tracked providers.

    Uses a rolling 6-month window from the most recent snapshot.
    Older snapshots may only contain cumulative totals; week/month/year values
    will appear after the fetcher starts persisting Registry summary metrics.
    """
    files = sorted(glob.glob("data/raw/providers_[0-9][0-9][0-9][0-9]-*.json"))
    if not files:
        print("No snapshot files found in data/raw/")
        return [], {}
    
    # Rolling 6-month window
    from datetime import datetime, timedelta
    latest_date = files[-1].split("providers_")[1].replace(".json", "")
    cutoff = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=183)).strftime("%Y-%m-%d")
    files = [f for f in files if f.split("providers_")[1].replace(".json", "") >= cutoff]

    dates = []
    series = {metric: {provider: [] for provider in PROVIDERS} for metric in METRICS}

    for fp in files:
        date = fp.split("providers_")[1].replace(".json", "")
        with open(fp) as f:
            data = json.load(f)

        providers = data.get("providers", []) if isinstance(data, dict) else data
        lookup = {}
        for p in providers:
            fn = p.get("full_name", "")
            if fn in PROVIDERS:
                summary = p.get("download_summary", {}) or {}
                total = summary.get("total", p.get("downloads", 0))
                lookup[fn] = {
                    "total": total if total and total > 0 else None,
                    "week": summary.get("week"),
                    "month": summary.get("month"),
                    "year": summary.get("year"),
                }

        dates.append(date)
        for provider in PROVIDERS:
            values = lookup.get(provider, {})
            for metric in METRICS:
                series[metric][provider].append(values.get(metric))

    return dates, series


def fmt(n):
    """Format large numbers with B/M/K suffix."""
    if n is None:
        return "N/A"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.3f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.3f}M"
    if n >= 1_000:
        return f"{n / 1_000:.3f}K"
    return str(n)


def build_datasets(metric_series):
    """Build Chart.js datasets for a single metric."""
    datasets = []
    for key, meta in PROVIDERS.items():
        datasets.append({
            "label": meta["label"],
            "data": metric_series[key],
            "borderColor": meta["color"],
            "backgroundColor": meta["color"] + "20",
            "borderWidth": 2,
            "pointRadius": 4,
            "pointHoverRadius": 6,
            "tension": 0.3,
            "spanGaps": True,
        })
    return datasets


def latest_metric_values(metric_series):
    """Get the latest non-null value for each tracked provider."""
    latest = {}
    for key in PROVIDERS:
        vals = [v for v in metric_series[key] if v is not None]
        latest[key] = vals[-1] if vals else None
    return latest


def generate_html(dates, series):
    """Generate the standalone HTML page."""
    chart_datasets = {metric: build_datasets(metric_series) for metric, metric_series in series.items()}
    latest_total = latest_metric_values(series["total"])
    latest_summary = {
        metric: latest_metric_values(series[metric])
        for metric in ("week", "month", "year")
    }

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cloud Provider Download Trends</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --primary: #06b6d4;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}

        .header {{
            margin-bottom: 10px;
        }}
        .header h1 {{
            font-size: 1.8rem;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .header h1 svg {{ height: 1.2em; }}
        .topnav {{
            display: flex;
            align-items: center;
            gap: 0;
            margin-bottom: 24px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
        }}
        .topnav a {{
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.9rem;
            padding: 12px 20px;
            transition: all 0.2s;
            border-right: 1px solid var(--border);
        }}
        .topnav a:last-child {{ border-right: none; }}
        .topnav a:hover {{ color: var(--text); background: rgba(6, 182, 212, 0.1); }}
        .topnav a.active {{
            color: var(--primary);
            background: rgba(6, 182, 212, 0.1);
            font-weight: 600;
        }}
        .subtitle {{
            color: var(--text-muted);
            margin-bottom: 30px;
            font-size: 0.9rem;
        }}

        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin-bottom: 30px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            text-align: center;
        }}
        .card .value {{
            font-size: 1.5rem;
            font-weight: 700;
        }}
        .card .label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 4px;
        }}
        .card .group {{
            font-size: 0.65rem;
            color: var(--text-muted);
            opacity: 0.7;
        }}

        .note {{
            background: rgba(6, 182, 212, 0.08);
            border: 1px solid rgba(6, 182, 212, 0.25);
            border-radius: 10px;
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-bottom: 24px;
            padding: 14px 16px;
        }}

        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        .chart-section h2 {{
            font-size: 1.2rem;
            margin-bottom: 16px;
            color: var(--text);
        }}
        .chart-wrap {{
            position: relative;
            height: 400px;
        }}
        @media (max-width: 768px) {{
            .chart-wrap {{ height: 300px; }}
        }}

        .toggle-row {{
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 16px;
        }}
        .toggle-row label {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.85rem;
            cursor: pointer;
            color: var(--text-muted);
        }}
        .toggle-row input[type="checkbox"] {{
            accent-color: var(--primary);
        }}

        .table-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            overflow-x: auto;
        }}
        .table-section h2 {{
            font-size: 1.2rem;
            margin-bottom: 16px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }}
        th, td {{
            padding: 10px 12px;
            text-align: right;
            border-bottom: 1px solid var(--border);
        }}
        th {{ color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }}
        td:first-child, th:first-child {{ text-align: left; }}
        tr:hover td {{ background: rgba(6, 182, 212, 0.05); }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>
            <svg viewBox="0 0 64 64" fill="currentColor"><polygon points="22.5,11.2 22.5,33.5 41.9,44.7 41.9,22.3"/><polygon points="44.1,22.3 44.1,44.7 63.5,33.5 63.5,11.2"/><polygon points="0.5,0 0.5,22.3 19.9,33.5 19.9,11.2"/><polygon points="22.5,36.1 22.5,58.4 41.9,69.6 41.9,47.3"/></svg>
            Cloud Provider Download Trends
        </h1>
    </div>
    <nav class="topnav">
        <a href="index.html">All Providers</a>
        <a href="downloads.html" class="active">📈 Download Trends</a>
    </nav>
    <p class="subtitle">
        Cumulative totals plus Terraform Registry week/month/year download summaries
        for AWS, Azure, and GCP Terraform providers
        &middot; {len(dates)} snapshots from {dates[0]} to {dates[-1]}
        &middot; Generated {now}
    </p>

    <div class="cards">
"""

    # Summary cards
    for key, meta in PROVIDERS.items():
        html += f"""        <div class="card">
            <div class="value" style="color:{meta['color']}">{fmt(latest_total[key])}</div>
            <div class="label">{meta['label']}</div>
            <div class="group">{meta['group']} &middot; total</div>
        </div>\n"""

    html += f"""    </div>

    <div class="note">
        Registry week/month/year metrics are stored only in snapshots captured after the
        fetcher started saving <code>download_summary</code>. Older dates may show gaps for
        those charts until new scheduled runs accumulate more history.
    </div>

    <div class="table-section">
        <h2>Latest Registry Summary</h2>
        <table>
            <thead>
                <tr>
                    <th>Provider</th>
                    <th>This Week</th>
                    <th>This Month</th>
                    <th>This Year</th>
                    <th>Total</th>
                </tr>
            </thead>
            <tbody>
"""

    for key, meta in PROVIDERS.items():
        html += f"""                <tr>
                    <td>{meta['label']}</td>
                    <td>{fmt(latest_summary['week'][key])}</td>
                    <td>{fmt(latest_summary['month'][key])}</td>
                    <td>{fmt(latest_summary['year'][key])}</td>
                    <td>{fmt(latest_total[key])}</td>
                </tr>
"""

    html += f"""            </tbody>
        </table>
    </div>

"""

    chart_sections = [
        ("total", "totalChart", "total-toggles"),
        ("week", "weekChart", "week-toggles"),
        ("month", "monthChart", "month-toggles"),
        ("year", "yearChart", "year-toggles"),
    ]
    for metric, chart_id, toggle_id in chart_sections:
        html += f"""    <div class="chart-section">
        <h2>{METRICS[metric]['title']}</h2>
        <p class="subtitle">{METRICS[metric]['subtitle']}</p>
        <div class="toggle-row" id="{toggle_id}"></div>
        <div class="chart-wrap">
            <canvas id="{chart_id}"></canvas>
        </div>
    </div>

"""

    html += f"""

    <!-- Data table -->
    <div class="table-section">
        <h2>Cumulative Totals By Snapshot</h2>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
"""

    for key, meta in PROVIDERS.items():
        html += f"                    <th>{meta['label']}</th>\n"

    html += """                </tr>
            </thead>
            <tbody>
"""

    for i, date in enumerate(dates):
        html += f"                <tr><td>{date}</td>"
        for key in PROVIDERS:
            v = series['total'][key][i]
            html += f"<td>{fmt(v)}</td>"
        html += "</tr>\n"

    html += f"""            </tbody>
        </table>
    </div>
</div>

<script>
const dates = {json.dumps(dates)};
const chartData = {json.dumps(chart_datasets)};

// Number formatting for axes
function fmtAxis(value) {{
    if (value >= 1e9) return (value / 1e9).toFixed(3) + 'B';
    if (value >= 1e6) return (value / 1e6).toFixed(3) + 'M';
    if (value >= 1e3) return (value / 1e3).toFixed(3) + 'K';
    return value;
}}

function fmtTooltip(value) {{
    if (value >= 1e9) return (value / 1e9).toFixed(3) + 'B';
    if (value >= 1e6) return (value / 1e6).toFixed(3) + 'M';
    if (value >= 1e3) return (value / 1e3).toFixed(3) + 'K';
    return value;
}}

const chartDefaults = {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
        legend: {{
            display: true,
            labels: {{ color: '#94a3b8', boxWidth: 12, padding: 16, font: {{ size: 12 }} }}
        }},
        tooltip: {{
            backgroundColor: '#1e293b',
            titleColor: '#e2e8f0',
            bodyColor: '#e2e8f0',
            borderColor: '#334155',
            borderWidth: 1,
            callbacks: {{
                label: function(ctx) {{
                    return ctx.dataset.label + ': ' + fmtTooltip(ctx.parsed.y);
                }}
            }}
        }}
    }},
    scales: {{
        x: {{
            ticks: {{ color: '#94a3b8' }},
            grid: {{ color: '#334155', lineWidth: 0.5 }}
        }},
        y: {{
            ticks: {{
                color: '#94a3b8',
                callback: function(value) {{ return fmtAxis(value); }}
            }},
            grid: {{ color: '#334155', lineWidth: 0.5 }}
        }}
    }}
}};

function createLineChart(canvasId, datasets) {{
    const ctx = document.getElementById(canvasId).getContext('2d');
    return new Chart(ctx, {{
        type: 'line',
        data: {{ labels: dates, datasets }},
        options: chartDefaults,
    }});
}}

const charts = {{
    total: createLineChart('totalChart', chartData.total),
    week: createLineChart('weekChart', chartData.week),
    month: createLineChart('monthChart', chartData.month),
    year: createLineChart('yearChart', chartData.year),
}};

// Build toggle checkboxes for each chart
function buildToggles(chart, containerId) {{
    const container = document.getElementById(containerId);
    chart.data.datasets.forEach((ds, idx) => {{
        const label = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.addEventListener('change', () => {{
            chart.setDatasetVisibility(idx, cb.checked);
            chart.update();
        }});
        label.appendChild(cb);
        label.appendChild(document.createTextNode(' ' + ds.label));
        container.appendChild(label);
    }});
}}

buildToggles(charts.total, 'total-toggles');
buildToggles(charts.week, 'week-toggles');
buildToggles(charts.month, 'month-toggles');
buildToggles(charts.year, 'year-toggles');
</script>
</body>
</html>"""

    return html


def main():
    dates, series = load_snapshots()
    if not dates:
        return

    html = generate_html(dates, series)

    out_path = Path("docs/downloads.html")
    out_path.write_text(html)
    print(f"Generated {out_path} ({len(dates)} snapshots, {len(PROVIDERS)} providers)")
    print(f"Date range: {dates[0]} → {dates[-1]}")


if __name__ == "__main__":
    main()
