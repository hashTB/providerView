# Terraform Provider Scanner

A Python script that collects data from Terraform providers via the Terraform Registry API and produces a CSV similar to "The Provider 500" format.

## Features

- Scans all Terraform providers from the registry
- Collects version information, protocol versions, and feature counts
- Supports filtering by tier (official, partner, community)
- Supports scanning a single provider
- Outputs data in CSV format matching the original "Provider 500" structure

## Requirements

- Python 3.7+
- No external dependencies (uses only standard library)

## Usage

### Scan a single provider (recommended for testing)

```bash
python3 tf_provider_scanner.py --provider hashicorp/azurerm
```

### Scan all official providers

```bash
python3 tf_provider_scanner.py --tier official
```

### Scan all partner providers

```bash
python3 tf_provider_scanner.py --tier partner
```

### Scan with a limit

```bash
python3 tf_provider_scanner.py --tier official --limit 50
```

### Scan all providers (takes a long time!)

```bash
python3 tf_provider_scanner.py
```

### Custom output file

```bash
python3 tf_provider_scanner.py --provider hashicorp/aws --output aws_provider.csv
```

## Output Format

The CSV output includes the following columns:

| Column | Description |
|--------|-------------|
| Provider | Full provider name (namespace/name) |
| Tier | official, partner, or community |
| Latest Version | Current latest version |
| Latest Version Published | Date of latest version release |
| Created At | Date of first version release |
| Protocol v4 | ✅ if supports Protocol v4 |
| Protocol v5 | ✅ if supports Protocol v5 |
| Protocol v6 | ✅ if supports Protocol v6 |
| Cohort: Framework only | ✅ if using terraform-plugin-framework only |
| Cohort: SDKv2 only | ✅ if using terraform-plugin-sdk v2 only |
| Cohort: Framework+SDKv2 | ✅ if using both Framework and SDKv2 |
| Managed Resources | Number of managed resources |
| Resource Identities | Number of resources with identity support (currently 0) |
| Data Sources | Number of data sources |
| Ephemeral Resources | Number of ephemeral resources |
| List Resources | Number of list resources |
| Actions | Number of actions |
| Provider Functions | Number of provider functions |
| Total Features | Sum of all feature counts |

## Notes

### Rate Limiting

The Terraform Registry API has rate limits. The script includes:
- 100ms delay between requests
- Automatic retry with exponential backoff on 429 errors
- Maximum 3 retries per request

### Resource Identities

The "Resource Identities" column is currently set to 0 for all providers. Properly detecting this would require:
- Downloading and parsing provider schemas
- Analyzing resource definitions for identity support

This would significantly increase scan time and complexity.

### Cohort Detection

The cohort (Framework vs SDKv2) detection is based on protocol versions:
- Protocol v6 typically indicates terraform-plugin-framework
- Protocol v5 typically indicates terraform-plugin-sdk v2
- Some providers support both

More accurate cohort detection would require analyzing the provider's Go module dependencies.

## Examples

### Quick test with Azure provider

```bash
python3 tf_provider_scanner.py -p hashicorp/azurerm
```

Output:
```
============================================================
Terraform Provider Scanner
============================================================

Scanning hashicorp/azurerm...

Written 1 providers to terraform_providers.csv

============================================================
Summary
============================================================
Total providers scanned: 1
Total managed resources: 1,113
Total data sources: 389
Total features: 1,518
```

### Scan major cloud providers

```bash
# AWS
python3 tf_provider_scanner.py -p hashicorp/aws -o aws.csv

# Azure
python3 tf_provider_scanner.py -p hashicorp/azurerm -o azure.csv

# GCP
python3 tf_provider_scanner.py -p hashicorp/google -o gcp.csv
```

## License

MIT
