# Terraform Provider Scanner

A Python-based tool that collects comprehensive data from Terraform providers via the Terraform Registry API, generates CSV reports similar to "The Provider 500" format, and creates an interactive HTML dashboard for visualization.

## 🌟 Features

### Scanner (`tf_provider_scanner.py`)
- **Full Registry Scanning** - Scans all Terraform providers from the registry (official, partner, community)
- **Incremental Mode** - Only rescans providers with version changes (massive time savings!)
- **Version & Protocol Detection** - Collects version info, protocol versions (v4, v5, v6)
- **SDK/Framework Cohort Detection** - Determines if provider uses terraform-plugin-sdk v2, terraform-plugin-framework, or both
- **GitHub go.mod Analysis** - Optional deep analysis of provider source for accurate SDK detection
- **Feature Counting** - Counts managed resources, data sources, actions, ephemeral resources, list resources, and provider functions
- **Detailed Docs Export** - Exports full feature names and registry links to JSON for dashboard use
- **Parallel Scanning** - Optional parallel execution for faster scans
- **Flexible Filtering** - Filter by tier, limit results, scan single provider

### Dashboard (`generate_html_dashboard.py`)
- **Interactive HTML Dashboard** - Sortable, filterable table with DataTables
- **Dark Theme UI** - Modern dark theme with metrics cards
- **Tier & Cohort Filters** - Quick filter buttons for provider tiers and SDK cohorts
- **Clickable Feature Details** - Click on Actions, Resources, etc. to see the full list with registry links
- **Export Options** - Export to CSV, Excel, or copy to clipboard
- **Charts** - Visual breakdown of providers by tier and cohort
- **Summary Statistics** - Total counts for resources, data sources, and features

### GitHub Actions Workflow
- **Automated Daily Scans** - Scheduled workflow runs daily at 6 AM UTC
- **Incremental by Default** - Uses cached data to only scan changed providers
- **GitHub Pages Deployment** - Automatically deploys dashboard to GitHub Pages
- **Manual Trigger Options** - Configurable tier, limit, and incremental mode

## Requirements

- Python 3.7+
- No external dependencies (uses only standard library)

## Usage

### Basic Commands

```bash
# Scan a single provider (recommended for testing)
python3 tf_provider_scanner.py --provider hashicorp/azurerm

# Scan all official providers
python3 tf_provider_scanner.py --tier official

# Scan all partner providers  
python3 tf_provider_scanner.py --tier partner

# Scan with a limit
python3 tf_provider_scanner.py --tier official --limit 50

# Scan all providers (takes a long time!)
python3 tf_provider_scanner.py

# Custom output file
python3 tf_provider_scanner.py --provider hashicorp/aws --output aws_provider.csv
```

### Incremental Scanning (⚡ Fast!)

```bash
# First run - full scan
python3 tf_provider_scanner.py --tier official

# Subsequent runs - only scan changed providers
python3 tf_provider_scanner.py --tier official --incremental

# Short form
python3 tf_provider_scanner.py -i --tier official
```

The incremental mode:
1. Loads existing CSV data
2. Checks each provider's current version via quick API call
3. Reuses data for unchanged providers
4. Only performs full scan on new/changed providers

### Parallel Scanning

```bash
# Use 4 parallel workers (be careful with rate limits!)
python3 tf_provider_scanner.py --tier official --parallel 4
```

### Disable GitHub Checks

```bash
# Faster scan without GitHub go.mod analysis
python3 tf_provider_scanner.py --no-github
```

### Generate Dashboard

```bash
# Generate HTML dashboard from CSV
python3 generate_html_dashboard.py terraform_providers.csv docs/index.html
```

## Output Files

| File | Description |
|------|-------------|
| `terraform_providers.csv` | Main CSV with all provider data |
| `terraform_providers_details.json` | Detailed feature info for dashboard modals |
| `docs/index.html` | Interactive HTML dashboard |

## CSV Columns

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
| Resource Identities | Estimated resources with identity support |
| Data Sources | Number of data sources |
| Ephemeral Resources | Number of ephemeral resources |
| List Resources | Number of list resources |
| Actions | Number of provider actions |
| Provider Functions | Number of provider functions |
| Total Features | Sum of all feature counts |

## Dashboard Features

### Interactive Table
- **Sort** by any column (click headers)
- **Search** across all columns
- **Paginate** with configurable page size
- **Export** to CSV, Excel, or clipboard

### Quick Filters
- **Tier buttons**: Official, Partner, Community
- **Cohort buttons**: Framework Only, SDKv2 Only, Framework+SDKv2

### Feature Details Modal
Click on any non-zero feature count (Actions, Resources, etc.) to see:
- Full list of feature names
- Direct links to Terraform Registry documentation
- Subcategory organization

### Metrics Cards
- Total providers scanned
- Total managed resources
- Total data sources
- Total features

## GitHub Actions Setup

The included workflow (`.github/workflows/update-dashboard.yml`) provides:

1. **Scheduled runs** - Daily at 6 AM UTC
2. **Manual dispatch** - Run on demand with options:
   - Tier selection (all, official, partner)
   - Provider limit
   - Incremental mode toggle
3. **Caching** - Persists CSV/JSON between runs for incremental scanning
4. **GitHub Pages deployment** - Auto-deploys to `https://<username>.github.io/<repo>/`

### Setup Steps

1. Push repository to GitHub
2. Enable GitHub Pages (Settings → Pages → Source: GitHub Actions)
3. The workflow will run automatically or manually trigger it

## Technical Notes

### Rate Limiting

The Terraform Registry API has rate limits. The script includes:
- 100ms delay between requests
- Automatic retry with exponential backoff on 429 errors
- Maximum 3 retries per request

### Cohort Detection

SDK/Framework detection uses multiple methods:
1. **Protocol versions** - v6 typically indicates Framework, v5 indicates SDKv2
2. **GitHub go.mod analysis** (when `--no-github` is not set) - Parses dependencies for accurate detection

### Incremental Scanning Performance

| Scenario | Time (500 providers) |
|----------|---------------------|
| Full scan | ~15-20 minutes |
| Incremental (no changes) | ~2-3 minutes |
| Incremental (10% changed) | ~4-5 minutes |

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
Written details for 1 providers to terraform_providers_details.json

============================================================
Summary
============================================================
Total providers: 1
Total managed resources: 1,113
Total data sources: 389
Total features: 1,518
```

### Incremental scan output

```
Incremental mode enabled - checking for version changes...
Loaded 145 existing providers from terraform_providers.csv
  ✓ hashicorp/aws v5.82.0 (unchanged)
  ✓ hashicorp/azurerm v4.15.0 (unchanged)
  ↻ hashicorp/google: 6.14.0 → 6.15.0 (needs scan)
  + hashicorp/new-provider (new)

Summary: 143 unchanged, 2 to scan
```

### Full workflow

```bash
# 1. Scan providers
python3 tf_provider_scanner.py --tier official --incremental

# 2. Generate dashboard
python3 generate_html_dashboard.py terraform_providers.csv docs/index.html

# 3. Open dashboard
open docs/index.html
```

## License

MIT
