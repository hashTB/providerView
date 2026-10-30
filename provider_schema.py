#!/usr/bin/env python3
"""
Ask a released Terraform provider what it serves, via ``terraform providers schema -json``.

Shared by the AzureRM, AWS and Google list-check generators so that their headline
counts come from the provider binary itself instead of source-layout heuristics,
which silently break whenever upstream reorganises its code.

Requires Terraform >= 1.14 on PATH, or its path in the TERRAFORM_BIN environment variable.

Usage (standalone check):
    python3 provider_schema.py hashicorp/google
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

REGISTRY_PROVIDER_URL = "https://registry.terraform.io/v1/providers/{provider}"
USER_AGENT = "providerView-provider-schema/1.0"
MIN_TERRAFORM_VERSION = (1, 14, 0)
PLUGIN_CACHE_DIR_NAME = "terraform-plugin-cache"
COMMAND_TIMEOUT_SECONDS = 900

SCHEMA_KEYS = {
    "resources": "resource_schemas",
    "data_sources": "data_source_schemas",
    "ephemeral_resources": "ephemeral_resource_schemas",
    "list_resources": "list_resource_schemas",
    "identity_resources": "resource_identity_schemas",
    "actions": "action_schemas",
}


class ProviderSchemaError(RuntimeError):
    pass


def find_terraform() -> str:
    terraform = os.environ.get("TERRAFORM_BIN") or shutil.which("terraform")
    if not terraform:
        raise ProviderSchemaError(
            "terraform CLI not found: install Terraform >= 1.14 or set TERRAFORM_BIN"
        )
    return terraform


def get_terraform_version(terraform: str) -> str:
    result = subprocess.run(
        [terraform, "version", "-json"],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ProviderSchemaError(f"terraform version failed: {result.stderr.strip()}")
    version = json.loads(result.stdout)["terraform_version"]
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:3])
    if parts < MIN_TERRAFORM_VERSION:
        minimum = ".".join(str(p) for p in MIN_TERRAFORM_VERSION)
        raise ProviderSchemaError(
            f"Terraform {version} is too old to report list resources; need >= {minimum}"
        )
    return version


def get_latest_provider_version(provider: str) -> str:
    """Latest published version, i.e. the version the Registry docs describe."""
    req = urllib.request.Request(
        REGISTRY_PROVIDER_URL.format(provider=provider),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["version"]


def run_terraform(terraform: str, args: list[str], cwd: str, env: dict) -> str:
    try:
        result = subprocess.run(
            [terraform, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderSchemaError(f"terraform {' '.join(args)} timed out after {exc.timeout}s") from exc
    if result.returncode != 0:
        raise ProviderSchemaError(
            f"terraform {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout


def fetch_provider_schema(provider: str, cache_dir: Path, version: str | None = None) -> dict:
    """Install ``provider`` (latest release by default) and summarise its schema."""
    terraform = find_terraform()
    terraform_version = get_terraform_version(terraform)
    version = version or get_latest_provider_version(provider)
    local_name = provider.split("/")[1]

    plugin_cache = (cache_dir / PLUGIN_CACHE_DIR_NAME).resolve()
    plugin_cache.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TF_PLUGIN_CACHE_DIR": str(plugin_cache),
        "TF_IN_AUTOMATION": "1",
        "CHECKPOINT_DISABLE": "1",
    }

    with tempfile.TemporaryDirectory(prefix="provider-schema-") as work_dir:
        Path(work_dir, "main.tf").write_text(
            "terraform {\n"
            "  required_providers {\n"
            f"    {local_name} = {{\n"
            f"      source  = \"{provider}\"\n"
            f"      version = \"{version}\"\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        run_terraform(terraform, ["init", "-input=false", "-no-color", "-backend=false"], work_dir, env)
        raw = json.loads(run_terraform(terraform, ["providers", "schema", "-json"], work_dir, env))

    address = f"registry.terraform.io/{provider}"
    schemas = raw.get("provider_schemas", {}).get(address)
    if schemas is None:
        raise ProviderSchemaError(f"no schema for {address} in terraform output")

    return {
        "provider": provider,
        "version": version,
        "terraform_version": terraform_version,
        "counts": {key: len(schemas.get(field) or {}) for key, field in SCHEMA_KEYS.items()},
        "list_resources": sorted(schemas.get("list_resource_schemas") or {}),
    }


def load_registry_list_slugs(details_path: Path, provider: str) -> list[str] | None:
    """Registry ``list-resources`` doc slugs for ``provider``, or None if the details file is missing."""
    try:
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    except FileNotFoundError:
        return None

    entry = details.get(provider, {})
    docs = entry.get("docs", {}) if isinstance(entry, dict) else {}
    items = docs.get("list-resources", []) if isinstance(docs, dict) else []
    slugs: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            slug = item.get("slug") or item.get("title")
        else:
            slug = item
        if isinstance(slug, str) and slug:
            slugs.add(slug)
    return sorted(slugs)


def compare_with_registry(
    schema_names: list[str],
    registry_slugs: list[str] | None,
    resource_prefix: str,
    other_provider_names: list[str] | None = None,
) -> dict:
    """Match Registry list-resource doc pages to the list resources the provider serves.

    Registry slugs omit the provider prefix for some providers (azurerm, aws), a single
    page can document several resources (google_project_iam covers the _member and
    _binding list resources), and a provider's docs can include pages for resources only
    a sibling provider serves (google-beta).
    """
    schema_set = set(schema_names)
    if registry_slugs is None:
        return {
            "registry_pages": None,
            "exact_pages": 0,
            "combined_pages": {},
            "other_provider_pages": [],
            "unexplained_registry_pages": [],
            "undocumented_list_resources": sorted(schema_set),
            "in_sync": False,
        }

    prefix = f"{resource_prefix}_"
    pages = sorted({slug if slug.startswith(prefix) else prefix + slug for slug in registry_slugs})
    exact = [page for page in pages if page in schema_set]
    without_own_page = schema_set - set(exact)

    combined: dict[str, list[str]] = {}
    other_provider: list[str] = []
    unexplained: list[str] = []
    other_set = set(other_provider_names or [])
    for page in pages:
        if page in schema_set:
            continue
        members = sorted(name for name in without_own_page if name.startswith(page + "_"))
        if members:
            combined[page] = members
        elif page in other_set:
            other_provider.append(page)
        else:
            unexplained.append(page)

    covered = set(exact) | {name for members in combined.values() for name in members}
    undocumented = sorted(schema_set - covered)
    return {
        "registry_pages": len(pages),
        "exact_pages": len(exact),
        "combined_pages": combined,
        "other_provider_pages": other_provider,
        "unexplained_registry_pages": unexplained,
        "undocumented_list_resources": undocumented,
        "in_sync": not unexplained and not undocumented,
    }


def diff_source_scan(schema_names: list[str], source_names: set[str] | list[str]) -> dict:
    """Compare a source scan of the default branch with the released provider.

    ``not_in_source_scan`` entries are served by the release but invisible to the scan,
    which usually means the scan has a blind spot (e.g. a code-layout change upstream).
    """
    schema_set, source_set = set(schema_names), set(source_names)
    return {
        "source_only": sorted(source_set - schema_set),
        "not_in_source_scan": sorted(schema_set - source_set),
    }


def render_differences_html(
    comparison: dict,
    source_diff: dict | None,
    source_label: str,
    other_provider_label: str = "another provider",
) -> str:
    """Meta-rows explaining every Registry/schema/source difference; empty when there are none."""

    def names(values) -> str:
        return " ".join(f"<code>{html.escape(v)}</code>" for v in values)

    rows = []
    for page, members in comparison.get("combined_pages", {}).items():
        rows.append((f"Registry page <code>{html.escape(page)}</code> documents", names(members)))
    if comparison.get("other_provider_pages"):
        rows.append((f"Registry pages for {html.escape(other_provider_label)}-only resources", names(comparison["other_provider_pages"])))
    if comparison.get("unexplained_registry_pages"):
        rows.append(("⚠️ Registry pages the provider doesn't serve", names(comparison["unexplained_registry_pages"])))
    if comparison.get("undocumented_list_resources"):
        rows.append(("⚠️ Served without a Registry page", names(comparison["undocumented_list_resources"])))
    if source_diff and source_diff.get("not_in_source_scan"):
        rows.append((f"⚠️ Released but missed by the {html.escape(source_label)}", names(source_diff["not_in_source_scan"])))
    if source_diff and source_diff.get("source_only"):
        rows.append((f"In the {html.escape(source_label)} but not released yet", names(source_diff["source_only"])))

    if not rows:
        return ""
    body = "".join(
        f"<div class=\"meta-row\"><span>{label}</span><span>{value}</span></div>" for label, value in rows
    )
    return f"<div class=\"meta-grid\" style=\"margin-top:12px; grid-template-columns: 1fr;\"><div>{body}</div></div>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise a provider's schema via Terraform")
    parser.add_argument("provider", help="Provider address, e.g. hashicorp/google")
    parser.add_argument("--version", help="Provider version (default: latest release)")
    parser.add_argument("--cache-dir", default="/tmp/tf_repos", help="Directory for the plugin cache")
    args = parser.parse_args()

    schema = fetch_provider_schema(args.provider, Path(args.cache_dir), args.version)
    print(json.dumps({k: v for k, v in schema.items() if k != "list_resources"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
