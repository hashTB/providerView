#!/usr/bin/env python3
"""
Build historical trends from provider snapshots.

This script reads all snapshot files and builds a history.json file
that can be used to display trends in the dashboard.

Usage:
    python3 build_history.py [snapshots_dir] [output_file]
    
Defaults:
    snapshots_dir: ./data/snapshots
    output_file: ./data/history.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
import csv


def parse_snapshot_date(filename: str) -> str:
    """Extract date from snapshot filename like 'snapshot_2026-01-21.json' or '2026-01-21.csv'."""
    name = Path(filename).stem
    # Try to find a date pattern YYYY-MM-DD
    import re
    match = re.search(r'(\d{4}-\d{2}-\d{2})', name)
    if match:
        return match.group(1)
    return None


def load_snapshot_json(filepath: str) -> dict:
    """Load a JSON snapshot file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_snapshot_csv(filepath: str) -> dict:
    """Load a CSV snapshot file and convert to dict keyed by provider."""
    providers = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            provider = row.get('Provider', '')
            if not provider or provider == 'TOTAL':
                continue
            
            def parse_int(val):
                if not val:
                    return 0
                return int(str(val).replace(',', '').replace('"', '').strip() or 0)
            
            def parse_downloads(val):
                if not val:
                    return 0
                val = str(val).strip()
                multipliers = {'B': 1e9, 'M': 1e6, 'K': 1e3}
                for suffix, mult in multipliers.items():
                    if val.endswith(suffix):
                        try:
                            return int(float(val[:-1]) * mult)
                        except:
                            return 0
                try:
                    return int(val.replace(',', ''))
                except:
                    return 0
            
            providers[provider] = {
                'downloads': parse_downloads(row.get('Downloads', '')),
                'resources': parse_int(row.get('Managed Resources', 0)),
                'data_sources': parse_int(row.get('Data Sources', 0)),
                'list_resources': parse_int(row.get('List Resources', 0)),
                'actions': parse_int(row.get('Actions', 0)),
                'ephemeral_resources': parse_int(row.get('Ephemeral Resources', 0)),
                'functions': parse_int(row.get('Provider Functions', 0)),
                'total_features': parse_int(row.get('Total Features', 0)),
                'version': row.get('Latest Version', ''),
                'version_count': parse_int(row.get('Version Count', 0)),
            }
    
    return providers


def load_snapshot(filepath: str) -> dict:
    """Load a snapshot file (JSON or CSV)."""
    if filepath.endswith('.json'):
        return load_snapshot_json(filepath)
    elif filepath.endswith('.csv'):
        return load_snapshot_csv(filepath)
    return {}


def build_history(snapshots_dir: str, output_file: str):
    """Build history from all snapshots in the directory."""
    snapshots_path = Path(snapshots_dir)
    
    if not snapshots_path.exists():
        print(f"Snapshots directory not found: {snapshots_dir}")
        print("Creating directory and exiting. Run scanner with --snapshot to create snapshots.")
        snapshots_path.mkdir(parents=True, exist_ok=True)
        return
    
    # Find all snapshot files
    snapshot_files = []
    for ext in ['*.json', '*.csv']:
        snapshot_files.extend(snapshots_path.glob(ext))
    
    if not snapshot_files:
        print(f"No snapshot files found in {snapshots_dir}")
        return
    
    print(f"Found {len(snapshot_files)} snapshot files")
    
    # Load all snapshots with their dates
    snapshots = []
    for filepath in sorted(snapshot_files):
        date = parse_snapshot_date(filepath.name)
        if not date:
            print(f"  Skipping {filepath.name} - no date found")
            continue
        
        print(f"  Loading {filepath.name}...")
        data = load_snapshot(str(filepath))
        if data:
            snapshots.append({
                'date': date,
                'providers': data
            })
    
    if not snapshots:
        print("No valid snapshots loaded")
        return
    
    # Sort by date
    snapshots.sort(key=lambda x: x['date'])
    
    print(f"\nProcessing {len(snapshots)} snapshots from {snapshots[0]['date']} to {snapshots[-1]['date']}")
    
    # Build per-provider history
    history = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'snapshot_count': len(snapshots),
        'date_range': {
            'start': snapshots[0]['date'],
            'end': snapshots[-1]['date']
        },
        'dates': [s['date'] for s in snapshots],
        'providers': {}
    }
    
    # Collect all provider names
    all_providers = set()
    for snapshot in snapshots:
        all_providers.update(snapshot['providers'].keys())
    
    print(f"Found {len(all_providers)} unique providers")
    
    # Build time series for each provider
    metrics = ['downloads', 'resources', 'data_sources', 'list_resources', 'actions', 
               'ephemeral_resources', 'functions', 'total_features', 'version_count']
    
    for provider in sorted(all_providers):
        provider_history = {metric: [] for metric in metrics}
        provider_history['versions'] = []
        
        for snapshot in snapshots:
            pdata = snapshot['providers'].get(provider, {})
            for metric in metrics:
                provider_history[metric].append(pdata.get(metric, 0))
            provider_history['versions'].append(pdata.get('version', ''))
        
        # Only include providers that have some data
        if any(provider_history['total_features']):
            history['providers'][provider] = provider_history
    
    # Calculate trends (change from first to last snapshot)
    for provider, pdata in history['providers'].items():
        pdata['trends'] = {}
        for metric in metrics:
            values = pdata[metric]
            if values and len(values) >= 2 and values[0] > 0:
                first_nonzero = next((v for v in values if v > 0), 0)
                last = values[-1]
                if first_nonzero > 0:
                    pdata['trends'][metric] = {
                        'absolute': last - first_nonzero,
                        'percent': round(((last - first_nonzero) / first_nonzero) * 100, 1)
                    }
    
    # Write output
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2)
    
    print(f"\n✅ Written history to {output_file}")
    print(f"   {len(history['providers'])} providers with historical data")
    print(f"   {len(history['dates'])} data points")


def main():
    snapshots_dir = sys.argv[1] if len(sys.argv) > 1 else './data/snapshots'
    output_file = sys.argv[2] if len(sys.argv) > 2 else './data/history.json'
    
    build_history(snapshots_dir, output_file)


if __name__ == '__main__':
    main()
