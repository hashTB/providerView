#!/usr/bin/env python3
"""
Generate a standalone HTML page showing download trends over time
for major cloud providers, plus monthly consolidation/rate views.

Reads from data/raw/providers_*.json snapshots and produces docs/downloads.html.

Usage:
    python3 generate_download_trends.py
    open docs/downloads.html
"""

import glob
import json
from datetime import datetime, timedelta
from pathlib import Path

# Provider lines for long-running charts
PROVIDERS = {
    "hashicorp/aws": {"label": "AWS", "color": "#FF9900", "group": "AWS"},
    "hashicorp/awscc": {"label": "AWS CC", "color": "#FF6600", "group": "AWS"},
    "hashicorp/azurerm": {"label": "Azure RM", "color": "#0078D4", "group": "Azure"},
    "hashicorp/azuread": {"label": "Azure AD", "color": "#50A0E6", "group": "Azure"},
    "hashicorp/azurestack": {"label": "Azure Stack", "color": "#00BCF2", "group": "Azure"},
    "hashicorp/google": {"label": "Google", "color": "#4285F4", "group": "GCP"},
    "hashicorp/google-beta": {"label": "Google Beta", "color": "#34A853", "group": "GCP"},
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

# Consolidation groups requested by user
CLOUD_FAMILIES = {
    "Azure": {
        "providers": [
            "hashicorp/azurerm",
            "azure/azapi",
            "hashicorp/azuread",
            "microsoft/fabric",
        ],
        "color": "#00BCF2",
    },
    "AWS": {
        "providers": ["hashicorp/aws", "hashicorp/awscc"],
        "color": "#FF9900",
    },
    "GCP": {
        "providers": ["hashicorp/google", "hashicorp/google-beta"],
        "color": "#4285F4",
    },
}

KEY_PROVIDERS = {
    "hashicorp/azurerm": "Azure RM",
    "azure/azapi": "AzAPI",
    "hashicorp/azuread": "Azure AD",
    "microsoft/fabric": "Microsoft Fabric",
    "hashicorp/aws": "AWS",
    "hashicorp/awscc": "AWS CC",
    "hashicorp/google": "Google",
    "hashicorp/google-beta": "Google Beta",
}


def fmt(n):
    if n is None:
        return "N/A"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.3f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.3f}M"
    if n >= 1_000:
        return f"{n / 1_000:.3f}K"
    return str(n)


def fmt_pct(value):
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def pct_change(curr, prev):
    if curr is None or prev is None or prev == 0:
        return None
    return ((curr - prev) / prev) * 100.0


