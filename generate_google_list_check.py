#!/usr/bin/env python3
"""
Inspect terraform-provider-google source code to validate List Resources.

Mirrors the AzureRM/AWS list-check approach but uses pure source-code
inspection (no upstream script/gist exists yet for GCP). It looks at three
converging signals inside the cloned provider repo:

1. Registration site:
   ``google/fwprovider/framework_provider_mmv1_resources.go`` declares
   ``generatedListResources`` and ``handwrittenListResources`` slices that
   feed ``FrameworkProvider.ListResources()``. We parse the
   ``listResourceFunc(<pkg>.New<Name>ListResource())`` entries.
2. Per-service files: ``google/services/*/list_*.go`` (excluding tests).
3. ``TypeName = "google_..."`` assignments inside the per-service files —
   the canonical resource type names that line up with Registry docs.

The three signals must agree. The result is also cross-checked against the
Registry-reflected ``list-resources`` count from
``terraform_providers_details.json``.

Outputs:
- ``data/google_list_check.json`` – compact summary for the dashboard
- ``docs/google-list-check.html`` – standalone report page

Usage::

    python3 generate_google_list_check.py
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

USER_AGENT = "providerView-google-list-check/1.0"
RAW_OUTPUT_JSON = "data/google_list_check.json"
REPORT_HTML = "docs/google-list-check.html"
DETAILS_FILE = "terraform_providers_details.json"

PROVIDER_REPO_URL = "https://github.com/hashicorp/terraform-provider-google.git"
PROVIDER_REPO_DIR_NAME = "hashicorp_terraform-provider-google"
DASHBOARD_PROVIDER = "hashicorp/google"

# Path within the provider repo to the framework registration file.
REGISTRATION_FILE_REL = Path("google/fwprovider/framework_provider_mmv1_resources.go")
SERVICES_DIR_REL = Path("google/services")
MMV1_SOURCE_NOTE = (
    "Magic Modules generator emits these registrations into the provider repo. "
    "Source-of-truth lives in GoogleCloudPlatform/magic-modules under "
    "mmv1/third_party/terraform/fwprovider/framework_provider_mmv1_resources.go."
)


# ---------------------------------------------------------------------------
# Repo management
# ---------------------------------------------------------------------------

def clone_or_update_repo(repos_dir: Path) -> Path | None:
    repo_dir = repos_dir / PROVIDER_REPO_DIR_NAME

    if repo_dir.exists():
        print("  Updating terraform-provider-google...")
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Warning: git pull failed: {result.stderr}")
    else:
        print("  Cloning terraform-provider-google...")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", PROVIDER_REPO_URL, str(repo_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Error: git clone failed: {result.stderr}")
            return None
    return repo_dir


def get_repo_commit(repo_dir: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Source inspection
# ---------------------------------------------------------------------------

# Matches: listResourceFunc(<pkg>.New<Name>ListResource())
REGISTRATION_LINE_RE = re.compile(
    r"listResourceFunc\(\s*([a-zA-Z0-9_]+)\s*\.\s*(New[A-Za-z0-9_]+ListResource)\s*\(\s*\)\s*\)"
)
# Matches the slice headers so we can attribute each registration to its bucket.
SLICE_HEADER_RE = re.compile(
    r"var\s+(generatedListResources|handwrittenListResources)\s*="
)
# Matches assignments like: listR.TypeName = "google_xxx"
TYPE_NAME_RE = re.compile(
    r'(?:listR|l|r)\s*\.\s*TypeName\s*=\s*"(google_[a-z0-9_]+)"'
)


def parse_registrations(reg_text: str) -> dict:
    """Parse the framework_provider_mmv1_resources.go file.

    Returns::

        {
            "generated":   [{"package": ..., "constructor": ..., "line": N}, ...],
            "handwritten": [{"package": ..., "constructor": ..., "line": N}, ...],
        }
    """
    buckets: dict[str, list[dict]] = {"generated": [], "handwritten": []}
    current = None
    for lineno, line in enumerate(reg_text.splitlines(), start=1):
        header = SLICE_HEADER_RE.search(line)
        if header:
            current = "generated" if header.group(1) == "generatedListResources" else "handwritten"
            continue
        if current is None:
            continue
        # Closing brace of the slice literal ends the current bucket scope.
        stripped = line.strip()
        if stripped.startswith("}"):
            current = None
            continue
        match = REGISTRATION_LINE_RE.search(line)
        if match:
            buckets[current].append({
                "package": match.group(1),
                "constructor": match.group(2),
                "line": lineno,
            })
    return buckets


def scan_service_list_files(services_dir: Path) -> dict[str, list[dict]]:
    """Walk google/services/*/list_*.go and pull TypeName + service.

    Returns: ``{ service_name: [{"file": rel, "type_name": str, "constructor": str|None}] }``
    """
    by_service: dict[str, list[dict]] = {}
    if not services_dir.exists():
        return by_service

    for path in sorted(services_dir.glob("*/list_*.go")):
        if path.name.endswith("_test.go"):
            continue
        service = path.parent.name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        type_names = sorted(set(TYPE_NAME_RE.findall(text)))
        constructor_match = re.search(
            r"func\s+(New[A-Za-z0-9_]+ListResource)\s*\(", text
        )
        constructor = constructor_match.group(1) if constructor_match else None

        if not type_names:
            # File found but no TypeName extractable — record as anomaly.
            by_service.setdefault(service, []).append({
                "file": str(path.relative_to(services_dir.parent.parent)),
                "type_name": None,
                "constructor": constructor,
            })
            continue

        for type_name in type_names:
            by_service.setdefault(service, []).append({
                "file": str(path.relative_to(services_dir.parent.parent)),
                "type_name": type_name,
                "constructor": constructor,
            })

    return by_service


