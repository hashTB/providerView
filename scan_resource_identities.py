#!/usr/bin/env python3
"""
Scan Provider Source Code for Resource Identities

This script clones/fetches provider source code and counts resources
that implement Resource Identity (have the Identity: &schema.ResourceIdentity{} field).

Usage:
    python scan_resource_identities.py                    # Scan all known providers
    python scan_resource_identities.py --provider aws     # Scan specific provider
    python scan_resource_identities.py --output data/resource_identities.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


# Known major providers to scan
PROVIDERS_TO_SCAN = {
    'hashicorp/aws': {
        'repo': 'hashicorp/terraform-provider-aws',
        'resource_path': 'internal/service',
        'pattern': r'\.go$',  # All go files
        'identity_file_pattern': r'_identity_gen_test\.go$',  # AWS uses identity test files
    },
    'hashicorp/azurerm': {
        'repo': 'hashicorp/terraform-provider-azurerm', 
        'resource_path': 'internal/services',
        'pattern': r'_resource\.go$',
        'identity_file_pattern': None,  # Uses code pattern
    },
    'hashicorp/google': {
        'repo': 'hashicorp/terraform-provider-google',
        'resource_path': 'google/services',
        'pattern': r'\.go$',
        'identity_file_pattern': None,
    },
    'hashicorp/google-beta': {
        'repo': 'hashicorp/terraform-provider-google-beta',
        'resource_path': 'google-beta/services',
        'pattern': r'\.go$',
        'identity_file_pattern': None,
    },
}

# Patterns that indicate Resource Identity support
IDENTITY_PATTERNS = [
    r'Identity:\s*&schema\.ResourceIdentity\{',
    r'Identity:\s*&pluginsdk\.ResourceIdentity\{',
    r'schema\.ResourceIdentity\{',
    r'pluginsdk\.ResourceIdentity\{',
    r'GenerateIdentitySchema\(',
    r'ImporterValidatingIdentity\(',
]


def clone_or_update_repo(repo: str, target_dir: Path) -> bool:
    """Clone or update a GitHub repository."""
    repo_url = f"https://github.com/{repo}.git"
    
    if target_dir.exists():
        print(f"  Updating {repo}...")
        result = subprocess.run(
            ['git', '-C', str(target_dir), 'pull', '--ff-only'],
            capture_output=True, text=True
        )
        return result.returncode == 0
    else:
        print(f"  Cloning {repo}...")
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, str(target_dir)],
            capture_output=True, text=True
        )
        return result.returncode == 0


def find_resource_files(base_path: Path, resource_path: str, pattern: str) -> List[Path]:
    """Find all resource files matching the pattern."""
    search_path = base_path / resource_path
    if not search_path.exists():
        print(f"  Warning: {search_path} does not exist")
        return []
    
    regex = re.compile(pattern)
    resource_files = []
    
    for root, dirs, files in os.walk(search_path):
        for file in files:
            if regex.search(file):
                resource_files.append(Path(root) / file)
    
    return resource_files


def check_file_for_identity(file_path: Path) -> Tuple[bool, Optional[str]]:
    """Check if a file contains Resource Identity definition."""
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        
        for pattern in IDENTITY_PATTERNS:
            if re.search(pattern, content):
                # Try to extract resource name
                # Look for patterns like: const resourceName = "azurerm_resource_group"
                # Or func resource...() *pluginsdk.Resource
                name_match = re.search(r'const \w+ResourceName\s*=\s*["\']([^"\']+)["\']', content)
                if not name_match:
                    name_match = re.search(r'ResourceType:\s*["\']([^"\']+)["\']', content)
                if not name_match:
                    # Use filename
                    name = file_path.stem.replace('_resource', '')
                else:
                    name = name_match.group(1)
                return True, name
        
        return False, None
    except Exception as e:
        print(f"  Error reading {file_path}: {e}")
        return False, None


def scan_provider(provider_name: str, config: dict, repos_dir: Path) -> Dict:
    """Scan a provider for Resource Identity support."""
    print(f"\nScanning {provider_name}...")
    
    repo = config['repo']
    repo_dir = repos_dir / repo.replace('/', '_')
    
    # Clone/update repo
    if not clone_or_update_repo(repo, repo_dir):
        print(f"  Failed to clone/update {repo}")
        return {'error': 'Failed to clone repo', 'resources_with_identity': 0}
    
    resources_with_identity = []
    total_resource_files = 0
    
    # Check if we use identity file pattern (like AWS's _identity_gen_test.go files)
    identity_file_pattern = config.get('identity_file_pattern')
    
    if identity_file_pattern:
        # AWS-style: count files matching the identity pattern
        identity_files = find_resource_files(
            repo_dir,
            config['resource_path'],
            identity_file_pattern
        )
        print(f"  Found {len(identity_files)} identity test files")
        
        for file_path in identity_files:
            # Extract resource name from filename like "cluster_identity_gen_test.go"
            filename = file_path.stem
            resource_name = filename.replace('_identity_gen_test', '').replace('_identity_gen', '')
            resources_with_identity.append({
                'name': resource_name,
                'file': str(file_path.relative_to(repo_dir)),
            })
        
        # Also count total resource files for reference
        resource_files = find_resource_files(
            repo_dir,
            config['resource_path'],
            r'\.go$'
        )
        # Filter out test files for count
        total_resource_files = len([f for f in resource_files if '_test.go' not in str(f)])
    else:
        # Azure/Google-style: scan resource files for identity patterns in code
        resource_files = find_resource_files(
            repo_dir, 
            config['resource_path'], 
            config['pattern']
        )
        total_resource_files = len(resource_files)
        print(f"  Found {total_resource_files} resource files")
        
        for file_path in resource_files:
            has_identity, resource_name = check_file_for_identity(file_path)
            if has_identity:
                resources_with_identity.append({
                    'name': resource_name,
                    'file': str(file_path.relative_to(repo_dir)),
                })
    
    print(f"  Found {len(resources_with_identity)} resources with Identity support")
    
    return {
        'provider': provider_name,
        'repo': repo,
        'total_resource_files': total_resource_files,
        'resources_with_identity_count': len(resources_with_identity),
        'resources_with_identity': resources_with_identity,
        'scanned_at': datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description='Scan providers for Resource Identity support')
    parser.add_argument('--provider', '-p', help='Specific provider to scan (e.g., aws, azurerm)')
    parser.add_argument('--output', '-o', default='data/resource_identities.json', help='Output file')
    parser.add_argument('--repos-dir', default=None, help='Directory to store cloned repos')
    args = parser.parse_args()
    
    # Set up repos directory
    if args.repos_dir:
        repos_dir = Path(args.repos_dir)
        repos_dir.mkdir(parents=True, exist_ok=True)
    else:
        repos_dir = Path(tempfile.mkdtemp(prefix='tf_providers_'))
        print(f"Using temp directory: {repos_dir}")
    
    # Determine which providers to scan
    if args.provider:
        # Find matching provider
        provider_key = None
        for key in PROVIDERS_TO_SCAN:
            if args.provider.lower() in key.lower():
                provider_key = key
                break
        
        if not provider_key:
            print(f"Unknown provider: {args.provider}")
            print(f"Known providers: {', '.join(PROVIDERS_TO_SCAN.keys())}")
            sys.exit(1)
        
        providers = {provider_key: PROVIDERS_TO_SCAN[provider_key]}
    else:
        providers = PROVIDERS_TO_SCAN
    
    # Scan providers
    results = {
        'scanned_at': datetime.now().isoformat(),
        'providers': {},
    }
    
    for provider_name, config in providers.items():
        result = scan_provider(provider_name, config, repos_dir)
        results['providers'][provider_name] = result
    
    # Summary
    print("\n" + "="*50)
    print("SUMMARY - Resources with Resource Identity Support")
    print("="*50)
    total_identities = 0
    for provider_name, data in results['providers'].items():
        count = data.get('resources_with_identity_count', 0)
        total_identities += count
        print(f"  {provider_name}: {count}")
    print(f"\nTotal: {total_identities}")
    
    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n✅ Saved to {output_path}")
    
    return results


if __name__ == '__main__':
    main()
