#!/usr/bin/env python3
"""
Generate a static HTML dashboard from the provider CSV data.

This creates a standalone HTML file that can be:
- Opened locally in any browser
- Uploaded to a private web server
- Shared via private file sharing

Usage:
    python3 generate_html_dashboard.py terraform_providers.csv
    open dashboard.html
"""

import csv
import json
import sys
from pathlib import Path
from datetime import datetime


def load_embedded_json(json_path: Path, empty_message: str, loaded_message: str) -> str:
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except FileNotFoundError:
        print(empty_message)
        return '{}'
    except json.JSONDecodeError as exc:
        print(f"   ⚠️  Failed to parse JSON from {json_path}: {exc}")
        return '{}'

    if payload in ({}, [], None):
        print(f"   ⚠️  {json_path.name} exists but is empty")
        return '{}'

    print(loaded_message)
    return json.dumps(payload, ensure_ascii=False, indent=2)


HTML_PART1 = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terraform Provider Dashboard</title>
    <link href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css" rel="stylesheet">
    <link href="https://cdn.datatables.net/buttons/2.4.1/css/buttons.dataTables.min.css" rel="stylesheet">
    <style>
        :root {
            --primary: #06b6d4;
            --primary-dark: #0891b2;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --border: #334155;
            --success: #22c55e;
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }
        
        .container {
            max-width: 1800px;
            margin: 0 auto;
        }
        
        h1 {
            font-size: 2rem;
            margin-bottom: 10px;
            color: var(--text);
        }
        
        .subtitle {
            color: var(--text-muted);
            margin-bottom: 30px;
        }
        
        .metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .metric-card {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border);
        }
        
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--primary);
        }
        
        .metric-label {
            color: var(--text-muted);
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .filters {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid var(--border);
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            align-items: center;
        }
        
        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        
        .filter-group label {
            font-size: 0.75rem;
            text-transform: uppercase;
            color: var(--text-muted);
        }
        
        select, input[type="text"], input[type="number"] {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 8px 12px;
            color: var(--text);
            font-size: 0.875rem;
            min-width: 150px;
        }
        
        select:focus, input:focus {
            outline: none;
            border-color: var(--primary);
        }
        
        .checkbox-group {
            display: flex;
            gap: 15px;
            align-items: center;
        }
        
        .checkbox-group label {
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
            font-size: 0.875rem;
        }
        
        .table-container {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border);
            overflow-x: auto;
        }
        
        table.dataTable {
            width: 100% !important;
            border-collapse: collapse;
        }
        
        table.dataTable thead th {
            background: var(--bg);
            color: var(--text);
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            border-bottom: 2px solid var(--border);
        }
        
        table.dataTable tbody td {
            padding: 10px 8px;
            border-bottom: 1px solid var(--border);
            font-size: 0.875rem;
        }
        
        table.dataTable tbody tr:hover {
            background: rgba(100, 116, 139, 0.15);
        }
        
        .check-mark {
            color: var(--success);
            font-weight: bold;
        }
        
        .tier-official {
            background: #22c55e33;
            color: #22c55e;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .tier-partner {
            background: #3b82f633;
            color: #3b82f6;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .tier-community {
            background: #f59e0b33;
            color: #f59e0b;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .dataTables_wrapper .dataTables_filter input {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 8px 12px;
            color: var(--text);
        }
        
        .dataTables_wrapper .dataTables_length select {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 4px 8px;
            color: var(--text);
        }
        
        .dataTables_wrapper .dataTables_info,
        .dataTables_wrapper .dataTables_length,
        .dataTables_wrapper .dataTables_filter {
            color: var(--text-muted);
        }
        
        .dataTables_wrapper .dataTables_paginate .paginate_button {
            color: var(--text) !important;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 4px;
            margin: 0 2px;
        }
        
        .dataTables_wrapper .dataTables_paginate .paginate_button:hover {
            background: var(--primary) !important;
            color: white !important;
            border-color: var(--primary);
        }
        
        .dataTables_wrapper .dataTables_paginate .paginate_button.current {
            background: var(--primary) !important;
            color: white !important;
            border-color: var(--primary);
        }
        
        .dt-buttons {
            margin-bottom: 15px;
        }
        
        .dt-button {
            background: var(--primary) !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            padding: 8px 16px !important;
            font-weight: 500 !important;
            cursor: pointer;
        }
        
        .dt-button:hover {
            background: var(--primary-dark) !important;
        }
        
        footer {
            text-align: center;
            margin-top: 40px;
            color: var(--text-muted);
            font-size: 0.875rem;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 20px;
            flex-wrap: wrap;
        }
        
        .color-picker-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .color-picker-group label {
            font-size: 0.75rem;
            text-transform: uppercase;
        }
        
        .color-picker-group input[type="color"] {
            width: 32px;
            height: 32px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            background: transparent;
        }
        
        .color-presets {
            display: flex;
            gap: 6px;
        }
        
        .color-preset {
            width: 24px;
            height: 24px;
            border-radius: 4px;
            cursor: pointer;
            border: 2px solid transparent;
            transition: border-color 0.2s;
        }
        
        .color-preset:hover {
            border-color: var(--text);
        }
        
        /* Modal styles */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            overflow-y: auto;
        }
        
        .modal-content {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 30px;
            max-width: 900px;
            margin: 50px auto;
            border: 1px solid var(--border);
            position: relative;
        }
        
        .modal-close {
            position: absolute;
            top: 15px;
            right: 20px;
            font-size: 1.5rem;
            cursor: pointer;
            color: var(--text-muted);
        }
        
        .modal-close:hover {
            color: var(--text);
        }
        
        .modal-title {
            font-size: 1.5rem;
            margin-bottom: 20px;
            color: var(--text);
        }
        
        .feature-list {
            list-style: none;
            padding: 0;
        }
        
        .feature-item {
            background: var(--bg);
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid var(--border);
        }
        
        .feature-item h4 {
            color: var(--primary);
            margin-bottom: 5px;
        }
        
        .feature-item p {
            color: var(--text-muted);
            font-size: 0.875rem;
            margin: 0;
        }
        
        .feature-item a {
            color: var(--primary);
            text-decoration: none;
        }
        
        .feature-item a:hover {
            text-decoration: underline;
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: var(--text-muted);
        }
        
        .clickable {
            cursor: pointer;
            color: var(--primary);
            text-decoration: underline;
        }
        
        .clickable:hover {
            color: var(--primary-dark);
        }
        
        /* Identity modal styles */
        .identity-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        
        .identity-stat-card {
            background: var(--bg);
            border-radius: 8px;
            padding: 15px;
            border: 1px solid var(--border);
            text-align: center;
        }
        
        .identity-stat-value {
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--primary);
        }
        
        .identity-stat-label {
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
        }
        
        .identity-breakdown {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 20px;
        }
        
        @media (max-width: 700px) {
            .identity-breakdown {
                grid-template-columns: 1fr;
            }
        }
        
        .identity-section {
            background: var(--bg);
            border-radius: 8px;
            padding: 15px;
            border: 1px solid var(--border);
        }
        
        .identity-section h4 {
            color: var(--primary);
            margin-bottom: 12px;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .identity-row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid var(--border);
            font-size: 0.875rem;
        }
        
        .identity-row:last-child {
            border-bottom: none;
        }
        
        .identity-row-label {
            color: var(--text-muted);
        }
        
        .identity-row-value {
            color: var(--text);
            font-weight: 600;
        }
        
        .identity-bar {
            height: 8px;
            background: var(--border);
            border-radius: 4px;
            margin-top: 10px;
            overflow: hidden;
        }
        
        .identity-bar-fill {
            height: 100%;
            background: var(--primary);
            border-radius: 4px;
            transition: width 0.3s ease;
        }
        
        .reason-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 10px;
            margin: 4px 0;
            background: var(--bg-card);
            border-radius: 6px;
            font-size: 0.85rem;
        }
        
        .reason-name {
            color: var(--text);
        }
        
        .reason-count {
            background: var(--primary);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .tab-buttons {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        
        .tab-btn {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 8px 16px;
            color: var(--text);
            cursor: pointer;
            font-size: 0.875rem;
        }
        
        .tab-btn.active {
            background: var(--primary);
            border-color: var(--primary);
        }
        
        .tab-btn:hover {
            border-color: var(--primary);
        }
        
        .chart-section {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }
        
        @media (max-width: 900px) {
            .chart-section {
                grid-template-columns: 1fr;
            }
        }
        
        .chart-card {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border);
        }
        
        .chart-card h3 {
            margin-bottom: 15px;
            font-size: 1rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .chart-container {
            position: relative;
            height: 280px;
        }
        .topnav {
            display: flex;
            align-items: center;
            gap: 0;
            margin-bottom: 24px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
        }
        .topnav a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.9rem;
            padding: 12px 20px;
            transition: all 0.2s;
            border-right: 1px solid var(--border);
        }
        .topnav a:last-child { border-right: none; }
        .topnav a:hover { color: var(--text); background: rgba(6, 182, 212, 0.1); }
        .topnav a.active {
            color: var(--primary);
            background: rgba(6, 182, 212, 0.1);
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1><svg style="height: 1.2em; vertical-align: middle; margin-right: 8px;" viewBox="0 0 64 64" fill="currentColor"><polygon points="22.5,11.2 22.5,33.5 41.9,44.7 41.9,22.3"/><polygon points="44.1,22.3 44.1,44.7 63.5,33.5 63.5,11.2"/><polygon points="0.5,0 0.5,22.3 19.9,33.5 19.9,11.2"/><polygon points="22.5,36.1 22.5,58.4 41.9,69.6 41.9,47.3"/></svg>Terraform Provider Dashboard</h1>
        <p class="subtitle">Generated on '''

HTML_PART2 = '''</p>
        <nav class="topnav">
            <a href="index.html" class="active">All Providers</a>
            <a href="downloads.html">📈 Download Trends</a>
                <a href="cloud-devex.html">Cloud DevEx</a>
            <a href="azurerm-list-check.html">✅ AzureRM List Check</a>
            <a href="aws-list-check.html">AWS List Check</a>
        </nav>
        
        <div class="metrics" id="metrics">
            <div class="metric-card">
                <div class="metric-value" id="total-providers">0</div>
                <div class="metric-label">Total Providers</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-downloads">0</div>
                <div class="metric-label">Total Downloads</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-resources">0</div>
                <div class="metric-label">Total Resources</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-datasources">0</div>
                <div class="metric-label">Total Data Sources</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-features">0</div>
                <div class="metric-label">Total Features</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-list-resources">0</div>
                <div class="metric-label">List Resources</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-actions">0</div>
                <div class="metric-label">Actions</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="v5-count">0</div>
                <div class="metric-label">Protocol v5 (SDKv2)</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="v6-count">0</div>
                <div class="metric-label">Protocol v6 (Framework)</div>
            </div>
        </div>
        
        <div class="chart-section">
            <div class="chart-card">
                <h3>Downloads by Provider</h3>
                <div class="chart-container">
                    <canvas id="downloads-chart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <h3>Resources by Provider</h3>
                <div class="chart-container">
                    <canvas id="resources-chart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="filters">
            <div class="filter-group">
                <label>Tier</label>
                <select id="filter-tier">
                    <option value="">All Tiers</option>
                    <option value="official" selected>Official</option>
                    <option value="partner">Partner</option>
                    <option value="community">Community</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Min Resources</label>
                <input type="number" id="filter-min-resources" value="0" min="0">
            </div>
            
            <div class="filter-group">
                <label>Protocol</label>
                <select id="filter-protocol">
                    <option value="">All Protocols</option>
                    <option value="v5">v5 only (SDKv2)</option>
                    <option value="v6">v6 only (Framework)</option>
                    <option value="both">v5 + v6 (Mixed)</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Cohort</label>
                <select id="filter-cohort">
                    <option value="">All Cohorts</option>
                    <option value="framework">Framework only</option>
                    <option value="sdkv2">SDKv2 only</option>
                    <option value="mixed">Framework+SDKv2</option>
                </select>
            </div>
        </div>
        
        <div class="table-container">
            <h2 style="color: var(--primary); font-size: 1.1rem; margin-bottom: 10px; padding-left: 4px;">☁️ Official Cloud Providers (AWS / Azure / GCP)</h2>
            <table id="cloud-table" class="display">
                <thead>
                    <tr>
                        <th>Provider</th>
                        <th>Tier</th>
                        <th>Downloads</th>
                        <th>Versions</th>
                        <th>Version</th>
                        <th>Published</th>
                        <th>Days</th>
                        <th>v5</th>
                        <th>v6</th>
                        <th>Cohort</th>
                        <th>Services</th>
                        <th>Resources</th>
                        <th>List</th>
                        <th>Actions</th>
                        <th>Identities</th>
                        <th>Data Sources</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                </tbody>
            </table>
        </div>
        
        <div class="table-container" style="margin-top: 30px;">
            <h2 style="color: var(--text-muted); font-size: 1.1rem; margin-bottom: 10px; padding-left: 4px;">📦 Partner &amp; Community Providers</h2>
            <table id="other-table" class="display">
                <thead>
                    <tr>
                        <th>Provider</th>
                        <th>Tier</th>
                        <th>Downloads</th>
                        <th>Versions</th>
                        <th>Version</th>
                        <th>Published</th>
                        <th>Days</th>
                        <th>v5</th>
                        <th>v6</th>
                        <th>Cohort</th>
                        <th>Services</th>
                        <th>Resources</th>
                        <th>List</th>
                        <th>Actions</th>
                        <th>Identities</th>
                        <th>Data Sources</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                </tbody>
            </table>
        </div>
        
        <footer>
            <span>Data from Terraform Registry API |</span>
            <span id="provider-count">'''

HTML_PART3 = ''' providers</span>
            <div class="color-picker-group">
                <label>Theme:</label>
                <div class="color-presets">
                    <div class="color-preset" style="background: #06b6d4" onclick="setColor('#06b6d4', '#0891b2')" title="Cyan"></div>
                    <div class="color-preset" style="background: #8b5cf6" onclick="setColor('#8b5cf6', '#7c3aed')" title="Purple"></div>
                    <div class="color-preset" style="background: #f59e0b" onclick="setColor('#f59e0b', '#d97706')" title="Amber"></div>
                    <div class="color-preset" style="background: #10b981" onclick="setColor('#10b981', '#059669')" title="Emerald"></div>
                    <div class="color-preset" style="background: #ec4899" onclick="setColor('#ec4899', '#db2777')" title="Pink"></div>
                    <div class="color-preset" style="background: #3b82f6" onclick="setColor('#3b82f6', '#2563eb')" title="Blue"></div>
                </div>
                <input type="color" id="custom-color" value="#06b6d4" onchange="setColor(this.value, this.value)" title="Custom color">
            </div>
        </footer>
    </div>
    
    <!-- Modal for feature details -->
    <div id="feature-modal" class="modal-overlay">
        <div class="modal-content">
            <span class="modal-close" onclick="closeModal()">&times;</span>
            <h2 class="modal-title" id="modal-title">Provider Details</h2>
            <div id="trend-chart-container" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--text-muted); font-size: 0.875rem; margin-bottom: 10px;">📈 HISTORICAL TRENDS</h3>
                <div style="margin-bottom: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8rem; margin-right: 10px;">Right axis:</span>
                    <label style="color: #06b6d4; margin-right: 12px; cursor: pointer; font-size: 0.85rem;">
                        <input type="checkbox" id="toggle-downloads" checked onchange="updateTrendDatasets()"> Downloads
                    </label>
                    <label style="color: #22c55e; margin-right: 12px; cursor: pointer; font-size: 0.85rem;">
                        <input type="checkbox" id="toggle-resources" onchange="updateTrendDatasets()"> Resources
                    </label>
                    <label style="color: #f97316; margin-right: 12px; cursor: pointer; font-size: 0.85rem;">
                        <input type="checkbox" id="toggle-datasources" onchange="updateTrendDatasets()"> Data Sources
                    </label>
                </div>
                <div style="height: 200px;">
                    <canvas id="trend-chart"></canvas>
                </div>
            </div>
            <div class="tab-buttons" id="tab-buttons"></div>
            <div id="modal-body">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    
    <!-- Modal for Resource Identity details (Azure) -->
    <div id="identity-modal" class="modal-overlay">
        <div class="modal-content" style="max-width: 1000px;">
            <span class="modal-close" onclick="closeIdentityModal()">&times;</span>
            <h2 class="modal-title" id="identity-modal-title">🔐 Azure Resource Identity Analysis</h2>
            <div id="identity-modal-body">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/buttons/2.4.1/js/dataTables.buttons.min.js"></script>
    <script src="https://cdn.datatables.net/buttons/2.4.1/js/buttons.html5.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
    <script>
        const providers = '''

HTML_PART3B = ''';
        const providerDetails = '''

HTML_PART3C = ''';
        const providerHistory = '''

HTML_PART3D = ''';
        const azureIdentityData = '''

HTML_PART3E = ''';
        const awsIdentityData = '''

HTML_PART3F = ''';
    const azurermListCheck = '''

HTML_PART3G = ''';
    const awsListCheck = '''

HTML_PART4 = ''';
        
        function setColor(primary, dark) {
            document.documentElement.style.setProperty('--primary', primary);
            document.documentElement.style.setProperty('--primary-dark', dark);
            document.getElementById('custom-color').value = primary;
            localStorage.setItem('dashboard-color', primary);
            localStorage.setItem('dashboard-color-dark', dark);
        }
        
        // Load saved color on page load
        (function() {
            var savedColor = localStorage.getItem('dashboard-color');
            var savedDark = localStorage.getItem('dashboard-color-dark');
            if (savedColor && savedDark) {
                setColor(savedColor, savedDark);
            }
        })();
        
        function formatNumber(n) {
            return n.toLocaleString();
        }
        
        function formatDownloads(n) {
            if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
            if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
            if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
            return n.toString();
        }
        
        function getTierBadge(tier) {
            const cls = 'tier-' + tier.toLowerCase();
            return '<span class="' + cls + '">' + tier + '</span>';
        }
        
        function getCohort(p) {
            if (p.cohort_framework_only) return 'Framework';
            if (p.cohort_sdkv2_only) return 'SDKv2';
            if (p.cohort_framework_sdkv2) return 'Mixed';
            return '-';
        }
        
        function updateMetrics(data) {
            document.getElementById('total-providers').textContent = formatNumber(data.length);
            var totalDownloads = data.reduce(function(s, p) { return s + (p.downloads || 0); }, 0);
            document.getElementById('total-downloads').textContent = formatDownloads(totalDownloads);
            document.getElementById('total-resources').textContent = formatNumber(data.reduce(function(s, p) { return s + p.resources; }, 0));
            document.getElementById('total-datasources').textContent = formatNumber(data.reduce(function(s, p) { return s + p.data_sources; }, 0));
            document.getElementById('total-features').textContent = formatNumber(data.reduce(function(s, p) { return s + p.total_features; }, 0));
            document.getElementById('total-list-resources').textContent = formatNumber(data.reduce(function(s, p) { return s + (p.list_resources || 0); }, 0));
            document.getElementById('total-actions').textContent = formatNumber(data.reduce(function(s, p) { return s + (p.actions || 0); }, 0));
            document.getElementById('v5-count').textContent = data.filter(function(p) { return p.protocol_v5; }).length;
            document.getElementById('v6-count').textContent = data.filter(function(p) { return p.protocol_v6; }).length;
            
            // Update charts
            updateCharts(data);
        }
        
        var downloadsChart = null;
        var resourcesChart = null;
        
        function updateCharts(data) {
            var chartColors = [
                '#06b6d4', '#8b5cf6', '#f59e0b', '#10b981', '#ec4899', 
                '#3b82f6', '#ef4444', '#84cc16', '#f97316', '#6366f1',
                '#14b8a6', '#a855f7', '#eab308', '#22c55e', '#f43f5e'
            ];
            
            // Sort by downloads and take top providers
            var sorted = data.slice().sort(function(a, b) { return (b.downloads || 0) - (a.downloads || 0); });
            var topN = Math.min(8, sorted.length);
            var top = sorted.slice(0, topN);
            var othersDownloads = sorted.slice(topN).reduce(function(s, p) { return s + (p.downloads || 0); }, 0);
            
            var labels = top.map(function(p) { return p.provider.split('/')[1]; });
            var downloadData = top.map(function(p) { return p.downloads || 0; });
            
            if (othersDownloads > 0) {
                labels.push('Others');
                downloadData.push(othersDownloads);
            }
            
            var totalDl = downloadData.reduce(function(s, v) { return s + v; }, 0);
            
            // Destroy existing charts
            if (downloadsChart) downloadsChart.destroy();
            if (resourcesChart) resourcesChart.destroy();
            
            // Downloads pie chart
            var ctx1 = document.getElementById('downloads-chart').getContext('2d');
            downloadsChart = new Chart(ctx1, {
                type: 'pie',
                data: {
                    labels: labels,
                    datasets: [{
                        data: downloadData,
                        backgroundColor: chartColors.slice(0, labels.length),
                        borderColor: '#1e293b',
                        borderWidth: 2
                    }]
                },
                plugins: [ChartDataLabels],
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        datalabels: {
                            color: '#fff',
                            font: { weight: 'bold', size: 11 },
                            formatter: function(value, ctx) {
                                var pct = ((value / totalDl) * 100).toFixed(1);
                                if (pct < 3) return '';
                                var label = ctx.chart.data.labels[ctx.dataIndex];
                                return label + '\\n' + pct + '%';
                            },
                            textAlign: 'center'
                        },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    var pct = ((ctx.raw / totalDl) * 100).toFixed(1);
                                    return ctx.label + ': ' + formatDownloads(ctx.raw) + ' (' + pct + '%)';
                                }
                            }
                        }
                    }
                }
            });
            
            // Resources chart - same approach
            var sortedRes = data.slice().sort(function(a, b) { return (b.resources || 0) - (a.resources || 0); });
            var topRes = sortedRes.slice(0, topN);
            var othersRes = sortedRes.slice(topN).reduce(function(s, p) { return s + (p.resources || 0); }, 0);
            
            var resLabels = topRes.map(function(p) { return p.provider.split('/')[1]; });
            var resData = topRes.map(function(p) { return p.resources || 0; });
            
            if (othersRes > 0) {
                resLabels.push('Others');
                resData.push(othersRes);
            }
            
            var totalRes = resData.reduce(function(s, v) { return s + v; }, 0);
            
            var ctx2 = document.getElementById('resources-chart').getContext('2d');
            resourcesChart = new Chart(ctx2, {
                type: 'pie',
                data: {
                    labels: resLabels,
                    datasets: [{
                        data: resData,
                        backgroundColor: chartColors.slice(0, resLabels.length),
                        borderColor: '#1e293b',
                        borderWidth: 2
                    }]
                },
                plugins: [ChartDataLabels],
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        datalabels: {
                            color: '#fff',
                            font: { weight: 'bold', size: 11 },
                            formatter: function(value, ctx) {
                                var pct = ((value / totalRes) * 100).toFixed(1);
                                if (pct < 3) return '';
                                var label = ctx.chart.data.labels[ctx.dataIndex];
                                return label + '\\n' + pct + '%';
                            },
                            textAlign: 'center'
                        },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    var pct = ((ctx.raw / totalRes) * 100).toFixed(1);
                                    return ctx.label + ': ' + formatNumber(ctx.raw) + ' (' + pct + '%)';
                                }
                            }
                        }
                    }
                }
            });
        }
        
        // Modal functionality
        var currentProviderData = null;
        
        function closeModal() {
            document.getElementById('feature-modal').style.display = 'none';
        }
        
        // Identity Modal functionality
        function closeIdentityModal() {
            document.getElementById('identity-modal').style.display = 'none';
        }
        
        function openIdentityModal(provider) {
            var modal = document.getElementById('identity-modal');
            var title = document.getElementById('identity-modal-title');
            var body = document.getElementById('identity-modal-body');
            
            modal.style.display = 'block';
            
            // Determine which provider data to use
            var identityData = null;
            var providerLabel = '';
            var isAzure = provider === 'hashicorp/azurerm';
            var isAWS = provider === 'hashicorp/aws';
            
            if (isAzure && azureIdentityData && azureIdentityData.summary) {
                identityData = azureIdentityData;
                providerLabel = 'Azure';
                title.textContent = '🔐 Azure Resource Identity Analysis';
            } else if (isAWS && awsIdentityData && awsIdentityData.summary) {
                identityData = awsIdentityData;
                providerLabel = 'AWS';
                title.textContent = '🔐 AWS Resource Identity Analysis';
            }
            
            if (!identityData) {
                body.innerHTML = '<div class="loading">No identity data available for ' + provider + '.</div>';
                return;
            }
            
            var s = identityData.summary;
            var wi = s.with_identity;
            var wo = s.without_identity;
            
            // Build the modal content
            var html = '';
            
            // Summary cards
            html += '<div class="identity-grid">';
            html += '<div class="identity-stat-card">';
            html += '<div class="identity-stat-value">' + s.total_resources + '</div>';
            html += '<div class="identity-stat-label">Total Resources</div>';
            html += '</div>';
            html += '<div class="identity-stat-card">';
            html += '<div class="identity-stat-value" style="color: #22c55e;">' + wi.count + '</div>';
            html += '<div class="identity-stat-label">With Identity (' + wi.percentage + '%)</div>';
            html += '</div>';
            html += '<div class="identity-stat-card">';
            html += '<div class="identity-stat-value" style="color: #ef4444;">' + wo.count + '</div>';
            html += '<div class="identity-stat-label">Without Identity (' + wo.percentage + '%)</div>';
            html += '</div>';
            html += '<div class="identity-stat-card">';
            html += '<div class="identity-stat-value" style="color: #8b5cf6;">' + wi.with_list + '</div>';
            html += '<div class="identity-stat-label">With List Support</div>';
            html += '</div>';
            html += '</div>';
            
            // Progress bar - Identity coverage
            html += '<div style="margin-bottom: 8px;">';
            html += '<div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-muted); margin-bottom: 4px;">';
            html += '<span>🔐 Identity Support</span>';
            html += '<span>' + wi.count + ' / ' + s.total_resources + ' (' + wi.percentage + '%)</span>';
            html += '</div>';
            html += '<div class="identity-bar">';
            html += '<div class="identity-bar-fill" style="width: ' + wi.percentage + '%; background: linear-gradient(90deg, #22c55e 0%, #10b981 100%);"></div>';
            html += '</div>';
            html += '</div>';
            
            // Progress bar - List support (within those with identity)
            var listPercentage = wi.count > 0 ? ((wi.with_list / wi.count) * 100).toFixed(1) : 0;
            html += '<div style="margin-bottom: 20px;">';
            html += '<div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-muted); margin-bottom: 4px;">';
            html += '<span>📋 List Support (of identity resources)</span>';
            html += '<span>' + wi.with_list + ' / ' + wi.count + ' (' + listPercentage + '%)</span>';
            html += '</div>';
            html += '<div class="identity-bar">';
            html += '<div class="identity-bar-fill" style="width: ' + listPercentage + '%; background: linear-gradient(90deg, #8b5cf6 0%, #7c3aed 100%);"></div>';
            html += '</div>';
            html += '</div>';

            if (isAzure && azurermListCheck && azurermListCheck.validation) {
                var validation = azurermListCheck.validation;
                var validationIcon = validation.matches ? '✅' : '⚠️';
                html += '<div style="margin-bottom: 20px; padding: 14px 16px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px;">';
                html += '<div style="display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;">';
                html += '<span><strong>' + validationIcon + ' AzureRM List Validation</strong></span>';
                html += '<a href="azurerm-list-check.html" target="_blank" style="color: var(--primary); text-decoration: underline;">View full scan output</a>';
                html += '</div>';
                html += '<div style="margin-top: 6px; color: var(--text-muted); font-size: 0.85rem;">';
                html += 'Registry-reflected list resources: <strong style="color: var(--text);">' + validation.dashboard_list_resources + '</strong> &middot; ';
                html += 'Gist scan with list: <strong style="color: var(--text);">' + validation.gist_with_list + '</strong>';
                html += '</div>';
                html += '</div>';
            }

            if (isAWS && awsListCheck && awsListCheck.validation) {
                var awsValidation = awsListCheck.validation;
                var awsValidationIcon = awsValidation.matches ? '✅' : '⚠️';
                html += '<div style="margin-bottom: 20px; padding: 14px 16px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px;">';
                html += '<div style="display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;">';
                html += '<span><strong>' + awsValidationIcon + ' AWS List Validation</strong></span>';
                html += '<a href="aws-list-check.html" target="_blank" style="color: var(--primary); text-decoration: underline;">View full scan output</a>';
                html += '</div>';
                html += '<div style="margin-top: 6px; color: var(--text-muted); font-size: 0.85rem;">';
                html += 'Registry-reflected list resources: <strong style="color: var(--text);">' + awsValidation.dashboard_list_resources + '</strong> &middot; ';
                html += 'Tracking script list resources: <strong style="color: var(--text);">' + awsValidation.script_implemented_list + '</strong>';
                html += '</div>';
                html += '</div>';
            }
            
            // Two-column breakdown
            html += '<div class="identity-breakdown">';
            
            // Left column - different for Azure vs AWS
            html += '<div class="identity-section">';
            html += '<h4>✅ Resources WITH Identity</h4>';
            
            if (isAzure) {
                html += '<div class="identity-row"><span class="identity-row-label">Typed (Plugin Framework)</span><span class="identity-row-value">' + wi.typed + '</span></div>';
                html += '<div class="identity-row"><span class="identity-row-label">Untyped (SDK v2)</span><span class="identity-row-value">' + wi.untyped + '</span></div>';
            } else if (isAWS) {
                html += '<div class="identity-row"><span class="identity-row-label">Regional Scope</span><span class="identity-row-value">' + (wi.regional || 0) + '</span></div>';
                html += '<div class="identity-row"><span class="identity-row-label">Global Scope</span><span class="identity-row-value">' + (wi.global || 0) + '</span></div>';
                html += '<div class="identity-row"><span class="identity-row-label">ARN-based</span><span class="identity-row-value">' + (wi.arn_based || 0) + '</span></div>';
                html += '<div class="identity-row"><span class="identity-row-label">Parameterized</span><span class="identity-row-value">' + (wi.parameterized || 0) + '</span></div>';
                html += '<div class="identity-row"><span class="identity-row-label">Singleton</span><span class="identity-row-value">' + (wi.singleton || 0) + '</span></div>';
            }
            html += '<div class="identity-row"><span class="identity-row-label">With List Support</span><span class="identity-row-value">' + wi.with_list + '</span></div>';
            html += '<div class="identity-row"><span class="identity-row-label">Without List</span><span class="identity-row-value">' + wi.without_list + '</span></div>';
            html += '</div>';
            
            // Right column - breakdown by reason/type
            html += '<div class="identity-section">';
            
            if (isAzure && wo.by_reason) {
                html += '<h4>❌ Resources WITHOUT Identity (by reason)</h4>';
                var reasons = wo.by_reason || {};
                var reasonLabels = {
                    'eligible': '🎯 Eligible (can add identity)',
                    'custom_parse_id': '🔧 Custom Parse ID',
                    'nested_resource': '📦 Nested Resource',
                    'azuread_provider': '🔑 AzureAD Provider',
                    'numeric_segment': '🔢 Numeric Segment',
                    'scoped_id': '🎯 Scoped ID',
                    'data_plane': '☁️ Data Plane',
                    'composite_id': '🧩 Composite ID',
                    'extension_resource': '🔌 Extension Resource',
                    'provider_component': '⚙️ Provider Component'
                };
                
                var sortedReasons = Object.keys(reasons).sort(function(a, b) { return reasons[b] - reasons[a]; });
                sortedReasons.forEach(function(reason) {
                    var label = reasonLabels[reason] || reason;
                    var count = reasons[reason];
                    html += '<div class="reason-item">';
                    html += '<span class="reason-name">' + label + '</span>';
                    html += '<span class="reason-count">' + count + '</span>';
                    html += '</div>';
                });
            } else if (isAWS && s.by_identity_type) {
                html += '<h4>🏷️ Identity Types Used</h4>';
                var types = s.by_identity_type || {};
                var typeLabels = {
                    'RegionalARNIdentity': '🌐 Regional ARN',
                    'GlobalARNIdentity': '🌍 Global ARN',
                    'RegionalSingleParameterIdentity': '📍 Regional Single Param',
                    'GlobalSingleParameterIdentity': '🗺️ Global Single Param',
                    'RegionalParameterizedIdentity': '📍 Regional Multi Param',
                    'GlobalParameterizedIdentity': '🗺️ Global Multi Param',
                    'RegionalSingletonIdentity': '📍 Regional Singleton',
                    'GlobalSingletonIdentity': '🗺️ Global Singleton'
                };
                
                var sortedTypes = Object.keys(types).sort(function(a, b) { return types[b] - types[a]; });
                sortedTypes.forEach(function(type) {
                    var label = typeLabels[type] || type;
                    var count = types[type];
                    html += '<div class="reason-item">';
                    html += '<span class="reason-name">' + label + '</span>';
                    html += '<span class="reason-count">' + count + '</span>';
                    html += '</div>';
                });
            }
            
            html += '</div>';
            html += '</div>';
            
            // Footer note
            html += '<div style="margin-top: 20px; padding: 15px; background: var(--bg); border-radius: 8px; border: 1px solid var(--border);">';
            html += '<p style="color: var(--text-muted); font-size: 0.85rem; margin: 0;">';
            html += '📊 <strong>Source:</strong> Scanned from terraform-provider-' + providerLabel.toLowerCase() + ' source code on GitHub<br>';
            if (isAzure) {
                html += '🎯 <strong>Eligible</strong> resources can potentially have Identity support added. Other categories have technical constraints.';
            } else if (isAWS) {
                html += '🔐 <strong>Identity</strong> enables resource imports and tracking across regions/accounts.';
            }
            html += '</p>';
            html += '</div>';
            
            body.innerHTML = html;
        }
        
        function openModal(provider, version, category) {
            var modal = document.getElementById('feature-modal');
            var title = document.getElementById('modal-title');
            var body = document.getElementById('modal-body');
            var tabs = document.getElementById('tab-buttons');
            
            title.textContent = provider + ' - Features';
            body.innerHTML = '<div class="loading">Loading...</div>';
            tabs.innerHTML = '';
            modal.style.display = 'block';
            
            var parts = provider.split('/');
            var namespace = parts[0];
            var name = parts[1];
            
            // Check if we have embedded data first
            if (providerDetails && providerDetails[provider] && providerDetails[provider].docs) {
                var categories = providerDetails[provider].docs;
                displayCategories(categories, category, namespace, name, tabs, body);
                showTrendChart(provider);
                return;
            }
            
            // No embedded data - show helpful message instead of trying API (CORS will fail)
            body.innerHTML = '<div class="loading" style="text-align: center; padding: 40px;">' +
                '<p style="font-size: 1.2em; margin-bottom: 15px;">📋 No cached data available for this provider</p>' +
                '<p style="color: var(--text-muted);">View directly on Terraform Registry:</p>' +
                '<a href="https://registry.terraform.io/providers/' + namespace + '/' + name + '/latest/docs" ' +
                'target="_blank" style="color: var(--primary); text-decoration: underline;">' +
                'registry.terraform.io/providers/' + namespace + '/' + name + '</a>' +
                '<p style="color: var(--text-muted); margin-top: 20px; font-size: 0.9em;">' +
                'Run the scanner again to cache this provider\\'s details.</p>' +
                '</div>';
            showTrendChart(provider);
        }
        
        function displayCategories(categories, category, namespace, name, tabs, body) {
            // Create tabs
            var tabOrder = ['actions', 'resources', 'list-resources', 'data-sources', 'ephemeral-resources', 'functions'];
            var tabLabels = {
                'actions': 'Actions',
                'resources': 'Resources',
                'list-resources': 'List Resources',
                'data-sources': 'Data Sources',
                'ephemeral-resources': 'Ephemeral',
                'functions': 'Functions'
            };
            
            tabs.innerHTML = '';
            var firstTab = null;
            tabOrder.forEach(function(cat) {
                if (categories[cat] && categories[cat].length > 0) {
                    var btn = document.createElement('button');
                    btn.className = 'tab-btn' + (cat === category ? ' active' : '');
                    btn.textContent = tabLabels[cat] + ' (' + categories[cat].length + ')';
                    btn.onclick = function() { showCategory(cat, categories, namespace, name); };
                    tabs.appendChild(btn);
                    if (!firstTab) firstTab = cat;
                }
            });
            
            // Show requested category or first available
            showCategory(categories[category] ? category : firstTab, categories, namespace, name);
        }
        
        function showCategory(category, categories, namespace, name) {
            var body = document.getElementById('modal-body');
            var tabs = document.querySelectorAll('.tab-btn');
            
            // Update active tab
            tabs.forEach(function(btn) {
                btn.classList.remove('active');
                if (btn.textContent.toLowerCase().startsWith(category.replace('-', ' '))) {
                    btn.classList.add('active');
                }
            });
            
            var docs = categories[category] || [];
            if (docs.length === 0) {
                body.innerHTML = '<div class="loading">No items in this category</div>';
                return;
            }
            
            // Group docs by subcategory (service)
            var byService = {};
            var noService = [];
            docs.forEach(function(doc) {
                var svc = doc.subcategory || '';
                if (svc) {
                    if (!byService[svc]) byService[svc] = [];
                    byService[svc].push(doc);
                } else {
                    noService.push(doc);
                }
            });
            
            var serviceNames = Object.keys(byService).sort();
            var html = '';
            
            // Show service summary badges
            if (serviceNames.length > 1) {
                html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;">';
                serviceNames.forEach(function(svc) {
                    var svcId = 'svc-' + svc.replace(/[^a-zA-Z0-9]/g, '-');
                    html += '<span style="background:rgba(6,182,212,0.15);color:var(--primary);padding:4px 10px;border-radius:6px;font-size:0.8rem;cursor:pointer;" onclick="document.getElementById(\\'' + svcId + '\\').scrollIntoView({behavior:\\'smooth\\',block:\\'start\\'})">' + svc + ' (' + byService[svc].length + ')</span>';
                });
                if (noService.length > 0) {
                    html += '<span style="background:rgba(148,163,184,0.15);color:var(--text-muted);padding:4px 10px;border-radius:6px;font-size:0.8rem;cursor:pointer;" onclick="document.getElementById(\\'svc-other\\').scrollIntoView({behavior:\\'smooth\\',block:\\'start\\'})">Other (' + noService.length + ')</span>';
                }
                html += '</div>';
            }
            
            // Render items grouped by service
            function renderDocItem(doc) {
                var title = doc.title || doc.slug || 'Unknown';
                var slug = doc.slug || '';
                var registryUrl = 'https://registry.terraform.io/providers/' + namespace + '/' + name + '/latest/docs/' + category + '/' + slug;
                var out = '<li class="feature-item">';
                out += '<h4><a href="' + registryUrl + '" target="_blank">' + title + '</a></h4>';
                out += '</li>';
                return out;
            }
            
            serviceNames.forEach(function(svc) {
                var svcId = 'svc-' + svc.replace(/[^a-zA-Z0-9]/g, '-');
                html += '<div id="' + svcId + '" style="margin-bottom:12px;">';
                html += '<h4 style="color:var(--primary);font-size:0.85rem;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:4px;">' + svc + ' <span style="color:var(--text-muted);font-weight:normal;">(' + byService[svc].length + ')</span></h4>';
                html += '<ul class="feature-list" style="margin-left:8px;">';
                byService[svc].forEach(function(doc) { html += renderDocItem(doc); });
                html += '</ul></div>';
            });
            
            if (noService.length > 0) {
                if (serviceNames.length > 0) {
                    html += '<div id="svc-other" style="margin-bottom:12px;">';
                    html += '<h4 style="color:var(--text-muted);font-size:0.85rem;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:4px;">Other</h4>';
                    html += '<ul class="feature-list" style="margin-left:8px;">';
                } else {
                    html += '<ul class="feature-list">';
                }
                noService.forEach(function(doc) { html += renderDocItem(doc); });
                html += '</ul>';
                if (serviceNames.length > 0) html += '</div>';
            }
            
            body.innerHTML = html;
        }
        
        // Trend chart instance and data
        var trendChartInstance = null;
        var trendChartData = null;
        
        function updateTrendDatasets() {
            if (!trendChartInstance || !trendChartData) return;
            
            var showDownloads = document.getElementById('toggle-downloads').checked;
            var showResources = document.getElementById('toggle-resources').checked;
            var showDataSources = document.getElementById('toggle-datasources').checked;
            
            // Build datasets array - List Resources always first (left axis)
            var datasets = [{
                label: 'List Resources',
                data: trendChartData.listResources,
                borderColor: 'rgba(168, 85, 247, 1)',
                backgroundColor: 'rgba(168, 85, 247, 0.1)',
                yAxisID: 'y1',
                tension: 0.3,
                pointRadius: 6,
                borderWidth: 3,
                spanGaps: true
            }];
            
            if (showDownloads) {
                datasets.push({
                    label: 'Downloads',
                    data: trendChartData.downloads,
                    borderColor: 'rgba(6, 182, 212, 1)',
                    backgroundColor: 'rgba(6, 182, 212, 0.1)',
                    yAxisID: 'y2',
                    tension: 0.3,
                    pointRadius: 4,
                    spanGaps: true
                });
            }
            if (showResources) {
                datasets.push({
                    label: 'Resources',
                    data: trendChartData.resources,
                    borderColor: 'rgba(34, 197, 94, 1)',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    yAxisID: 'y2',
                    tension: 0.3,
                    pointRadius: 4,
                    spanGaps: true
                });
            }
            if (showDataSources) {
                datasets.push({
                    label: 'Data Sources',
                    data: trendChartData.dataSources,
                    borderColor: 'rgba(249, 115, 22, 1)',
                    backgroundColor: 'rgba(249, 115, 22, 0.1)',
                    yAxisID: 'y2',
                    tension: 0.3,
                    pointRadius: 4,
                    spanGaps: true
                });
            }
            
            trendChartInstance.data.datasets = datasets;
            trendChartInstance.update();
        }
        
        function showTrendChart(provider) {
            var container = document.getElementById('trend-chart-container');
            var canvas = document.getElementById('trend-chart');
            
            // Check if we have history data
            if (!providerHistory || !providerHistory.providers || !providerHistory.providers[provider]) {
                container.style.display = 'none';
                return;
            }
            
            var dates = providerHistory.dates || [];
            var pHistory = providerHistory.providers[provider];
            
            // Need at least 1 data point to show chart
            if (dates.length < 1) {
                container.style.display = 'none';
                return;
            }
            
            container.style.display = 'block';
            
            // Prepare labels (MM-DD format)
            var labels = dates.map(function(d) { 
                return d.substring(5); // MM-DD
            });
            
            // Get data arrays directly from history
            var downloads = pHistory.downloads || [];
            var resources = pHistory.resources || [];
            var dataSources = pHistory.data_sources || [];
            var listResources = pHistory.list_resources || [];
            
            // Store for toggle updates
            trendChartData = {
                labels: labels,
                downloads: downloads,
                resources: resources,
                dataSources: dataSources,
                listResources: listResources
            };
            
            // Destroy previous chart
            if (trendChartInstance) {
                trendChartInstance.destroy();
            }
            
            // Reset checkboxes
            document.getElementById('toggle-downloads').checked = true;
            document.getElementById('toggle-resources').checked = false;
            document.getElementById('toggle-datasources').checked = false;
            
            // Create new chart with List Resources on left, Downloads on right by default
            var ctx = canvas.getContext('2d');
            trendChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'List Resources',
                            data: listResources,
                            borderColor: 'rgba(168, 85, 247, 1)',
                            backgroundColor: 'rgba(168, 85, 247, 0.1)',
                            yAxisID: 'y1',
                            tension: 0.3,
                            pointRadius: 6,
                            borderWidth: 3,
                            spanGaps: true
                        },
                        {
                            label: 'Downloads',
                            data: downloads,
                            borderColor: 'rgba(6, 182, 212, 1)',
                            backgroundColor: 'rgba(6, 182, 212, 0.1)',
                            yAxisID: 'y2',
                            tension: 0.3,
                            pointRadius: 4,
                            spanGaps: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            labels: { color: '#94a3b8' }
                        },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    var val = ctx.parsed.y;
                                    if (ctx.dataset.label === 'Downloads') {
                                        return ctx.dataset.label + ': ' + formatNumber(val);
                                    }
                                    return ctx.dataset.label + ': ' + val;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            ticks: { color: '#94a3b8' },
                            grid: { color: 'rgba(148, 163, 184, 0.1)' }
                        },
                        y1: {
                            type: 'linear',
                            position: 'left',
                            title: { display: true, text: 'List Resources', color: 'rgba(168, 85, 247, 1)' },
                            ticks: { 
                                color: 'rgba(168, 85, 247, 1)'
                            },
                            grid: { color: 'rgba(148, 163, 184, 0.1)' }
                        },
                        y2: {
                            type: 'linear',
                            position: 'right',
                            title: { display: true, text: 'Other Metrics', color: '#94a3b8' },
                            ticks: { 
                                color: 'rgba(6, 182, 212, 1)',
                                callback: function(val) { return formatNumber(val); }
                            },
                            grid: { drawOnChartArea: false }
                        }
                    }
                }
            });
        }
        
        // Close modal on click outside
        document.getElementById('feature-modal').onclick = function(e) {
            if (e.target === this) closeModal();
        };
        
        document.getElementById('identity-modal').onclick = function(e) {
            if (e.target === this) closeIdentityModal();
        };
        
        // Close modal on Escape key
        document.onkeydown = function(e) {
            if (e.key === 'Escape') {
                closeModal();
                closeIdentityModal();
            }
        };
        
        // Render clickable action count
        function renderClickable(data, type, row, category) {
            if (type !== 'display') return data;
            if (!data || data === 0) return '0';
            return '<span class="clickable" onclick="openModal(\\'' + row.provider + '\\', \\'' + row.version + '\\', \\'' + category + '\\')">' + formatNumber(data) + '</span>';
        }

        function getAzurermListCheckBadge() {
            if (!azurermListCheck || !azurermListCheck.validation) return '';

            var validation = azurermListCheck.validation;
            var matches = validation.matches;
            var icon = matches ? '✅' : '⚠️';
            var title = matches
                ? 'Validated against latest AzureRM gist scan: ' + validation.gist_with_list + ' matches Registry-reflected ' + validation.dashboard_list_resources
                : 'AzureRM gist validation mismatch: gist ' + validation.gist_with_list + ' vs Registry-reflected ' + validation.dashboard_list_resources;

            return ' <a href="azurerm-list-check.html" title="' + title + '" style="text-decoration:none;">' + icon + '</a>';
        }

        function getAwsListCheckBadge() {
            if (!awsListCheck || !awsListCheck.validation) return '';

            var validation = awsListCheck.validation;
            var matches = validation.matches;
            var icon = matches ? '✅' : '⚠️';
            var title = matches
                ? 'Validated against latest AWS tracking script: ' + validation.script_implemented_list + ' matches Registry-reflected ' + validation.dashboard_list_resources
                : 'AWS tracking validation mismatch: script ' + validation.script_implemented_list + ' vs Registry-reflected ' + validation.dashboard_list_resources;

            return ' <a href="aws-list-check.html" title="' + title + '" style="text-decoration:none;">' + icon + '</a>';
        }

        function renderListResources(data, type, row) {
            if (type !== 'display') return data;
            var content = renderClickable(data, type, row, 'list-resources');
            if (row.provider === 'hashicorp/azurerm' && data && data > 0) {
                content += getAzurermListCheckBadge();
            }
            if (row.provider === 'hashicorp/aws' && data && data > 0) {
                content += getAwsListCheckBadge();
            }
            return content;
        }
        
        // Render identities with click for Azure and AWS
        function renderIdentities(data, type, row) {
            if (type !== 'display') return data || 0;
            if (!data || data === 0) return '0';
            
            // Check if this is Azure provider and we have identity data
            if (row.provider === 'hashicorp/azurerm' && azureIdentityData && azureIdentityData.summary) {
                return '<span class="clickable" onclick="openIdentityModal(\\'' + row.provider + '\\')" title="Click for identity breakdown">' + formatNumber(data) + ' 🔍</span>';
            }
            
            // Check if this is AWS provider and we have identity data
            if (row.provider === 'hashicorp/aws' && awsIdentityData && awsIdentityData.summary) {
                return '<span class="clickable" onclick="openIdentityModal(\\'' + row.provider + '\\')" title="Click for identity breakdown">' + formatNumber(data) + ' 🔍</span>';
            }
            
            return formatNumber(data);
        }
        
        $(document).ready(function() {
            // Split providers into official cloud vs rest
            var officialNames = ['hashicorp/aws', 'hashicorp/awscc', 'hashicorp/azurerm', 'hashicorp/azuread',
                'hashicorp/azurestack', 'hashicorp/google', 'hashicorp/google-beta'];
            var cloudProviders = [];
            var otherProviders = [];
            
            // Override counts with details data (HCL-only) when available
            providers.forEach(function(p) {
                if (providerDetails && providerDetails[p.provider] && providerDetails[p.provider].docs) {
                    var docs = providerDetails[p.provider].docs;
                    if (docs['resources']) p.resources = docs['resources'].length;
                    if (docs['data-sources']) p.data_sources = docs['data-sources'].length;
                    if (docs['list-resources']) p.list_resources = docs['list-resources'].length;
                    if (docs['actions']) p.actions = docs['actions'].length;
                    if (docs['ephemeral-resources']) p.ephemeral_resources = docs['ephemeral-resources'].length;
                    if (docs['functions']) p.functions = docs['functions'].length;
                    // Recalculate total_features from corrected counts
                    p.total_features = (p.resources || 0) + (p.identities || 0) + (p.data_sources || 0) +
                        (p.ephemeral_resources || 0) + (p.list_resources || 0) + (p.actions || 0) + (p.functions || 0);
                }
                if (officialNames.indexOf(p.provider) !== -1) {
                    cloudProviders.push(p);
                } else {
                    otherProviders.push(p);
                }
            });
            
            var columnDefs = [
                    { data: 'provider', render: function(d, t, row) {
                        if (t !== 'display') return d;
                        return '<span class="clickable" onclick="openModal(\\'' + d + '\\', \\'' + row.version + '\\', \\'actions\\')">' + d + '</span>';
                    }},
                    { data: 'tier', render: function(d) { return getTierBadge(d); } },
                    { data: 'downloads', render: function(d, t) { 
                        if (t === 'sort' || t === 'type') return d || 0;
                        return formatDownloads(d || 0); 
                    }},
                    { data: 'version_count', render: function(d, t) { 
                        if (t === 'sort' || t === 'type') return d || 0;
                        return formatNumber(d || 0); 
                    }},
                    { data: 'version' },
                    { data: 'published' },
                    { data: 'days_since_update', render: function(d) { return d != null ? d : '-'; } },
                    { data: 'protocol_v5', render: function(d) { return d ? '<span class="check-mark">✓</span>' : ''; } },
                    { data: 'protocol_v6', render: function(d) { return d ? '<span class="check-mark">✓</span>' : ''; } },
                    { data: null, render: function(d, t, row) { return getCohort(row); } },
                    { data: 'subcategories', render: function(d, t) { 
                        if (t === 'sort' || t === 'type') return d || 0;
                        return formatNumber(d || 0); 
                    }},
                    { data: 'resources', render: function(d, t, row) { return renderClickable(d, t, row, 'resources'); } },
                    { data: 'list_resources', render: function(d, t, row) { return renderListResources(d, t, row); } },
                    { data: 'actions', render: function(d, t, row) { return renderClickable(d, t, row, 'actions'); } },
                    { data: 'identities', render: function(d, t, row) { return renderIdentities(d, t, row); } },
                    { data: 'data_sources', render: function(d, t, row) { return renderClickable(d, t, row, 'data-sources'); } },
                    { data: 'total_features', render: formatNumber }
            ];
            
            var cloudTable = $('#cloud-table').DataTable({
                data: cloudProviders,
                columns: columnDefs,
                order: [[2, 'desc']],
                pageLength: 25,
                paging: false,
                searching: false,
                info: false,
                dom: 'Brt',
                buttons: ['csv', 'excel'],
                language: {
                    search: "Search:",
                    lengthMenu: "Show _MENU_ entries"
                }
            });
            
            var otherTable = $('#other-table').DataTable({
                data: otherProviders,
                columns: columnDefs,
                order: [[2, 'desc']],
                pageLength: 25,
                dom: 'Bfrtip',
                buttons: ['csv', 'excel'],
                language: {
                    search: "Search:",
                    lengthMenu: "Show _MENU_ entries"
                }
            });
            
            updateMetrics(providers);
            
            // Custom filters (apply to other table only)
            $.fn.dataTable.ext.search.push(function(settings, data, dataIndex) {
                if (settings.nTable.id === 'cloud-table') return true;
                
                var row = otherProviders[dataIndex];
                if (!row) return true;
                
                var tier = $('#filter-tier').val();
                if (tier && row.tier.toLowerCase() !== tier) return false;
                
                var minRes = parseInt($('#filter-min-resources').val()) || 0;
                if (row.resources < minRes) return false;
                
                var protocol = $('#filter-protocol').val();
                if (protocol === 'v5' && (!row.protocol_v5 || row.protocol_v6)) return false;
                if (protocol === 'v6' && (!row.protocol_v6 || row.protocol_v5)) return false;
                if (protocol === 'both' && !(row.protocol_v5 && row.protocol_v6)) return false;
                
                var cohort = $('#filter-cohort').val();
                if (cohort === 'framework' && !row.cohort_framework_only) return false;
                if (cohort === 'sdkv2' && !row.cohort_sdkv2_only) return false;
                if (cohort === 'mixed' && !row.cohort_framework_sdkv2) return false;
                
                return true;
            });
            
            $('#filter-tier, #filter-cohort, #filter-protocol').on('change', function() {
                otherTable.draw();
                updateMetrics(otherTable.rows({ search: 'applied' }).data().toArray().concat(cloudProviders));
            });
            
            $('#filter-min-resources').on('input', function() {
                otherTable.draw();
                updateMetrics(otherTable.rows({ search: 'applied' }).data().toArray().concat(cloudProviders));
            });
            
            // Apply default tier filter on load
            otherTable.draw();
            updateMetrics(otherTable.rows({ search: 'applied' }).data().toArray().concat(cloudProviders));
        });
    </script>
