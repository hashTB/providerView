#!/usr/bin/env python3
"""
Generate a Cloud DevEx page for Azure/AWS/GCP provider repositories.

This page combines existing providerView CSV metrics with public GitHub repo
metadata and go.mod dependency signals for selected cloud providers.

Outputs:
- docs/cloud-devex.html
- data/cloud_devex.json
"""

import base64
import csv
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CLOUD_PROVIDERS = {
    "Azure": [
        "hashicorp/azurerm",
        "azure/azapi",
        "hashicorp/azuread",
        "microsoft/fabric",
        "hashicorp/azurestack",
    ],
    "AWS": [
        "hashicorp/aws",
        "hashicorp/awscc",
    ],
    "GCP": [
        "hashicorp/google",
        "hashicorp/google-beta",
    ],
}

FAMILY_COLORS = {
    "Azure": "#00BCF2",
    "AWS": "#FF9900",
    "GCP": "#4285F4",
}


def parse_bool(value):
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y", "t", "x", "check", "checked", "\u2713", "\u2705"}


def to_int(value):
    if value is None:
        return 0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def iso_to_date(value):
    if not value:
        return ""
    return value[:10]


def years_since(iso_date):
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    return round((now - dt).days / 365.25, 2)


def fmt_num(n):
    if n is None:
        return "N/A"
    return f"{n:,}"


