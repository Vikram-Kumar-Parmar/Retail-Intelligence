"""Retail Intelligence — interactive local DW + MESHJOIN presentation dashboard."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_layer import load_csv, load_mysql, safe_pct

st.set_page_config(page_title='Retail Intelligence | Data Warehouse', page_icon='◈',
                   layout='wide', initial_sidebar_state='expanded')

INK = '#0A1020'
CYAN = '#66E4D8'
BLUE = '#7A9CFF'
ORANGE = '#FFB86B'
PINK = '#F795C3'
PALETTE = [CYAN, BLUE, ORANGE, PINK, '#A5E8A3', '#C6A6FF', '#F4D35E']

st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
html, body, [class*="css"], [data-testid="stApp"] {font-family: 'DM Sans',sans-serif;}
[data-testid="stApp"] {background: radial-gradient(ellipse at top right, #15253e 0%, #0a1020 42%, #080d19 100%);color:#e8f1fa;}
[data-testid="stSidebar"] {background:#0c1526; border-right:1px solid #22334b;}
h1,h2,h3 {font-family:'Space Grotesk',sans-serif!important;letter-spacing:-.035em;}
h1 {color:#f3f8ff!important;font-size:2.7rem!important;}h2 {font-size:1.45rem!important;}
[data-testid="stMetric"] {background:linear-gradient(135deg,#15243a,#101c30);border:1px solid #243651;border-radius:16px;padding:17px 18px;min-height:124px;}
[data-testid="stMetricLabel"] {color:#99acc6;}[data-testid="stMetricValue"] {color:#f0f7ff;font-family:'Space Grotesk';}
[data-testid="stMetricDelta"]{color:#66e4d8;}
[data-testid="stPlotlyChart"] {border:1px solid #1e3047;border-radius:16px;background:#0e192b;padding:8px;}
[data-testid="stDataFrame"] {border:1px solid #22334b;border-radius:12px;}
.stButton button,.stDownloadButton button {background:#163a4e;color:#8cf8e8;border:1px solid #286b78;border-radius:10px;}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p {color:#aec0d9;}
.hero {padding:24px 28px;border:1px solid #2a4861;border-radius:18px;background:linear-gradient(108deg,#143748,#111a30 65%,#182546);margin-bottom:24px;}
.eyebrow {font-size:11px;letter-spacing:3px;font-weight:700;color:#66e4d8;text-transform:uppercase;}
.hero h1{margin:6px 0 4px;font-size:42px;}.hero p{margin:0;color:#afc4db;font-size:14px;}
.info-note {background:#132337;padding:12px 16px;border-radius:10px;border-left:3px solid #66e4d8;color:#b7cee2;margin:12px 0;}
.answer {background:#121f32;border:1px solid #253c54;border-radius:12px;padding:18px 20px;margin:10px 0;}
.small-muted {font-size:12px;color:#8ea3bd;}
</style>''', unsafe_allow_html=True)

@st.cache_data(show_spinner='Preparing retail analytics…', ttl=900)
def get_data(source):
    return load_mysql() if source == 'MySQL' else load_csv()


def fig_style(fig, height=350):
    fig.update_layout(height=height, margin=dict(l=18,r=16,t=30,b=15),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#c9d7e8',family='DM Sans'),
        xaxis=dict(showgrid=False,zeroline=False),
        yaxis=dict(gridcolor='#22334a',zeroline=False),
        legend=dict(orientation='h',y=-0.27,x=0),
        hoverlabel=dict(bgcolor='#14263b',font_color='#efffff'))
    return fig


def line(data, x, y, color=None, title=None):
    fig = px.line(data,x=x,y=y,color=color,markers=True,title=title,
                  color_discrete_sequence=PALETTE)
    fig.update_traces(line_width=3, marker_size=5)
    return fig_style(fig)


def bars(data, x, y, color=None, horizontal=False, title=None):
    fig=px.bar(data,x=x,y=y,color=color,title=title,
               orientation='h' if horizontal else 'v',color_discrete_sequence=PALETTE)
    fig.update_traces(marker_line_width=0)
    return fig_style(fig)


def section(title, subtitle=''):
    st.markdown('### '+title)
    if subtitle: st.caption(subtitle)


def money(v): return '£{:,.2f}'.format(float(v))
def num(v): return '{:,.0f}'.format(float(v))

with st.sidebar:
    st.markdown('## ◈ Retail Intelligence')
    st.caption('DATA WAREHOUSE / BI STUDIO')
    st.divider()
    source = st.radio('Data source', ['CSV', 'MySQL'], horizontal=True,
                      help='CSV works offline without MySQL; MySQL shows actual operational records.')
    if source == 'MySQL':
        st.caption('Reads DWH_HOST, DWH_PORT, DWH_DB, DWH_USER, DWH_PASSWORD from environment.')
    page = st.radio('Explore', ['Overview', 'Sales & Stores', 'Products',
                                'Customers', 'MESHJOIN Operations', 'Ask the Warehouse'],
                    label_visibility='collapsed')
    st.divider()
    st.caption('Interactive filters')

try:
    df, ops, actual_source = get_data(source)
except Exception as exc:
    st.error(f'Cannot load {source} data: {exc}')
    st.info('Choose CSV in the sidebar for an instant demo using the bundled data. See Dashboard/README.md for MySQL setup.')
    st.stop()

with st.sidebar:
    dates = (df.date.min().date(),df.date.max().date())
    period = st.date_input('Date range', value=dates, min_value=dates[0], max_value=dates[1])
    stores = sorted(df.store_name.unique())
    store_filter = st.multiselect('Stores', stores, default=stores)
    modes = sorted(df.sales_mode.unique())
    mode_filter = st.multiselect('Sales channels', modes, default=modes)
    st.divider()
    if st.button('↻ Refresh source', use_container_width=True):
        get_data.clear()
        st.rerun()
    st.caption('Source: ' + ('live MySQL warehouse' if source == 'MySQL' else 'submitted cleaned CSVs'))
    st.caption('Academic / portfolio demonstration')

if isinstance(period, (tuple, list)) and len(period)==2:
    start,end=period
else:
    start=end=period if not isinstance(period,(tuple,list)) else period[0]
filtered = df.loc[(df.date >= pd.Timestamp(start)) & (df.date <= pd.Timestamp(end)) &
                   df.store_name.isin(store_filter) & df.sales_mode.isin(mode_filter)]

st.markdown('''<div class="hero"><div class="eyebrow">FAST NUCES · DATA WAREHOUSING & BI</div>
<h1>Retail Intelligence <span style="color:#66e4d8">◈</span></h1>
<p>From raw transactions to business decisions — powered by a star schema and MESHJOIN pipeline</p></div>''',unsafe_allow_html=True)

if source == 'CSV':
    st.info('CSV demo coverage: the provided online_sales_stream.csv contains online records only (278,740 sales lines). Full 397,884-row online + periodic insights require the loaded MySQL warehouse. Operations statistics are shown as historical submitted-run snapshots.')

if filtered.empty:
    st.warning('No rows match the filters. Expand the date range, stores, or sales channels.')
    st.stop()

rows=len(filtered)
orders=filtered.transaction_id.nunique()
revenue=filtered.revenue.sum()
customers=filtered.customer_id.nunique()
products=filtered.product_id.nunique()

if page == 'Overview':
    section('Executive overview','Key performance indicators for the selected period and stores')
    a,b,c,d,e=st.columns(5)
    a.metric('Revenue',money(revenue)); b.metric('Sales lines',num(rows))
    c.metric('Unique invoices',num(orders)); d.metric('Active customers',num(customers))
    e.metric('Revenue / invoice',money(revenue/orders))
    st.write('')
    monthly=filtered.groupby('month',as_index=False).agg(revenue=('revenue','sum'))
    by_store=filtered.groupby('store_name',as_index=False).agg(revenue=('revenue','sum')).sort_values('revenue',ascending=False)
    left,right=st.columns([1.6,1])
    with left:
        section('Revenue momentum')
        st.plotly_chart(line(monthly,'month','revenue'),use_container_width=True)
    with right:
        section('Store leaderboard')
        st.plotly_chart(bars(by_store,'store_name','revenue',color='store_name'),use_container_width=True)
    col1,col2=st.columns(2)
    with col1:
        section('Sales mode distribution')
        mode=filtered.groupby('sales_mode',as_index=False).agg(rows=('quantity','size'))
        fig=px.pie(mode,names='sales_mode',values='rows',hole=.68,color_discrete_sequence=PALETTE)
        st.plotly_chart(fig_style(fig),use_container_width=True)
    with col2:
        section('Top product categories')
        cats=filtered.groupby('product_category',as_index=False).revenue.sum().nlargest(7,'revenue').sort_values('revenue')
        st.plotly_chart(bars(cats,'revenue','product_category',horizontal=True,color='product_category'),use_container_width=True)

elif page == 'Sales & Stores':
    section('Sales & store performance','Compare geography, channels, seasonality and purchase patterns')
    a,b,c,d=st.columns(4)
    a.metric('Total revenue',money(revenue));b.metric('Units sold',num(filtered.quantity.sum()))
    c.metric('Average invoice',money(revenue/orders));d.metric('Stores represented',num(filtered.store_id.nunique()))
    monthly=filtered.groupby(['month','sales_mode'],as_index=False).revenue.sum()
    st.plotly_chart(line(monthly,'month','revenue','sales_mode',title='Channel revenue over time'),use_container_width=True)
    c1,c2=st.columns(2)
    with c1:
        day=filtered.groupby('weekday',as_index=False).revenue.sum()
        order=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
        day['weekday']=pd.Categorical(day.weekday,categories=order,ordered=True)
        st.plotly_chart(bars(day.sort_values('weekday'),'weekday','revenue',title='Revenue by weekday'),use_container_width=True)
    with c2:
        hours=filtered.groupby('hour',as_index=False).revenue.sum()
        st.plotly_chart(bars(hours,'hour','revenue',title='Revenue by source transaction hour'),use_container_width=True)
    detail=filtered.groupby(['store_name','sales_mode'],as_index=False).agg(revenue=('revenue','sum'),sales_lines=('revenue','size'),units=('quantity','sum'),invoices=('transaction_id','nunique'))
    section('Store performance table')
    st.dataframe(detail.sort_values('revenue',ascending=False),hide_index=True,use_container_width=True)

elif page == 'Products':
    section('Product intelligence','Discover revenue leaders, category mix and high-volume merchandise')
    a,b,c,d=st.columns(4)
    a.metric('Distinct products',num(products));b.metric('Distinct suppliers',num(filtered.supplier_id.nunique()))
    c.metric('Units sold',num(filtered.quantity.sum()));d.metric('Category count',num(filtered.product_category.nunique()))
    metric=st.selectbox('Rank products by', ['Revenue','Units','Sales lines'])
    prod=filtered.groupby(['product_id','product_name','product_category'],as_index=False).agg(revenue=('revenue','sum'),units=('quantity','sum'),sales_lines=('quantity','size'))
    target={'Revenue':'revenue','Units':'units','Sales lines':'sales_lines'}[metric]
    prod=prod.sort_values(target,ascending=False)
    top=prod.head(12).sort_values(target)
    top['label']=top.product_name.str.slice(0,32)+' · '+top.product_id
    st.plotly_chart(bars(top,target,'label',horizontal=True,title='Top 12 products by '+metric.lower()),use_container_width=True)
    a,b=st.columns(2)
    with a:
        cat=filtered.groupby('product_category',as_index=False).revenue.sum().sort_values('revenue',ascending=False)
        st.plotly_chart(bars(cat,'product_category','revenue',color='product_category',title='Category revenue'),use_container_width=True)
    with b:
        supplier=filtered.groupby('supplier_id',as_index=False).revenue.sum().sort_values('revenue',ascending=False)
        st.plotly_chart(bars(supplier,'supplier_id','revenue',color='supplier_id',title='Supplier contribution'),use_container_width=True)
    section('Searchable product ranking')
    query=st.text_input('Search products by ID or name',placeholder='e.g. HEART or 85123A')
    displayed=prod[prod.product_name.str.contains(query,case=False,regex=False)|prod.product_id.str.contains(query,case=False,regex=False)] if query else prod
    st.dataframe(displayed.head(100),hide_index=True,use_container_width=True)
    st.download_button('Download product ranking CSV',data=displayed.to_csv(index=False).encode(),file_name='product_ranking.csv',mime='text/csv')

elif page == 'Customers':
    section('Customer intelligence','Profile customer contribution across enriched demographic attributes')
    a,b,c,d=st.columns(4)
    a.metric('Active customers',num(customers));b.metric('Revenue / customer',money(revenue/customers))
    c.metric('Unique invoices',num(orders));d.metric('Sales lines / customer',f'{rows/customers:,.1f}')
    a,b=st.columns(2)
    with a:
        g=filtered.groupby('gender',as_index=False).revenue.sum()
        fig=px.pie(g,names='gender',values='revenue',hole=.55,color_discrete_sequence=PALETTE,title='Revenue by gender')
        st.plotly_chart(fig_style(fig),use_container_width=True)
    with b:
        age=filtered.groupby('age_group',as_index=False).revenue.sum().sort_values('revenue',ascending=False)
        st.plotly_chart(bars(age,'age_group','revenue',color='age_group',title='Revenue by age group'),use_container_width=True)
    c1,c2=st.columns(2)
    with c1:
        occ=filtered.groupby('occupation',as_index=False).revenue.sum().sort_values('revenue')
        st.plotly_chart(bars(occ,'revenue','occupation',horizontal=True,title='Revenue by occupation'),use_container_width=True)
    with c2:
        cross=filtered.groupby(['city_category','gender'],as_index=False).revenue.sum()
        st.plotly_chart(bars(cross,'city_category','revenue','gender',title='City category × gender'),use_container_width=True)
    section('Customer lifetime value (within selected data)')
    clv=filtered.groupby('customer_id',as_index=False).agg(revenue=('revenue','sum'),invoices=('transaction_id','nunique'),units=('quantity','sum')).sort_values('revenue',ascending=False)
    st.dataframe(clv.head(30),hide_index=True,use_container_width=True)
    st.caption('Age, gender, occupation and city-category values are synthetic deterministic enrichment from the submitted project, not UCI-reported demographics.')

elif page == 'MESHJOIN Operations':
    section('MESHJOIN pipeline observability','Understand the batch/stream architecture and what the recorded run achieved')
    if source == 'CSV':
        st.markdown('<div class="info-note">CSV mode: processing and shedding statistics are historical snapshots from <b>run_statistics.md</b>, not live operational state. Select MySQL to query actual working tables.</div>',unsafe_allow_html=True)
    else:
        st.markdown('<div class="info-note">MySQL mode: operational counts reflect the currently connected database. They are not streaming telemetry; use Refresh source to query again.</div>',unsafe_allow_html=True)
    full=len(df)
    a,b,c,d=st.columns(4)
    a.metric('Rows in selected source',num(full))
    b.metric('Shed to disk',num(ops.get('shed_total') or 0) if ops.get('shed_total') is not None else 'n/a')
    c.metric('Replayed',num(ops.get('shed_replayed') or 0) if ops.get('shed_replayed') is not None else 'n/a')
    d.metric('Pending shed rows',num(ops.get('shed_pending') or 0) if ops.get('shed_pending') is not None else 'n/a')
    if source == 'MySQL':
        proc=df.groupby(['sales_mode','processing_mode'],as_index=False).size().rename(columns={'size':'rows'})
        st.plotly_chart(bars(proc,'processing_mode','rows','sales_mode',title='Actual warehouse processing modes'),use_container_width=True)
    else:
        st.caption('Actual processing_mode values do not exist in the cleaned CSV. The complete-warehouse submission report recorded Live: 278,740; Hourly: 43,818; Shed-Replay: 75,326. These counts are NOT the filtered CSV breakdown.')
        proc=pd.DataFrame({'processing_mode':['Live','Hourly','Shed-Replay'],'rows':[278740,43818,75326]})
        st.plotly_chart(bars(proc,'processing_mode','rows',color='processing_mode',title='Processing modes — submitted run snapshot'),use_container_width=True)
    st.markdown('#### The five-step story')
    st.markdown('''**1. Ingest:** Clean 541,909 raw rows into 397,884 positive, non-cancelled, customer-linked sales lines.  
**2. Route:** Deterministic invoice MD5 splits Online and Periodic sales.  
**3. Join:** MESHJOIN enriches stream chunks using paged customer and product master data.  
**4. Protect:** Peak-speed periodic overflow is written to a shed table, then replayed in non-peak mode.  
**5. Analyze:** Enriched facts in MySQL feed a dimensional OLAP warehouse and this dashboard.''')
    st.divider()
    a,b,c=st.columns(3)
    a.metric('Periodic groups',str(ops.get('periodic_groups','n/a')))
    b.metric('Released groups',str(ops.get('periodic_released','n/a')))
    c.metric('Live cache pending',str(ops.get('cache_pending','n/a')))
    online=int((df.sales_mode=='Online').sum()); periodic=int((df.sales_mode=='Periodic').sum())
    mode=pd.DataFrame({'sales_mode':['Online','Periodic'],'lines':[online,periodic]})
    st.plotly_chart(bars(mode,'sales_mode','lines',color='sales_mode',title='Invoice-based deterministic channel allocation'),use_container_width=True)
    st.caption('Counts describe sales lines, not unique invoices. Peak shed and replay numbers depend on the historical execution configuration.')

else:
    section('Ask the warehouse','One-click answers to questions a judge, recruiter or stakeholder might ask')
    questions=[
        'What is the total revenue, and how many invoices are represented?',
        'Which store brings in the most revenue?',
        'Which product is the top revenue generator?',
        'Which category should be prioritized?',
        'Are online or periodic sales contributing more revenue?',
        'Which month had the highest revenue?',
        'How do weekday and weekend sales compare?',
        'Which customer segment contributes the most?',
        'How many sales were shed and subsequently replayed?',
        'What evidence shows the warehouse pipeline is reliable?',
    ]
    selection=st.selectbox('Choose a question',questions)
    answer=''
    if selection==questions[0]: answer=f'The selected records total **{money(revenue)}**, across **{orders:,} unique invoices** and **{rows:,} invoice lines**. Invoice counts use distinct transaction_id.'
    elif selection==questions[1]:
        g=filtered.groupby('store_name').revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0]}** leads with **{money(g.iloc[0])}** ({safe_pct(g.iloc[0],revenue):.1f}% of selected revenue).'
    elif selection==questions[2]:
        g=filtered.groupby(['product_id','product_name']).revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0][1]}** (SKU `{g.index[0][0]}`) leads with **{money(g.iloc[0])}**.'
    elif selection==questions[3]:
        g=filtered.groupby('product_category').revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0]}** contributes the highest observed revenue: **{money(g.iloc[0])}**. This is a sales ranking, not a profit-margin recommendation.'
    elif selection==questions[4]:
        g=filtered.groupby('sales_mode').revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0]}** contributes more revenue: **{money(g.iloc[0])}**, representing **{safe_pct(g.iloc[0],revenue):.1f}%** of the selected total.'
    elif selection==questions[5]:
        g=filtered.groupby('month').revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0]}** records the highest monthly revenue: **{money(g.iloc[0])}**. Note: the dataset starts December 2010 and ends December 9, 2011, so boundary months are incomplete.'
    elif selection==questions[6]:
        g=filtered.groupby('day_type').revenue.sum().sort_values(ascending=False)
        answer='; '.join(f'**{k}: {money(v)}**' for k,v in g.items())+'. These are revenue totals, not normalized revenue per active day.'
    elif selection==questions[7]:
        g=filtered.groupby('age_group').revenue.sum().sort_values(ascending=False)
        answer=f'**{g.index[0]}** is the highest-revenue synthetic age group at **{money(g.iloc[0])}**. These demographic labels come from the project’s deterministic enrichment, not original UCI observations.'
    elif selection==questions[8]:
        answer=f"The recorded source reports **{ops.get('shed_total','unknown'):,} shed** and **{ops.get('shed_replayed','unknown'):,} replayed** sales lines, with **{ops.get('shed_pending','unknown')} pending**." if ops.get('shed_total') is not None else 'Operational tables were not accessible in this source.'
    else:
        answer=('The submitted project reports **397,884 fact rows**, no duplicate source keys, matching total quantity × price revenue, '
                'and a **passing 397,884-row line-level comparison** in `verify_rows.py`. '
                'However `verify_load.py` reported a failing invoice-level customer/time assertion: 30 invoices had multiple event timestamps. '
                'Do not describe every verification test as passing without addressing this distinction.')
    with st.container(border=True):
        st.markdown(answer)
    st.caption('Answers use the active date/store/channel filters, except historical run-level operations and validation facts.')
    section('Explain it in one sentence')
    st.markdown('> “We built a hybrid online-and-periodic retail ingestion pipeline that joins streaming sales to master data with MESHJOIN, protects overflow via disk shedding, and exposes the resulting warehouse as business intelligence.”')

st.divider()
st.caption(f'Data source: {actual_source}  ·  Showing {rows:,} of {len(df):,} rows  ·  {start} → {end}  ·  Submitted UCI Online Retail derived data (2010–2011).')
