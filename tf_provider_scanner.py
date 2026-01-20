#!/usr/bin/env python3
"""
Terraform Provider Scanner

This script collects data from Terraform providers via the Terraform Registry API
and produces a CSV similar to "The Provider 500" format.

Usage:
    python tf_provider_scanner.py                          # Scan all providers
    python tf_provider_scanner.py --provider hashicorp/azurerm  # Scan single provider
    python tf_provider_scanner.py --tier official          # Scan only official providers
    python tf_provider_scanner.py --limit 50               # Scan first 50 providers
"""

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


# API Base URLs
REGISTRY_V1_BASE = "https://registry.terraform.io/v1"
REGISTRY_V2_BASE = "https://registry.terraform.io/v2"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# Request headers
HEADERS = {
    "User-Agent": "TerraformProviderScanner/1.0",
    "Accept": "application/json",
}

# Rate limiting
REQUEST_DELAY = 0.1  # seconds between requests
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

# Global flag for GitHub checks
CHECK_GITHUB = True


@dataclass
class ProviderData:
    """Data class to hold provider information matching CSV structure."""
    provider: str = ""
    tier: str = ""
    latest_version: str = ""
    latest_version_published: str = ""
    created_at: str = ""
    
    # Protocol versions
    protocol_v4: bool = False
    protocol_v5: bool = False
    protocol_v6: bool = False
    
    # Cohorts (SDK/Framework detection)
    cohort_framework_only: bool = False
    cohort_sdkv2_only: bool = False
    cohort_framework_sdkv2: bool = False
    
    # Feature counts
    managed_resources: int = 0
    resource_identities: int = 0
    data_sources: int = 0
    ephemeral_resources: int = 0
    list_resources: int = 0
    actions: int = 0
    provider_functions: int = 0
    total_features: int = 0
    
    # Detailed docs (for JSON export)
    docs_detailed: dict = None
    
    # Error tracking
    error: str = ""


