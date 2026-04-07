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
    service_resources = {}
    details_pattern = re.compile(
        r"<details><summary><code>([^<]+)</code>\s+(\d+)%\s*\((\d+)/(\d+)\)(?:\s+[^<]+)?</summary><br>\s*(.*?)\s*</details>",
        re.S,
    )
    for match in details_pattern.finditer(output):
        service = match.group(1)
        services.append(
            {
                "service": service,
                "percent": int(match.group(2)),
                "implemented": int(match.group(3)),
                "total": int(match.group(4)),
            }
        )

        service_block = match.group(5)
        all_resources = []
        list_resources = []
        for row in re.finditer(r"\|\s+`([^`]+)`\s+\|\s+([✅❌])\s+\|\s+([✅❌])\s+\|", service_block):
            resource_path = row.group(1).strip()
            if not resource_path:
                continue
            all_resources.append(resource_path)
            if row.group(3) == "✅":
                list_resources.append(resource_path)

        service_resources[service] = {
            "all_resources": sorted(set(all_resources)),
            "list_resources": sorted(set(list_resources)),
        }

    return {
        "overall": {
            "percent": int(overall_match.group(1)) if overall_match else None,
            "implemented": int(overall_match.group(2)) if overall_match else None,
            "total": int(overall_match.group(3)) if overall_match else None,
        },
        "service_count": len(services),
        "services": services,
        "service_resources": service_resources,
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
            "service_resources": parsed.get("service_resources", {}),
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
    service_resources_js = json.dumps(coverage.get("service_resources", {}), ensure_ascii=False)

    if top_rows:
        table_rows = "\n".join(
            (
                "<tr>"
                f"<td><button class=\"service-link\" data-service=\"{html.escape(row['service'])}\">{html.escape(row['service'])}</button></td>"
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

    <div class="section">
        <h2>Service Coverage (List)</h2>
        <p class="subtitle" style="margin-bottom: 12px;">Top services by implemented list resources. Services with at least one list-enabled resource: <strong>{fmt_val(coverage.get('services_with_list'))}</strong>.</p>
        <div class="table-controls">
            <input id="aws-service-search" type="text" placeholder="Search by service (e.g. ec2, s3, iam)" />
            <select id="aws-service-sort">
                <option value="list_desc" selected>Sort: List Resources (high to low)</option>
                <option value="coverage_desc">Sort: Coverage % (high to low)</option>
                <option value="coverage_asc">Sort: Coverage % (low to high)</option>
                <option value="service_asc">Sort: Service (A-Z)</option>
                <option value="service_desc">Sort: Service (Z-A)</option>
            </select>
        </div>
        <table id="aws-coverage-table">
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

    <div class=\"section\">
        <h2>Raw Script Output</h2>
        <pre>{escaped_output}</pre>
    </div>
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
    initCoverageTable('aws-coverage-table', 'aws-service-search', 'aws-service-sort');
</script>
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