def cross_check(registrations: dict, by_service: dict[str, list[dict]]) -> dict:
    """Confirm registration constructors map to service files and TypeNames."""
    registered_constructors = {
        entry["constructor"] for bucket in registrations.values() for entry in bucket
    }
    file_constructors = {
        item["constructor"]
        for items in by_service.values()
        for item in items
        if item.get("constructor")
    }

    file_type_names = sorted({
        item["type_name"]
        for items in by_service.values()
        for item in items
        if item.get("type_name")
    })

    missing_files = sorted(registered_constructors - file_constructors)
    orphan_files = sorted(file_constructors - registered_constructors)

    return {
        "registered_constructors": sorted(registered_constructors),
        "file_constructors": sorted(file_constructors),
        "file_type_names": file_type_names,
        "missing_files_for_registration": missing_files,
        "orphan_files_without_registration": orphan_files,
        "registration_count": len(registered_constructors),
        "file_type_name_count": len(file_type_names),
        "signals_agree": (
            len(missing_files) == 0
            and len(orphan_files) == 0
            and len(registered_constructors) == len(file_type_names)
        ),
    }


# ---------------------------------------------------------------------------
# Dashboard correlation
# ---------------------------------------------------------------------------

def load_dashboard_list_count(details_path: Path) -> int | None:
    try:
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    except FileNotFoundError:
        return None

    entry = details.get(DASHBOARD_PROVIDER, {})
    docs = entry.get("docs", {}) if isinstance(entry, dict) else {}
    list_resources = docs.get("list-resources", []) if isinstance(docs, dict) else []
    if isinstance(list_resources, list):
        return len(list_resources)
    return None


# ---------------------------------------------------------------------------
# Summary build
# ---------------------------------------------------------------------------

