#!/usr/bin/env python3
"""
Terraform Provider Dashboard

A simple Streamlit dashboard to view and explore Terraform provider data.

Usage:
    pip install streamlit pandas
    streamlit run dashboard.py

The dashboard will open in your browser at http://localhost:8501
It's only accessible on your local machine (private by default).
"""

import pandas as pd
import streamlit as st
from pathlib import Path

# Page config
st.set_page_config(
    page_title="Terraform Provider Dashboard",
    page_icon="🏗️",
    layout="wide",
)

# Title
st.title("🏗️ Terraform Provider Dashboard")
st.markdown("---")

# Load data
@st.cache_data
def load_data(csv_path: str) -> pd.DataFrame:
    """Load and clean the CSV data."""
    df = pd.read_csv(csv_path, skip_blank_lines=True)
    
    # Clean up column names (remove newlines)
    df.columns = [col.replace('\n', ' ').strip() for col in df.columns]
    
    # Skip the summary row if present (first row with empty Provider)
    if df.iloc[0]['Provider'] == '' or pd.isna(df.iloc[0]['Provider']):
        df = df.iloc[1:]
    
    # Convert numeric columns
    numeric_cols = ['Managed Resources', 'Resource Identities', 'Data Sources', 
                    'Ephemeral Resources', 'List Resources', 'Actions', 
                    'Provider Functions', 'Total Features']
    
    for col in numeric_cols:
        if col in df.columns:
            # Remove commas and convert to int
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('"', '')
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
    
    return df

# File selector
csv_files = list(Path('.').glob('*.csv'))
csv_file = st.sidebar.selectbox(
    "Select CSV file",
    options=[f.name for f in csv_files],
    index=0 if csv_files else None
)

if not csv_file:
    st.warning("No CSV files found in current directory. Run the scanner first!")
    st.code("python3 tf_provider_scanner.py --tier official")
    st.stop()

# Load data
try:
    df = load_data(csv_file)
except Exception as e:
    st.error(f"Error loading {csv_file}: {e}")
    st.stop()

# Sidebar filters
st.sidebar.header("Filters")

# Tier filter
tiers = ['All'] + sorted(df['Tier'].dropna().unique().tolist())
selected_tier = st.sidebar.selectbox("Tier", tiers)

if selected_tier != 'All':
    df = df[df['Tier'] == selected_tier]

# Protocol filter
st.sidebar.subheader("Protocols")
show_v4 = st.sidebar.checkbox("Protocol v4", value=True)
show_v5 = st.sidebar.checkbox("Protocol v5", value=True)
show_v6 = st.sidebar.checkbox("Protocol v6", value=True)

# Cohort filter
st.sidebar.subheader("Cohort")
cohort_options = ['All', 'Framework only', 'SDKv2 only', 'Framework+SDKv2']
selected_cohort = st.sidebar.selectbox("Cohort", cohort_options)

if selected_cohort == 'Framework only':
    df = df[df.get('Cohort: Framework only', '') == '✅']
elif selected_cohort == 'SDKv2 only':
    df = df[df.get('Cohort: SDKv2 only', df.get('Cohort:  SDKv2 only', '')) == '✅']
elif selected_cohort == 'Framework+SDKv2':
    df = df[df.get('Cohort: Framework+SDKv2', '') == '✅']

# Search
search = st.sidebar.text_input("Search provider name")
if search:
    df = df[df['Provider'].str.contains(search, case=False, na=False)]

# Minimum resources filter
min_resources = st.sidebar.slider("Minimum resources", 0, 500, 0)
if min_resources > 0:
    df = df[df['Managed Resources'] >= min_resources]

# Summary metrics
st.header("📊 Summary")
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Total Providers", len(df))
with col2:
    st.metric("Total Resources", f"{df['Managed Resources'].sum():,}")
with col3:
    st.metric("Total Data Sources", f"{df['Data Sources'].sum():,}")
with col4:
    st.metric("Total Features", f"{df['Total Features'].sum():,}")
