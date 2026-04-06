#!/usr/bin/env python3
"""
Run the latest AzureRM list-resource gist checker and generate validation artifacts.

This script:
- Fetches the latest revision of katbyte's list_identity_list.sh gist
- Runs it against the latest terraform-provider-azurerm source
- Compares the gist-derived List Resources count with the dashboard count
- Writes a compact JSON summary for the main dashboard
- Writes a standalone HTML report page with the full gist output

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

from scan_azure_identity_detailed import clone_or_update_repo

GIST_PAGE_URL = "https://gist.github.com/katbyte/502302a237bb765e0d24236070f0fa31"
GIST_RAW_FALLBACK_URL = (
    "https://gist.githubusercontent.com/katbyte/502302a237bb765e0d24236070f0fa31/raw/"
    "list_identity_list.sh"
)
USER_AGENT = "providerView-azurerm-list-check/1.0"
RAW_OUTPUT_JSON = "data/azurerm_list_check.json"
REPORT_HTML = "docs/azurerm-list-check.html"
DETAILS_FILE = "terraform_providers_details.json"


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


def run_gist_mode(script_path: Path, repo_dir: Path, mode: str) -> dict:
    result = subprocess.run(
        [str(script_path), mode, "internal/services"],
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


def load_dashboard_list_count(details_path: Path) -> int | None:
    try:
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    except FileNotFoundError:
        return None

    entry = details.get("hashicorp/azurerm", {})
    docs = entry.get("docs", {}) if isinstance(entry, dict) else {}
    list_resources = docs.get("list-resources", []) if isinstance(docs, dict) else []
    if isinstance(list_resources, list):
        return len(list_resources)
    return None


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


def build_summary_data(gist: dict, repo_dir: Path, outputs: dict, details_path: Path) -> dict:
    dashboard_count = load_dashboard_list_count(details_path)

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
    all_services = set(implemented_by_service.keys()) | set(missing_list_by_service.keys())
    for service in all_services:
        implemented = implemented_by_service.get(service, {"identity": set(), "list": set()})
        list_resources = implemented.get("list", set())
        missing_resources = missing_list_by_service.get(service, set())

        total_resources = len(list_resources | missing_resources)
        if total_resources == 0:
            continue

        list_count = len(list_resources)
        coverage_percent = round((list_count / total_resources) * 100)
        service_rows.append(
            {
                "service": service,
                "list_resources": list_count,
                "total_resources": total_resources,
                "percent": coverage_percent,
            }
        )

    service_rows.sort(key=lambda row: (-row["list_resources"], -row["percent"], row["service"]))

    matches = (
        dashboard_count is not None
        and gist_with_list is not None
        and dashboard_count == gist_with_list
    )

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
            "provider": "hashicorp/azurerm",
            "list_resources": dashboard_count,
        },
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
        },
        "validation": {
            "matches": matches,
            "dashboard_list_resources": dashboard_count,
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
    coverage = summary.get("service_coverage", {})
    match_icon = "✅" if validation["matches"] else "⚠️"
    match_text = "match" if validation["matches"] else "do not match"

    def fmt_val(value: int | None) -> str:
        return "N/A" if value is None else f"{value:,}"

    top_rows = coverage.get("top_services", [])
    if top_rows:
        table_rows = "\n".join(
            (
                "<tr>"
                f"<td><code>{html.escape(row['service'])}</code></td>"
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
    </style>
</head>
<body>
<div class=\"container\">
    <h1>AzureRM List Validation</h1>
    <nav class=\"topnav\">
        <a href=\"index.html\">All Providers</a>
        <a href=\"downloads.html\">📈 Download Trends</a>
        <a href=\"cloud-devex.html\">Cloud DevEx</a>
        <a href=\"azurerm-list-check.html\" class=\"active\">✅ AzureRM List Check</a>
        <a href=\"aws-list-check.html\">AWS List Check</a>
    </nav>
    <p class=\"subtitle\">Latest gist-backed AzureRM source scan compared against Registry-reflected list resources for hashicorp/azurerm. Generated {html.escape(summary['generated_at'])}.</p>

    <div class=\"cards\">
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['dashboard_list_resources'])}</div><div class=\"label\">Registry-Reflected List Resources</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['gist_with_list'])}</div><div class=\"label\">Gist Scan With List</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['total_resources'])}</div><div class=\"label\">AzureRM Resources Scanned</div></div>
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Counts {match_text}</div></div>
    </div>

    <div class=\"note\">
        <strong>{match_icon} Validation:</strong> Registry currently reflects <strong>{fmt_val(validation['dashboard_list_resources'])}</strong>,
        gist scan reports <strong>{fmt_val(validation['gist_with_list'])}</strong> list-enabled resources.
        This script fetches the latest secret gist on each run, so gist changes automatically flow into the next workflow execution.
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
    </div>

    <div class="section">
        <h2>Best Covered Services (List)</h2>
        <p class="subtitle" style="margin-bottom: 12px;">Top services by list-enabled resources from implemented mode. Services with at least one list-enabled resource: <strong>{fmt_val(coverage.get('services_with_list'))}</strong>.</p>
        <table>
            <thead>
                <tr>
                    <th>Service</th>
                    <th>List Resources</th>
                    <th>Total Resources</th>
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

    gist = discover_gist()

    repos_dir = Path(args.repos_dir)
    repos_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        print("ERROR: Failed to clone/update terraform-provider-azurerm", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="azurerm-list-check-") as temp_dir:
        script_path = Path(temp_dir) / "list_identity_list.sh"
        script_path.write_text(gist["script_text"], encoding="utf-8")
        script_path.chmod(0o755)

        outputs = {}
        for mode in ("implemented", "list", "identity"):
            result = run_gist_mode(script_path, repo_dir, mode)
            if result["returncode"] != 0:
                print(result["stderr"], file=sys.stderr)
                print(f"ERROR: gist mode {mode} failed", file=sys.stderr)
                return result["returncode"]
            result["summary"] = parse_mode_summary(result["stdout"], mode)
            outputs[mode] = result

    summary = build_summary_data(gist, repo_dir, outputs, Path(args.details_file))

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    generate_report(summary, outputs, Path(args.output_html))

    print(
        "Generated AzureRM list validation artifacts: "
        f"{args.output_json}, {args.output_html}"
    )
    print(
        "Validation: dashboard="
        f"{summary['validation']['dashboard_list_resources']} "
        "gist="
        f"{summary['validation']['gist_with_list']} "
        f"match={summary['validation']['matches']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())