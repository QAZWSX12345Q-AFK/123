import streamlit as st
import akshare as ak
import pandas as pd
import requests
import re
from datetime import datetime, date, timedelta

st.set_page_config(page_title="基金定投滚存计算器", page_icon="📈", layout="centered")

# ============================================================
# 多源分级数据获取引擎
# ============================================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Referer': 'https://fund.eastmoney.com/',
}

def _source_tencent(code):
    """P0: 腾讯财经实时行情（ETF/LOF 最稳定）"""
    try:
        url = f"https://qt.gtimg.cn/q={code}"
        res = requests.get(url, timeout=5)
        res.encoding = 'gbk'
        text = res.text
        # 腾讯返回格式: v_sz159326="51~电网设备ETF~159326~1.234~..."
        match = re.search(r'="(.+?)"', text)
        if not match:
            return None
        fields = match.group(1).split('~')
        if len(fields) < 5:
            return None
        return {
            'name': fields[1],
            'code': fields[2],
            'price': float(fields[3]),        # 当前价/估值
            'prev_close': float(fields[4]),    # 昨收
            'change_pct': float(fields[32]) if len(fields) > 32 else 0,
            'source': '腾讯财经(实时)',
            'is_realtime': True,
        }
    except Exception:
        return None

def _source_akshare_em(code):
    """P1: AkShare 天天基金数据（开放式基金净值）"""
    try:
        # 先尝试获取实时估值
        df_val = ak.fund_value_estimation_em(symbol="全部")
        row = df_val[df_val['基金代码'] == code]
        if not row.empty:
            r = row.iloc[0]
            return {
                'name': r['基金简称'],
                'code': code,
                'price': float(r['估算值']),
                'prev_close': float(r['单位净值']),
                'change_pct': float(r['估算增长率'].replace('%','')),
                'source': 'AkShare-天天基金(估值)',
                'is_realtime': True,
            }
    except Exception:
        pass

    # 降级：获取最新公布净值
    try:
        df_nav = ak.fund_open_fund_daily_em()
        row = df_nav[df_nav['基金代码'] == code]
        if not row.empty:
            r = row.iloc[0]
            return {
                'name': r['基金简称'],
                'code': code,
                'price': float(r['单位净值']),
                'prev_close': float(r['单位净值']),
                'change_pct': float(r['日增长率'].replace('%','')),
                'source': 'AkShare-天天基金(日净值)',
                'is_realtime': False,
            }
    except Exception:
        pass
    return None

def _source_akshare_sina(code):
    """P2: AkShare 新浪财经（ETF/LOF 实时）"""
    try:
        df = ak.fund_etf_category_sina(symbol="ETF基金")
        row = df[df['代码'].str.contains(code)]
        if not row.empty:
            r = row.iloc[0]
            return {
                'name': r['名称'],
                'code': code,
                'price': float(r['最新价']),
                'prev_close': float(r['昨收']),
                'change_pct': float(r['涨跌幅']),
                'source': 'AkShare-新浪财经(实时)',
                'is_realtime': True,
            }
    except Exception:
        pass
    return None

def _source_html_parse(code):
    """P3: 天天基金网页嵌入变量（用户方案，作为兜底）"""
    try:
        url = f"https://fund.eastmoney.com/{code}.html"
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code != 200:
            return None
        html = res.text
        name = re.search(r'var fS_name = "(.*?)";', html)
        gsz = re.search(r'var gz_gsz = "(.*?)";', html)
        gszzl = re.search(r'var gz_gszzl = "(.*?)";', html)
        dwjz = re.search(r'var dwjz = "(.*?)";', html)
        if name and dwjz:
            return {
                'name': name.group(1),
                'code': code,
                'price': float(gsz.group(1)) if gsz and gsz.group(1) else float(dwjz.group(1)),
                'prev_close': float(dwjz.group(1)),
                'change_pct': float(gszzl.group(1)) if gszzl and gszzl.group(1) else 0,
                'source': '天天基金(网页解析)',
                'is_realtime': bool(gsz and gsz.group(1)),
            }
    except Exception:
        pass
    return None