with col5:
    framework_count = len(df[df.get('Cohort: Framework only', '') == '✅']) + \
                      len(df[df.get('Cohort: Framework+SDKv2', '') == '✅'])
    st.metric("Using Framework", framework_count)

st.markdown("---")

# Charts
st.header("📈 Charts")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Providers by Tier")
    tier_counts = df['Tier'].value_counts()
    st.bar_chart(tier_counts)

with col2:
    st.subheader("Top 10 by Resources")
    top_10 = df.nlargest(10, 'Managed Resources')[['Provider', 'Managed Resources']]
    top_10 = top_10.set_index('Provider')
    st.bar_chart(top_10)

st.markdown("---")

# Data table
st.header("📋 Provider Data")

# Column selector
available_cols = df.columns.tolist()
default_cols = ['Provider', 'Tier', 'Latest Version', 'Managed Resources', 
                'Data Sources', 'Total Features']
default_cols = [c for c in default_cols if c in available_cols]

selected_cols = st.multiselect(
    "Select columns to display",
    options=available_cols,
    default=default_cols
)

if selected_cols:
    # Sort options
    sort_col = st.selectbox("Sort by", selected_cols, index=0)
    sort_order = st.radio("Order", ["Descending", "Ascending"], horizontal=True)
    
    sorted_df = df[selected_cols].sort_values(
        sort_col, 
        ascending=(sort_order == "Ascending")
    )
    
    st.dataframe(
        sorted_df,
        use_container_width=True,
        height=600
    )
    
    # Download button
    csv_data = sorted_df.to_csv(index=False)
    st.download_button(
        label="📥 Download filtered data as CSV",
        data=csv_data,
        file_name="filtered_providers.csv",
        mime="text/csv"
    )

st.markdown("---")

# Provider detail view
st.header("🔍 Provider Details")
provider_list = sorted(df['Provider'].tolist())
selected_provider = st.selectbox("Select a provider", provider_list)

if selected_provider:
    provider_data = df[df['Provider'] == selected_provider].iloc[0]
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("### Basic Info")
        st.write(f"**Provider:** {provider_data['Provider']}")
        st.write(f"**Tier:** {provider_data['Tier']}")
        st.write(f"**Version:** {provider_data['Latest Version']}")
        st.write(f"**Published:** {provider_data.get('Latest Version Published', 'N/A')}")
        st.write(f"**Created:** {provider_data.get('Created At', 'N/A')}")
    
    with col2:
        st.markdown("### Protocols & Cohort")
        protocols = []
        if provider_data.get('Protocol v4') == '✅':
            protocols.append('v4')
        if provider_data.get('Protocol v5') == '✅':
            protocols.append('v5')
        if provider_data.get('Protocol v6') == '✅':
            protocols.append('v6')
        st.write(f"**Protocols:** {', '.join(protocols) if protocols else 'None'}")
        
        cohort = 'Unknown'
        if provider_data.get('Cohort: Framework only') == '✅':
            cohort = 'Framework only'
        elif provider_data.get('Cohort: SDKv2 only', provider_data.get('Cohort:  SDKv2 only')) == '✅':
            cohort = 'SDKv2 only'
        elif provider_data.get('Cohort: Framework+SDKv2') == '✅':
            cohort = 'Framework+SDKv2'
        st.write(f"**Cohort:** {cohort}")
    
    with col3:
        st.markdown("### Features")
        st.write(f"**Resources:** {provider_data['Managed Resources']:,}")
        st.write(f"**Resource Identities:** {provider_data.get('Resource Identities', 0):,}")
        st.write(f"**Data Sources:** {provider_data['Data Sources']:,}")
        st.write(f"**Ephemeral Resources:** {provider_data.get('Ephemeral Resources', 0)}")
        st.write(f"**List Resources:** {provider_data.get('List Resources', 0)}")
        st.write(f"**Actions:** {provider_data.get('Actions', 0)}")
        st.write(f"**Functions:** {provider_data.get('Provider Functions', 0)}")
        st.write(f"**Total Features:** {provider_data['Total Features']:,}")

# Footer
st.markdown("---")
st.caption("Data from Terraform Registry API | Dashboard powered by Streamlit")
