import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date, timedelta
import math

# 设置页面配置，适配手机端
st.set_page_config(
    page_title="基金定投收益计算器", 
    page_icon="📈", 
    layout="centered"
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://fund.eastmoney.com/'
}

@st.cache_data(ttl=3600) # 缓存数据，避免频繁请求（1小时过期）
def get_fund_history(code, start_date_str):
    """获取基金历史净值"""
    all_data = []
    page_index = 1
    page_size = 1000 # 一次抓取尽量多
    has_more = True

    while has_more:
        url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex={page_index}&pageSize={page_size}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            json_data = res.json()
            if not json_data.get('Data') or not json_data['Data'].get('LSJZList'):
                break
            
            lsjz_list = json_data['Data']['LSJZList']
            for item in lsjz_list:
                # 只保留开始日期之后的数据
                if item['FSRQ'] >= start_date_str:
                    all_data.append({
                        'date': item['FSRQ'],
                        'dwjz': float(item['DWJZ']),
                        'jzzzl': float(item['JZZZL']) if item['JZZZL'] else 0.0
                    })
                
                # 如果当前返回的最后一条数据已经早于开始日期，说明不需要再翻页了
                if item['FSRQ'] < start_date_str:
                    has_more = False
                    break
            
            if len(lsjz_list) < page_size:
                break
            page_index += 1
        except Exception as e:
            st.error(f"获取历史净值失败: {e}")
            break

    # 按日期正序排列（从早到晚）
    all_data.reverse()
    return all_data

@st.cache_data(ttl=300) # 实时估值缓存5分钟
def get_realtime_estimate(code):
    """获取基金实时估值"""
    try:
        url = f"https://fundgz.1234567.com.cn/js/{code}.js"
        res = requests.get(url, headers=HEADERS, timeout=5)
        match = res.text.find('{')
        if match != -1:
            import json
            json_str = res.text[match:res.text.rfind('}')+1]
            return json.loads(json_str)
    except Exception:
        pass
    return None

# --- 页面 UI 渲染 ---
st.title("📈 基金定投滚存计算器")
st.markdown("模拟每日/定期定投，计算累计投入、累计份额与总资产。")

with st.form("dca_form"):
    fund_code = st.text_input("基金代码 (如：161119)", value="161119")
    
    col1, col2 = st.columns(2)
    with col1:
        # 默认开始日期设为一年前
        default_start = date.today() - timedelta(days=365)
        start_date = st.date_input("定投开始日期", value=default_start)
    with col2:
        daily_amount = st.number_input("每个交易日定投金额 (元)", min_value=1.0, value=100.0, step=100.0)
    
    # 定投频率选择（进阶）
    freq = st.selectbox("定投频率", ["每个交易日", "每周五", "每月1号"])
    
    submitted = st.form_submit_button("开始计算", type="primary", use_container_width=True)

if submitted:
    if not fund_code:
        st.warning("请输入基金代码")
    elif start_date >= date.today():
        st.warning("定投开始日期必须早于今天")
    else:
        with st.spinner("正在抓取历史数据并模拟定投..."):
            start_date_str = start_date.strftime('%Y-%m-%d')
            history_data = get_fund_history(fund_code, start_date_str)
            
            if not history_data:
                st.error("未获取到该时间段内的历史净值，请检查基金代码或调整开始日期。")
            else:
                # --- 定投模拟引擎 ---
                total_shares = 0.0
                total_invested = 0.0
                records = []
                
                # 过滤定投频率
                for i, day in enumerate(history_data):
                    current_date = datetime.strptime(day['date'], '%Y-%m-%d').date()
                    should_invest = False
                    
                    if freq == "每个交易日":
                        should_invest = True
                    elif freq == "每周五" and current_date.weekday() == 4:
                        should_invest = True
                    elif freq == "每月1号" and current_date.day == 1:
                        should_invest = True
                    
                    if should_invest:
                        # 买入份额 = 定投金额 / 当日净值
                        bought_shares = daily_amount / day['dwjz']
                        total_shares += bought_shares
                        total_invested += daily_amount
                    
                    # 记录每日的累计状态
                    records.append({
                        '日期': day['date'],
                        '单位净值': day['dwjz'],
                        '累计投入': total_invested,
                        '累计份额': total_shares,
                        '当日总资产': total_shares * day['dwjz']
                    })
                
                df = pd.DataFrame(records)
                
                # --- 获取当前实时估值 ---
                realtime_data = get_realtime_estimate(fund_code)
                
                if realtime_data and realtime_data.get('gsz'):
                    # 使用盘中估值计算实时总资产
                    current_nav = float(realtime_data['gsz'])
                    nav_date = realtime_data['gztime']
                    fund_name = realtime_data['name']
                    is_estimate = True
                else:
                    # 降级使用最新公布净值
                    current_nav = history_data[-1]['dwjz']
                    nav_date = history_data[-1]['date'] + " (已公布)"
                    fund_name = fund_code # 历史接口可能没有名称
                    is_estimate = False
                
                current_total_asset = total_shares * current_nav
                total_profit = current_total_asset - total_invested
                profit_rate = (total_profit / total_invested * 100) if total_invested > 0 else 0
                
                # --- 结果展示 ---
                st.divider()
                st.subheader(f"📊 {fund_name} ({fund_code})")
                st.caption(f"数据更新至：{nav_date} | 当前净值：{current_nav}")
                
                # 核心指标
                col1, col2, col3 = st.columns(3)
                col1.metric("累计投入本金", f"{total_invested:.2f} 元")
                col2.metric("累计持有份额", f"{total_shares:.2f} 份")
                col3.metric("当前总资产", f"{current_total_asset:.2f} 元")
                
                # 盈亏展示
                profit_color = "red" if total_profit > 0 else ("green" if total_profit < 0 else "gray")
                profit_str = f"{total_profit:+.2f} 元"
                rate_str = f"{profit_rate:+.2f}%"
                
                st.markdown(
                    f"<h3 style='text-align: center; margin-top: 10px;'>"
                    f"累计收益：<span style='color: {profit_color};'>{profit_str} ({rate_str})</span>"
                    f"</h3>", 
                    unsafe_allow_html=True
                )
                
                if is_estimate:
                    st.info("💡 当前总资产包含盘中实时估值，最终以晚间公布净值为准。")
                
                # --- 图表展示 ---
                st.divider()
                st.subheader("📉 资产走势图")
                # 将日期设置为索引，方便画图
                chart_data = df.set_index('日期')[['累计投入', '当日总资产']]
                st.line_chart(chart_data, color=["#FF4B4B", "#1F77B4"])
                
                # --- 明细数据展开 ---
                with st.expander("查看每日定投明细"):
                    st.dataframe(df.sort_values('日期', ascending=False), use_container_width=True)