def make_request(url: str, retries: int = MAX_RETRIES) -> Optional[dict]:
    """Make an HTTP request with retry logic."""
    for attempt in range(retries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:  # Rate limited
                wait_time = RETRY_DELAY * (attempt + 1)
                print(f"  Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            if attempt == retries - 1:
                print(f"  HTTP Error {e.code} for {url}")
                return None
        except URLError as e:
            if attempt == retries - 1:
                print(f"  URL Error for {url}: {e.reason}")
                return None
        except Exception as e:
            if attempt == retries - 1:
                print(f"  Error for {url}: {e}")
                return None
        time.sleep(RETRY_DELAY)
    return None


def get_all_providers(tier: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Fetch all providers from the registry."""
    providers = []
    page = 1
    page_size = 100
    
    while True:
        url = f"{REGISTRY_V2_BASE}/providers?page[number]={page}&page[size]={page_size}"
        if tier:
            url += f"&filter[tier]={tier}"
        
        print(f"Fetching providers page {page}...")
        data = make_request(url)
        
        if not data or 'data' not in data:
            break
        
        for provider in data['data']:
            attrs = provider.get('attributes', {})
            providers.append({
                'full_name': attrs.get('full-name', ''),
                'tier': attrs.get('tier', ''),
                'namespace': attrs.get('namespace', ''),
                'name': attrs.get('name', ''),
                'source': attrs.get('source', ''),
            })
            
            if limit and len(providers) >= limit:
                return providers
        
        # Check for more pages
        meta = data.get('meta', {}).get('pagination', {})
        if page >= meta.get('total-pages', 1):
            break
        
        page += 1
        time.sleep(REQUEST_DELAY)
    
    return providers


def get_provider_versions(namespace: str, name: str) -> tuple[str, str, str, list[str]]:
    """Get version info for a provider."""
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    data = make_request(url)
    
    if not data:
        return "", "", "", []
    
    versions = data.get('versions', [])
    latest_version = versions[-1] if versions else ""
    first_version = versions[0] if versions else ""
    
    # Get published dates
    latest_published = ""
    created_at = ""
    
    if latest_version:
        version_url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{latest_version}"
        version_data = make_request(version_url)
        if version_data:
            latest_published = version_data.get('published_at', '')[:10]
    
    if first_version:
        version_url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{first_version}"
        version_data = make_request(version_url)
        if version_data:
            created_at = version_data.get('published_at', '')[:10]
    
    return latest_version, latest_published, created_at, versions


def get_protocol_versions(namespace: str, name: str, version: str) -> list[str]:
    """Get protocol versions supported by a provider version."""
    if not version:
        return []
    
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{version}/download/linux/amd64"
    data = make_request(url)
    
    if not data:
        return []
    
    return data.get('protocols', [])


def get_provider_docs(namespace: str, name: str, version: str = None) -> dict:
    """Get documentation/feature counts for a provider."""
    if version:
        url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{version}"
    else:
        url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    
    data = make_request(url)
    
    if not data:
        return {}
    
    docs = data.get('docs', [])
    
    # Count by category, only HCL docs (excluding CDKTF variants)
    categories = {}
    for doc in docs:
        lang = doc.get('language', 'hcl')
        if lang != 'hcl':
            continue
        cat = doc.get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    return categories


def get_provider_docs_detailed(namespace: str, name: str, version: str = None) -> dict:
    """Get detailed documentation including item names for a provider."""
    if version:
        url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{version}"
    else:
        url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    
    data = make_request(url)
    
    if not data:
        return {}
    
    docs = data.get('docs', [])
    
    # Group by category with details, only HCL docs
    categories = {}
    for doc in docs:
        lang = doc.get('language', 'hcl')
        if lang != 'hcl':
            continue
        cat = doc.get('category', 'unknown')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            'title': doc.get('title', doc.get('slug', 'Unknown')),
            'slug': doc.get('slug', ''),
            'subcategory': doc.get('subcategory', '')
        })
    
    return categories


def detect_cohort(protocols: list[str]) -> tuple[bool, bool, bool]:
    """
    Detect the SDK/Framework cohort based on protocols.
    
    Protocol mapping:
    - v4: Usually older SDK v1
    - v5: SDK v2 or Framework (both support v5)
    - v6: Framework (terraform-plugin-framework)
    
    Returns: (framework_only, sdkv2_only, framework_sdkv2)
    """
    has_v4 = any('4' in p for p in protocols)
    has_v5 = any('5' in p for p in protocols)
    has_v6 = any('6' in p for p in protocols)
    
    # Protocol v6 is a strong indicator of terraform-plugin-framework
    # Protocol v5 alone usually means SDK v2
    # Some providers support both v5 and v6 (mixed Framework + SDK v2)
    
    framework_only = has_v6 and not has_v5 and not has_v4
    sdkv2_only = (has_v5 or has_v4) and not has_v6
    framework_sdkv2 = has_v6 and (has_v5 or has_v4)
    
    return framework_only, sdkv2_only, framework_sdkv2


def detect_cohort_from_github(source_url: str) -> tuple[bool, bool, bool, bool]:
    """
    Detect the SDK/Framework cohort by checking the provider's go.mod file.
    
    This provides more accurate detection than protocol versions alone.
    
    Returns: (framework_only, sdkv2_only, framework_sdkv2, detected)
    The 'detected' flag indicates whether we could determine the cohort.
    """
    if not CHECK_GITHUB or not source_url:
        return False, False, False, False
    
    # Extract GitHub repo from source URL
    # Example: https://github.com/hashicorp/terraform-provider-azurerm
    if 'github.com' not in source_url:
        return False, False, False, False
    
    try:
        # Parse the repo path
        parts = source_url.replace('https://github.com/', '').split('/')
        if len(parts) < 2:
            return False, False, False, False
        
        owner, repo = parts[0], parts[1]
        
        # Try to fetch go.mod from main branch
        go_mod_url = f"{GITHUB_RAW_BASE}/{owner}/{repo}/main/go.mod"
        request = Request(go_mod_url, headers=HEADERS)
        
        with urlopen(request, timeout=10) as response:
            content = response.read().decode('utf-8')
        
        # Parse go.mod for direct vs indirect dependencies
        lines = content.split('\n')
        has_framework_direct = False
        has_sdk_v2_direct = False
        
        for line in lines:
            line_stripped = line.strip()
            # Check for direct framework dependency
            if 'github.com/hashicorp/terraform-plugin-framework ' in line_stripped:
                if '// indirect' not in line_stripped:
                    has_framework_direct = True
            # Check for direct SDK v2 dependency
            if 'github.com/hashicorp/terraform-plugin-sdk/v2' in line_stripped:
                if '// indirect' not in line_stripped:
                    has_sdk_v2_direct = True
        
        framework_only = has_framework_direct and not has_sdk_v2_direct
        sdkv2_only = has_sdk_v2_direct and not has_framework_direct
        framework_sdkv2 = has_framework_direct and has_sdk_v2_direct
        
        return framework_only, sdkv2_only, framework_sdkv2, True
        
    except Exception:
        # Fall back to protocol-based detection
        return False, False, False, False


def estimate_resource_identities(managed_resources: int, framework_only: bool, 
                                  framework_sdkv2: bool, provider_name: str) -> int:
    """
    Estimate the number of resources that support Resource Identity.
    
    Resource Identity is a terraform-plugin-framework feature that allows resources
    to be imported using identity attributes rather than just an ID string.
    
    Heuristics:
    - Framework-only providers: All resources have identity support
    - SDK v2 only providers: No resources have identity support  
    - Mixed providers: Some resources have identity support (estimate ~5-10%)
    
    Note: This is an estimate. Accurate detection requires schema analysis.
    """
    if framework_only:
        # Framework-only providers - all resources support identity
        return managed_resources
    elif framework_sdkv2:
        # Mixed providers - estimate based on typical adoption rates
        # Most large providers are gradually migrating resources to framework
        # Estimate ~5% of resources have been migrated with identity support
        return int(managed_resources * 0.05)
    else:
        # SDK v2 only - no identity support
        return 0


def scan_provider(provider_info: dict) -> ProviderData:
    """Scan a single provider and collect all its data."""
    full_name = provider_info['full_name']
    namespace = provider_info['namespace']
    name = provider_info['name']
    tier = provider_info['tier']
    source_url = provider_info.get('source', '')
    
    print(f"Scanning {full_name}...")
    
    result = ProviderData(provider=full_name, tier=tier)
    
    try:
        # Get version info
        latest_version, latest_published, created_at, versions = get_provider_versions(namespace, name)
        result.latest_version = latest_version
        result.latest_version_published = latest_published
        result.created_at = created_at
        
        # Get protocol versions
        protocols = get_protocol_versions(namespace, name, latest_version)
        result.protocol_v4 = any('4' in p for p in protocols)
        result.protocol_v5 = any('5' in p for p in protocols)
        result.protocol_v6 = any('6' in p for p in protocols)
        
        # Try to detect cohort from GitHub first (more accurate)
        gh_framework_only, gh_sdkv2_only, gh_framework_sdkv2, detected = detect_cohort_from_github(source_url)
        
        if detected:
            result.cohort_framework_only = gh_framework_only
            result.cohort_sdkv2_only = gh_sdkv2_only
            result.cohort_framework_sdkv2 = gh_framework_sdkv2
            # Update protocol v6 if we detected framework
            if gh_framework_only or gh_framework_sdkv2:
                result.protocol_v6 = True
        else:
            # Fall back to protocol-based detection
            result.cohort_framework_only, result.cohort_sdkv2_only, result.cohort_framework_sdkv2 = detect_cohort(protocols)
        
        # Get documentation/feature counts
        doc_counts = get_provider_docs(namespace, name, latest_version)
        
        result.managed_resources = doc_counts.get('resources', 0)
        result.data_sources = doc_counts.get('data-sources', 0)
        result.ephemeral_resources = doc_counts.get('ephemeral-resources', 0)
        result.list_resources = doc_counts.get('list-resources', 0)
        result.actions = doc_counts.get('actions', 0)
        result.provider_functions = doc_counts.get('functions', 0)
        
        # Get detailed docs for JSON export (for all providers with any features)
        if result.managed_resources > 0 or result.data_sources > 0 or result.actions > 0 or result.list_resources > 0:
            result.docs_detailed = get_provider_docs_detailed(namespace, name, latest_version)
        
        # Estimate resource identities based on framework usage
        result.resource_identities = estimate_resource_identities(
            result.managed_resources,
            result.cohort_framework_only,
            result.cohort_framework_sdkv2,
            full_name
        )
        
        # Calculate total features
        result.total_features = (
            result.managed_resources +
            result.resource_identities +
            result.data_sources +
            result.ephemeral_resources +
            result.list_resources +
            result.actions +
            result.provider_functions
        )
        
    except Exception as e:
        result.error = str(e)
        print(f"  Error scanning {full_name}: {e}")
    
    time.sleep(REQUEST_DELAY)
    return result


def format_number(n: int) -> str:
    """Format number with comma separators for large numbers."""
    return f"{n:,}"


def write_csv(providers: list[ProviderData], output_file: str, include_summary: bool = True):
    """Write provider data to CSV file."""
    # Match the CSV header format from the original
    headers = [
        'Provider',
        'Tier',
        'Latest Version',
        'Latest Version Published',
        'Created At',
        '',  # Empty column separator
        'Protocol v4',
        'Protocol v5',
        'Protocol v6',
        '',  # Empty column separator
        'Cohort: Framework only',
        'Cohort: SDKv2 only',
        'Cohort: Framework+SDKv2',
        '',  # Empty column separator
        'Managed Resources',
        'Resource Identities',
        'Data Sources',
        'Ephemeral Resources',
        'List Resources',
        'Actions',
        'Provider Functions',
        'Total Features',
    ]
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        # Calculate totals for summary row
        if include_summary and len(providers) > 1:
            total_providers = len(providers)
            total_protocol_v4 = sum(1 for p in providers if p.protocol_v4)
            total_protocol_v5 = sum(1 for p in providers if p.protocol_v5)
            total_protocol_v6 = sum(1 for p in providers if p.protocol_v6)
            total_framework_only = sum(1 for p in providers if p.cohort_framework_only)
            total_sdkv2_only = sum(1 for p in providers if p.cohort_sdkv2_only)
            total_framework_sdkv2 = sum(1 for p in providers if p.cohort_framework_sdkv2)
            total_resources = sum(p.managed_resources for p in providers)
            total_identities = sum(p.resource_identities for p in providers)
            total_data_sources = sum(p.data_sources for p in providers)
            total_ephemeral = sum(p.ephemeral_resources for p in providers)
            total_list = sum(p.list_resources for p in providers)
            total_actions = sum(p.actions for p in providers)
            total_functions = sum(p.provider_functions for p in providers)
            total_features = sum(p.total_features for p in providers)
            
            summary_row = [
                '',  # No provider name for summary
                '',
                '',
                '',
                '',
                '',
                total_protocol_v4,
                total_protocol_v5,
                total_protocol_v6,
                '',
                total_framework_only,
                total_sdkv2_only,
                total_framework_sdkv2,
                '',
                format_number(total_resources),
                format_number(total_identities),
                format_number(total_data_sources),
                total_ephemeral,
                total_list,
                total_actions,
                total_functions,
                format_number(total_features),
            ]
            writer.writerow(summary_row)
        
        for p in providers:
            row = [
                p.provider,
                p.tier,
                p.latest_version,
                p.latest_version_published,
                p.created_at,
                '',
                '✅' if p.protocol_v4 else '',
                '✅' if p.protocol_v5 else '',
                '✅' if p.protocol_v6 else '',
                '',
                '✅' if p.cohort_framework_only else '',
                '✅' if p.cohort_sdkv2_only else '',
                '✅' if p.cohort_framework_sdkv2 else '',
                '',
                format_number(p.managed_resources),
                format_number(p.resource_identities),
                format_number(p.data_sources),
                p.ephemeral_resources,
                p.list_resources,
                p.actions,
                p.provider_functions,
                format_number(p.total_features),
            ]
            writer.writerow(row)
    
    print(f"\nWritten {len(providers)} providers to {output_file}")


def write_details_json(providers: list, output_file: str):
    """Write detailed docs to a JSON file for the dashboard."""
    import json
    
    details = {}
    for p in providers:
        if p.docs_detailed:
            details[p.provider] = {
                'version': p.latest_version,
                'docs': p.docs_detailed
            }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(details, f, indent=2)
    
    print(f"Written details for {len(details)} providers to {output_file}")


def load_existing_csv(csv_file: str) -> dict:
    """Load existing CSV data for incremental scanning.
    
    Returns a dict keyed by provider name with:
    - version: the version from the CSV
    - row_data: dict of all column data for reuse
    """
    import json
    existing = {}
    
    if not os.path.exists(csv_file):
        return existing
    
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                provider = row.get('Provider', '').strip()
                if not provider or provider == 'TOTAL':
                    continue
                version = row.get('Latest Version', '').strip()
                existing[provider] = {
                    'version': version,
                    'row_data': row
                }
        print(f"Loaded {len(existing)} existing providers from {csv_file}")
    except Exception as e:
        print(f"Warning: Could not load existing CSV: {e}")
    
    return existing


def load_existing_details_json(json_file: str) -> dict:
    """Load existing details JSON for incremental scanning."""
    import json
    
    if not os.path.exists(json_file):
        return {}
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load existing details JSON: {e}")
        return {}


def row_to_provider_data(row: dict, detailed_docs: dict = None) -> ProviderData:
    """Convert a CSV row back to ProviderData for reuse."""
    
    def parse_number(s):
        """Parse number from CSV format (handles commas)."""
        if not s:
            return 0
        return int(str(s).replace(',', '').strip() or 0)
    
    def parse_bool(s):
        """Parse boolean checkmark."""
        return s.strip() == '✅' if s else False
    
    return ProviderData(
        provider=row.get('Provider', ''),
        tier=row.get('Tier', ''),
        latest_version=row.get('Latest Version', ''),
        latest_version_published=row.get('Latest Version Published', ''),
        created_at=row.get('Created At', ''),
        protocol_v4=parse_bool(row.get('Protocol v4', '')),
        protocol_v5=parse_bool(row.get('Protocol v5', '')),
        protocol_v6=parse_bool(row.get('Protocol v6', '')),
        cohort_framework_only=parse_bool(row.get('Cohort: Framework only', '')),
        cohort_sdkv2_only=parse_bool(row.get('Cohort: SDKv2 only', '')),
        cohort_framework_sdkv2=parse_bool(row.get('Cohort: Framework+SDKv2', '')),
        managed_resources=parse_number(row.get('Managed Resources', '0')),
        resource_identities=parse_number(row.get('Resource Identities', '0')),
        data_sources=parse_number(row.get('Data Sources', '0')),
        ephemeral_resources=parse_number(row.get('Ephemeral Resources', '0')),
        list_resources=parse_number(row.get('List Resources', '0')),
        actions=parse_number(row.get('Actions', '0')),
        provider_functions=parse_number(row.get('Provider Functions', '0')),
        total_features=parse_number(row.get('Total Features', '0')),
        docs_detailed=detailed_docs,
        error=''
    )


def get_provider_version_quick(namespace: str, name: str) -> str:
    """Quick version check without full scan."""
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    data = make_request(url)
    if data:
        return data.get('version', '')
    return ''


def main():
    parser = argparse.ArgumentParser(
        description='Scan Terraform providers and collect feature data.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Scan all providers
  %(prog)s --provider hashicorp/azurerm       # Scan a single provider
  %(prog)s --tier official                    # Scan only official providers
  %(prog)s --tier partner --limit 100         # Scan first 100 partner providers
  %(prog)s --output my_providers.csv          # Custom output file
  %(prog)s --incremental                      # Only scan changed providers (faster)
  %(prog)s -i --tier official                 # Incremental scan of official providers
        """
    )
    
    parser.add_argument(
        '--provider', '-p',
        type=str,
        help='Scan a single provider (format: namespace/name, e.g., hashicorp/azurerm)'
    )
    
    parser.add_argument(
        '--tier', '-t',
        type=str,
        choices=['official', 'partner', 'community'],
        help='Filter by provider tier'
    )
    
    parser.add_argument(
        '--limit', '-l',
        type=int,
        help='Limit number of providers to scan'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default='terraform_providers.csv',
        help='Output CSV file (default: terraform_providers.csv)'
    )
    
    parser.add_argument(
        '--parallel', '-j',
        type=int,
        default=1,
        help='Number of parallel workers (default: 1, be careful with rate limiting)'
    )
    
    parser.add_argument(
        '--no-github',
        action='store_true',
        help='Disable GitHub checks for SDK/Framework detection (faster but less accurate)'
    )
    
    parser.add_argument(
        '--incremental', '-i',
        action='store_true',
        help='Only scan providers that have changed since last run (compares versions)'
    )
    
    args = parser.parse_args()
    
    # Set global flag for GitHub checks
    global CHECK_GITHUB
    CHECK_GITHUB = not args.no_github
    
    print("=" * 60)
    print("Terraform Provider Scanner")
    print("=" * 60)
    print()
    
    # Initialize for summary stats
    providers_reused = []
    scanned_results = []
    
    if args.provider:
        # Single provider mode
        parts = args.provider.split('/')
        if len(parts) != 2:
            print(f"Error: Provider must be in format 'namespace/name', got: {args.provider}")
            sys.exit(1)
        
        namespace, name = parts
        provider_info = {
            'full_name': args.provider,
            'namespace': namespace,
            'name': name,
            'tier': '',  # Will be filled in
            'source': '',  # Will be filled in
        }
        
        # Get tier and source info
        url = f"{REGISTRY_V2_BASE}/providers/{namespace}/{name}"
        data = make_request(url)
        if data and 'data' in data:
            attrs = data['data'].get('attributes', {})
            provider_info['tier'] = attrs.get('tier', '')
            provider_info['source'] = attrs.get('source', '')
        
        result = scan_provider(provider_info)
        results = [result]
        scanned_results = [result]
        
    else:
        # Multi-provider mode
        print(f"Fetching provider list...")
        if args.tier:
            print(f"  Filtering by tier: {args.tier}")
        if args.limit:
            print(f"  Limiting to: {args.limit} providers")
        
        providers = get_all_providers(tier=args.tier, limit=args.limit)
        print(f"Found {len(providers)} providers to scan\n")
        
        # Incremental mode: load existing data and check for changes
        existing_data = {}
        existing_details = {}
        providers_to_scan = []
        providers_reused = []
        
        if args.incremental:
            print("Incremental mode enabled - checking for version changes...")
            existing_data = load_existing_csv(args.output)
            json_output = args.output.replace('.csv', '_details.json')
            existing_details = load_existing_details_json(json_output)
            
            for provider_info in providers:
                full_name = provider_info['full_name']
                namespace = provider_info['namespace']
                name = provider_info['name']
                
                if full_name in existing_data:
                    # Check if version has changed
                    existing_version = existing_data[full_name]['version']
                    current_version = get_provider_version_quick(namespace, name)
                    
                    if current_version and current_version == existing_version:
                        # Version unchanged - reuse existing data
                        detailed_docs = existing_details.get(full_name, {}).get('docs')
                        reused = row_to_provider_data(
                            existing_data[full_name]['row_data'],
                            detailed_docs
                        )
                        providers_reused.append(reused)
                        print(f"  ✓ {full_name} v{current_version} (unchanged)")
                    else:
                        # Version changed - needs rescan
                        providers_to_scan.append(provider_info)
                        print(f"  ↻ {full_name}: {existing_version} → {current_version} (needs scan)")
                else:
                    # New provider
                    providers_to_scan.append(provider_info)
                    print(f"  + {full_name} (new)")
            
            print(f"\nSummary: {len(providers_reused)} unchanged, {len(providers_to_scan)} to scan\n")
        else:
            providers_to_scan = providers
        
        # Scan providers that need scanning
        scanned_results = []
        if providers_to_scan:
            if args.parallel > 1:
                # Parallel scanning
                with ThreadPoolExecutor(max_workers=args.parallel) as executor:
                    futures = {executor.submit(scan_provider, p): p for p in providers_to_scan}
                    for future in as_completed(futures):
                        result = future.result()
                        scanned_results.append(result)
            else:
                # Sequential scanning
                for i, provider_info in enumerate(providers_to_scan, 1):
                    print(f"[{i}/{len(providers_to_scan)}] ", end='')
                    result = scan_provider(provider_info)
                    scanned_results.append(result)
        
        # Combine reused and newly scanned results
        results = providers_reused + scanned_results

    
    # Sort by provider name
    results.sort(key=lambda x: x.provider.lower())
    
    # Write results
    write_csv(results, args.output)
    
    # Write details JSON for dashboard
    json_output = args.output.replace('.csv', '_details.json')
    write_details_json(results, json_output)
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total providers: {len(results)}")
    
    if args.incremental and not args.provider:
        print(f"  - Reused (unchanged): {len(providers_reused)}")
        print(f"  - Scanned (new/changed): {len(scanned_results)}")
    
    total_resources = sum(p.managed_resources for p in results)
    total_data_sources = sum(p.data_sources for p in results)
    total_features = sum(p.total_features for p in results)
    
    print(f"Total managed resources: {total_resources:,}")
    print(f"Total data sources: {total_data_sources:,}")
    print(f"Total features: {total_features:,}")
    
    errors = [p for p in results if p.error]
    if errors:
        print(f"\nProviders with errors: {len(errors)}")
        for p in errors[:5]:  # Show first 5 errors
            print(f"  - {p.provider}: {p.error}")


if __name__ == '__main__':
    main()