def get_fund_quote(code):
    """分级回退：依次尝试所有数据源"""
    for source_fn in [_source_tencent, _source_akshare_em, _source_akshare_sina, _source_html_parse]:
        result = source_fn(code)
        if result:
            return result
    return None

@st.cache_data(ttl=3600)
def get_fund_history_akshare(code, start_date_str):
    """使用 AkShare 获取历史净值（自动处理反爬）"""
    try:
        df = ak.fund_open_fund_info_em(fund=code, indicator="单位净值走势")
        df['净值日期'] = pd.to_datetime(df['净值日期']).dt.strftime('%Y-%m-%d')
        df = df[df['净值日期'] >= start_date_str].copy()
        df = df.rename(columns={'净值日期': 'date', '单位净值': 'dwjz'})
        df['dwjz'] = pd.to_numeric(df['dwjz'], errors='coerce')
        df = df.dropna(subset=['dwjz']).sort_values('date').reset_index(drop=True)
        return df.to_dict('records')
    except Exception as e:
        st.warning(f"AkShare 历史数据获取失败: {e}")
        return []


# ============================================================
# UI 渲染
# ============================================================

st.title("📈 基金定投滚存计算器")
st.caption("数据源：腾讯财经 / AkShare-天天基金 / AkShare-新浪财经（多源回退）")

with st.form("dca_form"):
    fund_code = st.text_input("基金代码 (如：021778)", value="021778")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("定投开始日期", value=date.today() - timedelta(days=365))
    with col2:
        daily_amount = st.number_input("定投金额 (元)", min_value=1.0, value=100.0, step=100.0)
    freq = st.selectbox("定投频率", ["每个交易日", "每周五", "每月1号"])
    submitted = st.form_submit_button("开始计算", type="primary", use_container_width=True)

if submitted:
    if not fund_code:
        st.warning("请输入基金代码")
    else:
        with st.spinner("正在获取数据..."):
            quote = get_fund_quote(fund_code)
            if not quote:
                st.error("❌ 所有数据源均获取失败，请检查基金代码或稍后重试。")
                st.stop()

            st.success(f"✅ {quote['name']} | 数据源：{quote['source']}")

            history = get_fund_history_akshare(fund_code, start_date.strftime('%Y-%m-%d'))

            if not history:
                st.warning("未获取到历史净值，将仅显示当前行情。")
                st.metric("最新价", f"{quote['price']:.4f}")
                st.metric("涨跌幅", f"{quote['change_pct']:+.2f}%")
                st.stop()

            # 定投计算
            total_shares, total_invested, records = 0.0, 0.0, []
            for day in history:
                d = datetime.strptime(day['date'], '%Y-%m-%d').date()
                should = freq == "每个交易日" or (freq == "每周五" and d.weekday() == 4) or (freq == "每月1号" and d.day == 1)
                if should:
                    total_shares += daily_amount / day['dwjz']
                    total_invested += daily_amount
                records.append({
                    '日期': day['date'], '单位净值': day['dwjz'],
                    '累计投入': total_invested, '累计份额': total_shares,
                    '当日总资产': total_shares * day['dwjz']
                })

            df = pd.DataFrame(records)
            current_asset = total_shares * quote['price']
            total_profit = current_asset - total_invested
            profit_rate = (total_profit / total_invested * 100) if total_invested > 0 else 0

            st.divider()
            st.subheader(f"📊 {quote['name']} 定投结算")
            st.caption(f"当前估值：{quote['price']:.4f} | {quote['source']}")

            c1, c2, c3 = st.columns(3)
            c1.metric("累计投入", f"{total_invested:.2f} 元")
            c2.metric("累计份额", f"{total_shares:.2f} 份")
            c3.metric("当前总资产", f"{current_asset:.2f} 元")

            color = "red" if total_profit > 0 else "green"
            st.markdown(f"<h3 style='text-align:center'>总收益：<span style='color:{color}'>{total_profit:+.2f} 元 ({profit_rate:+.2f}%)</span></h3>", unsafe_allow_html=True)

            st.line_chart(df.set_index('日期')[['累计投入', '当日总资产']])
            with st.expander("查看定投明细"):
                st.dataframe(df.sort_values('日期', ascending=False), use_container_width=True)