</body>
</html>
'''


def parse_csv(csv_path: str) -> list:
    """Parse the CSV file into a list of provider dicts."""
    providers = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            # Skip summary row (has no Provider or has 'TOTAL')
            if not row.get('Provider') or row.get('Provider', '').strip() == 'TOTAL':
                continue
            
            def parse_int(val):
                if not val:
                    return 0
                return int(str(val).replace(',', '').replace('"', ''))
            
            def parse_downloads(val):
                """Parse download string like '5.4B' or '100M' back to number."""
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
            
            providers.append({
                'provider': row.get('Provider', ''),
                'tier': row.get('Tier', ''),
                'downloads': parse_downloads(row.get('Downloads', '')),
                'version_count': parse_int(row.get('Version Count', 0)),
                'version': row.get('Latest Version', ''),
                'published': row.get('Latest Version Published', ''),
                'days_since_update': parse_int(row.get('Days Since Update', 0)),
                'created': row.get('Created At', ''),
                'protocol_v4': row.get('Protocol v4', '') == '✅',
                'protocol_v5': row.get('Protocol v5', '') == '✅',
                'protocol_v6': row.get('Protocol v6', '') == '✅',
                'cohort_framework_only': row.get('Cohort: Framework only', '') == '✅',
                'cohort_sdkv2_only': row.get('Cohort: SDKv2 only', row.get('Cohort:\nSDKv2 only', '')) == '✅',
                'cohort_framework_sdkv2': row.get('Cohort: Framework+SDKv2', '') == '✅',
                'subcategories': parse_int(row.get('Subcategories', 0)),
                'resources': parse_int(row.get('Managed Resources', 0)),
                'identities': parse_int(row.get('Resource Identities', 0)),
                'data_sources': parse_int(row.get('Data Sources', 0)),
                'ephemeral': parse_int(row.get('Ephemeral Resources', 0)),
                'list_resources': parse_int(row.get('List Resources', 0)),
                'actions': parse_int(row.get('Actions', 0)),
                'functions': parse_int(row.get('Provider Functions', 0)),
                'total_features': parse_int(row.get('Total Features', 0)),
            })
    
    return providers


def generate_html(csv_path: str, output_path: str = 'dashboard.html', history_file: str = None):
    """Generate the HTML dashboard from CSV data."""
    providers = parse_csv(csv_path)
    generated_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    provider_count = len(providers)
    providers_json = json.dumps(providers, indent=2)
    
    # Try to load details JSON if it exists
    details_path = csv_path.replace('.csv', '_details.json')
    details_json = '{}'
    try:
        with open(details_path, 'r', encoding='utf-8') as f:
            details_json = f.read()
        print(f"   Loaded provider details from {details_path}")
    except FileNotFoundError:
        print(f"   No details file found at {details_path}, modal will fetch from API")
    
    # Try to load history JSON - use explicit path if provided, otherwise auto-detect
    history_json = '{}'
    if history_file:
        history_path = Path(history_file)
    else:
        csv_dir = Path(csv_path).parent
        history_path = csv_dir / 'data' / 'history.json'
    
    print(f"   Looking for history at: {history_path.absolute()}")
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            history_json = f.read()
        # Validate it's not empty
        if history_json.strip() and history_json.strip() != '{}':
            print(f"   ✅ Loaded history data from {history_path} ({len(history_json)} bytes)")
        else:
            print(f"   ⚠️  History file exists but is empty at {history_path}")
            history_json = '{}'
    except FileNotFoundError:
        print(f"   ⚠️  No history file found at {history_path}, trend charts will be disabled")
    
    # Try to load Azure identity details JSON
    azure_identity_json = '{}'
    csv_dir = Path(csv_path).parent
    azure_identity_path = csv_dir / 'data' / 'azure_identity_detailed.json'
    
    print(f"   Looking for Azure identity data at: {azure_identity_path.absolute()}")
    try:
        with open(azure_identity_path, 'r', encoding='utf-8') as f:
            azure_identity_json = f.read()
        if azure_identity_json.strip() and azure_identity_json.strip() != '{}':
            print(f"   ✅ Loaded Azure identity data from {azure_identity_path}")
        else:
            print(f"   ⚠️  Azure identity file exists but is empty")
            azure_identity_json = '{}'
    except FileNotFoundError:
        print(f"   ⚠️  No Azure identity file found, identity popup will be disabled")
    
    # Try to load AWS identity details JSON
    aws_identity_json = '{}'
    aws_identity_path = csv_dir / 'data' / 'aws_identity_detailed.json'
    
    print(f"   Looking for AWS identity data at: {aws_identity_path.absolute()}")
    try:
        with open(aws_identity_path, 'r', encoding='utf-8') as f:
            aws_identity_json = f.read()
        if aws_identity_json.strip() and aws_identity_json.strip() != '{}':
            print(f"   ✅ Loaded AWS identity data from {aws_identity_path}")
        else:
            print(f"   ⚠️  AWS identity file exists but is empty")
            aws_identity_json = '{}'
    except FileNotFoundError:
        print(f"   ⚠️  No AWS identity file found, identity popup will be disabled for AWS")

    # Try to load AzureRM list validation JSON
    azurerm_list_check_path = csv_dir / 'data' / 'azurerm_list_check.json'

    print(f"   Looking for AzureRM list validation at: {azurerm_list_check_path.absolute()}")
    azurerm_list_check_json = load_embedded_json(
        azurerm_list_check_path,
        '   ⚠️  No AzureRM list validation file found',
        f"   ✅ Loaded AzureRM list validation from {azurerm_list_check_path}",
    )

    # Try to load AWS list validation JSON
    aws_list_check_path = csv_dir / 'data' / 'aws_list_check.json'

    print(f"   Looking for AWS list validation at: {aws_list_check_path.absolute()}")
    aws_list_check_json = load_embedded_json(
        aws_list_check_path,
        '   ⚠️  No AWS list validation file found',
        f"   ✅ Loaded AWS list validation from {aws_list_check_path}",
    )
    
    html = (
        HTML_PART1 +
        generated_date +
        HTML_PART2 +
        str(provider_count) +
        HTML_PART3 +
        providers_json +
        HTML_PART3B +
        details_json +
        HTML_PART3C +
        history_json +
        HTML_PART3D +
        azure_identity_json +
        HTML_PART3E +
        aws_identity_json +
        HTML_PART3F +
        azurerm_list_check_json +
        HTML_PART3G +
        aws_list_check_json +
        HTML_PART4
    )
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ Generated {output_path} with {len(providers)} providers")
    print(f"   Open in browser: file://{Path(output_path).absolute()}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        csv_files = list(Path('.').glob('*.csv'))
        if csv_files:
            csv_path = str(csv_files[0])
            print(f"Using: {csv_path}")
        else:
            print("Usage: python3 generate_html_dashboard.py <csv_file> [output_file] [history_file]")
            sys.exit(1)
    else:
        csv_path = sys.argv[1]
    
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'dashboard.html'
    history_path_arg = sys.argv[3] if len(sys.argv) > 3 else None
    generate_html(csv_path, output_path, history_path_arg)
