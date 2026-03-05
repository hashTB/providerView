#!/usr/bin/env python3
"""
Refresh terraform_providers_details.json with the latest doc details from
the Terraform Registry API.

Reads data/raw/providers_latest.json to identify providers that have
list-resources, actions, or ephemeral-resources, then fetches their full
doc listings from the registry and updates the details JSON.

Preserves existing data and only updates/adds entries.

Usage:
    python3 refresh_provider_details.py
    python3 refresh_provider_details.py --all          # Refresh all official providers
    python3 refresh_provider_details.py --provider hashicorp/aws
"""

import json
import time
import urllib.request
import urllib.error
import argparse
from pathlib import Path
from datetime import datetime

REGISTRY_V1_BASE = "https://registry.terraform.io/v1"
DETAILS_FILE = "terraform_providers_details.json"
RAW_LATEST = "data/raw/providers_latest.json"

# Rate limiting
REQUEST_DELAY = 0.3  # seconds between API calls


def make_request(url: str, retries: int = 2) -> dict:
    """Make an HTTP request with retries."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "terraform-provider-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  ⚠️  Failed to fetch {url}: {e}")
            return {}


def fetch_provider_docs(namespace: str, name: str) -> dict:
    """Fetch doc listings for a provider from the registry API.
    
    Returns a dict of category -> list of doc items (HCL only).
    """
    url = f"{REGISTRY_V1_BASE}/providers/{namespace}/{name}"
    data = make_request(url)
    
    if not data:
        return {}
    
    docs = data.get("docs", [])
    
    # Group by category, HCL only
    categories = {}
    for doc in docs:
        lang = doc.get("language", "hcl")
        if lang != "hcl":
            continue
        cat = doc.get("category", "unknown")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            "title": doc.get("title", doc.get("slug", "Unknown")),
            "slug": doc.get("slug", ""),
            "subcategory": doc.get("subcategory", ""),
        })
    
    return categories


def load_existing_details() -> dict:
    """Load existing details JSON, or return empty dict."""
    path = Path(DETAILS_FILE)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_raw_latest() -> list:
    """Load the latest raw snapshot to get provider metadata."""
    path = Path(RAW_LATEST)
    if not path.exists():
        print(f"⚠️  {RAW_LATEST} not found")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("providers", [])


def find_providers_to_refresh(providers: list, existing: dict, refresh_all_official: bool = False) -> list:
    """Find providers that need their details refreshed.
    
    Targets providers that have list-resources or actions (the new feature types),
    or any official provider if refresh_all_official is True.
    """
    to_refresh = []
    
    for p in providers:
        full_name = p.get("full_name", "")
        tier = p.get("tier", "")
        docs = p.get("docs", {})
        
        list_resources = docs.get("list_resources", 0)
        actions = docs.get("actions", 0)
        
        # Always refresh if provider has list-resources or actions
        if list_resources > 0 or actions > 0:
            to_refresh.append(full_name)
            continue
        
        # Optionally refresh all official providers
        if refresh_all_official and tier == "official":
            to_refresh.append(full_name)
    
    return sorted(set(to_refresh))


def refresh_provider(full_name: str, existing: dict) -> dict:
    """Fetch fresh doc details for a provider and merge with existing data."""
    parts = full_name.split("/")
    if len(parts) != 2:
        return existing.get(full_name, {})
    
    namespace, name = parts
    categories = fetch_provider_docs(namespace, name)
    
    if not categories:
        return existing.get(full_name, {})
    
    # Merge with existing entry (preserve version, downloads etc.)
    entry = existing.get(full_name, {}).copy()
    entry["docs"] = categories
    entry["docs_refreshed_at"] = datetime.now().isoformat()
    
    return entry


def main():
    parser = argparse.ArgumentParser(description="Refresh provider details JSON")
    parser.add_argument("--all", action="store_true",
                        help="Refresh all official providers (not just those with list/actions)")
    parser.add_argument("--provider", type=str,
                        help="Refresh a specific provider (e.g. hashicorp/aws)")
    args = parser.parse_args()
    
    print("=== Provider Details Refresh ===")
    print(f"Details file: {DETAILS_FILE}")
    print(f"Raw data: {RAW_LATEST}")
    print()
    
    existing = load_existing_details()
    print(f"Existing details: {len(existing)} providers")
    
    if args.provider:
        # Refresh a single provider
        to_refresh = [args.provider]
    else:
        # Find providers to refresh from raw data
        providers = load_raw_latest()
        if not providers:
            print("No providers found in raw data")
            return
        
        to_refresh = find_providers_to_refresh(providers, existing, refresh_all_official=args.all)
    
    print(f"Providers to refresh: {len(to_refresh)}")
    print()
    
    refreshed = 0
    for i, full_name in enumerate(to_refresh):
        print(f"[{i+1}/{len(to_refresh)}] {full_name}...", end=" ", flush=True)
        
        entry = refresh_provider(full_name, existing)
        if entry and entry.get("docs"):
            docs = entry["docs"]
            lr = len(docs.get("list-resources", []))
            act = len(docs.get("actions", []))
            res = len(docs.get("resources", []))
            ds = len(docs.get("data-sources", []))
            print(f"resources={res} data-sources={ds} list={lr} actions={act}")
            existing[full_name] = entry
            refreshed += 1
        else:
            print("no docs")
        
        time.sleep(REQUEST_DELAY)
    
    # Save updated details
    with open(DETAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    
    print()
    print(f"✅ Refreshed {refreshed}/{len(to_refresh)} providers")
    print(f"   Total providers in details: {len(existing)}")
    print(f"   Saved to {DETAILS_FILE}")


if __name__ == "__main__":
    main()