def build_summary(
    repo_dir: Path,
    reg_text: str,
    registrations: dict,
    by_service: dict[str, list[dict]],
    cross: dict,
    details_path: Path,
) -> dict:
    dashboard_count = load_dashboard_list_count(details_path)
    code_count = cross["file_type_name_count"]
    matches = (
        dashboard_count is not None
        and dashboard_count == code_count
    )

    service_rows = []
    service_resource_map: dict[str, dict[str, list[str]]] = {}
    for service, items in by_service.items():
        type_names = sorted({i["type_name"] for i in items if i.get("type_name")})
        if not type_names:
            continue
        service_rows.append({
            "service": service,
            "list_resources": len(type_names),
        })
        service_resource_map[service] = {
            "list_resources": type_names,
        }
    service_rows.sort(key=lambda row: (-row["list_resources"], row["service"]))

    reg_sha = hashlib.sha256(reg_text.encode("utf-8")).hexdigest()

    return {
        "generated_at": datetime.now().isoformat(),
        "source_repo": {
            "repo": "hashicorp/terraform-provider-google",
            "url": PROVIDER_REPO_URL,
            "commit": get_repo_commit(repo_dir),
        },
        "registration_file": {
            "path": str(REGISTRATION_FILE_REL),
            "sha256": reg_sha,
            "note": MMV1_SOURCE_NOTE,
        },
        "dashboard": {
            "provider": DASHBOARD_PROVIDER,
            "list_resources": dashboard_count,
        },
        "registrations": registrations,
        "by_service": by_service,
        "cross_check": cross,
        "service_coverage": {
            "services_with_list": len(service_rows),
            "rows": service_rows,
            "service_resources": service_resource_map,
        },
        "validation": {
            "matches": matches,
            "dashboard_list_resources": dashboard_count,
            "code_list_resources": code_count,
            "registered_count": cross["registration_count"],
            "signals_agree": cross["signals_agree"],
        },
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def fmt(value: int | None) -> str:
    return "N/A" if value is None else f"{value:,}"


def render_registration_block(registrations: dict) -> str:
    def render_bucket(name: str, label: str) -> str:
        items = registrations.get(name, [])
        if not items:
            return (
                f"<div class=\"reg-bucket\"><h3>{html.escape(label)}</h3>"
                f"<p class=\"resource-empty\">No entries.</p></div>"
            )
        rows = "\n".join(
            f"<li><code>{html.escape(it['package'])}.{html.escape(it['constructor'])}()</code>"
            f" <span class=\"muted\">line {it['line']}</span></li>"
            for it in items
        )
        return (
            f"<div class=\"reg-bucket\"><h3>{html.escape(label)} ({len(items)})</h3>"
            f"<ul class=\"resource-list\">{rows}</ul></div>"
        )

    return (
        "<div class=\"resource-columns\">"
        + render_bucket("handwritten", "Handwritten Registrations")
        + render_bucket("generated", "Magic-Modules Generated Registrations")
        + "</div>"
    )


def generate_report(summary: dict, out_path: Path) -> None:
    validation = summary["validation"]
    cross = summary["cross_check"]
    repo = summary["source_repo"]
    reg_file = summary["registration_file"]
    coverage = summary.get("service_coverage", {})

    match_icon = "✅" if validation["matches"] else "⚠️"
    match_text = "match" if validation["matches"] else "do not match"
    signals_icon = "✅" if validation["signals_agree"] else "⚠️"

    rows = coverage.get("rows", [])
    service_resources_js = json.dumps(coverage.get("service_resources", {}), ensure_ascii=False)
    if rows:
        table_rows = "\n".join(
            (
                "<tr>"
                f"<td><button class=\"service-link\" data-service=\"{html.escape(row['service'])}\">{html.escape(row['service'])}</button></td>"
                f"<td>{fmt(row['list_resources'])}</td>"
                "</tr>"
            )
            for row in rows
        )
    else:
        table_rows = '<tr><td colspan="2" style="color: var(--text-muted);">No list resources detected in source.</td></tr>'

    type_names_block = "\n".join(
        f"<li><code>{html.escape(name)}</code></li>"
        for name in cross.get("file_type_names", [])
    ) or '<li class="resource-empty">None.</li>'

    anomalies = []
    for c in cross.get("missing_files_for_registration", []):
        anomalies.append(
            f"<li>Constructor <code>{html.escape(c)}</code> is registered but no matching <code>list_*.go</code> file was found.</li>"
        )
    for c in cross.get("orphan_files_without_registration", []):
        anomalies.append(
            f"<li>Constructor <code>{html.escape(c)}</code> exists in a <code>list_*.go</code> file but is not registered.</li>"
        )
    anomalies_block = (
        "<ul class=\"resource-list\">" + "\n".join(anomalies) + "</ul>"
        if anomalies
        else '<p class="resource-empty">No anomalies — registrations and source files agree.</p>'
    )

    registration_block = render_registration_block(summary.get("registrations", {}))

    html_text = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Google List Validation</title>
    <style>
        :root {{
            --primary: #06b6d4;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --border: #334155;
            --success: #22c55e;
            --warning: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1280px; margin: 0 auto; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 10px; }}
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
        .topnav a.active {{ color: var(--primary); background: rgba(6, 182, 212, 0.1); font-weight: 600; }}
        .subtitle {{ color: var(--text-muted); margin-bottom: 24px; }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }}
        .value {{ font-size: 1.6rem; font-weight: 700; }}
        .label {{ font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 4px; }}
        .note, .section {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
        .note {{ color: var(--text-muted); }}
        .meta-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 14px; }}
        .meta-row {{ display: flex; justify-content: space-between; gap: 12px; font-size: 0.9rem; color: var(--text-muted); margin-bottom: 6px; }}
        .meta-row span:last-child {{ color: var(--text); text-align: right; }}
        .section h2 {{ margin-bottom: 12px; font-size: 1.2rem; }}
        pre {{
            background: #111827;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            white-space: pre-wrap;
            word-break: break-word;
            overflow-x: auto;
            color: #dbeafe;
            font-size: 0.84rem;
        }}
        code {{ color: #93c5fd; }}
        a {{ color: var(--primary); }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ border-bottom: 1px solid var(--border); padding: 10px 8px; text-align: left; font-size: 0.9rem; }}
        th {{ color: var(--text-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }}
        .table-controls {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; margin-bottom: 8px; }}
        .table-controls input, .table-controls select {{
            background: #111827;
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text);
            padding: 8px 10px;
            font-size: 0.9rem;
        }}
        .table-controls input {{ min-width: 240px; }}
        .service-link {{
            background: none; border: none; color: #93c5fd; cursor: pointer;
            padding: 0; font: inherit; text-decoration: underline;
        }}
        .service-link:hover {{ color: #bfdbfe; }}
        .modal {{
            position: fixed; inset: 0; background: rgba(2, 6, 23, 0.7);
            display: none; align-items: center; justify-content: center;
            padding: 16px; z-index: 999;
        }}
        .modal.open {{ display: flex; }}
        .modal-card {{
            width: min(720px, 100%); max-height: 82vh; overflow: auto;
            background: #0b1220; border: 1px solid var(--border);
            border-radius: 12px; padding: 18px;
        }}
        .modal-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 10px; }}
        .modal-title {{ font-size: 1.05rem; font-weight: 700; }}
        .modal-close {{
            background: #111827; border: 1px solid var(--border);
            color: var(--text); border-radius: 8px; padding: 6px 10px; cursor: pointer;
        }}
        .resource-columns {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
        .resource-box, .reg-bucket {{ background: #111827; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }}
        .reg-bucket h3, .resource-box h3 {{ font-size: 0.9rem; margin-bottom: 8px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }}
        .resource-list {{ list-style: none; margin: 0; padding: 0; max-height: 52vh; overflow: auto; }}
        .resource-list li {{ padding: 4px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.15); font-size: 0.85rem; }}
        .resource-list li:last-child {{ border-bottom: none; }}
        .resource-empty {{ color: var(--text-muted); font-size: 0.85rem; }}
        .muted {{ color: var(--text-muted); font-size: 0.78rem; margin-left: 6px; }}
    </style>
</head>
<body>
<div class=\"container\">
    <h1>Google List Validation</h1>
    <nav class=\"topnav\">
        <a href=\"index.html\">All Providers</a>
        <a href=\"downloads.html\">📈 Download Trends</a>
        <a href=\"cloud-devex.html\">Cloud DevEx</a>
        <a href=\"azurerm-list-check.html\">AzureRM List Check</a>
        <a href=\"aws-list-check.html\">AWS List Check</a>
        <a href=\"google-list-check.html\" class=\"active\">Google List Check</a>
    </nav>
    <p class=\"subtitle\">Source-code inspection of <code>terraform-provider-google</code> compared against Registry-reflected list resources for <code>{html.escape(DASHBOARD_PROVIDER)}</code>. Generated {html.escape(summary['generated_at'])}.</p>

    <div class=\"cards\">
        <div class=\"card\"><div class=\"value\">{fmt(validation['dashboard_list_resources'])}</div><div class=\"label\">Registry-Reflected List Resources</div></div>
        <div class=\"card\"><div class=\"value\">{fmt(validation['code_list_resources'])}</div><div class=\"label\">Source TypeNames</div></div>
        <div class=\"card\"><div class=\"value\">{fmt(validation['registered_count'])}</div><div class=\"label\">Registered Constructors</div></div>
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Counts {match_text}</div></div>
        <div class=\"card\"><div class=\"value\">{signals_icon}</div><div class=\"label\">Source Signals Agree</div></div>
    </div>

    <div class=\"note\">
        <strong>{match_icon} Validation:</strong> Registry reflects
        <strong>{fmt(validation['dashboard_list_resources'])}</strong>;
        source inspection finds <strong>{fmt(validation['code_list_resources'])}</strong>
        TypeName(s) across <strong>{fmt(validation['registered_count'])}</strong> registered constructor(s).
        Unlike AWS/Azure (which have established lists of expected resources to track gaps), GCP is
        in the early phase of adopting List Resources — the report focuses on
        <em>what is implemented today</em>.
        <div class=\"meta-grid\">
            <div>
                <div class=\"meta-row\"><span>Provider repo</span><span><a href=\"{html.escape(repo['url'])}\">{html.escape(repo['repo'])}</a></span></div>
                <div class=\"meta-row\"><span>Repo commit</span><span><code>{html.escape(repo['commit'] or 'unknown')}</code></span></div>
                <div class=\"meta-row\"><span>Registration file</span><span><code>{html.escape(reg_file['path'])}</code></span></div>
                <div class=\"meta-row\"><span>Reg-file SHA-256</span><span><code>{html.escape(reg_file['sha256'][:16])}...</code></span></div>
            </div>
            <div>
                <div class=\"meta-row\"><span>Dashboard provider</span><span><code>{html.escape(DASHBOARD_PROVIDER)}</code></span></div>
                <div class=\"meta-row\"><span>Magic Modules note</span><span style=\"text-align:left;\">{html.escape(reg_file['note'])}</span></div>
            </div>
        </div>
    </div>

    <div class=\"section\">
        <h2>Service Coverage (List)</h2>
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">Services exposing at least one List Resource: <strong>{fmt(coverage.get('services_with_list'))}</strong>.</p>
        <div class=\"table-controls\">
            <input id=\"gc-service-search\" type=\"text\" placeholder=\"Search by service (e.g. compute, iam, storage)\" />
            <select id=\"gc-service-sort\">
                <option value=\"list_desc\" selected>Sort: List Resources (high to low)</option>
                <option value=\"service_asc\">Sort: Service (A-Z)</option>
                <option value=\"service_desc\">Sort: Service (Z-A)</option>
            </select>
        </div>
        <table id=\"gc-coverage-table\">
            <thead>
                <tr><th>Service</th><th>List Resources</th></tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>

    <div class=\"section\">
        <h2>Source Registrations</h2>
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">Parsed from <code>{html.escape(str(REGISTRATION_FILE_REL))}</code>.</p>
        {registration_block}
    </div>

    <div class=\"section\">
        <h2>Discovered TypeNames</h2>
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">From <code>TypeName = "google_..."</code> assignments inside <code>list_*.go</code> files.</p>
        <ul class=\"resource-list\">{type_names_block}</ul>
    </div>

    <div class=\"section\">
        <h2>Cross-Check Anomalies</h2>
        {anomalies_block}
    </div>
</div>
<div class=\"modal\" id=\"service-modal\" aria-hidden=\"true\">
    <div class=\"modal-card\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"service-modal-title\">
        <div class=\"modal-head\">
            <div class=\"modal-title\" id=\"service-modal-title\">Service Resources</div>
            <button class=\"modal-close\" id=\"service-modal-close\" type=\"button\">Close</button>
        </div>
        <div class=\"resource-box\">
            <h3>List-Enabled Resources</h3>
            <ul class=\"resource-list\" id=\"service-list-list\"></ul>
            <p class=\"resource-empty\" id=\"service-list-empty\" style=\"display:none;\">No list-enabled resources in this service.</p>
        </div>
    </div>
</div>
<script>
    const serviceResources = {service_resources_js};

    function fillResourceList(listElement, emptyElement, values) {{
        listElement.innerHTML = '';
        const items = Array.isArray(values) ? values : [];
        if (!items.length) {{ emptyElement.style.display = 'block'; return; }}
        emptyElement.style.display = 'none';
        items.forEach((value) => {{
            const li = document.createElement('li');
            li.textContent = value;
            listElement.appendChild(li);
        }});
    }}

    function initServiceModal() {{
        const modal = document.getElementById('service-modal');
        const closeBtn = document.getElementById('service-modal-close');
        const title = document.getElementById('service-modal-title');
        const listList = document.getElementById('service-list-list');
        const listEmpty = document.getElementById('service-list-empty');
        if (!modal || !closeBtn || !title || !listList || !listEmpty) return;

        const close = () => {{ modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true'); }};
        closeBtn.addEventListener('click', close);
        modal.addEventListener('click', (e) => {{ if (e.target === modal) close(); }});
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape' && modal.classList.contains('open')) close();
        }});

        document.addEventListener('click', (event) => {{
            const trigger = event.target.closest('.service-link');
            if (!trigger) return;
            const service = trigger.getAttribute('data-service') || '';
            const data = serviceResources[service] || {{ list_resources: [] }};
            title.textContent = `${{service}} List Resources`;
            fillResourceList(listList, listEmpty, data.list_resources);
            modal.classList.add('open');
            modal.setAttribute('aria-hidden', 'false');
        }});
    }}

    function initCoverageTable(tableId, searchId, sortId) {{
        const table = document.getElementById(tableId);
        const search = document.getElementById(searchId);
        const sort = document.getElementById(sortId);
        if (!table || !search || !sort) return;
        const tbody = table.querySelector('tbody');
        const allRows = Array.from(tbody.querySelectorAll('tr')).map((row) => {{
            const cells = row.querySelectorAll('td');
            if (cells.length < 2) return null;
            const service = cells[0].innerText.trim();
            const listResources = parseInt(cells[1].innerText.replace(/,/g, ''), 10) || 0;
            return {{ row, service, listResources }};
        }}).filter(Boolean);

        function compare(a, b, mode) {{
            if (mode === 'service_asc') return a.service.localeCompare(b.service);
            if (mode === 'service_desc') return b.service.localeCompare(a.service);
            return (b.listResources - a.listResources) || a.service.localeCompare(b.service);
        }}
        function render() {{
            const q = search.value.trim().toLowerCase();
            const mode = sort.value;
            const visible = allRows
                .filter((item) => item.service.toLowerCase().includes(q))
                .sort((a, b) => compare(a, b, mode));
            tbody.innerHTML = '';
            visible.forEach((item) => tbody.appendChild(item.row));
        }}
        search.addEventListener('input', render);
        sort.addEventListener('change', render);
        render();
    }}

    initServiceModal();
    initCoverageTable('gc-coverage-table', 'gc-service-search', 'gc-service-sort');
</script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Google list validation artifacts")
    parser.add_argument("--repos-dir", default="/tmp/tf_repos", help="Directory for cloned repos")
    parser.add_argument("--output-json", default=RAW_OUTPUT_JSON, help="Path for JSON summary output")
    parser.add_argument("--output-html", default=REPORT_HTML, help="Path for standalone HTML output")
    parser.add_argument("--details-file", default=DETAILS_FILE, help="Dashboard details JSON to validate against")
    args = parser.parse_args()

    repos_dir = Path(args.repos_dir)
    repos_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        print("ERROR: Failed to clone/update terraform-provider-google", file=sys.stderr)
        return 1

    reg_path = repo_dir / REGISTRATION_FILE_REL
    if not reg_path.exists():
        print(f"ERROR: registration file not found at {reg_path}", file=sys.stderr)
        return 2
    reg_text = reg_path.read_text(encoding="utf-8", errors="replace")

    registrations = parse_registrations(reg_text)
    by_service = scan_service_list_files(repo_dir / SERVICES_DIR_REL)
    cross = cross_check(registrations, by_service)

    summary = build_summary(
        repo_dir=repo_dir,
        reg_text=reg_text,
        registrations=registrations,
        by_service=by_service,
        cross=cross,
        details_path=Path(args.details_file),
    )

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    generate_report(summary, Path(args.output_html))

    print(
        "Generated Google list validation artifacts: "
        f"{args.output_json}, {args.output_html}"
    )
    print(
        "Validation: dashboard="
        f"{summary['validation']['dashboard_list_resources']} "
        "code="
        f"{summary['validation']['code_list_resources']} "
        f"registered={summary['validation']['registered_count']} "
        f"match={summary['validation']['matches']} "
        f"signals_agree={summary['validation']['signals_agree']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
