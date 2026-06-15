#!/usr/bin/env python3
"""
Stage 1: Fetch Providers from Terraform Registry API

This script fetches raw provider data from the Terraform Registry API
and saves it as a dated JSON file. This is the slow part that makes
many API calls.

Usage:
    python fetch_providers.py                     # Fetch all providers
    python fetch_providers.py --tier official     # Fetch only official
    python fetch_providers.py --limit 100         # Fetch first 100
    python fetch_providers.py --output data/raw   # Custom output dir

Output:
    data/raw/providers_YYYY-MM-DD.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


# API Base URLs
REGISTRY_V1_BASE = "https://registry.terraform.io/v1"
REGISTRY_V2_BASE = "https://registry.terraform.io/v2"

# Request headers
# Include a date-stamped UA so each daily run lands on a distinct CDN cache key,
# and explicit no-cache directives to discourage stale CDN responses (we observed
# weekly snapshots returning identical totals because of CDN caching).
_RUN_STAMP = datetime.now().strftime('%Y%m%d')
HEADERS = {
    "User-Agent": f"TerraformProviderScanner/2.1 (run={_RUN_STAMP})",
    "Accept": "application/json",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Rate limiting
REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_DELAY = 2

# Cloud providers shown on the downloads trends page. We only fetch the
# summary metrics for these providers to avoid thousands of extra API calls.
TRACKED_DOWNLOAD_SUMMARY_PROVIDERS = {
    'hashicorp/aws',
    'hashicorp/awscc',
    'hashicorp/azurerm',
    'azure/azapi',
    'hashicorp/azuread',
    'hashicorp/azurestack',
    'microsoft/fabric',
    'hashicorp/google',
    'hashicorp/google-beta',
}

# The registry list endpoint occasionally omits some major providers.
# Ensure these are always present by resolving them directly.
REQUIRED_PROVIDERS = TRACKED_DOWNLOAD_SUMMARY_PROVIDERS


def get_provider_by_full_name(full_name: str) -> Optional[Dict]:
    """Fetch a provider directly from the v2 provider endpoint."""
    parts = full_name.split('/', 1)
    if len(parts) != 2:
        return None

    namespace, name = parts
    url = f"{REGISTRY_V2_BASE}/providers/{namespace}/{name}"
    data = make_request(url)

    if not data or 'data' not in data:
        return None

    provider_data = data['data']
    attrs = provider_data.get('attributes', {})
    return {
        'id': provider_data.get('id'),
        'full_name': attrs.get('full-name', full_name),
        'tier': attrs.get('tier', ''),
        'namespace': attrs.get('namespace', namespace),
        'name': attrs.get('name', name),
        'source': attrs.get('source', ''),
        'description': attrs.get('description', ''),
        'downloads': attrs.get('downloads', 0),
        'published_at': attrs.get('published-at', ''),
    }


def make_request(url: str, retries: int = MAX_RETRIES) -> Optional[Dict]:
    """Make an API request with retry logic.

    Adds a per-call cache-buster query param so CDN edge caches can't return a
    stale response keyed only on the URL. Honours ``Retry-After`` on HTTP 429.
    """
    sep = '&' if '?' in url else '?'
    bust_url = f"{url}{sep}_={int(time.time() * 1000)}"
    for attempt in range(retries):
        try:
            request = Request(bust_url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
            time.sleep(REQUEST_DELAY)
            return data
        except HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = RETRY_DELAY
                try:
                    wait = max(wait, int(e.headers.get('Retry-After', RETRY_DELAY)))
                except (TypeError, ValueError):
                    pass
                print(f"  HTTP 429 from {url}; sleeping {wait}s then retrying")
                time.sleep(wait)
                continue
            if attempt < retries - 1:
                print(f"  Retry {attempt + 1}/{retries} for {url}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  Failed after {retries} attempts: {url}")
                return None
        except (URLError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt + 1}/{retries} for {url}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  Failed after {retries} attempts: {url}")
                return None
    return None


def get_all_providers(tier: str = None, limit: int = None) -> List[Dict]:
    """Fetch all providers from the v2 API."""
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
                'id': provider.get('id'),
                'full_name': attrs.get('full-name', ''),
                'tier': attrs.get('tier', ''),
                'namespace': attrs.get('namespace', ''),
                'name': attrs.get('name', ''),
                'source': attrs.get('source', ''),
                'description': attrs.get('description', ''),
                'downloads': attrs.get('downloads', 0),
                'published_at': attrs.get('published-at', ''),
            })
            
            if limit and len(providers) >= limit:
                print(f"Reached limit of {limit} providers")
                return providers
        
        meta = data.get('meta', {}).get('pagination', {})
        total_pages = meta.get('total-pages', 1)
        print(f"  Got {len(data['data'])} providers (page {page}/{total_pages})")
        
        if page >= total_pages:
            break
        page += 1
    
    # Keep explicit limits stable for ad-hoc debugging runs.
    if not limit:
        present = {p.get('full_name', '') for p in providers}
        for full_name in sorted(REQUIRED_PROVIDERS):
            if full_name in present:
                continue

            direct = get_provider_by_full_name(full_name)
            if not direct:
                print(f"  Warning: Could not resolve required provider {full_name}")
                continue

            if tier and direct.get('tier') != tier:
                continue

            providers.append(direct)
            present.add(full_name)
            print(f"  Added missing required provider via direct lookup: {full_name}")

    return providers


def get_provider_versions(namespace: str, name: str) -> dict:
    """Get version info for a provider."""
    # First get the main endpoint to get the ACTUAL latest version
    main_url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    main_data = make_request(main_url)
    
    # Then get all versions for the count
    versions_url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/versions"
    versions_data = make_request(versions_url)
    
    result = {
        'versions': [],
        'version_count': 0,
        'latest': None,
        'latest_published': '',
        'protocols': [],
    }
    
    # Get latest version from main endpoint (this is authoritative)
    if main_data:
        result['latest'] = main_data.get('version', '')
        result['latest_published'] = main_data.get('published_at', '')
    
    # Get all versions and protocols from versions endpoint
    if versions_data and 'versions' in versions_data:
        versions = versions_data['versions']
        result['versions'] = [v.get('version', '') for v in versions]
        result['version_count'] = len(versions)
        
        # Find the protocols for the actual latest version
        latest_version = result['latest']
        for v in versions:
            if v.get('version') == latest_version:
                result['protocols'] = v.get('protocols', [])
                break
        
        # If we didn't find protocols for latest, collect all unique protocols
        if not result['protocols']:
            all_protocols = set()
            for v in versions:
                all_protocols.update(v.get('protocols', []))
            result['protocols'] = sorted(list(all_protocols))
    
    return result


def get_provider_docs(namespace: str, name: str, version: str) -> dict:
    """Get documentation/feature counts for a provider."""
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/{version}"
    data = make_request(url)
    
    if not data or 'docs' not in data:
        return {}
    
    docs = data['docs']
    counts = {}
    subcategories = set()
    
    for doc in docs:
        # Only count HCL docs (skip cdktf Python/TypeScript/etc.)
        if doc.get('language', 'hcl') != 'hcl':
            continue
        category = doc.get('category', 'other')
        counts[category] = counts.get(category, 0) + 1
        if subcat := doc.get('subcategory'):
            subcategories.add(subcat)
    
    return {
        'resources': counts.get('resources', 0),
        'data_sources': counts.get('data-sources', 0),
        'guides': counts.get('guides', 0),
        'functions': counts.get('functions', 0),
        'ephemeral_resources': counts.get('ephemeral-resources', 0),
        'list_resources': counts.get('list-resources', 0),
        'actions': counts.get('actions', 0),
        'subcategories': list(subcategories),
        'subcategory_count': len(subcategories),
    }


def get_provider_metadata(namespace: str, name: str) -> dict:
    """Get additional metadata from v2 API."""
    url = f"{REGISTRY_V2_BASE}/providers/{namespace}/{name}"
    data = make_request(url)
    
    if not data or 'data' not in data:
        return {}
    
    attrs = data['data'].get('attributes', {})
    return {
        'created_at': attrs.get('created-at', ''),
        'featured': attrs.get('featured', False),
        'logo_url': attrs.get('logo-url', ''),
        'robots_noindex': attrs.get('robots-noindex', False),
    }


def get_provider_download_summary(provider_id: str) -> dict:
    """Get Registry download summary metrics for a provider.

    The Registry page uses this undocumented v2 endpoint to show the current
    week/month/year/total download figures. We persist it in raw snapshots so
    the trends page can render the Registry's own numbers over time.

    If the response comes back with ``total == 0`` (a known stale-CDN failure
    mode where the endpoint silently returns zeros), we retry up to twice with
    a brief delay before giving up.
    """
    url = f"{REGISTRY_V2_BASE}/providers/{provider_id}/downloads/summary?version=all"

    for attempt in range(3):
        data = make_request(url)
        if not data or 'data' not in data:
            return {}

        attrs = data['data'].get('attributes', {})
        result = {
            'week': attrs.get('week'),
            'month': attrs.get('month'),
            'year': attrs.get('year'),
            'total': attrs.get('total'),
        }
        # Treat all-zero responses as suspect and retry — the Registry returns
        # real numbers when called interactively but the scheduled runner
        # occasionally gets a zeroed response.
        if result['total'] not in (None, 0):
            return result
        if attempt < 2:
            print(f"  Summary returned total=0 for provider id={provider_id}; retrying ({attempt + 1}/2)")
            time.sleep(RETRY_DELAY)
    return result


def fetch_provider_details(provider: dict) -> dict:
    """Fetch all details for a single provider."""
    namespace = provider['namespace']
    name = provider['name']
    full_name = provider['full_name']
    
    print(f"  Fetching {full_name}...")
    
    # Get version info
    version_info = get_provider_versions(namespace, name)
    provider['version_info'] = version_info
    
    # Get docs/features if we have a version
    if version_info.get('latest'):
        docs_info = get_provider_docs(namespace, name, version_info['latest'])
        provider['docs'] = docs_info
    else:
        provider['docs'] = {}
    
    # Get additional metadata
    metadata = get_provider_metadata(namespace, name)
    provider['metadata'] = metadata
    
    return provider


def main():
    parser = argparse.ArgumentParser(
        description='Fetch provider data from Terraform Registry API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--tier', choices=['official', 'partner', 'community'],
                        help='Filter by provider tier')
    parser.add_argument('--limit', type=int, default=0,
                        help='Maximum number of providers to fetch (0 = no limit)')
    parser.add_argument('--output', default='data/raw',
                        help='Output directory for JSON files')
    parser.add_argument('--skip-details', action='store_true',
                        help='Skip fetching individual provider details (faster)')
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate output filename with date
    date_str = datetime.now().strftime('%Y-%m-%d')
    output_file = output_dir / f'providers_{date_str}.json'
    
    print(f"=== Terraform Provider Fetcher ===")
    print(f"Output: {output_file}")
    print(f"Tier: {args.tier or 'all'}")
    print(f"Limit: {args.limit or 'none'}")
    print()
    
    # Fetch provider list
    print("Stage 1: Fetching provider list...")
    providers = get_all_providers(
        tier=args.tier,
        limit=args.limit if args.limit > 0 else None
    )
    print(f"Found {len(providers)} providers")
    print()

    # Fetch Registry summary metrics for the tracked cloud providers even when
    # running with --skip-details, since the downloads trends page relies on
    # these values being present in raw snapshots.
    tracked_lower = {name.lower() for name in TRACKED_DOWNLOAD_SUMMARY_PROVIDERS}
    tracked_providers = [
        provider for provider in providers
        if provider['full_name'].lower() in tracked_lower
    ]
    if tracked_providers:
        print("Stage 1b: Fetching download summaries for tracked providers...")
        for i, provider in enumerate(tracked_providers):
            print(f"[{i+1}/{len(tracked_providers)}] Summary for {provider['full_name']}...")
            provider['download_summary'] = get_provider_download_summary(provider['id'])
        print()
    
    # Fetch details for each provider
    if not args.skip_details:
        print("Stage 2: Fetching provider details...")
        for i, provider in enumerate(providers):
            print(f"[{i+1}/{len(providers)}]", end='')
            fetch_provider_details(provider)
        print()
    
    # Build output structure
    output = {
        'fetched_at': datetime.now().isoformat(),
        'date': date_str,
        'tier_filter': args.tier,
        'provider_count': len(providers),
        'providers': providers,
    }
    
    # Save to file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)
    
    print(f"✅ Saved {len(providers)} providers to {output_file}")
    print(f"   File size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")
    
    # Also save as 'latest' symlink/copy for easy access
    latest_file = output_dir / 'providers_latest.json'
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)
    print(f"   Also saved as {latest_file}")


if __name__ == '__main__':
    main()
