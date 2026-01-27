#!/usr/bin/env python3
"""
Detailed Azure Resource Identity Scanner

Based on katbyte's script, this scans the Azure provider for:
- Resources with/without Resource Identity
- Typed vs Untyped resources  
- List vs No List support
- Reasons why identity can't be implemented (scoped, composite, etc.)

Usage:
    python scan_azure_identity_detailed.py                    # Scan with default path
    python scan_azure_identity_detailed.py --repos-dir /tmp   # Custom repos dir
    python scan_azure_identity_detailed.py --output data/azure_identity_detailed.json
"""

import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def clone_or_update_repo(repos_dir: Path) -> Optional[Path]:
    """Clone or update the Azure provider repo."""
    repo_url = "https://github.com/hashicorp/terraform-provider-azurerm.git"
    repo_dir = repos_dir / "hashicorp_terraform-provider-azurerm"
    
    if repo_dir.exists():
        print("  Updating terraform-provider-azurerm...")
        result = subprocess.run(
            ['git', '-C', str(repo_dir), 'pull', '--ff-only'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  Warning: git pull failed: {result.stderr}")
    else:
        print("  Cloning terraform-provider-azurerm...")
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, str(repo_dir)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  Error: git clone failed: {result.stderr}")
            return None
    
    return repo_dir


def is_typed_resource(content: str) -> bool:
    """Check if resource uses typed SDK (sdk.Resource interface)."""
    return bool(re.search(r'func.*Create\(\).*sdk\.ResourceFunc|_ sdk\.Resource', content))


def has_resource_identity(content: str, is_typed: bool) -> bool:
    """Check if resource has Resource Identity implemented."""
    if is_typed:
        return bool(re.search(r'sdk\.ResourceWithIdentity', content))
    else:
        return bool(re.search(
            r'GenerateIdentitySchema|ImporterValidatingIdentity|Identity:\s*&schema\.ResourceIdentity',
            content
        ))


def has_list_support(service_dir: Path, basename: str) -> bool:
    """Check if resource has List implementation."""
    list_file = service_dir / f"{basename}_resource_list.go"
    if list_file.exists():
        content = list_file.read_text(errors='ignore')
        return 'FrameworkListWrappedResource' in content
    return False


def check_scoped_id(content: str, service_dir: Path) -> bool:
    """Check if resource uses scoped IDs (variable parent scope)."""
    if re.search(r'Scoped[A-Za-z]*ID|ParseScoped|NewScoped|ValidateScoped|ScopeSegment|ScopedExtension|ScopedFlux', content):
        return True
    
    # Check for base resource delegation
    if re.search(r'base\s+[a-zA-Z]+BaseResource', content):
        for base_file in service_dir.glob('*_base.go'):
            base_content = base_file.read_text(errors='ignore')
            if re.search(r'Scoped[A-Za-z]*ID|ParseScoped|NewScoped|ValidateScoped|ScopeSegment', base_content):
                return True
    
    return False


def check_composite_id(content: str) -> bool:
    """Check if resource uses composite IDs."""
    return 'CompositeResourceID' in content


def check_provider_component(content: str) -> bool:
    """Check if resource is a provider-level component."""
    return 'ProviderComponent' in content


def check_data_plane(content: str) -> bool:
    """Check if resource uses data plane API (non-ARM)."""
    return bool(re.search(r'DataPlane|dataplane', content, re.IGNORECASE))


def check_custom_parse_id(content: str) -> bool:
    """Check if resource uses custom parse package."""
    return bool(re.search(r'parse\.[A-Z][a-zA-Z]*ID', content))


def check_nested_resource(content: str) -> bool:
    """Check if resource is nested (ID from property)."""
    return bool(
        re.search(r'd\.Get\("[a-z_]+_id"\)', content) and 
        re.search(r'Parse.*ID', content)
    )


def check_numeric_segment(content: str) -> bool:
    """Check if resource has numeric segment in ID (breaks strcase.ToSnake)."""
    return bool(re.search(
        r'[a-z][0-9]+(Name|Id|ID)\b|\.([A-Za-z]+v[0-9]+[A-Za-z]*)\b|ServerGroupsv2|ApiV[0-9]|Version[0-9]Name',
        content
    ))


def check_extension_resource(filename: str) -> bool:
    """Check if resource is an extension (attaches to any resource)."""
    return '_extension' in filename


def check_azuread_provider(resource_file: Path) -> bool:
    """Check if test file uses azuread provider."""
    test_file = resource_file.with_name(resource_file.stem + '_test.go')
    if test_file.exists():
        content = test_file.read_text(errors='ignore')
        return bool(re.search(r'provider\s+"azuread"|azuread_|"hashicorp/azuread"', content))
    return False


def get_resource_name(content: str, basename: str, is_typed: bool) -> str:
    """Extract the Terraform resource name."""
    if is_typed:
        match = re.search(r'func.*ResourceType\(\).*string.*\n.*return\s*"(azurerm_[^"]*)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    
    match = re.search(r'"(azurerm_[a-z0-9_]*)"', content)
    if match:
        return match.group(1)
    
    return f"azurerm_{basename}"


def determine_reason(content: str, service_dir: Path, filename: str, resource_file: Path) -> str:
    """Determine why a resource doesn't have identity implemented."""
    if check_azuread_provider(resource_file):
        return "azuread_provider"
    if check_numeric_segment(content):
        return "numeric_segment"
    if check_scoped_id(content, service_dir):
        return "scoped_id"
    if check_composite_id(content):
        return "composite_id"
    if check_provider_component(content):
        return "provider_component"
    if check_data_plane(content):
        return "data_plane"
    if check_custom_parse_id(content):
        return "custom_parse_id"
    if check_nested_resource(content):
        return "nested_resource"
    if check_extension_resource(filename):
        return "extension_resource"
    return "eligible"


def scan_azure_provider(repos_dir: Path) -> Dict:
    """Scan the Azure provider for detailed Resource Identity info."""
    print("\nScanning hashicorp/azurerm...")
    
    repo_dir = clone_or_update_repo(repos_dir)
    if not repo_dir:
        return {'error': 'Failed to clone repo'}
    
    services_dir = repo_dir / 'internal' / 'services'
    if not services_dir.exists():
        return {'error': 'Services directory not found'}
    
    resources = []
    
    # Scan all service directories
    for service_dir in sorted(services_dir.iterdir()):
        if not service_dir.is_dir():
            continue
        
        service_name = service_dir.name
        
        for resource_file in service_dir.glob('*_resource.go'):
            filename = resource_file.name
            
            # Skip test files, data sources, list files, base files
            if any(x in filename for x in ['_test.go', '_data_source.go', '_resource_list.go', '_base.go']):
                continue
            
            basename = filename.replace('_resource.go', '')
            
            try:
                content = resource_file.read_text(errors='ignore')
            except Exception as e:
                print(f"  Error reading {resource_file}: {e}")
                continue
            
            # Determine resource type
            is_typed = is_typed_resource(content)
            resource_type = "typed" if is_typed else "untyped"
            
            # Check for Resource Identity
            has_identity = has_resource_identity(content, is_typed)
            
            # Check for List support
            has_list = has_list_support(service_dir, basename)
            
            # Get resource name
            resource_name = get_resource_name(content, basename, is_typed)
            
            # Determine reason if no identity
            reason = "implemented" if has_identity else determine_reason(
                content, service_dir, filename, resource_file
            )
            
            resources.append({
                'service': service_name,
                'resource_name': resource_name,
                'resource_type': resource_type,
                'has_identity': has_identity,
                'has_list': has_list,
                'reason': reason,
                'file': str(resource_file.relative_to(repo_dir)),
            })
    
    # Calculate summaries
    total = len(resources)
    with_identity = [r for r in resources if r['has_identity']]
    without_identity = [r for r in resources if not r['has_identity']]
    
    typed_with_identity = len([r for r in with_identity if r['resource_type'] == 'typed'])
    untyped_with_identity = len([r for r in with_identity if r['resource_type'] == 'untyped'])
    
    with_list = len([r for r in resources if r['has_list']])
    identity_with_list = len([r for r in with_identity if r['has_list']])
    
    # Reason breakdown for resources without identity
    reason_counts = {}
    for r in without_identity:
        reason = r['reason']
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    
    summary = {
        'total_resources': total,
        'with_identity': {
            'count': len(with_identity),
            'percentage': round(len(with_identity) / total * 100, 1) if total else 0,
            'typed': typed_with_identity,
            'untyped': untyped_with_identity,
            'with_list': identity_with_list,
            'without_list': len(with_identity) - identity_with_list,
        },
        'without_identity': {
            'count': len(without_identity),
            'percentage': round(len(without_identity) / total * 100, 1) if total else 0,
            'by_reason': reason_counts,
        },
        'list_support': {
            'total_with_list': with_list,
            'percentage': round(with_list / total * 100, 1) if total else 0,
        },
    }
    
    print(f"\n  Total resources: {total}")
    print(f"  With Identity: {len(with_identity)} ({summary['with_identity']['percentage']}%)")
    print(f"    - Typed: {typed_with_identity}")
    print(f"    - Untyped: {untyped_with_identity}")
    print(f"    - With List: {identity_with_list}")
    print(f"  Without Identity: {len(without_identity)} ({summary['without_identity']['percentage']}%)")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    - {reason}: {count}")
    
    return {
        'provider': 'hashicorp/azurerm',
        'scanned_at': datetime.now().isoformat(),
        'summary': summary,
        'resources': resources,
    }


def main():
    parser = argparse.ArgumentParser(description='Detailed Azure Resource Identity Scanner')
    parser.add_argument('--repos-dir', default='/tmp/tf_repos', help='Directory for cloned repos')
    parser.add_argument('--output', '-o', default='data/azure_identity_detailed.json', help='Output file')
    args = parser.parse_args()
    
    repos_dir = Path(args.repos_dir)
    repos_dir.mkdir(parents=True, exist_ok=True)
    
    result = scan_azure_provider(repos_dir)
    
    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"\n✅ Saved detailed results to {output_path}")
    
    # Also update the simple resource_identities.json
    simple_output = Path('data/resource_identities.json')
    if simple_output.exists():
        with open(simple_output) as f:
            simple_data = json.load(f)
    else:
        simple_data = {'providers': {}}
    
    simple_data['providers']['hashicorp/azurerm'] = {
        'resources_with_identity_count': result['summary']['with_identity']['count'],
        'typed_count': result['summary']['with_identity']['typed'],
        'untyped_count': result['summary']['with_identity']['untyped'],
        'with_list_count': result['summary']['with_identity']['with_list'],
        'scanned_at': result['scanned_at'],
    }
    
    with open(simple_output, 'w') as f:
        json.dump(simple_data, f, indent=2)
    
    print(f"✅ Updated {simple_output}")


if __name__ == '__main__':
    main()