def clean_metric(value):
    """Treat 0/empty summary metrics as missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and value <= 0:
        return None
    return value


def collapse_to_monthly(dates, values):
    """Pick the latest snapshot in each YYYY-MM bucket."""
    monthly = {}
    for date, value in zip(dates, values):
        month = date[:7]
        existing = monthly.get(month)
        if existing is None or date > existing["date"]:
            monthly[month] = {"date": date, "value": value}

    months = sorted(monthly.keys())
    month_values = [monthly[m]["value"] for m in months]
    return months, month_values


def build_monthly_series(dates, monthly_snap_values, total_snap_values):
    """Monthly totals: prefer registry month metric, fallback to cumulative deltas."""
    m_months, m_values = collapse_to_monthly(dates, monthly_snap_values)
    t_months, t_values = collapse_to_monthly(dates, total_snap_values)

    month_to_metric = dict(zip(m_months, m_values))
    month_to_total = dict(zip(t_months, t_values))
    months = sorted(set(m_months) | set(t_months))

    values = []
    for idx, month in enumerate(months):
        explicit = month_to_metric.get(month)
        if explicit is not None:
            values.append(explicit)
            continue

        # Fallback: derive monthly increment from cumulative totals.
        curr_total = month_to_total.get(month)
        prev_total = month_to_total.get(months[idx - 1]) if idx > 0 else None
        if curr_total is not None and prev_total is not None:
            values.append(max(curr_total - prev_total, 0))
        else:
            values.append(None)

    return months, values


def filter_completed_months(months, values):
    """Keep only fully completed months by excluding current YYYY-MM bucket."""
    current_month = datetime.now().strftime("%Y-%m")
    filtered = [(m, v) for m, v in zip(months, values) if m < current_month]
    if not filtered:
        # If only current-month data exists, keep original data instead of empty output.
        return months, values
    return [m for m, _ in filtered], [v for _, v in filtered]


def load_snapshots():
    files = sorted(glob.glob("data/raw/providers_[0-9][0-9][0-9][0-9]-*.json"))
    if not files:
        print("No snapshot files found in data/raw/")
        return None

    latest_date = files[-1].split("providers_")[1].replace(".json", "")
    cutoff = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=183)).strftime("%Y-%m-%d")
    files = [f for f in files if f.split("providers_")[1].replace(".json", "") >= cutoff]

    dates = []
    series = {metric: {provider: [] for provider in PROVIDERS} for metric in METRICS}
    family_snapshot_month = {family: [] for family in CLOUD_FAMILIES}
    family_snapshot_total = {family: [] for family in CLOUD_FAMILIES}
    key_snapshot_month = {provider: [] for provider in KEY_PROVIDERS}
    key_snapshot_total = {provider: [] for provider in KEY_PROVIDERS}

    for fp in files:
        date = fp.split("providers_")[1].replace(".json", "")
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)

        providers = data.get("providers", []) if isinstance(data, dict) else data
        by_name = {}
        for p in providers:
            full_name = p.get("full_name", "")
            if not full_name:
                continue
            summary = p.get("download_summary", {}) or {}
            total = summary.get("total", p.get("downloads", 0))
            by_name[full_name.lower()] = {
                "total": clean_metric(total),
                "week": clean_metric(summary.get("week")),
                "month": clean_metric(summary.get("month")),
                "year": clean_metric(summary.get("year")),
            }

        # Existing line-chart providers
        dates.append(date)
        for provider in PROVIDERS:
            values = by_name.get(provider.lower(), {})
            for metric in METRICS:
                series[metric][provider].append(values.get(metric))

        # Family consolidations (monthly metric)
        for family, meta in CLOUD_FAMILIES.items():
            month_vals = []
            total_vals = []
            for provider in meta["providers"]:
                provider_values = by_name.get(provider.lower(), {})
                month_val = provider_values.get("month")
                total_val = provider_values.get("total")
                if month_val is not None:
                    month_vals.append(month_val)
                if total_val is not None:
                    total_vals.append(total_val)
            family_snapshot_month[family].append(sum(month_vals) if month_vals else None)
            family_snapshot_total[family].append(sum(total_vals) if total_vals else None)

        # Key providers (monthly metric)
        for provider in KEY_PROVIDERS:
            provider_values = by_name.get(provider.lower(), {})
            key_snapshot_month[provider].append(provider_values.get("month"))
            key_snapshot_total[provider].append(provider_values.get("total"))

    # Collapse snapshot series to month buckets
    family_monthly = {}
    for family in family_snapshot_month:
        months, month_values = build_monthly_series(
            dates,
            family_snapshot_month[family],
            family_snapshot_total[family],
        )
        family_monthly[family] = {"months": months, "values": month_values}

    key_monthly = {}
    for provider in key_snapshot_month:
        months, month_values = build_monthly_series(
            dates,
            key_snapshot_month[provider],
            key_snapshot_total[provider],
        )
        key_monthly[provider] = {"months": months, "values": month_values}

    return {
        "dates": dates,
        "series": series,
        "family_monthly": family_monthly,
        "key_monthly": key_monthly,
    }


def build_datasets(metric_series):
    datasets = []
    for key, meta in PROVIDERS.items():
        datasets.append(
            {
                "label": meta["label"],
                "data": metric_series[key],
                "borderColor": meta["color"],
                "backgroundColor": meta["color"] + "20",
                "borderWidth": 2,
                "pointRadius": 4,
                "pointHoverRadius": 6,
                "tension": 0.3,
                "spanGaps": True,
            }
        )
    return datasets


def latest_metric_values(metric_series):
    latest = {}
    for key in PROVIDERS:
        vals = [v for v in metric_series[key] if v is not None]
        latest[key] = vals[-1] if vals else None
    return latest


def compute_rate_summary(months, values):
    if not values:
        return {
            "latest_month": None,
            "latest_value": None,
            "mom": None,
            "qoq": None,
            "yoy": None,
        }

    latest_idx = len(values) - 1
    latest_value = values[latest_idx]
    prev_1 = values[latest_idx - 1] if latest_idx >= 1 else None
    prev_3 = values[latest_idx - 3] if latest_idx >= 3 else None
    prev_12 = values[latest_idx - 12] if latest_idx >= 12 else None

    return {
        "latest_month": months[latest_idx] if months else None,
        "latest_value": latest_value,
        "mom": pct_change(latest_value, prev_1),
        "qoq": pct_change(latest_value, prev_3),
        "yoy": pct_change(latest_value, prev_12),
    }


def generate_html(data):
    dates = data["dates"]
    series = data["series"]
    family_monthly = data["family_monthly"]
    key_monthly = data["key_monthly"]

    completed_family_monthly = {}
    for family, values in family_monthly.items():
        months, month_values = filter_completed_months(values["months"], values["values"])
        completed_family_monthly[family] = {"months": months, "values": month_values}

    completed_key_monthly = {}
    for provider, values in key_monthly.items():
        months, month_values = filter_completed_months(values["months"], values["values"])
        completed_key_monthly[provider] = {"months": months, "values": month_values}

    chart_datasets = {metric: build_datasets(metric_series) for metric, metric_series in series.items()}
    latest_total = latest_metric_values(series["total"])
    latest_summary = {metric: latest_metric_values(series[metric]) for metric in ("week", "month", "year")}

    family_rates = {
        family: compute_rate_summary(v["months"], v["values"])
        for family, v in completed_family_monthly.items()
    }
    key_rates = {
        provider: compute_rate_summary(v["months"], v["values"])
        for provider, v in completed_key_monthly.items()
    }

    # Build consolidated family chart on month buckets
    union_months = sorted({m for fam in completed_family_monthly.values() for m in fam["months"]})
    family_chart_datasets = []
    for family, meta in CLOUD_FAMILIES.items():
        month_to_value = dict(
            zip(
                completed_family_monthly[family]["months"],
                completed_family_monthly[family]["values"],
            )
        )
        family_chart_datasets.append(
            {
                "label": family,
                "data": [month_to_value.get(m) for m in union_months],
                "borderColor": meta["color"],
                "backgroundColor": meta["color"] + "20",
                "borderWidth": 3,
                "pointRadius": 4,
                "spanGaps": True,
                "tension": 0.25,
            }
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Cloud Provider Download Trends</title>
    <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
    <style>
        :root {{
            --primary: #06b6d4;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --border: #334155;
            --good: #22c55e;
            --warn: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1320px; margin: 0 auto; }}
        .header {{ margin-bottom: 10px; }}
        .header h1 {{ font-size: 1.8rem; display: flex; align-items: center; gap: 10px; }}
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
        .subtitle {{ color: var(--text-muted); margin-bottom: 24px; font-size: 0.9rem; }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            text-align: center;
        }}
        .card .value {{ font-size: 1.5rem; font-weight: 700; }}
        .card .label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 4px;
        }}
        .card .group {{ font-size: 0.65rem; color: var(--text-muted); opacity: 0.7; }}
        .note {{
            background: rgba(6, 182, 212, 0.08);
            border: 1px solid rgba(6, 182, 212, 0.25);
            border-radius: 10px;
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-bottom: 24px;
            padding: 14px 16px;
        }}
        .section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            overflow-x: auto;
        }}
        .section h2 {{ font-size: 1.2rem; margin-bottom: 8px; }}
        .section .sub {{ color: var(--text-muted); font-size: 0.85rem; margin-bottom: 14px; }}
        .chart-wrap {{ position: relative; height: 380px; }}
        @media (max-width: 768px) {{ .chart-wrap {{ height: 300px; }} }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid var(--border); }}
        th {{ color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }}
        td:first-child, th:first-child {{ text-align: left; }}
        tr:hover td {{ background: rgba(6, 182, 212, 0.05); }}
        .pct-pos {{ color: var(--good); }}
        .pct-neg {{ color: #f97316; }}
        .pct-na {{ color: var(--text-muted); }}
        .toggle-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
        .toggle-row label {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.85rem;
            cursor: pointer;
            color: var(--text-muted);
        }}
        .toggle-row input[type=\"checkbox\"] {{ accent-color: var(--primary); }}
    </style>
</head>
<body>
<div class=\"container\">
    <div class=\"header\">
        <h1>
            <svg viewBox=\"0 0 64 64\" fill=\"currentColor\"><polygon points=\"22.5,11.2 22.5,33.5 41.9,44.7 41.9,22.3\"/><polygon points=\"44.1,22.3 44.1,44.7 63.5,33.5 63.5,11.2\"/><polygon points=\"0.5,0 0.5,22.3 19.9,33.5 19.9,11.2\"/><polygon points=\"22.5,36.1 22.5,58.4 41.9,69.6 41.9,47.3\"/></svg>
            Cloud Provider Download Trends
        </h1>
    </div>
    <nav class=\"topnav\">
        <a href=\"index.html\">All Providers</a>
                    <a href=\"downloads.html\" class=\"active\">📈 Download Trends</a>
                    <a href=\"cloud-devex.html\">Cloud DevEx</a>
        <a href=\"azurerm-list-check.html\">✅ AzureRM List Check</a>
    </nav>
    <p class=\"subtitle\">
        Consolidated monthly totals and rates for Azure/AWS/GCP, plus key providers
        &middot; {len(dates)} snapshots from {dates[0]} to {dates[-1]}
        &middot; Generated {now}
    </p>

    <div class=\"cards\">
"""

    for key, meta in PROVIDERS.items():
        html += f"""        <div class=\"card\">
            <div class=\"value\" style=\"color:{meta['color']}\">{fmt(latest_total[key])}</div>
            <div class=\"label\">{meta['label']}</div>
            <div class=\"group\">{meta['group']} &middot; total</div>
        </div>\n"""

    html += """    </div>

    <div class=\"note\">
        Monthly comparisons below use month buckets and take the latest snapshot within each month.
        Monthly trend tables and cloud comparison chart use latest completed month (current partial month excluded).
        MoM/QoQ/YoY are computed from the Registry monthly metric when available.
        YoY requires at least 13 months of monthly points, so it may show N/A for now.
    </div>

    <div class=\"section\">
        <h2>Cloud Family Monthly Consolidation</h2>
        <div class=\"sub\">Azure includes AzureRM, AzAPI, AzureAD, Microsoft Fabric. Compare against consolidated AWS and GCP.</div>
        <table>
            <thead>
                <tr>
                    <th>Family</th>
                    <th>Latest Month</th>
                    <th>Monthly Total</th>
                    <th>MoM</th>
                    <th>QoQ</th>
                    <th>YoY</th>
                    <th>Providers Included</th>
                </tr>
            </thead>
            <tbody>
"""

    for family, stats in family_rates.items():
        def pct_cell(v):
            if v is None:
                return '<span class="pct-na">N/A</span>'
            cls = "pct-pos" if v >= 0 else "pct-neg"
            return f'<span class="{cls}">{fmt_pct(v)}</span>'

        included = ", ".join(CLOUD_FAMILIES[family]["providers"])
        html += (
            "                <tr>"
            f"<td>{family}</td>"
            f"<td>{stats['latest_month'] or 'N/A'}</td>"
            f"<td>{fmt(stats['latest_value'])}</td>"
            f"<td>{pct_cell(stats['mom'])}</td>"
            f"<td>{pct_cell(stats['qoq'])}</td>"
            f"<td>{pct_cell(stats['yoy'])}</td>"
            f"<td>{included}</td>"
            "</tr>\n"
        )

    html += """            </tbody>
        </table>
    </div>

    <div class=\"section\">
        <h2>Consolidated Cloud Monthly Trend</h2>
        <div class=\"sub\">Month-end bucketed totals for Azure vs AWS vs GCP.</div>
        <div class=\"toggle-row\" id=\"family-toggles\"></div>
        <div class=\"chart-wrap\"><canvas id=\"familyMonthlyChart\"></canvas></div>
    </div>

    <div class=\"section\">
        <h2>Key Provider Monthly Totals and Rates</h2>
        <div class=\"sub\">Use this to inspect one provider at a time (for example AzureRM, AzAPI, AWS, Google).</div>
        <table>
            <thead>
                <tr>
                    <th>Provider</th>
                    <th>Latest Month</th>
                    <th>Monthly Total</th>
                    <th>MoM</th>
                    <th>QoQ</th>
                    <th>YoY</th>
                </tr>
            </thead>
            <tbody>
"""

    for provider, label in KEY_PROVIDERS.items():
        stats = key_rates[provider]

        def pct_cell(v):
            if v is None:
                return '<span class="pct-na">N/A</span>'
            cls = "pct-pos" if v >= 0 else "pct-neg"
            return f'<span class="{cls}">{fmt_pct(v)}</span>'

        html += (
            "                <tr>"
            f"<td>{label} <span class=\"pct-na\">({provider})</span></td>"
            f"<td>{stats['latest_month'] or 'N/A'}</td>"
            f"<td>{fmt(stats['latest_value'])}</td>"
            f"<td>{pct_cell(stats['mom'])}</td>"
            f"<td>{pct_cell(stats['qoq'])}</td>"
            f"<td>{pct_cell(stats['yoy'])}</td>"
            "</tr>\n"
        )

    html += """            </tbody>
        </table>
    </div>

    <div class=\"section\">
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

    html += """            </tbody>
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
        html += f"""    <div class=\"section\">
        <h2>{METRICS[metric]['title']}</h2>
        <div class=\"sub\">{METRICS[metric]['subtitle']}</div>
        <div class=\"toggle-row\" id=\"{toggle_id}\"></div>
        <div class=\"chart-wrap\"><canvas id=\"{chart_id}\"></canvas></div>
    </div>

"""

    html += f"""
</div>

<script>
const dates = {json.dumps(dates)};
const chartData = {json.dumps({"series": chart_datasets, "familyMonths": union_months, "familyDatasets": family_chart_datasets})};

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

function createLineChart(canvasId, labels, datasets) {{
    const ctx = document.getElementById(canvasId).getContext('2d');
    return new Chart(ctx, {{
        type: 'line',
        data: {{ labels, datasets }},
        options: chartDefaults,
    }});
}}

const charts = {{
    total: createLineChart('totalChart', dates, chartData.series.total),
    week: createLineChart('weekChart', dates, chartData.series.week),
    month: createLineChart('monthChart', dates, chartData.series.month),
    year: createLineChart('yearChart', dates, chartData.series.year),
    family: createLineChart('familyMonthlyChart', chartData.familyMonths, chartData.familyDatasets),
}};

function buildToggles(chart, containerId) {{
    const container = document.getElementById(containerId);
    if (!container) return;
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

buildToggles(charts.family, 'family-toggles');
buildToggles(charts.total, 'total-toggles');
buildToggles(charts.week, 'week-toggles');
buildToggles(charts.month, 'month-toggles');
buildToggles(charts.year, 'year-toggles');
</script>
</body>
</html>
"""

    return html


def main():
    data = load_snapshots()
    if not data:
        return

    html = generate_html(data)

    out_path = Path("docs/downloads.html")
    out_path.write_text(html, encoding="utf-8")

    print(f"Generated {out_path} ({len(data['dates'])} snapshots, {len(PROVIDERS)} providers)")
    print(f"Date range: {data['dates'][0]} -> {data['dates'][-1]}")


if __name__ == "__main__":
    main()
