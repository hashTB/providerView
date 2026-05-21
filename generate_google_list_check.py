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

# Root within the provider repo where we look for list-resource registrations.
# As of magic-modules PR #17361, the central
# google/fwprovider/framework_provider_mmv1_resources.go file was removed;
# list resources now self-register via init() functions calling
# registry.FrameworkListResource{...}.Register() (collected by
# registry.FrameworkListResourceFuncs() from framework_provider.go).
SCAN_ROOT_REL = Path("google")
SERVICES_DIR_REL = Path("google/services")
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
# Matches assignments like: listR.TypeName = "google_xxx"
TYPE_NAME_RE = re.compile(
    r'(?:listR|l|r)\s*\.\s*TypeName\s*=\s*"(google_[a-z0-9_]+)"'
)


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

def load_dashboard_list_resources(details_path: Path) -> list[str] | None:
    """Return the list-resource slugs the Terraform Registry currently exposes
    for ``DASHBOARD_PROVIDER``, or ``None`` if the details file is missing."""
    try:
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    except FileNotFoundError:
        return None

    entry = details.get(DASHBOARD_PROVIDER, {})
    docs = entry.get("docs", {}) if isinstance(entry, dict) else {}
    list_resources = docs.get("list-resources", []) if isinstance(docs, dict) else []
    if not isinstance(list_resources, list):
        return []
    names: list[str] = []
    for item in list_resources:
        if isinstance(item, dict):
            slug = item.get("slug") or item.get("title")
            if isinstance(slug, str) and slug:
                names.append(slug)
        elif isinstance(item, str):
            names.append(item)
    return sorted(set(names))


# ---------------------------------------------------------------------------
# Summary build
# ---------------------------------------------------------------------------

def build_summary(
    repo_dir: Path,
    registrations: dict,
    source_files: list[dict],
    by_service: dict[str, list[dict]],
    cross: dict,
    details_path: Path,
) -> dict:
    dashboard_names = load_dashboard_list_resources(details_path)
    dashboard_count = None if dashboard_names is None else len(dashboard_names)
    code_count = cross["file_type_name_count"]
    code_names = list(cross["file_type_names"])

    dash_set = set(dashboard_names or [])
    code_set = set(code_names)
    only_in_registry = sorted(dash_set - code_set)
    only_in_source = sorted(code_set - dash_set)
    # The Terraform Registry trails source: a release must be published before
    # newly-added list resources show up in the Registry docs. Treat
    # "Registry \u2286 Source" as healthy; flag only when the Registry advertises
    # something the source no longer implements.
    matches = dashboard_names is not None and not only_in_registry

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

    # Hash a canonical, ordering-stable view of every registration so changes
    # to the upstream wiring produce a stable, comparable fingerprint even
    # though registrations now live in many files instead of one.
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
            "dashboard_list_resource_names": dashboard_names or [],
            "code_list_resource_names": code_names,
            "only_in_registry": only_in_registry,
            "only_in_source": only_in_source,
            "registry_is_subset": dashboard_names is not None and not only_in_registry,
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
    cross = summary["cross_check"]
    repo = summary["source_repo"]
    reg_file = summary["registration_sources"]
    coverage = summary.get("service_coverage", {})

    match_icon = "\u2705" if validation["matches"] else "\u26a0\ufe0f"
    if validation["matches"]:
        if validation["only_in_source"]:
            match_text = "healthy (Registry trails source)"
        else:
            match_text = "in sync"
    else:
        match_text = "out of sync"
    signals_icon = "\u2705" if validation["signals_agree"] else "\u26a0\ufe0f"

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

    def _name_list(names: list[str]) -> str:
        if not names:
            return '<span class="resource-empty">None.</span>'
        return " ".join(f"<code>{html.escape(n)}</code>" for n in names)

    diff_html = ""
    if validation["only_in_source"] or validation["only_in_registry"]:
        parts = []
        if validation["only_in_source"]:
            parts.append(
                "<div class=\"meta-row\"><span>Source only "
                "(pending Registry publish)</span><span>"
                f"{_name_list(validation['only_in_source'])}</span></div>"
            )
        if validation["only_in_registry"]:
            parts.append(
                "<div class=\"meta-row\"><span>Registry only "
                "(possible regression)</span><span>"
                f"{_name_list(validation['only_in_registry'])}</span></div>"
            )
        diff_html = (
            "<div class=\"meta-grid\" style=\"margin-top:12px;\"><div>"
            + "".join(parts)
            + "</div></div>"
        )

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
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Validation: {match_text}</div></div>
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
                <div class=\"meta-row\"><span>Registration sources</span><span>{fmt(reg_file['file_count'])} file(s) under <code>{html.escape(reg_file['scan_root'])}/</code></span></div>
                <div class=\"meta-row\"><span>Registrations SHA-256</span><span><code>{html.escape(reg_file['sha256'][:16])}...</code></span></div>
            </div>
            <div>
                <div class=\"meta-row\"><span>Dashboard provider</span><span><code>{html.escape(DASHBOARD_PROVIDER)}</code></span></div>
                <div class=\"meta-row\"><span>Magic Modules note</span><span style=\"text-align:left;\">{html.escape(reg_file['note'])}</span></div>
            </div>
        </div>
        {diff_html}
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
        <p class=\"subtitle\" style=\"margin-bottom: 12px;\">Parsed from <code>registry.FrameworkListResource{{...}}.Register()</code> blocks across <strong>{fmt(reg_file['file_count'])}</strong> source file(s) under <code>{html.escape(reg_file['scan_root'])}/</code>.</p>
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

    by_service = scan_service_list_files(repo_dir / SERVICES_DIR_REL)
    cross = cross_check(registrations, by_service)

    summary = build_summary(
        repo_dir=repo_dir,
        registrations=registrations,
        source_files=source_files,
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
    v = summary["validation"]
    print(
        "Validation: dashboard="
        f"{v['dashboard_list_resources']} "
        f"code={v['code_list_resources']} "
        f"registered={v['registered_count']} "
        f"match={v['matches']} "
        f"signals_agree={v['signals_agree']}"
    )
    if v["only_in_source"]:
        print(
            "  Source-only (pending Registry publish): "
            + ", ".join(v["only_in_source"])
        )
    if v["only_in_registry"]:
        print(
            "  Registry-only (regression?): "
            + ", ".join(v["only_in_registry"]),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
