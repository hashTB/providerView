#!/usr/bin/env python3
"""
Stage 2: Analyze Provider Data

This script reads the raw provider JSON from Stage 1 and:
- Detects SDK/Framework cohorts (from known list + go.mod)
- Calculates resource identities
- Computes totals and statistics
- Outputs analyzed JSON and CSV

Usage:
    python analyze_providers.py                              # Use latest raw data
    python analyze_providers.py --input data/raw/providers_2026-01-27.json
    python analyze_providers.py --check-github               # Check go.mod for unknown providers

Output:
    data/processed/providers_analyzed.json
    terraform_providers.csv
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


# Known major providers that use Framework+SDKv2 (verified from go.mod)
# This avoids GitHub API rate limits for the most important providers
KNOWN_FRAMEWORK_SDKV2_PROVIDERS = {
    'hashicorp/aws',
    'hashicorp/azurerm', 
    'hashicorp/google',
    'hashicorp/google-beta',
    'hashicorp/azuread',
    'hashicorp/azurestack',
    'hashicorp/kubernetes',
    'hashicorp/helm',
    'hashicorp/vault',
    'hashicorp/consul',
    'hashicorp/nomad',
    'hashicorp/boundary',
    'hashicorp/waypoint',
    'hashicorp/hcp',
    'hashicorp/tfe',
    'mongodb/mongodbatlas',
    'datadog/datadog',
    'cloudflare/cloudflare',
    'grafana/grafana',
    'newrelic/newrelic',
    'pagerduty/pagerduty',
    'snowflake-labs/snowflake',
    'databricks/databricks',
    'oracle/oci',
    'digitalocean/digitalocean',
    'linode/linode',
    'vultr/vultr',
    'fastly/fastly',
    'auth0/auth0',
    'okta/okta',
    'splunk-terraform/splunk',
}

# Known Framework-only providers (no SDKv2)
KNOWN_FRAMEWORK_ONLY_PROVIDERS = {
    # Add providers that use only terraform-plugin-framework
}

# GitHub API settings
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_HEADERS = {
    "User-Agent": "TerraformProviderAnalyzer/2.0",
    "Accept": "application/vnd.github.v3.raw",
}
if GITHUB_TOKEN:
    GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def detect_cohort_from_protocols(protocols: list[str]) -> tuple[bool, bool, bool]:
    """
    Detect SDK/Framework cohort based on protocol versions.
    Returns: (framework_only, sdkv2_only, framework_sdkv2)
    """
    has_v4 = any('4' in str(p) for p in protocols)
    has_v5 = any('5' in str(p) for p in protocols)
    has_v6 = any('6' in str(p) for p in protocols)
    
    framework_only = has_v6 and not has_v5 and not has_v4
    sdkv2_only = (has_v5 or has_v4) and not has_v6
    framework_sdkv2 = has_v6 and (has_v5 or has_v4)
    
    return framework_only, sdkv2_only, framework_sdkv2


def detect_cohort_from_github(source_url: str) -> tuple[bool, bool, bool, bool]:
    """
    Detect cohort by checking go.mod on GitHub.
    Returns: (framework_only, sdkv2_only, framework_sdkv2, detected)
    """
    if not source_url or 'github.com' not in source_url:
        return False, False, False, False
    
    try:
        parts = source_url.replace('https://github.com/', '').split('/')
        if len(parts) < 2:
            return False, False, False, False
        
        owner, repo = parts[0], parts[1]
        go_mod_url = f"{GITHUB_RAW_BASE}/{owner}/{repo}/main/go.mod"
        
        request = Request(go_mod_url, headers=GITHUB_HEADERS)
        with urlopen(request, timeout=10) as response:
            content = response.read().decode('utf-8')
        
        has_framework = 'github.com/hashicorp/terraform-plugin-framework ' in content
        has_sdk_v2 = 'github.com/hashicorp/terraform-plugin-sdk/v2' in content
        
        # Check for indirect dependencies
        lines = content.split('\n')
        has_framework_direct = False
        has_sdk_v2_direct = False
        
        for line in lines:
            line = line.strip()
            if 'github.com/hashicorp/terraform-plugin-framework ' in line:
                if '// indirect' not in line:
                    has_framework_direct = True
            if 'github.com/hashicorp/terraform-plugin-sdk/v2' in line:
                if '// indirect' not in line:
                    has_sdk_v2_direct = True
        
        framework_only = has_framework_direct and not has_sdk_v2_direct
        sdkv2_only = has_sdk_v2_direct and not has_framework_direct
        framework_sdkv2 = has_framework_direct and has_sdk_v2_direct
        
        return framework_only, sdkv2_only, framework_sdkv2, True
        
    except Exception:
        return False, False, False, False


def get_resource_identities() -> int:
    """
    Get Resource Identity count.
    
    Only AWS and Azure have actual scanned data - loaded separately in dashboard.
    All other providers return 0 (no estimation).
    """
    return 0


def analyze_provider(provider: dict, check_github: bool = False) -> dict:
    """Analyze a single provider and compute derived fields."""
    full_name = provider.get('full_name', '')
    source_url = provider.get('source', '')
    
    # Get version info
    version_info = provider.get('version_info', {})
    protocols = version_info.get('protocols', [])
    
    # Detect cohort
    if full_name in KNOWN_FRAMEWORK_SDKV2_PROVIDERS:
        framework_only = False
        sdkv2_only = False
        framework_sdkv2 = True
    elif full_name in KNOWN_FRAMEWORK_ONLY_PROVIDERS:
        framework_only = True
        sdkv2_only = False
        framework_sdkv2 = False
    elif check_github and source_url:
        gh_fo, gh_so, gh_fs, detected = detect_cohort_from_github(source_url)
        if detected:
            framework_only, sdkv2_only, framework_sdkv2 = gh_fo, gh_so, gh_fs
        else:
            framework_only, sdkv2_only, framework_sdkv2 = detect_cohort_from_protocols(protocols)
    else:
        framework_only, sdkv2_only, framework_sdkv2 = detect_cohort_from_protocols(protocols)
    
    # Get feature counts
    docs = provider.get('docs', {})
    managed_resources = docs.get('resources', 0)
    data_sources = docs.get('data_sources', 0)
    functions = docs.get('functions', 0)
    ephemeral_resources = docs.get('ephemeral_resources', 0)
    list_resources = docs.get('list_resources', 0)
    actions = docs.get('actions', 0)
    guides = docs.get('guides', 0)
    subcategories = docs.get('subcategory_count', 0)
    
    # Resource identities - only from scanned data (AWS/Azure), not estimated
    resource_identities = get_resource_identities()
    
    # Calculate total features
    total_features = (
        managed_resources + resource_identities + data_sources +
        ephemeral_resources + list_resources + actions + functions
    )
    
    # Protocol detection
    has_v4 = any('4' in str(p) for p in protocols)
    has_v5 = any('5' in str(p) for p in protocols)
    has_v6 = any('6' in str(p) for p in protocols) or framework_only or framework_sdkv2
    
    # Calculate days since update
    days_since_update = 0
    if latest_pub := version_info.get('latest_published'):
        try:
            pub_date = datetime.fromisoformat(latest_pub.replace('Z', '+00:00'))
            days_since_update = (datetime.now(pub_date.tzinfo) - pub_date).days
        except:
            pass
    
    # Build analyzed result
    return {
        'provider': full_name,
        'tier': provider.get('tier', ''),
        'namespace': provider.get('namespace', ''),
        'name': provider.get('name', ''),
        'source': source_url,
        'description': provider.get('description', ''),
        
        # Version info
        'latest_version': version_info.get('latest', ''),
        'latest_published': version_info.get('latest_published', ''),
        'version_count': version_info.get('version_count', 0),
        'created_at': provider.get('metadata', {}).get('created_at', ''),
        'days_since_update': days_since_update,
        
        # Protocols
        'protocol_v4': has_v4,
        'protocol_v5': has_v5,
        'protocol_v6': has_v6,
        
        # Cohorts
        'cohort_framework_only': framework_only,
        'cohort_sdkv2_only': sdkv2_only,
        'cohort_framework_sdkv2': framework_sdkv2,
        
        # Features
        'managed_resources': managed_resources,
        'resource_identities': resource_identities,
        'data_sources': data_sources,
        'ephemeral_resources': ephemeral_resources,
        'list_resources': list_resources,
        'actions': actions,
        'functions': functions,
        'guides': guides,
        'subcategories': subcategories,
        'total_features': total_features,
        
        # Downloads
        'downloads': provider.get('downloads', 0),
    }


def format_number(n: int) -> str:
    """Format number with commas or abbreviations."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    elif n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n:,}"
    return str(n)


