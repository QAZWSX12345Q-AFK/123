import streamlit as st
import requests
import pandas as pd
import re
from datetime import datetime, date, timedelta

st.set_page_config(page_title="基金定投计算器", page_icon="📈", layout="centered")

# 腾讯实时行情接口（对云端 IP 极其友好，不封禁）
def get_realtime_quote(code):
    # 判断是 ETF(5/1开头) 还是场外基金
    prefix = "sz" if code.startswith(("15", "16", "18")) else "sh"
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        res = requests.get(url, timeout=5)
        res.encoding = 'gbk'
        match = re.search(r'="(.+?)"', res.text)
        if match:
            fields = match.group(1).split('~')
            if len(fields) > 4:
                return {
                    'name': fields[1],
                    'price': float(fields[3]),
                    'prev_close': float(fields[4]),
                    'change_pct': float(fields[32]) if len(fields) > 32 else 0.0,
                    'source': '腾讯财经(实时)'
                }
    except Exception:
        pass
    return None

# 天天基金网页抓取（备用，用于场外基金）
def get_html_quote(code):
    url = f"https://fund.eastmoney.com/{code}.html"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36'}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            html = res.text
            name = re.search(r'var fS_name = "(.*?)";', html)
            gsz = re.search(r'var gz_gsz = "(.*?)";', html)
            dwjz = re.search(r'var dwjz = "(.*?)";', html)
            gszzl = re.search(r'var gz_gszzl = "(.*?)";', html)
            if name and dwjz:
                price = float(gsz.group(1)) if gsz and gsz.group(1) else float(dwjz.group(1))
                return {
                    'name': name.group(1),
                    'price': price,
                    'prev_close': float(dwjz.group(1)),
                    'change_pct': float(gszzl.group(1)) if gszzl and gszzl.group(1) else 0.0,
                    'source': '天天基金(网页)'
                }
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def get_history(code):
    """抓取历史净值（通过东方财富 API，云端有概率被拦，这里做静默处理）"""
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=500"
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://fund.eastmoney.com/'}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        data = res.json()
        if data.get('Data') and data['Data'].get('LSJZList'):
            df = pd.DataFrame(data['Data']['LSJZList'])
            df = df.rename(columns={'FSRQ': 'date', 'DWJZ': 'dwjz'})
            df['dwjz'] = pd.to_numeric(df['dwjz'])
            return df[['date', 'dwjz']].sort_values('date').reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()

# --- UI ---
st.title("📈 基金定投滚存计算器")

with st.form("form"):
    code = st.text_input("基金代码", value="021778")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("开始日期", value=date.today() - timedelta(days=365))
    with col2:
        amount = st.number_input("每期定投金额(元)", value=100.0, step=100.0)
    freq = st.selectbox("频率", ["每个交易日", "每周五", "每月1号"])
    submit = st.form_submit_button("开始计算", use_container_width=True, type="primary")

if submit:
    with st.spinner("获取数据中..."):
        quote = get_realtime_quote(code) or get_html_quote(code)
        if not quote:
            st.error("获取实时行情失败，请检查代码是否正确。")
            st.stop()
        
        st.success(f"✅ {quote['name']} | 来源: {quote['source']} | 当前价: {quote['price']}")
        
        history = get_history(code)
        if history.empty:
            st.warning("未能获取历史净值明细，可能被云端拦截。建议在本地电脑运行本项目以获取完整定投回测。")
            st.metric("最新价格", f"{quote['price']:.4f}")
            st.metric("今日涨跌", f"{quote['change_pct']:+.2f}%")
            st.stop()
            
        # 定投模拟
        history = history[history['date'] >= start_date.strftime('%Y-%m-%d')]
        total_shares, total_invested = 0.0, 0.0
        records = []
        for _, row in history.iterrows():
            d = datetime.strptime(row['date'], '%Y-%m-%d').date()
            should = freq == "每个交易日" or (freq == "每周五" and d.weekday() == 4) or (freq == "每月1号" and d.day == 1)
            if should:
                total_shares += amount / row['dwjz']
                total_invested += amount
            records.append({
                '日期': row['date'], '净值': row['dwjz'],
                '累计投入': total_invested, '累计份额': total_shares,
                '总资产': total_shares * row['dwjz']
            })
            
        df = pd.DataFrame(records)
        current_asset = total_shares * quote['price']
        profit = current_asset - total_invested
        profit_rate = (profit / total_invested * 100) if total_invested > 0 else 0
        
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("累计投入", f"{total_invested:.2f} 元")
        c2.metric("累计份额", f"{total_shares:.2f} 份")
        c3.metric("当前总资产", f"{current_asset:.2f} 元")
        
        color = "red" if profit > 0 else "green"
        st.markdown(f"<h3 style='text-align:center'>总收益：<span style='color:{color}'>{profit:+.2f} 元 ({profit_rate:+.2f}%)</span></h3>", unsafe_allow_html=True)
        st.line_chart(df.set_index('日期')[['累计投入', '总资产']])
        
        with st.expander("查看明细"):
            st.dataframe(df.sort_values('日期', ascending=False), use_container_width=True)
