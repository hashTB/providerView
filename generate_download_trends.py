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


def load_snapshots():
    """Load all raw snapshots and extract download counts for tracked providers.
    
    Uses a rolling 6-month window from the most recent snapshot.
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
    series = {k: [] for k in PROVIDERS}

    for fp in files:
        date = fp.split("providers_")[1].replace(".json", "")
        with open(fp) as f:
            data = json.load(f)

        providers = data.get("providers", []) if isinstance(data, dict) else data
        lookup = {}
        for p in providers:
            fn = p.get("full_name", "")
            if fn in PROVIDERS:
                lookup[fn] = p.get("downloads", 0)

        dates.append(date)
        for key in PROVIDERS:
            val = lookup.get(key)
            # Use None for missing data points (azurerm missing in some weeks)
            series[key].append(val if val and val > 0 else None)

    return dates, series


def compute_weekly_deltas(dates, series):
    """Compute week-over-week download increments."""
    deltas = {k: [] for k in PROVIDERS}
    for key in PROVIDERS:
        vals = series[key]
        for i in range(len(vals)):
            if i == 0 or vals[i] is None or vals[i - 1] is None:
                deltas[key].append(None)
            else:
                deltas[key].append(vals[i] - vals[i - 1])
    return deltas


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


def generate_html(dates, series, deltas):
    """Generate the standalone HTML page."""
    # Build Chart.js datasets for cumulative
    cumulative_datasets = []
    for key, meta in PROVIDERS.items():
        cumulative_datasets.append({
            "label": meta["label"],
            "data": series[key],
            "borderColor": meta["color"],
            "backgroundColor": meta["color"] + "20",
            "borderWidth": 2,
            "pointRadius": 4,
            "pointHoverRadius": 6,
            "tension": 0.3,
            "spanGaps": True,
        })

    # Build Chart.js datasets for weekly deltas (exclude first point which is always None)
    delta_datasets = []
    for key, meta in PROVIDERS.items():
        delta_datasets.append({
            "label": meta["label"],
            "data": deltas[key],
            "backgroundColor": meta["color"] + "CC",
            "borderColor": meta["color"],
            "borderWidth": 1,
            "borderRadius": 4,
        })

    # Latest values for the summary cards
    latest = {}
    for key, meta in PROVIDERS.items():
        vals = [v for v in series[key] if v is not None]
        latest[key] = vals[-1] if vals else 0

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
        Cumulative &amp; weekly download trends for AWS, Azure, and GCP Terraform providers
        &middot; {len(dates)} snapshots from {dates[0]} to {dates[-1]}
        &middot; Generated {now}
    </p>

    <div class="cards">
"""

    # Summary cards
    for key, meta in PROVIDERS.items():
        html += f"""        <div class="card">
            <div class="value" style="color:{meta['color']}">{fmt(latest[key])}</div>
            <div class="label">{meta['label']}</div>
            <div class="group">{meta['group']}</div>
        </div>\n"""

    html += f"""    </div>

    <!-- Cumulative downloads chart -->
    <div class="chart-section">
        <h2>Cumulative Downloads Over Time</h2>
        <div class="toggle-row" id="cumulative-toggles"></div>
        <div class="chart-wrap">
            <canvas id="cumulativeChart"></canvas>
        </div>
    </div>

    <!-- Weekly delta chart -->
    <div class="chart-section">
        <h2>Weekly Download Increments</h2>
        <div class="toggle-row" id="delta-toggles"></div>
        <div class="chart-wrap">
            <canvas id="deltaChart"></canvas>
        </div>
    </div>

    <!-- Data table -->
    <div class="table-section">
        <h2>Raw Data</h2>
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
            v = series[key][i]
            html += f"<td>{fmt(v)}</td>"
        html += "</tr>\n"

    html += f"""            </tbody>
        </table>
    </div>
</div>

<script>
const dates = {json.dumps(dates)};
const cumulativeData = {json.dumps(cumulative_datasets)};
const deltaData = {json.dumps(delta_datasets)};

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

// Cumulative chart
const cumCtx = document.getElementById('cumulativeChart').getContext('2d');
const cumChart = new Chart(cumCtx, {{
    type: 'line',
    data: {{ labels: dates, datasets: cumulativeData }},
    options: chartDefaults
}});

// Delta chart (bar)
const deltaCtx = document.getElementById('deltaChart').getContext('2d');
const deltaChart = new Chart(deltaCtx, {{
    type: 'bar',
    data: {{ labels: dates, datasets: deltaData }},
    options: {{
        ...chartDefaults,
        plugins: {{
            ...chartDefaults.plugins,
            legend: {{
                ...chartDefaults.plugins.legend,
            }}
        }}
    }}
}});

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

buildToggles(cumChart, 'cumulative-toggles');
buildToggles(deltaChart, 'delta-toggles');
</script>
</body>
</html>"""

    return html


def main():
    dates, series = load_snapshots()
    if not dates:
        return

    deltas = compute_weekly_deltas(dates, series)
    html = generate_html(dates, series, deltas)

    out_path = Path("docs/downloads.html")
    out_path.write_text(html)
    print(f"Generated {out_path} ({len(dates)} snapshots, {len(PROVIDERS)} providers)")
    print(f"Date range: {dates[0]} → {dates[-1]}")


if __name__ == "__main__":
    main()