def write_csv(providers: list[dict], output_path: str):
    """Write providers to CSV file."""
    
    # CSV columns matching the original format
    columns = [
        'Provider', 'Tier', 'Latest Version', 'Latest Version Published',
        'Created At', 'Days Since Update', '',
        'Protocol v4', 'Protocol v5', 'Protocol v6', '',
        'Cohort: Framework only', 'Cohort: SDKv2 only', 'Cohort: Framework+SDKv2', '',
        'Managed Resources', 'Resource Identities', 'Data Sources',
        'Ephemeral Resources', 'List Resources', 'Actions', 'Provider Functions',
        'Guides', 'Total Features', '',
        'Downloads', 'Version Count', 'Subcategories'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        
        # Calculate totals
        totals = {
            'managed_resources': 0,
            'resource_identities': 0,
            'data_sources': 0,
            'ephemeral_resources': 0,
            'list_resources': 0,
            'actions': 0,
            'functions': 0,
            'guides': 0,
            'total_features': 0,
            'downloads': 0,
            'framework_only': 0,
            'sdkv2_only': 0,
            'framework_sdkv2': 0,
        }
        
        # Write providers
        for p in providers:
            totals['managed_resources'] += p['managed_resources']
            totals['resource_identities'] += p['resource_identities']
            totals['data_sources'] += p['data_sources']
            totals['ephemeral_resources'] += p['ephemeral_resources']
            totals['list_resources'] += p['list_resources']
            totals['actions'] += p['actions']
            totals['functions'] += p['functions']
            totals['guides'] += p['guides']
            totals['total_features'] += p['total_features']
            totals['downloads'] += p['downloads']
            if p['cohort_framework_only']:
                totals['framework_only'] += 1
            if p['cohort_sdkv2_only']:
                totals['sdkv2_only'] += 1
            if p['cohort_framework_sdkv2']:
                totals['framework_sdkv2'] += 1
            
            row = [
                p['provider'],
                p['tier'],
                p['latest_version'],
                p['latest_published'][:10] if p['latest_published'] else '',
                p['created_at'][:10] if p['created_at'] else '',
                p['days_since_update'],
                '',
                '✅' if p['protocol_v4'] else '',
                '✅' if p['protocol_v5'] else '',
                '✅' if p['protocol_v6'] else '',
                '',
                '✅' if p['cohort_framework_only'] else '',
                '✅' if p['cohort_sdkv2_only'] else '',
                '✅' if p['cohort_framework_sdkv2'] else '',
                '',
                format_number(p['managed_resources']),
                format_number(p['resource_identities']),
                format_number(p['data_sources']),
                format_number(p['ephemeral_resources']),
                format_number(p['list_resources']),
                format_number(p['actions']),
                format_number(p['functions']),
                format_number(p['guides']),
                format_number(p['total_features']),
                '',
                format_number(p['downloads']),
                p['version_count'],
                p['subcategories'],
            ]
            writer.writerow(row)
        
        # Write totals row
        writer.writerow([
            f'TOTAL ({len(providers)} providers)',
            '', '', '', '', '', '',
            '', '', '', '',
            totals['framework_only'],
            totals['sdkv2_only'],
            totals['framework_sdkv2'],
            '',
            format_number(totals['managed_resources']),
            format_number(totals['resource_identities']),
            format_number(totals['data_sources']),
            format_number(totals['ephemeral_resources']),
            format_number(totals['list_resources']),
            format_number(totals['actions']),
            format_number(totals['functions']),
            format_number(totals['guides']),
            format_number(totals['total_features']),
            '',
            format_number(totals['downloads']),
            '', '',
        ])
    
    print(f"✅ Written CSV to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze provider data and compute derived fields',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i',
                        help='Input JSON file (default: data/raw/providers_latest.json)')
    parser.add_argument('--output', '-o', default='data/processed',
                        help='Output directory')
    parser.add_argument('--csv', default='terraform_providers.csv',
                        help='Output CSV filename')
    parser.add_argument('--check-github', action='store_true',
                        help='Check go.mod on GitHub for unknown providers (slow)')
    
    args = parser.parse_args()
    
    # Find input file
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = Path('data/raw/providers_latest.json')
    
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        print("Run fetch_providers.py first to get raw data")
        sys.exit(1)
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"=== Terraform Provider Analyzer ===")
    print(f"Input: {input_path}")
    print(f"Output: {output_dir}")
    print(f"Check GitHub: {args.check_github}")
    print()
    
    # Load raw data
    print("Loading raw data...")
    with open(input_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    providers_raw = raw_data.get('providers', [])
    print(f"Loaded {len(providers_raw)} providers from {raw_data.get('date', 'unknown')}")
    print()
    
    # Analyze each provider
    print("Analyzing providers...")
    analyzed = []
    for i, provider in enumerate(providers_raw):
        if (i + 1) % 500 == 0:
            print(f"  Processed {i + 1}/{len(providers_raw)}...")
        analyzed.append(analyze_provider(provider, check_github=args.check_github))
    
    # Sort by downloads (descending)
    analyzed.sort(key=lambda p: p['downloads'], reverse=True)
    
    print(f"Analyzed {len(analyzed)} providers")
    print()
    
    # Save analyzed JSON
    output_json = output_dir / 'providers_analyzed.json'
    output_data = {
        'analyzed_at': datetime.now().isoformat(),
        'source_file': str(input_path),
        'source_date': raw_data.get('date', ''),
        'provider_count': len(analyzed),
        'providers': analyzed,
    }
    
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2)
    print(f"✅ Saved analyzed JSON to {output_json}")
    
    # Write CSV
    write_csv(analyzed, args.csv)
    
    # Print summary
    print()
    print("=== Summary ===")
    framework_only = sum(1 for p in analyzed if p['cohort_framework_only'])
    sdkv2_only = sum(1 for p in analyzed if p['cohort_sdkv2_only'])
    framework_sdkv2 = sum(1 for p in analyzed if p['cohort_framework_sdkv2'])
    total_resources = sum(p['managed_resources'] for p in analyzed)
    total_identities = sum(p['resource_identities'] for p in analyzed)
    
    print(f"Total providers: {len(analyzed)}")
    print(f"Framework only: {framework_only}")
    print(f"SDKv2 only: {sdkv2_only}")
    print(f"Framework+SDKv2: {framework_sdkv2}")
    print(f"Total resources: {total_resources:,}")
    print(f"Total resource identities: {total_identities:,}")


if __name__ == '__main__':
    main()
