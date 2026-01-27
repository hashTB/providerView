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
HEADERS = {
    "User-Agent": "TerraformProviderScanner/2.0",
    "Accept": "application/json",
}

# Rate limiting
REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_DELAY = 2


def make_request(url: str, retries: int = MAX_RETRIES) -> Optional[Dict]:
    """Make an API request with retry logic."""
    for attempt in range(retries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
            time.sleep(REQUEST_DELAY)
            return data
        except (HTTPError, URLError, json.JSONDecodeError) as e:
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
    
    return providers


def get_provider_versions(namespace: str, name: str) -> dict:
    """Get version info for a provider."""
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}/versions"
    data = make_request(url)
    
    if not data or 'versions' not in data:
        return {'versions': [], 'latest': None, 'protocols': []}
    
    versions = data['versions']
    if not versions:
        return {'versions': [], 'latest': None, 'protocols': []}
    
    # Get latest version details
    latest = versions[0]
    latest_version = latest.get('version', '')
    protocols = latest.get('protocols', [])
    published_at = latest.get('published_at', '')
    
    # Get all version numbers
    all_versions = [v.get('version', '') for v in versions]
    
    return {
        'versions': all_versions,
        'version_count': len(all_versions),
        'latest': latest_version,
        'latest_published': published_at,
        'protocols': protocols,
    }


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
