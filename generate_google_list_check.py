#!/usr/bin/env python3
"""
Validate Google list resources against the released provider and generate artifacts.

This script:
- Asks the latest hashicorp/google release which list resources it serves
  (``terraform providers schema -json``, see provider_schema.py); this is the headline count
- Compares that with the Registry list-resources docs. The hashicorp/google docs also
  include pages for google-beta-only list resources, so the latest hashicorp/google-beta
  schema is read too, to tell those pages apart from real gaps
- Scans terraform-provider-google main for ``registry.FrameworkListResource{...}.Register()``
  blocks (the registry that ``FrameworkProvider.ListResources()`` serves) for per-service
  grouping and to spot list resources that are not released yet

Outputs:
- ``data/google_list_check.json`` – compact summary for the dashboard
- ``docs/google-list-check.html`` – standalone report page

Requires Terraform >= 1.14 on PATH (or TERRAFORM_BIN).

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

from provider_schema import (
    ProviderSchemaError,
    compare_with_registry,
    diff_source_scan,
    fetch_provider_schema,
    load_registry_list_slugs,
    render_differences_html,
)

USER_AGENT = "providerView-google-list-check/1.0"
RAW_OUTPUT_JSON = "data/google_list_check.json"
REPORT_HTML = "docs/google-list-check.html"
DETAILS_FILE = "terraform_providers_details.json"

PROVIDER_REPO_URL = "https://github.com/hashicorp/terraform-provider-google.git"
PROVIDER_REPO_DIR_NAME = "hashicorp_terraform-provider-google"
DASHBOARD_PROVIDER = "hashicorp/google"
BETA_PROVIDER = "hashicorp/google-beta"

# Root within the provider repo where we look for list-resource registrations.
# As of magic-modules PR #17361, the central
# google/fwprovider/framework_provider_mmv1_resources.go file was removed;
# list resources now self-register via init() functions calling
# registry.FrameworkListResource{...}.Register() (collected by
# registry.FrameworkListResourceFuncs() from framework_provider.go).
SCAN_ROOT_REL = Path("google")
MMV1_SOURCE_NOTE = (
    "Magic Modules generator emits these registrations into the provider repo. "
    "Each list resource self-registers in an init() block via "
    "registry.FrameworkListResource{...}.Register(); the provider exposes the "
    "aggregated set through registry.FrameworkListResourceFuncs(). "
    "Source-of-truth lives in GoogleCloudPlatform/magic-modules under "
    "mmv1/third_party/terraform/services/<service>/list_*.go and "
    "mmv1/third_party/terraform/registry/registry.go."
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

# Matches: registry.FrameworkListResource{ Name: "...", ProductName: "...",
#                                          Func: NewXxxListResource, ... }.Register()
REGISTRATION_BLOCK_RE = re.compile(
    r"registry\.FrameworkListResource\s*\{(?P<body>[^{}]*)\}\s*\.\s*Register\s*\(\s*\)",
    re.DOTALL,
)
NAME_FIELD_RE = re.compile(r'Name\s*:\s*"([^"]+)"')
PRODUCT_FIELD_RE = re.compile(r'ProductName\s*:\s*"([^"]+)"')
FUNC_FIELD_RE = re.compile(r'Func\s*:\s*([A-Za-z0-9_.]+)')
# Magic-Modules file headers carry: *** AUTO GENERATED CODE *** Type: <kind> ***
# where <kind> is "MMv1" (generated) or "Handwritten".
TYPE_MARKER_RE = re.compile(r"AUTO GENERATED CODE\s*\*+\s*Type:\s*(\w+)")


def scan_registrations(repo_dir: Path) -> tuple[dict, list[dict]]:
    """Walk the provider repo for ``registry.FrameworkListResource{...}.Register()`` calls.

    Returns a tuple ``(buckets, source_files)`` where::

        buckets = {
            "generated":   [{"name": ..., "package": ..., "constructor": ...,
                              "file": ..., "line": N}, ...],
            "handwritten": [...],
        }
        source_files = [{"file": rel, "type": "MMv1"|"Handwritten", "registrations": N}, ...]

    Files are classified via the Magic Modules header marker
    (``*** AUTO GENERATED CODE *** Type: <kind> ***``); anything that isn't
    explicitly ``MMv1`` is treated as handwritten.
    """
    buckets: dict[str, list[dict]] = {"generated": [], "handwritten": []}
    source_files: list[dict] = []
    scan_root = repo_dir / SCAN_ROOT_REL
    if not scan_root.exists():
        return buckets, source_files

    for path in sorted(scan_root.rglob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "registry.FrameworkListResource" not in text:
            continue

        type_marker = TYPE_MARKER_RE.search(text)
        kind = (type_marker.group(1) if type_marker else "").strip()
        bucket = "generated" if kind.lower() == "mmv1" else "handwritten"

        rel = str(path.relative_to(repo_dir))
        file_hits = 0
        for match in REGISTRATION_BLOCK_RE.finditer(text):
            body = match.group("body")
            name_m = NAME_FIELD_RE.search(body)
            func_m = FUNC_FIELD_RE.search(body)
            if not (name_m and func_m):
                continue
            product_m = PRODUCT_FIELD_RE.search(body)
            lineno = text.count("\n", 0, match.start()) + 1
            buckets[bucket].append({
                "name": name_m.group(1),
                "package": product_m.group(1) if product_m else "",
                "constructor": func_m.group(1),
                "file": rel,
                "line": lineno,
            })
            file_hits += 1

        if file_hits:
            source_files.append({
                "file": rel,
                "type": "MMv1" if bucket == "generated" else "Handwritten",
                "registrations": file_hits,
            })

    for bucket in buckets.values():
        bucket.sort(key=lambda e: (e["file"], e["line"]))
    return buckets, source_files


# ---------------------------------------------------------------------------
# Summary build
# ---------------------------------------------------------------------------

def build_summary(
    repo_dir: Path,
    registrations: dict,
    source_files: list[dict],
    details_path: Path,
    schema: dict,
    beta_schema: dict,
) -> dict:
    registry_slugs = load_registry_list_slugs(details_path, DASHBOARD_PROVIDER)
    dashboard_count = None if registry_slugs is None else len(registry_slugs)
    schema_names = schema["list_resources"]

    product_by_name = {
        entry["name"]: entry["package"] or "unknown"
        for bucket in registrations.values()
        for entry in bucket
    }
    comparison = compare_with_registry(
        schema_names, registry_slugs, "google", beta_schema["list_resources"]
    )
    source_diff = diff_source_scan(schema_names, product_by_name.keys())

    # Group released list resources by the ProductName they register under on main;
    # anything already removed from main lands in "unknown".
    service_resource_map: dict[str, dict[str, list[str]]] = {}
    for name in schema_names:
        service = product_by_name.get(name, "unknown")
        service_resource_map.setdefault(service, {"list_resources": []})["list_resources"].append(name)
    service_rows = sorted(
        (
            {"service": service, "list_resources": len(entry["list_resources"])}
            for service, entry in service_resource_map.items()
        ),
        key=lambda row: (-row["list_resources"], row["service"]),
    )

    # Hash a canonical, ordering-stable view of every registration so changes
    # to the upstream wiring produce a stable, comparable fingerprint.
    canonical = json.dumps(
        [
            {
                "name": entry["name"],
                "constructor": entry["constructor"],
                "file": entry["file"],
            }
            for bucket_name in ("handwritten", "generated")
            for entry in registrations.get(bucket_name, [])
        ],
        sort_keys=True,
    ).encode("utf-8")
    reg_sha = hashlib.sha256(canonical).hexdigest()

    sorted_sources = sorted(source_files, key=lambda x: x["file"])

    return {
        "generated_at": datetime.now().isoformat(),
        "source_repo": {
            "repo": "hashicorp/terraform-provider-google",
            "url": PROVIDER_REPO_URL,
            "commit": get_repo_commit(repo_dir),
        },
        "registration_sources": {
            "scan_root": str(SCAN_ROOT_REL),
            "file_count": len(sorted_sources),
            "files": sorted_sources,
            "sha256": reg_sha,
            "note": MMV1_SOURCE_NOTE,
        },
        "dashboard": {
            "provider": DASHBOARD_PROVIDER,
            "list_resources": dashboard_count,
        },
        "provider_schema": schema,
        "beta_provider_schema": {k: v for k, v in beta_schema.items() if k != "list_resources"},
        "registry_comparison": comparison,
        "source_scan_diff": source_diff,
        "registrations": registrations,
        "service_coverage": {
            "services_with_list": len(service_rows),
            "rows": service_rows,
            "service_resources": service_resource_map,
        },
        "validation": {
            "matches": comparison["in_sync"],
            "schema_list_resources": schema["counts"]["list_resources"],
            "schema_identity_resources": schema["counts"]["identity_resources"],
            "schema_provider_version": schema["version"],
            "dashboard_list_resources": dashboard_count,
            "registered_count": len(product_by_name),
            "source_scan_complete": not source_diff["not_in_source_scan"],
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
            f"<li><code>{html.escape(it.get('name', '') or it['constructor'])}</code>"
            f" &mdash; <code>{html.escape(it['constructor'])}</code>"
            f" <span class=\"muted\">{html.escape(it.get('file', ''))}:{it['line']}</span></li>"
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
    repo = summary["source_repo"]
    reg_file = summary["registration_sources"]
    schema = summary["provider_schema"]
    beta_schema = summary["beta_provider_schema"]
    coverage = summary.get("service_coverage", {})

    match_icon = "\u2705" if validation["matches"] else "\u26a0\ufe0f"
    match_text = "in sync" if validation["matches"] else "out of sync"
    scan_icon = "\u2705" if validation["source_scan_complete"] else "\u26a0\ufe0f"
    differences_html = render_differences_html(
        summary["registry_comparison"],
        summary["source_scan_diff"],
        "source registration scan of main",
        other_provider_label=BETA_PROVIDER,
    )

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
        table_rows = '<tr><td colspan="2" style="color: var(--text-muted);">No list resources in this release.</td></tr>'

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
    <p class=\"subtitle\">List resources served by the latest <code>{html.escape(DASHBOARD_PROVIDER)}</code> release, compared with the Registry docs and a scan of the <code>terraform-provider-google</code> source. Generated {html.escape(summary['generated_at'])}.</p>

    <div class=\"cards\">
        <div class=\"card\"><div class=\"value\">{fmt(validation['schema_list_resources'])}</div><div class=\"label\">List Resources in v{html.escape(schema['version'])}</div></div>
        <div class=\"card\"><div class=\"value\">{fmt(validation['dashboard_list_resources'])}</div><div class=\"label\">Registry List Pages</div></div>
        <div class=\"card\"><div class=\"value\">{fmt(validation['registered_count'])}</div><div class=\"label\">Source Registrations (main)</div></div>
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Registry {match_text}</div></div>
        <div class=\"card\"><div class=\"value\">{scan_icon}</div><div class=\"label\">Source Scan Sees All Released</div></div>
    </div>

    <div class=\"note\">
        <strong>{match_icon} Validation:</strong> <code>{html.escape(DASHBOARD_PROVIDER)}</code> v{html.escape(schema['version'])} serves
        <strong>{fmt(validation['schema_list_resources'])}</strong> list resources
        (<strong>{fmt(validation['schema_identity_resources'])}</strong> resources have identity), read from the released provider with
        <code>terraform providers schema -json</code> (Terraform {html.escape(schema['terraform_version'])}).
        The Registry documents <strong>{fmt(validation['dashboard_list_resources'])}</strong> list-resource pages; some pages cover several
        resources, and the docs also include pages for <code>{html.escape(BETA_PROVIDER)}</code>-only resources
        (v{html.escape(beta_schema['version'])} serves <strong>{fmt(beta_schema['counts']['list_resources'])}</strong>), as listed below.
        The source scan of main finds <strong>{fmt(validation['registered_count'])}</strong> registrations and supplies the per-service grouping.
        <div class=\"meta-grid\">
            <div>
                <div class=\"meta-row\"><span>Provider repo</span><span><a href=\"{html.escape(repo['url'])}\">{html.escape(repo['repo'])}</a></span></div>
                <div class=\"meta-row\"><span>Repo commit</span><span><code>{html.escape(repo['commit'] or 'unknown')}</code></span></div>
                <div class=\"meta-row\"><span>Registration sources</span><span>{fmt(reg_file['file_count'])} file(s) under <code>{html.escape(reg_file['scan_root'])}/</code></span></div>
                <div class=\"meta-row\"><span>Registrations SHA-256</span><span><code>{html.escape(reg_file['sha256'][:16])}...</code></span></div>
            </div>
            <div>
                <div class=\"meta-row\"><span>Dashboard provider</span><span><code>{html.escape(DASHBOARD_PROVIDER)}</code></span></div>
                <div class=\"meta-row\"><span>Magic Modules note</span><span style=\"text-align:left;\">{html.escape(reg_file['note'])}</span></div>
            </div>
        </div>
        {differences_html}
    </div>

    <div class=\"section\">
        <h2>Service Coverage (List)</h2>
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">Released list resources grouped by the <code>ProductName</code> they register under on main. Services exposing at least one List Resource: <strong>{fmt(coverage.get('services_with_list'))}</strong>.</p>
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
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">Parsed from <code>registry.FrameworkListResource{{...}}.Register()</code> blocks across <strong>{fmt(reg_file['file_count'])}</strong> source file(s) under <code>{html.escape(reg_file['scan_root'])}/</code>.</p>
        {registration_block}
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

    try:
        schema = fetch_provider_schema(DASHBOARD_PROVIDER, repos_dir)
        beta_schema = fetch_provider_schema(BETA_PROVIDER, repos_dir)
    except (ProviderSchemaError, OSError) as exc:
        print(f"ERROR: could not read the Google provider schemas: {exc}", file=sys.stderr)
        return 1
    print(
        f"  {DASHBOARD_PROVIDER} v{schema['version']} serves {schema['counts']['list_resources']} list resources; "
        f"{BETA_PROVIDER} v{beta_schema['version']} serves {beta_schema['counts']['list_resources']}"
    )

    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        print("ERROR: Failed to clone/update terraform-provider-google", file=sys.stderr)
        return 1

    reg_text = (
        "Registrations are sourced from registry.FrameworkListResource{...}.Register() "
        "blocks across the cloned provider repo."
    )
    print(f"  {reg_text}")

    registrations, source_files = scan_registrations(repo_dir)
    if not registrations["generated"] and not registrations["handwritten"]:
        print(
            "ERROR: no registry.FrameworkListResource{...}.Register() blocks found in "
            f"{repo_dir / SCAN_ROOT_REL}; upstream layout may have changed again.",
            file=sys.stderr,
        )
        return 2

    summary = build_summary(
        repo_dir=repo_dir,
        registrations=registrations,
        source_files=source_files,
        details_path=Path(args.details_file),
        schema=schema,
        beta_schema=beta_schema,
    )

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    generate_report(summary, Path(args.output_html))

    print(
        "Generated Google list validation artifacts: "
        f"{args.output_json}, {args.output_html}"
    )
    v = summary["validation"]
    comparison = summary["registry_comparison"]
    print(
        f"Validation: registry={v['dashboard_list_resources']} "
        f"schema={v['schema_list_resources']} "
        f"registered={v['registered_count']} "
        f"match={v['matches']} "
        f"source_scan_complete={v['source_scan_complete']}"
    )
    if summary["source_scan_diff"]["source_only"]:
        print("  On main, not released yet: " + ", ".join(summary["source_scan_diff"]["source_only"]))
    for label, names in (
        ("Registry pages the provider doesn't serve", comparison["unexplained_registry_pages"]),
        ("Served without a Registry page", comparison["undocumented_list_resources"]),
        ("Released but missed by the source scan", summary["source_scan_diff"]["not_in_source_scan"]),
    ):
        if names:
            print(f"  {label}: " + ", ".join(names), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
