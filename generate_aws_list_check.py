#!/usr/bin/env python3
"""
Run the upstream AWS list-tracking script and generate validation artifacts.

This script:
- Fetches the current list-tracking script used by terraform-provider-aws
- Runs it against the latest terraform-provider-aws source
- Compares tracked implemented list resources with the dashboard list-resources count
- Writes a compact JSON summary for the main dashboard
- Writes a standalone HTML report page with full script output

Usage:
    python3 generate_aws_list_check.py
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

from scan_aws_identity_detailed import clone_or_update_repo

ISSUE_URL = "https://github.com/hashicorp/terraform-provider-aws/issues/47005"
SCRIPT_RAW_URL = "https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/.ci/scripts/generate-list-tracking.sh"
SCRIPT_REPO_PATH = ".ci/scripts/generate-list-tracking.sh"
USER_AGENT = "providerView-aws-list-check/1.0"
RAW_OUTPUT_JSON = "data/aws_list_check.json"
REPORT_HTML = "docs/aws-list-check.html"
DETAILS_FILE = "terraform_providers_details.json"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def discover_script() -> dict:
    script_text = fetch_text(SCRIPT_RAW_URL)
    script_sha256 = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    return {
        "issue_url": ISSUE_URL,
        "raw_url": SCRIPT_RAW_URL,
        "repo_path": SCRIPT_REPO_PATH,
        "script_sha256": script_sha256,
        "script_text": script_text,
    }


def run_tracking_script(script_path: Path, repo_dir: Path) -> dict:
    result = subprocess.run(
        [str(script_path)],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def parse_tracking_output(output: str) -> dict:
    overall_match = re.search(r"Overall:\s*`\[[^\]]+\]\s*(\d+)%\s*\((\d+)/(\d+)\)`", output)
    updated_match = re.search(r"\*Last updated:\s*([^*]+)\*", output)

    services = []
    for match in re.finditer(
        r"<details><summary><code>([^<]+)</code>\s+(\d+)%\s*\((\d+)/(\d+)\)(?:\s+[^<]+)?</summary>",
        output,
    ):
        services.append(
            {
                "service": match.group(1),
                "percent": int(match.group(2)),
                "implemented": int(match.group(3)),
                "total": int(match.group(4)),
            }
        )

    return {
        "overall": {
            "percent": int(overall_match.group(1)) if overall_match else None,
            "implemented": int(overall_match.group(2)) if overall_match else None,
            "total": int(overall_match.group(3)) if overall_match else None,
        },
        "service_count": len(services),
        "services": services,
        "last_updated": updated_match.group(1).strip() if updated_match else None,
    }


def load_dashboard_list_count(details_path: Path) -> int | None:
    try:
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    except FileNotFoundError:
        return None

    entry = details.get("hashicorp/aws", {})
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


def build_summary_data(script: dict, repo_dir: Path, output: dict, details_path: Path) -> dict:
    parsed = parse_tracking_output(output["stdout"])
    dashboard_count = load_dashboard_list_count(details_path)
    implemented = parsed["overall"].get("implemented")
    total = parsed["overall"].get("total")
    service_rows = [s for s in parsed.get("services", []) if s.get("implemented", 0) > 0]
    service_rows.sort(key=lambda s: (-s["implemented"], -s["percent"], s["service"]))

    matches = (
        dashboard_count is not None
        and implemented is not None
        and dashboard_count == implemented
    )

    return {
        "generated_at": datetime.now().isoformat(),
        "issue": {
            "url": script["issue_url"],
        },
        "tracking_script": {
            "repo_path": script["repo_path"],
            "raw_url": script["raw_url"],
            "script_sha256": script["script_sha256"],
        },
        "source_repo": {
            "repo": "hashicorp/terraform-provider-aws",
            "commit": get_repo_commit(repo_dir),
        },
        "dashboard": {
            "provider": "hashicorp/aws",
            "list_resources": dashboard_count,
        },
        "script_results": {
            "returncode": output["returncode"],
            "stdout_line_count": len(output["stdout"].splitlines()),
            "stderr_line_count": len(output["stderr"].splitlines()),
            "parsed": parsed,
        },
        "service_coverage": {
            "services_with_list": len(service_rows),
            "top_services": service_rows[:15],
        },
        "validation": {
            "matches": matches,
            "dashboard_list_resources": dashboard_count,
            "script_implemented_list": implemented,
            "script_total_resources": total,
            "script_percent": parsed["overall"].get("percent"),
        },
    }


def generate_report(summary: dict, output: dict, out_path: Path) -> None:
    validation = summary["validation"]
    script = summary["tracking_script"]
    repo = summary["source_repo"]
    parsed = summary["script_results"]["parsed"]
    coverage = summary.get("service_coverage", {})
    match_icon = "OK" if validation["matches"] else "WARN"
    match_text = "match" if validation["matches"] else "do not match"

    def fmt_val(value: int | None) -> str:
        return "N/A" if value is None else f"{value:,}"

    escaped_output = html.escape(output["stdout"].strip() or "(no output)")
    top_rows = coverage.get("top_services", [])

    if top_rows:
        table_rows = "\n".join(
            (
                "<tr>"
                f"<td><code>{html.escape(row['service'])}</code></td>"
                f"<td>{fmt_val(row['implemented'])}</td>"
                f"<td>{fmt_val(row['total'])}</td>"
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
    <title>AWS List Validation</title>
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
    <h1>AWS List Validation</h1>
    <nav class=\"topnav\">
        <a href=\"index.html\">All Providers</a>
        <a href=\"downloads.html\">Download Trends</a>
        <a href=\"cloud-devex.html\">Cloud DevEx</a>
        <a href=\"azurerm-list-check.html\">AzureRM List Check</a>
        <a href=\"aws-list-check.html\" class=\"active\">AWS List Check</a>
    </nav>
    <p class=\"subtitle\">Latest AWS list-tracking scan compared against Registry-reflected list resources for hashicorp/aws. Generated {html.escape(summary['generated_at'])}.</p>


    <div class="section">
        <h2>Best Covered Services (List)</h2>
        <p class="subtitle" style="margin-bottom: 12px;">Top services by implemented list resources. Services with at least one list-enabled resource: <strong>{fmt_val(coverage.get('services_with_list'))}</strong>.</p>
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
    <div class=\"cards\">
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['dashboard_list_resources'])}</div><div class=\"label\">Registry-Reflected List Resources</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['script_implemented_list'])}</div><div class=\"label\">Tracking Script List Resources</div></div>
        <div class=\"card\"><div class=\"value\">{fmt_val(validation['script_total_resources'])}</div><div class=\"label\">AWS Resources Scanned</div></div>
        <div class=\"card\"><div class=\"value\">{match_icon}</div><div class=\"label\">Counts {match_text}</div></div>
    </div>

    <div class=\"note\">
        <strong>{match_icon} Validation:</strong> Registry currently reflects <strong>{fmt_val(validation['dashboard_list_resources'])}</strong> list resources,
        while the tracking script reports <strong>{fmt_val(validation['script_implemented_list'])}</strong> list-enabled resources.
        This script fetches the latest upstream AWS tracking script on each run, so upstream script changes flow into the next workflow execution.
        <div class=\"meta-grid\">
            <div>
                <div class=\"meta-row\"><span>Tracking issue</span><span><a href=\"{html.escape(summary['issue']['url'])}\">open</a></span></div>
                <div class=\"meta-row\"><span>Tracking script</span><span><a href=\"{html.escape(script['raw_url'])}\">open</a></span></div>
                <div class=\"meta-row\"><span>Script path</span><span><code>{html.escape(script['repo_path'])}</code></span></div>
                <div class=\"meta-row\"><span>Script last updated</span><span>{html.escape(parsed.get('last_updated') or 'N/A')}</span></div>
                <div class=\"meta-row\"><span>Service blocks</span><span>{fmt_val(parsed.get('service_count'))}</span></div>
            </div>
            <div>
                <div class=\"meta-row\"><span>AWS repo commit</span><span><code>{html.escape(repo['commit'] or 'unknown')}</code></span></div>
                <div class=\"meta-row\"><span>Overall progress</span><span>{fmt_val(validation['script_percent'])}% ({fmt_val(validation['script_implemented_list'])}/{fmt_val(validation['script_total_resources'])})</span></div>
                <div class=\"meta-row\"><span>Script SHA-256</span><span><code>{html.escape(script['script_sha256'][:16])}...</code></span></div>
            </div>
        </div>
    </div>

    <div class=\"section\">
        <h2>Raw Script Output</h2>
        <pre>{escaped_output}</pre>
    </div>
</div>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AWS list validation artifacts")
    parser.add_argument("--repos-dir", default="/tmp/tf_repos", help="Directory for cloned repos")
    parser.add_argument("--output-json", default=RAW_OUTPUT_JSON, help="Path for JSON summary output")
    parser.add_argument("--output-html", default=REPORT_HTML, help="Path for standalone HTML output")
    parser.add_argument("--details-file", default=DETAILS_FILE, help="Dashboard details JSON to validate against")
    args = parser.parse_args()

    script = discover_script()

    repos_dir = Path(args.repos_dir)
    repos_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        print("ERROR: Failed to clone/update terraform-provider-aws", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="aws-list-check-") as temp_dir:
        script_path = Path(temp_dir) / "generate-list-tracking.sh"
        script_path.write_text(script["script_text"], encoding="utf-8")
        script_path.chmod(0o755)

        output = run_tracking_script(script_path, repo_dir)
        if output["returncode"] != 0:
            print(output["stderr"], file=sys.stderr)
            print("ERROR: tracking script failed", file=sys.stderr)
            return output["returncode"]

    summary = build_summary_data(script, repo_dir, output, Path(args.details_file))

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    generate_report(summary, output, Path(args.output_html))

    print(
        "Generated AWS list validation artifacts: "
        f"{args.output_json}, {args.output_html}"
    )
    print(
        "Validation: dashboard="
        f"{summary['validation']['dashboard_list_resources']} "
        "script="
        f"{summary['validation']['script_implemented_list']} "
        f"match={summary['validation']['matches']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