def fetch_json(url):
    headers = {"User-Agent": "providerView-cloud-devex/1.0"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def load_provider_csv(path):
    providers = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Provider") or "").strip().lower()
            if not name:
                continue
            providers[name] = row
    return providers


def load_sources(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    providers = data.get("providers", []) if isinstance(data, dict) else data
    result = {}
    for p in providers:
        full_name = (p.get("full_name") or "").strip().lower()
        source = (p.get("source") or "").strip()
        if full_name:
            result[full_name] = source
    return result


def source_to_repo(source_url):
    if not source_url:
        return None
    try:
        parsed = urllib.parse.urlparse(source_url)
    except ValueError:
        return None
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def fetch_repo_metadata(repo):
    if not repo:
        return {}
    try:
        data = fetch_json(f"https://api.github.com/repos/{repo}")
    except urllib.error.HTTPError:
        return {}
    except urllib.error.URLError:
        return {}
    return {
        "created_at": data.get("created_at"),
        "pushed_at": data.get("pushed_at"),
        "archived": bool(data.get("archived", False)),
        "stars": int(data.get("stargazers_count", 0) or 0),
        "open_issues": int(data.get("open_issues_count", 0) or 0),
        "default_branch": data.get("default_branch") or "main",
    }


def fetch_gomod_flags(repo, default_branch):
    if not repo:
        return {
            "has_framework": False,
            "has_mux": False,
            "has_sdk_v2": False,
            "has_sdk_v1": False,
            "in_tree": False,
            "go_mod_not_found": True,
        }

    url = f"https://api.github.com/repos/{repo}/contents/go.mod?ref={urllib.parse.quote(default_branch)}"
    try:
        content_data = fetch_json(url)
    except urllib.error.HTTPError:
        return {
            "has_framework": False,
            "has_mux": False,
            "has_sdk_v2": False,
            "has_sdk_v1": False,
            "in_tree": False,
            "go_mod_not_found": True,
        }
    except urllib.error.URLError:
        return {
            "has_framework": False,
            "has_mux": False,
            "has_sdk_v2": False,
            "has_sdk_v1": False,
            "in_tree": False,
            "go_mod_not_found": True,
        }

    encoded = content_data.get("content") or ""
    decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore") if encoded else ""

    return {
        "has_framework": bool(re.search(r"terraform-plugin-framework", decoded)),
        "has_mux": bool(re.search(r"terraform-plugin-mux", decoded)),
        "has_sdk_v2": bool(re.search(r"terraform-plugin-sdk/v2", decoded)),
        "has_sdk_v1": bool(re.search(r"terraform-plugin-sdk(?!/v2)", decoded)),
        "in_tree": bool(re.search(r"github\.com/hashicorp/terraform\s", decoded)),
        "go_mod_not_found": False,
    }


def build_rows(csv_rows, sources):
    rows = []
    for family, providers in CLOUD_PROVIDERS.items():
        for full_name in providers:
            row = csv_rows.get(full_name.lower(), {})
            source = sources.get(full_name.lower(), "")
            repo = source_to_repo(source)
            repo_meta = fetch_repo_metadata(repo)
            go_mod = fetch_gomod_flags(repo, repo_meta.get("default_branch", "main"))

            enriched = {
                "family": family,
                "provider": full_name,
                "tier": row.get("Tier", ""),
                "latest_version": row.get("Latest Version", ""),
                "latest_published": row.get("Latest Version Published", ""),
                "protocol_v5": parse_bool(row.get("Protocol v5", "")),
                "protocol_v6": parse_bool(row.get("Protocol v6", "")),
                "cohort_framework_only": parse_bool(row.get("Cohort: Framework only", "")),
                "cohort_sdkv2_only": parse_bool(row.get("Cohort: SDKv2 only", "")),
                "cohort_mixed": parse_bool(row.get("Cohort: Framework+SDKv2", "")),
                "downloads": to_int(row.get("Downloads", "0")),
                "source": source,
                "repo": repo,
                "repo_created_at": repo_meta.get("created_at"),
                "repo_pushed_at": repo_meta.get("pushed_at"),
                "repo_archived": repo_meta.get("archived", False),
                "repo_stars": repo_meta.get("stars", 0),
                "repo_open_issues": repo_meta.get("open_issues", 0),
                "repo_age_years": years_since(repo_meta.get("created_at")),
                **go_mod,
            }
            rows.append(enriched)
    return rows


def summarize_family(rows, family):
    fam_rows = [r for r in rows if r["family"] == family]
    count = len(fam_rows)
    if count == 0:
        return {
            "count": 0,
            "framework_only_pct": 0,
            "sdk_only_pct": 0,
            "mixed_pct": 0,
            "mux_pct": 0,
            "sdk_v1_pct": 0,
            "archived_count": 0,
            "avg_repo_age_years": None,
        }

    def pct(v):
        return round((v / count) * 100, 1)

    ages = [r["repo_age_years"] for r in fam_rows if r["repo_age_years"] is not None]
    return {
        "count": count,
        "framework_only_pct": pct(sum(1 for r in fam_rows if r["cohort_framework_only"])),
        "sdk_only_pct": pct(sum(1 for r in fam_rows if r["cohort_sdkv2_only"])),
        "mixed_pct": pct(sum(1 for r in fam_rows if r["cohort_mixed"])),
        "mux_pct": pct(sum(1 for r in fam_rows if r["has_mux"])),
        "sdk_v1_pct": pct(sum(1 for r in fam_rows if r["has_sdk_v1"])),
        "archived_count": sum(1 for r in fam_rows if r["repo_archived"]),
        "avg_repo_age_years": round(sum(ages) / len(ages), 2) if ages else None,
    }


def generate_html(rows, family_summary):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def badge(flag):
        return "yes" if flag else "no"

    html_rows = []
    for r in rows:
        html_rows.append(
            "<tr>"
            f"<td>{r['family']}</td>"
            f"<td>{r['provider']}</td>"
            f"<td>{r['tier']}</td>"
            f"<td>{r['latest_version']}</td>"
            f"<td>{iso_to_date(r['latest_published'])}</td>"
            f"<td>{'yes' if r['protocol_v5'] else 'no'}</td>"
            f"<td>{'yes' if r['protocol_v6'] else 'no'}</td>"
            f"<td>{'framework' if r['cohort_framework_only'] else ('sdkv2' if r['cohort_sdkv2_only'] else ('mixed' if r['cohort_mixed'] else 'unknown'))}</td>"
            f"<td>{badge(r['has_framework'])}</td>"
            f"<td>{badge(r['has_mux'])}</td>"
            f"<td>{badge(r['has_sdk_v2'])}</td>"
            f"<td>{badge(r['has_sdk_v1'])}</td>"
            f"<td>{badge(r['in_tree'])}</td>"
            f"<td>{'yes' if r['repo_archived'] else 'no'}</td>"
            f"<td>{fmt_num(r['repo_stars'])}</td>"
            f"<td>{iso_to_date(r['repo_created_at'])}</td>"
            f"<td>{r['repo_age_years'] if r['repo_age_years'] is not None else 'N/A'}</td>"
            f"<td>{iso_to_date(r['repo_pushed_at'])}</td>"
            f"<td><a href=\"{r['source']}\" target=\"_blank\">repo</a></td>"
            "</tr>"
        )

    summary_rows = []
    for fam in ("Azure", "AWS", "GCP"):
        s = family_summary[fam]
        summary_rows.append(
            "<tr>"
            f"<td>{fam}</td>"
            f"<td>{s['count']}</td>"
            f"<td>{s['framework_only_pct']}%</td>"
            f"<td>{s['sdk_only_pct']}%</td>"
            f"<td>{s['mixed_pct']}%</td>"
            f"<td>{s['mux_pct']}%</td>"
            f"<td>{s['sdk_v1_pct']}%</td>"
            f"<td>{s['archived_count']}</td>"
            f"<td>{s['avg_repo_age_years'] if s['avg_repo_age_years'] is not None else 'N/A'}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Cloud DevEx Signals</title>
  <style>
    :root {{
      --bg: #0f172a;
      --bg-card: #1e293b;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --border: #334155;
      --primary: #06b6d4;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    .topnav {{ display: flex; background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin: 16px 0 20px; }}
    .topnav a {{ color: var(--muted); text-decoration: none; padding: 12px 18px; border-right: 1px solid var(--border); }}
    .topnav a:last-child {{ border-right: none; }}
    .topnav a.active {{ color: var(--primary); background: rgba(6,182,212,0.1); font-weight: 600; }}
    .subtitle {{ color: var(--muted); margin-bottom: 18px; }}
    .section {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 18px; overflow-x: auto; }}
    h1 {{ font-size: 1.8rem; }}
    h2 {{ font-size: 1.1rem; margin-bottom: 10px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 0.72rem; }}
    a {{ color: var(--primary); }}
    .note {{ color: var(--muted); font-size: 0.88rem; line-height: 1.45; }}
  </style>
</head>
<body>
  <div class=\"container\">
    <h1>Cloud DevEx Signals</h1>
    <nav class=\"topnav\">
      <a href=\"index.html\">All Providers</a>
      <a href=\"downloads.html\">Download Trends</a>
      <a href=\"cloud-devex.html\" class=\"active\">Cloud DevEx</a>
      <a href=\"azurerm-list-check.html\">AzureRM List Check</a>
    </nav>
    <p class=\"subtitle\">Public GitHub signals for core cloud provider repos (Azure, AWS, GCP). Generated {now}.</p>

    <div class=\"section\">
      <h2>Family Summary</h2>
      <table>
        <thead>
          <tr>
            <th>Family</th>
            <th>Providers</th>
            <th>Framework-only</th>
            <th>SDKv2-only</th>
            <th>Mixed</th>
            <th>Mux usage</th>
            <th>SDK v1 usage</th>
            <th>Archived repos</th>
            <th>Avg repo age (years)</th>
          </tr>
        </thead>
        <tbody>
          {''.join(summary_rows)}
        </tbody>
      </table>
    </div>

    <div class=\"section\">
      <h2>Provider-Level Signals</h2>
      <table>
        <thead>
          <tr>
            <th>Family</th>
            <th>Provider</th>
            <th>Tier</th>
            <th>Latest version</th>
            <th>Published</th>
            <th>v5</th>
            <th>v6</th>
            <th>Cohort</th>
            <th>Framework</th>
            <th>Mux</th>
            <th>SDK v2</th>
            <th>SDK v1</th>
            <th>In-tree</th>
            <th>Archived</th>
            <th>Stars</th>
            <th>Repo created</th>
            <th>Repo age</th>
            <th>Last push</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {''.join(html_rows)}
        </tbody>
      </table>
    </div>

    <div class=\"section\">
      <div class=\"note\">
        Signals in this page are public and fetched from GitHub API endpoints for each provider repository.
        Cohort values come from providerView CSV, while repo/go.mod fields are fetched live at generation time.
      </div>
    </div>
  </div>
</body>
</html>
"""


def main():
    csv_path = Path("terraform_providers.csv")
    raw_path = Path("data/raw/providers_latest.json")
    out_html = Path("docs/cloud-devex.html")
    out_json = Path("data/cloud_devex.json")

    if not csv_path.exists():
        raise SystemExit("terraform_providers.csv not found")
    if not raw_path.exists():
        raise SystemExit("data/raw/providers_latest.json not found")

    csv_rows = load_provider_csv(csv_path)
    sources = load_sources(raw_path)
    rows = build_rows(csv_rows, sources)

    family_summary = {
        fam: summarize_family(rows, fam)
        for fam in ("Azure", "AWS", "GCP")
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "families": family_summary,
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(generate_html(rows, family_summary), encoding="utf-8")

    print(f"Generated {out_html}")
    print(f"Generated {out_json}")


if __name__ == "__main__":
    main()