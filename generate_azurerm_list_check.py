#!/usr/bin/env python3
"""
Validate AzureRM list resources against the released provider and generate artifacts.

This script:
- Asks the latest hashicorp/azurerm release which list resources it serves
  (``terraform providers schema -json``, see provider_schema.py); this is the headline count
- Compares that with the Registry list-resources docs
- Fetches the latest revision of katbyte's list_identity_list.sh gist and runs it against
  terraform-provider-azurerm main for per-service detail, adapting resources that moved to
  the nested internal/services/<service>/resources/<name>/ layout into the flat
  <service>/<name>_resource.go layout the gist scans
- Writes a compact JSON summary for the main dashboard
- Writes a standalone HTML report page with the full gist output

Requires Terraform >= 1.14 on PATH (or TERRAFORM_BIN).

Usage:
    python3 generate_azurerm_list_check.py
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
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

GIST_PAGE_URL = "https://gist.github.com/katbyte/502302a237bb765e0d24236070f0fa31"
GIST_RAW_FALLBACK_URL = (
    "https://gist.githubusercontent.com/katbyte/502302a237bb765e0d24236070f0fa31/raw/"
    "list_identity_list.sh"
)
USER_AGENT = "providerView-azurerm-list-check/1.0"
RAW_OUTPUT_JSON = "data/azurerm_list_check.json"
REPORT_HTML = "docs/azurerm-list-check.html"
DETAILS_FILE = "terraform_providers_details.json"
PROVIDER = "hashicorp/azurerm"
SERVICES_DIR = Path("internal/services")
NESTED_RESOURCES_DIR = "resources"


def clone_or_update_repo(repos_dir: Path) -> Path | None:
    """Clone or update the Azure provider repo used by list validation."""
    repo_url = "https://github.com/hashicorp/terraform-provider-azurerm.git"
    repo_dir = repos_dir / "hashicorp_terraform-provider-azurerm"

    if repo_dir.exists():
        print("  Updating terraform-provider-azurerm...")
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Warning: git pull failed: {result.stderr}")
    else:
        print("  Cloning terraform-provider-azurerm...")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(repo_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Error: git clone failed: {result.stderr}")
            return None

    return repo_dir


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def discover_gist() -> dict:
    page_html = fetch_text(GIST_PAGE_URL)

    raw_match = re.search(
        r'https://gist\.github\.com/katbyte/502302a237bb765e0d24236070f0fa31/raw/([a-f0-9]+)/list_identity_list\.sh',
        page_html,
    )
    if raw_match:
        raw_url = raw_match.group(0)
        revision = raw_match.group(1)
    else:
        raw_url = GIST_RAW_FALLBACK_URL
        revision = None

    script_text = fetch_text(raw_url)
    script_sha256 = hashlib.sha256(script_text.encode("utf-8")).hexdigest()

    last_active_match = re.search(r"Last active\s+([^<\n]+)", page_html)
    revision_count_match = re.search(r"Revisions\s+([0-9]+)", page_html)

    return {
        "page_url": GIST_PAGE_URL,
        "raw_url": raw_url,
        "revision": revision,
        "revision_count": int(revision_count_match.group(1)) if revision_count_match else None,
        "last_active": last_active_match.group(1).strip() if last_active_match else None,
        "script_sha256": script_sha256,
        "script_text": script_text,
    }


def resolve_nested_resource_name(
    resource_dir: Path, service: str, source_text: str, registration_text: str
) -> str | None:
    """Resolve the registered azurerm_* name of a resources/<name>/ package."""
    typed_match = re.search(
        r'ResourceType\(\)\s*string\s*\{\s*return\s+"(azurerm_[a-z0-9_]+)"', source_text
    )
    if typed_match:
        return typed_match.group(1)

    package_match = re.search(r"^package\s+([A-Za-z0-9_]+)", source_text, re.MULTILINE)
    package_ident = package_match.group(1) if package_match else resource_dir.name
    alias_match = re.search(
        rf'^\s*([A-Za-z_][A-Za-z0-9_]*)\s+"[^"]*/{re.escape(str(SERVICES_DIR / service / NESTED_RESOURCES_DIR / resource_dir.name))}"',
        registration_text,
        re.MULTILINE,
    )
    if alias_match:
        package_ident = alias_match.group(1)

    untyped_match = re.search(
        rf'"(azurerm_[a-z0-9_]+)":\s*{re.escape(package_ident)}\.RegisterResource\(',
        registration_text,
    )
    return untyped_match.group(1) if untyped_match else None


def build_scan_tree(repo_dir: Path, scan_dir: Path) -> int:
    """Mirror internal/services into the flat layout the gist scans; return nested resources adapted.

    The gist only globs internal/services/<service>/*_resource.go, but AzureRM is moving
    resources to internal/services/<service>/resources/<name>/ (resource.go + r_*.go parts,
    list.go, resource_test.go), which the gist silently skips. Flat files are symlinked
    unchanged; each nested resource becomes <name>_resource.go (resource.go + r_*.go) with
    list.go and resource_test.go linked under the names the gist expects.
    """
    nested_count = 0
    for service_dir in sorted(p for p in (repo_dir / SERVICES_DIR).iterdir() if p.is_dir()):
        target_dir = scan_dir / service_dir.name
        target_dir.mkdir(parents=True)
        for path in service_dir.iterdir():
            if path.is_file():
                (target_dir / path.name).symlink_to(path.resolve())

        nested_root = service_dir / NESTED_RESOURCES_DIR
        if not nested_root.is_dir():
            continue

        registration_text = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(service_dir.glob("registration*.go"))
        )
        for resource_dir in sorted(p for p in nested_root.iterdir() if p.is_dir()):
            resource_file = resource_dir / "resource.go"
            if not resource_file.is_file():
                continue

            flat_file = target_dir / f"{resource_dir.name}_resource.go"
            if flat_file.exists() or flat_file.is_symlink():
                print(f"  Warning: skipping {resource_dir}: {flat_file.name} already exists in {service_dir.name}")
                continue

            parts = [resource_file] + sorted(
                p for p in resource_dir.glob("r_*.go") if not p.name.endswith("_test.go")
            )
            source_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in parts)
            name = resolve_nested_resource_name(resource_dir, service_dir.name, source_text, registration_text)
            # The gist resolves untyped names via `func resource*()` constructors, which the nested
            # layout replaced with RegisterResource(); its fallback takes the first quoted azurerm_* name.
            header = f'// azurerm-list-check: registered as "{name}"\n' if name else ""
            flat_file.write_text(header + source_text, encoding="utf-8")

            for source_name, flat_name in (
                ("list.go", f"{resource_dir.name}_resource_list.go"),
                ("resource_test.go", f"{resource_dir.name}_resource_test.go"),
            ):
                source_path = resource_dir / source_name
                if source_path.is_file():
                    (target_dir / flat_name).symlink_to(source_path.resolve())

            nested_count += 1

    return nested_count


def run_gist_mode(script_path: Path, repo_dir: Path, mode: str, scan_dir: Path) -> dict:
    result = subprocess.run(
        [str(script_path), mode, str(scan_dir)],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    return {
        "mode": mode,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def parse_mode_summary(output: str, mode: str) -> dict:
    summary: dict[str, int | None] = {}

    if mode == "implemented":
        total_match = re.search(r"Total with Identity:\s+(\d+) of (\d+)", output)
        with_list_match = re.search(r"With List:\s+(\d+)", output)
        without_list_match = re.search(r"Without List:\s+(\d+)", output)
        summary = {
            "with_identity": int(total_match.group(1)) if total_match else None,
            "total_resources": int(total_match.group(2)) if total_match else None,
            "with_list": int(with_list_match.group(1)) if with_list_match else None,
            "without_list": int(without_list_match.group(1)) if without_list_match else None,
        }
    elif mode == "list":
        missing_match = re.search(r"Total Missing List:\s+(\d+)", output)
        have_identity_match = re.search(r"Have Identity:\s+(\d+)", output)
        missing_identity_match = re.search(r"Missing Identity:\s+(\d+)", output)
        summary = {
            "missing_list": int(missing_match.group(1)) if missing_match else None,
            "have_identity": int(have_identity_match.group(1)) if have_identity_match else None,
            "missing_identity": int(missing_identity_match.group(1)) if missing_identity_match else None,
        }
    elif mode == "identity":
        total_match = re.search(r"Total Missing:\s+(\d+) of (\d+)", output)
        summary = {
            "missing_identity": int(total_match.group(1)) if total_match else None,
            "total_resources": int(total_match.group(2)) if total_match else None,
        }

    return summary


def parse_implemented_service_coverage(output: str) -> dict[str, dict[str, set[str]]]:
    service_stats: dict[str, dict[str, set[str]]] = {}
    row_pattern = re.compile(
        r"^\s*([a-z0-9_]+)\s+(azurerm_[a-z0-9_]+)\s+(?:\[[^\]]+\]\s+)?✅ Identity(?:\s+📋 List)?\s*$"
    )

    for line in output.splitlines():
        if "SUMMARY:" in line or "══" in line or "──" in line:
            continue

        match = row_pattern.match(line)
        if not match:
            continue

        service = match.group(1)
        resource = match.group(2)
        has_list = "📋 List" in line

        if service not in service_stats:
            service_stats[service] = {"identity": set(), "list": set()}

        service_stats[service]["identity"].add(resource)
        if has_list:
            service_stats[service]["list"].add(resource)

    return service_stats


def parse_missing_list_service_coverage(output: str) -> dict[str, set[str]]:
    service_stats: dict[str, set[str]] = {}
    row_pattern = re.compile(r"^\s*([a-z0-9_]+)\s+(azurerm_[a-z0-9_]+)\s+.*⬜ Missing List.*$")

    for line in output.splitlines():
        match = row_pattern.match(line)
        if not match:
            continue

        service = match.group(1)
        resource = match.group(2)
        service_stats.setdefault(service, set()).add(resource)

    return service_stats


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


def build_summary_data(gist: dict, repo_dir: Path, outputs: dict, details_path: Path, schema: dict) -> dict:
    registry_slugs = load_registry_list_slugs(details_path, PROVIDER)
    dashboard_count = None if registry_slugs is None else len(registry_slugs)

    implemented_summary = outputs["implemented"]["summary"]
    list_summary = outputs["list"]["summary"]
    identity_summary = outputs["identity"]["summary"]

    total_resources = implemented_summary.get("total_resources") or identity_summary.get("total_resources")
    gist_with_list = implemented_summary.get("with_list")
    derived_total_with_list = None
    if total_resources is not None and list_summary.get("missing_list") is not None:
        derived_total_with_list = total_resources - list_summary["missing_list"]

    implemented_by_service = parse_implemented_service_coverage(outputs["implemented"]["stdout"])
    missing_list_by_service = parse_missing_list_service_coverage(outputs["list"]["stdout"])

    service_rows = []
    service_resource_map: dict[str, dict[str, list[str]]] = {}
    all_services = set(implemented_by_service.keys()) | set(missing_list_by_service.keys())
    for service in all_services:
        implemented = implemented_by_service.get(service, {"identity": set(), "list": set()})
        list_resources = implemented.get("list", set())
        missing_resources = missing_list_by_service.get(service, set())

        provider_resources = list_resources | missing_resources
        service_total = len(provider_resources)
        if service_total == 0:
            continue

        list_count = len(list_resources)
        coverage_percent = round((list_count / service_total) * 100)
        service_resource_map[service] = {
            "all_resources": sorted(provider_resources),
            "list_resources": sorted(list_resources),
        }
        service_rows.append(
            {
                "service": service,
                "list_resources": list_count,
                "total_resources": service_total,
                "percent": coverage_percent,
            }
        )

    service_rows.sort(key=lambda row: (-row["list_resources"], -row["percent"], row["service"]))

    gist_list_names = {
        name for stats in implemented_by_service.values() for name in stats.get("list", set())
    }
    comparison = compare_with_registry(schema["list_resources"], registry_slugs, "azurerm")
    source_diff = diff_source_scan(schema["list_resources"], gist_list_names)

    return {
        "generated_at": datetime.now().isoformat(),
        "gist": {
            "page_url": gist["page_url"],
            "raw_url": gist["raw_url"],
            "revision": gist["revision"],
            "revision_count": gist["revision_count"],
            "last_active": gist["last_active"],
            "script_sha256": gist["script_sha256"],
        },
        "source_repo": {
            "repo": "hashicorp/terraform-provider-azurerm",
            "commit": get_repo_commit(repo_dir),
        },
        "dashboard": {
            "provider": PROVIDER,
            "list_resources": dashboard_count,
        },
        "provider_schema": schema,
        "registry_comparison": comparison,
        "source_scan_diff": source_diff,
        "gist_results": {
            mode: {
                "returncode": result["returncode"],
                "summary": result["summary"],
                "stdout_line_count": len(result["stdout"].splitlines()),
                "stderr_line_count": len(result["stderr"].splitlines()),
            }
            for mode, result in outputs.items()
        },
        "service_coverage": {
            "services_with_list": len([row for row in service_rows if row["list_resources"] > 0]),
            "top_services": [row for row in service_rows if row["list_resources"] > 0][:15],
            "service_resources": service_resource_map,
        },
        "validation": {
            "matches": comparison["in_sync"],
            "schema_list_resources": schema["counts"]["list_resources"],
            "schema_identity_resources": schema["counts"]["identity_resources"],
            "schema_provider_version": schema["version"],
            "dashboard_list_resources": dashboard_count,
            "source_scan_complete": not source_diff["not_in_source_scan"],
            "gist_with_list": gist_with_list,
            "derived_total_with_list": derived_total_with_list,
            "total_resources": total_resources,
        },
    }


def render_output_block(title: str, mode: str, output: str) -> str:
    escaped = html.escape(output.strip() or "(no output)")
    return f"""    <div class=\"section\">
        <h2>{title}</h2>
        <div class=\"meta-row\"><span>Mode</span><span><code>{mode}</code></span></div>
        <pre>{escaped}</pre>
    </div>
"""


def generate_report(summary: dict, outputs: dict, out_path: Path) -> None:
    validation = summary["validation"]
    gist = summary["gist"]
    repo = summary["source_repo"]
    schema = summary["provider_schema"]
    coverage = summary.get("service_coverage", {})
    match_icon = "✅" if validation["matches"] else "⚠️"
    match_text = "in sync" if validation["matches"] else "out of sync"
    scan_icon = "✅" if validation["source_scan_complete"] else "⚠️"
    differences_html = render_differences_html(
        summary["registry_comparison"], summary["source_scan_diff"], "gist scan of main"
    )

    def fmt_val(value: int | None) -> str:
        return "N/A" if value is None else f"{value:,}"

    top_rows = coverage.get("top_services", [])
    service_resources_js = json.dumps(coverage.get("service_resources", {}), ensure_ascii=False)
    if top_rows:
        table_rows = "\n".join(
            (
                "<tr>"
                f"<td><button class=\"service-link\" data-service=\"{html.escape(row['service'])}\">{html.escape(row['service'])}</button></td>"
                f"<td>{fmt_val(row['list_resources'])}</td>"
                f"<td>{fmt_val(row['total_resources'])}</td>"
                f"<td>{fmt_val(row['percent'])}%</td>"
                "</tr>"
            )
            for row in top_rows
        )
    else:
        table_rows = '<tr><td colspan="4" style="color: var(--text-muted);">No service-level list coverage found.</td></tr>'

    html_text = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>AzureRM List Validation</title>
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
            background: none;
            border: none;
            color: #93c5fd;
            cursor: pointer;
            padding: 0;
            font: inherit;
            text-decoration: underline;
        }}
        .service-link:hover {{ color: #bfdbfe; }}
        .modal {{
            position: fixed;
            inset: 0;
            background: rgba(2, 6, 23, 0.7);
            display: none;
            align-items: center;
            justify-content: center;
            padding: 16px;
            z-index: 999;
        }}
        .modal.open {{ display: flex; }}
        .modal-card {{
            width: min(860px, 100%);
            max-height: 82vh;
            overflow: auto;
            background: #0b1220;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
        }}
        .modal-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 10px; }}
        .modal-title {{ font-size: 1.05rem; font-weight: 700; }}
        .modal-close {{
            background: #111827;
            border: 1px solid var(--border);
            color: var(--text);
            border-radius: 8px;
            padding: 6px 10px;
            cursor: pointer;
        }}
        .resource-columns {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
        .resource-box {{ background: #111827; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }}
        .resource-box h3 {{ font-size: 0.9rem; margin-bottom: 8px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }}
        .resource-list {{ list-style: none; margin: 0; padding: 0; max-height: 52vh; overflow: auto; }}
        .resource-list li {{ padding: 4px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.15); font-size: 0.85rem; }}
        .resource-list li:last-child {{ border-bottom: none; }}
        .resource-empty {{ color: var(--text-muted); font-size: 0.85rem; }}
    </style>
</head>
<body>
<div class=\"container\">
    <h1>AzureRM List Validation</h1>
    <nav class=\"topnav\">
        <a href=\"index.html\">All Providers</a>
        <a href=\"downloads.html\">📈 Download Trends</a>
        <a href=\"cloud-devex.html\">Cloud DevEx</a>
        <a href=\"azurerm-list-check.html\" class=\"active\">AzureRM List Check</a>
        <a href=\"aws-list-check.html\">AWS List Check</a>
        <a href=\"google-list-check.html\">Google List Check</a>
    </nav>
    <p class=\"subtitle\">List resources served by the latest hashicorp/azurerm release, compared with the Registry docs and a gist-backed scan of the provider source. Generated {html.escape(summary['generated_at'])}.</p>

    <div class=\"cards\">
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['schema_list_resources'])}</div><div class=\"label\">List Resources in v{html.escape(schema['version'])}</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['dashboard_list_resources'])}</div><div class=\"label\">Registry List Pages</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['gist_with_list'])}</div><div class=\"label\">Gist Scan With List (main)</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['total_resources'])}</div><div class=\"label\">AzureRM Resources Scanned</div></div>
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Registry {match_text}</div></div>
        <div class=\"card\"><div class=\"value\">{scan_icon}</div><div class=\"label\">Gist Scan Sees All Released</div></div>
    </div>

    <div class=\"note\">
        <strong>{match_icon} Validation:</strong> hashicorp/azurerm v{html.escape(schema['version'])} serves <strong>{fmt_val(validation['schema_list_resources'])}</strong> list resources
        (<strong>{fmt_val(validation['schema_identity_resources'])}</strong> resources have identity), read from the released provider with
        <code>terraform providers schema -json</code> (Terraform {html.escape(schema['terraform_version'])}). The Registry documents
        <strong>{fmt_val(validation['dashboard_list_resources'])}</strong> list-resource pages.
        The gist scan of the provider's main branch adds the per-service detail below; it fetches the latest gist revision on each run and can run ahead of the release.
        <div class=\"meta-grid\">
            <div>
                <div class=\"meta-row\"><span>Gist page</span><span><a href=\"{html.escape(gist['page_url'])}\">open</a></span></div>
                <div class=\"meta-row\"><span>Raw script</span><span><a href=\"{html.escape(gist['raw_url'])}\">open</a></span></div>
                <div class=\"meta-row\"><span>Revision</span><span>{html.escape(gist['revision'] or 'latest')}</span></div>
                <div class=\"meta-row\"><span>Revision count</span><span>{html.escape(str(gist['revision_count'])) if gist['revision_count'] is not None else 'N/A'}</span></div>
                <div class=\"meta-row\"><span>Last active</span><span>{html.escape(gist['last_active'] or 'N/A')}</span></div>
            </div>
            <div>
                <div class=\"meta-row\"><span>AzureRM repo commit</span><span><code>{html.escape(repo['commit'] or 'unknown')}</code></span></div>
                <div class=\"meta-row\"><span>Script SHA-256</span><span><code>{html.escape(gist['script_sha256'][:16])}...</code></span></div>
            </div>
        </div>
        {differences_html}
    </div>

    <div class="section">
        <h2>Service Coverage (List)</h2>
        <p class="subtitle" style="margin-bottom: 12px;">Top services by list-enabled resources from implemented mode. Services with at least one list-enabled resource: <strong>{fmt_val(coverage.get('services_with_list'))}</strong>.</p>
        <div class="table-controls">
            <input id="az-service-search" type="text" placeholder="Search by service (e.g. network, storage, mssql)" />
            <select id="az-service-sort">
                <option value="list_desc" selected>Sort: List Resources (high to low)</option>
                <option value="coverage_desc">Sort: Coverage % (high to low)</option>
                <option value="coverage_asc">Sort: Coverage % (low to high)</option>
                <option value="service_asc">Sort: Service (A-Z)</option>
                <option value="service_desc">Sort: Service (Z-A)</option>
            </select>
        </div>
        <table id="az-coverage-table">
            <thead>
                <tr>
                    <th>Service</th>
                    <th>List Resources</th>
                    <th>Provider Total Resources</th>
                    <th>Coverage</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>

{render_output_block('Implemented Mode Output', 'implemented', outputs['implemented']['stdout'])}
{render_output_block('Missing List Mode Output', 'list', outputs['list']['stdout'])}
{render_output_block('Missing Identity Mode Output', 'identity', outputs['identity']['stdout'])}
</div>
<div class="modal" id="service-modal" aria-hidden="true">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="service-modal-title">
        <div class="modal-head">
            <div class="modal-title" id="service-modal-title">Service Resources</div>
            <button class="modal-close" id="service-modal-close" type="button">Close</button>
        </div>
        <div class="resource-columns">
            <div class="resource-box">
                <h3>Provider Resources</h3>
                <ul class="resource-list" id="service-all-list"></ul>
                <p class="resource-empty" id="service-all-empty" style="display:none;">No resources found for this service.</p>
            </div>
            <div class="resource-box">
                <h3>List-Enabled Resources</h3>
                <ul class="resource-list" id="service-list-list"></ul>
                <p class="resource-empty" id="service-list-empty" style="display:none;">No list-enabled resources in this service.</p>
            </div>
        </div>
    </div>
</div>
<script>
    const serviceResources = {service_resources_js};

    function fillResourceList(listElement, emptyElement, values) {{
        listElement.innerHTML = '';
        const items = Array.isArray(values) ? values : [];
        if (!items.length) {{
            emptyElement.style.display = 'block';
            return;
        }}
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
        const allList = document.getElementById('service-all-list');
        const allEmpty = document.getElementById('service-all-empty');
        const listList = document.getElementById('service-list-list');
        const listEmpty = document.getElementById('service-list-empty');
        if (!modal || !closeBtn || !title || !allList || !allEmpty || !listList || !listEmpty) return;

        const close = () => {{
            modal.classList.remove('open');
            modal.setAttribute('aria-hidden', 'true');
        }};

        closeBtn.addEventListener('click', close);
        modal.addEventListener('click', (event) => {{
            if (event.target === modal) close();
        }});
        document.addEventListener('keydown', (event) => {{
            if (event.key === 'Escape' && modal.classList.contains('open')) close();
        }});

        document.addEventListener('click', (event) => {{
            const trigger = event.target.closest('.service-link');
            if (!trigger) return;

            const service = trigger.getAttribute('data-service') || '';
            const data = serviceResources[service] || {{ all_resources: [], list_resources: [] }};
            title.textContent = `${{service}} Resources`;
            fillResourceList(allList, allEmpty, data.all_resources);
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
            if (cells.length < 4) return null;

            const service = cells[0].innerText.trim();
            const listResources = parseInt(cells[1].innerText.replace(/,/g, ''), 10) || 0;
            const totalResources = parseInt(cells[2].innerText.replace(/,/g, ''), 10) || 0;
            const coverage = parseInt(cells[3].innerText.replace('%', '').trim(), 10) || 0;
            return {{ row, service, listResources, totalResources, coverage }};
        }}).filter(Boolean);

        function compare(a, b, mode) {{
            if (mode === 'service_asc') return a.service.localeCompare(b.service);
            if (mode === 'service_desc') return b.service.localeCompare(a.service);
            if (mode === 'coverage_desc') return (b.coverage - a.coverage) || (b.listResources - a.listResources) || a.service.localeCompare(b.service);
            if (mode === 'coverage_asc') return (a.coverage - b.coverage) || (b.listResources - a.listResources) || a.service.localeCompare(b.service);
            return (b.listResources - a.listResources) || (b.coverage - a.coverage) || a.service.localeCompare(b.service);
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
    initCoverageTable('az-coverage-table', 'az-service-search', 'az-service-sort');
</script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AzureRM list validation artifacts")
    parser.add_argument("--repos-dir", default="/tmp/tf_repos", help="Directory for cloned repos")
    parser.add_argument("--output-json", default=RAW_OUTPUT_JSON, help="Path for JSON summary output")
    parser.add_argument("--output-html", default=REPORT_HTML, help="Path for standalone HTML output")
    parser.add_argument("--details-file", default=DETAILS_FILE, help="Dashboard details JSON to validate against")
    args = parser.parse_args()

    repos_dir = Path(args.repos_dir)
    repos_dir.mkdir(parents=True, exist_ok=True)

    try:
        schema = fetch_provider_schema(PROVIDER, repos_dir)
    except (ProviderSchemaError, OSError) as exc:
        print(f"ERROR: could not read the {PROVIDER} provider schema: {exc}", file=sys.stderr)
        return 1
    print(f"  {PROVIDER} v{schema['version']} serves {schema['counts']['list_resources']} list resources")

    gist = discover_gist()

    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        print("ERROR: Failed to clone/update terraform-provider-azurerm", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="azurerm-list-check-") as temp_dir:
        script_path = Path(temp_dir) / "list_identity_list.sh"
        script_path.write_text(gist["script_text"], encoding="utf-8")
        script_path.chmod(0o755)

        scan_dir = Path(temp_dir) / "services"
        nested_count = build_scan_tree(repo_dir, scan_dir)
        print(f"  Adapted {nested_count} resources from the nested resources/<name>/ layout")

        outputs = {}
        for mode in ("implemented", "list", "identity"):
            result = run_gist_mode(script_path, repo_dir, mode, scan_dir)
            if result["returncode"] != 0:
                print(result["stderr"], file=sys.stderr)
                print(f"ERROR: gist mode {mode} failed", file=sys.stderr)
                return result["returncode"]
            result["summary"] = parse_mode_summary(result["stdout"], mode)
            outputs[mode] = result

    summary = build_summary_data(gist, repo_dir, outputs, Path(args.details_file), schema)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    generate_report(summary, outputs, Path(args.output_html))

    print(
        "Generated AzureRM list validation artifacts: "
        f"{args.output_json}, {args.output_html}"
    )
    v = summary["validation"]
    print(
        f"Validation: registry={v['dashboard_list_resources']} "
        f"schema={v['schema_list_resources']} "
        f"gist={v['gist_with_list']} "
        f"match={v['matches']} "
        f"source_scan_complete={v['source_scan_complete']}"
    )
    if summary["source_scan_diff"]["not_in_source_scan"]:
        print(
            "  Released but missed by the gist scan: "
            + ", ".join(summary["source_scan_diff"]["not_in_source_scan"]),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())