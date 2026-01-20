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
            --primary: #64748b;
            --primary-dark: #475569;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --border: #334155;
            --success: #22c55e;
            --accent: #06b6d4;
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
    </style>
</head>
<body>
    <div class="container">
        <h1>🏗️ Terraform Provider Dashboard</h1>
        <p class="subtitle">Generated on '''

HTML_PART2 = '''</p>
        
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
        
        <div class="filters">
            <div class="filter-group">
                <label>Tier</label>
                <select id="filter-tier">
                    <option value="">All Tiers</option>
                    <option value="official">Official</option>
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
            <table id="providers-table" class="display">
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
                        <th>Subcats</th>
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
            Data from Terraform Registry API | '''

HTML_PART3 = ''' providers
        </footer>
    </div>
    
    <!-- Modal for feature details -->
    <div id="feature-modal" class="modal-overlay">
        <div class="modal-content">
            <span class="modal-close" onclick="closeModal()">&times;</span>
            <h2 class="modal-title" id="modal-title">Provider Details</h2>
            <div class="tab-buttons" id="tab-buttons"></div>
            <div id="modal-body">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/buttons/2.4.1/js/dataTables.buttons.min.js"></script>
    <script src="https://cdn.datatables.net/buttons/2.4.1/js/buttons.html5.min.js"></script>
    <script>
        const providers = '''

HTML_PART3B = ''';
        const providerDetails = '''

HTML_PART4 = ''';
        
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
        }
        
        // Modal functionality
        var currentProviderData = null;
        
        function closeModal() {
            document.getElementById('feature-modal').style.display = 'none';
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
            
            var html = '<ul class="feature-list">';
            docs.forEach(function(doc) {
                var title = doc.title || doc.slug || 'Unknown';
                var slug = doc.slug || '';
                var registryUrl = 'https://registry.terraform.io/providers/' + namespace + '/' + name + '/latest/docs/' + category + '/' + slug;
                
                html += '<li class="feature-item">';
                html += '<h4><a href="' + registryUrl + '" target="_blank">' + title + '</a></h4>';
                if (doc.subcategory) {
                    html += '<p>Category: ' + doc.subcategory + '</p>';
                }
                html += '</li>';
            });
            html += '</ul>';
            
            body.innerHTML = html;
        }
        
        // Close modal on click outside
        document.getElementById('feature-modal').onclick = function(e) {
            if (e.target === this) closeModal();
        };
        
        // Close modal on Escape key
        document.onkeydown = function(e) {
            if (e.key === 'Escape') closeModal();
        };
        
        // Render clickable action count
        function renderClickable(data, type, row, category) {
            if (type !== 'display') return data;
            if (!data || data === 0) return '0';
            return '<span class="clickable" onclick="openModal(\\'' + row.provider + '\\', \\'' + row.version + '\\', \\'' + category + '\\')">' + formatNumber(data) + '</span>';
        }
        
        $(document).ready(function() {
            var table = $('#providers-table').DataTable({
                data: providers,
                columns: [
                    { data: 'provider', render: function(d, t, row) {
                        if (t !== 'display') return d;
                        return '<span class="clickable" onclick="openModal(\\'' + d + '\\', \\'' + row.version + '\\', \\'actions\\')">' + d + '</span>';
                    }},
                    { data: 'tier', render: function(d) { return getTierBadge(d); } },
                    { data: 'downloads', render: function(d) { return formatDownloads(d || 0); } },
                    { data: 'version_count', render: function(d) { return formatNumber(d || 0); } },
                    { data: 'version' },
                    { data: 'published' },
                    { data: 'days_since_update', render: function(d) { return d != null ? d : '-'; } },
                    { data: 'protocol_v5', render: function(d) { return d ? '<span class="check-mark">✓</span>' : ''; } },
                    { data: 'protocol_v6', render: function(d) { return d ? '<span class="check-mark">✓</span>' : ''; } },
                    { data: null, render: function(d, t, row) { return getCohort(row); } },
                    { data: 'subcategories', render: function(d) { return formatNumber(d || 0); } },
                    { data: 'resources', render: function(d, t, row) { return renderClickable(d, t, row, 'resources'); } },
                    { data: 'list_resources', render: function(d, t, row) { return renderClickable(d, t, row, 'list-resources'); } },
                    { data: 'actions', render: function(d, t, row) { return renderClickable(d, t, row, 'actions'); } },
                    { data: 'identities', render: formatNumber },
                    { data: 'data_sources', render: function(d, t, row) { return renderClickable(d, t, row, 'data-sources'); } },
                    { data: 'total_features', render: formatNumber }
                ],
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
            
            // Custom filters
            $.fn.dataTable.ext.search.push(function(settings, data, dataIndex) {
                var row = providers[dataIndex];
                
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
                table.draw();
                updateMetrics(table.rows({ search: 'applied' }).data().toArray());
            });
            
            $('#filter-min-resources').on('input', function() {
                table.draw();
                updateMetrics(table.rows({ search: 'applied' }).data().toArray());
            });
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
                'version_count': parse_int(row.get('Versions', 0)),
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


def generate_html(csv_path: str, output_path: str = 'dashboard.html'):
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
    
    html = (
        HTML_PART1 +
        generated_date +
        HTML_PART2 +
        str(provider_count) +
        HTML_PART3 +
        providers_json +
        HTML_PART3B +
        details_json +
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
            print("Usage: python3 generate_html_dashboard.py <csv_file>")
            sys.exit(1)
    else:
        csv_path = sys.argv[1]
    
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'dashboard.html'
    generate_html(csv_path, output_path)
