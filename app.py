import streamlit as st
import pandas as pd
import yfinance as yf
import math
import urllib.request
import json
import re
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

st.set_page_config(
    page_title="Home Loan & Investment Tracker", 
    page_icon="🏡", 
    layout="wide"
)

def check_password():
    def password_entered():
        correct_password = str(st.secrets.get("APP_PASSWORD", st.secrets.get("theme", {}).get("APP_PASSWORD", "")))
        entered_password = str(st.session_state["password"]).strip()
        if entered_password == correct_password:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("### 🔒 Secure Login Required")
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.markdown("### 🔒 Secure Login Required")
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()

def format_inr(value):
    try:
        is_negative = value < 0
        value = abs(int(value))
        val_str = str(value)
        if len(val_str) <= 3:
            formatted = val_str
        else:
            last_three = val_str[-3:]
            other_digits = val_str[:-3]
            chunks = [other_digits[max(i-2, 0):i] for i in range(len(other_digits), 0, -2)]
            chunks.reverse()
            formatted = f"{','.join(chunks)},{last_three}"
        return f"-₹{formatted}" if is_negative else f"₹{formatted}"
    except ValueError:
        return "₹0"

EXACT_ETF_MAP = {
    "NIFTYBEES": "NIFTY 50",
    "HDFCNIFETF": "NIFTY 50",
    "JUNIORBEES": "Next 50",
    "NEXT50": "Next 50",
    "GOLDBEES": "GOLD",
    "LIQUIDBEES": "Liquid",
    "LIQUIDCASE": "Liquid"
}

EXCLUDE_KEYWORDS = ["FUT", "CE", "PE", "MCX", "GOLDPETAL", "GOLDGUINEA", "CRUDEOIL", "CALL", "PUT", "OPT", "FUTURES"]

def is_equity_or_etf(symbol_str):
    sym = str(symbol_str).upper()
    # Exclude option codes like 24OCT26000CE or 26APR22000PE
    if re.search(r'\b\d{2}[A-Z]{3}\b', sym) or re.search(r'\d+(CE|PE)\b', sym):
        return False
    for kw in EXCLUDE_KEYWORDS:
        if kw in sym:
            return False
    return True

@st.cache_data(ttl=1800)
def fetch_live_ltp(ticker):
    if ticker.startswith("AMFI:"):
        scheme_code = ticker.split(":")[1]
        try:
            url = f"https://api.mfapi.in/mf/{scheme_code}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if "data" in data and len(data["data"]) > 0:
                    return float(data["data"][0]["nav"])
        except Exception:
            return None

    try:
        data = yf.Ticker(ticker)
        try:
            price = data.fast_info.last_price
            if price is not None and not math.isnan(price) and price > 0:
                return float(price)
        except Exception:
            pass

        hist = data.history(period="5d")
        if not hist.empty and "Close" in hist.columns:
            valid_prices = hist["Close"].dropna()
            if not valid_prices.empty:
                val = float(valid_prices.iloc[-1])
                if val > 0:
                    return val
    except Exception:
        pass
    return None

def calc_rem_months(principal, emi, rate_monthly):
    if principal <= 0: return 0
    try:
        val = 1 - (principal * rate_monthly / emi)
        if val <= 0: return 9999 
        return -math.log(val) / math.log(1 + rate_monthly)
    except ValueError:
        return 0

def solve_xirr(cash_flows, dates, guess=0.12):
    try:
        if len(cash_flows) < 2 or sum(cash_flows) == 0: return 0.12
        d0 = dates[0]
        years = [(d - d0).days / 365.25 for d in dates]

        def f(r):
            if r <= -0.99: return 1e10
            return sum(cf / ((1 + r) ** y) for cf, y in zip(cash_flows, years))

        def df(r):
            if r <= -0.99: return -1e10
            return sum(-y * cf / ((1 + r) ** (y + 1)) for cf, y in zip(cash_flows, years))

        r = guess
        for _ in range(100):
            f_val = f(r)
            df_val = df(r)
            if abs(df_val) < 1e-12: break
            new_r = r - f_val / df_val
            if abs(new_r - r) < 1e-6:
                return max(0.05, min(new_r, 0.35))
            r = new_r
        return max(0.05, min(r, 0.35))
    except Exception:
        return 0.12

def process_raw_trades_tab(df_raw_trades, df_portfolio_base):
    if df_raw_trades.empty:
        return None, df_portfolio_base, pd.DataFrame()

    etf_categories = {cat: {"qty": 0.0, "invested": 0.0} for cat in df_portfolio_base["Category"].tolist()}
    mf_holdings = {}
    cash_flows = []
    dates = []

    df_raw = df_raw_trades.copy()
    if "Date" in df_raw.columns:
        df_raw["Date_DT"] = pd.to_datetime(df_raw["Date"], errors="coerce")
        df_raw = df_raw.dropna(subset=["Date_DT"]).sort_values("Date_DT")

    for _, row in df_raw.iterrows():
        sym = str(row.get("Symbol", "")).strip().upper()
        t_type = str(row.get("Type", "")).strip().lower()
        qty = float(row.get("Quantity", 0.0))
        price = float(row.get("Price", 0.0))
        trade_val = float(row.get("Value", qty * price))

        if not is_equity_or_etf(sym) or trade_val <= 0:
            continue

        if t_type == 'buy':
            cash_flows.append(-trade_val)
            dates.append(row["Date_DT"])
        elif t_type == 'sell':
            cash_flows.append(trade_val)
            dates.append(row["Date_DT"])

        matched_etf_cat = None
        for etf_key, cat_name in EXACT_ETF_MAP.items():
            if etf_key in sym:
                matched_etf_cat = cat_name
                break

        if matched_etf_cat and matched_etf_cat in etf_categories:
            if t_type == 'buy':
                etf_categories[matched_etf_cat]["qty"] += qty
                etf_categories[matched_etf_cat]["invested"] += trade_val
            elif t_type == 'sell':
                etf_categories[matched_etf_cat]["qty"] = max(0.0, etf_categories[matched_etf_cat]["qty"] - qty)
                etf_categories[matched_etf_cat]["invested"] = max(0.0, etf_categories[matched_etf_cat]["invested"] - trade_val)
        else:
            mf_key = sym.split('-')[0].strip()
            if mf_key not in mf_holdings:
                mf_holdings[mf_key] = {"qty": 0.0, "invested": 0.0, "last_price": price}
            if t_type == 'buy':
                mf_holdings[mf_key]["qty"] += qty
                mf_holdings[mf_key]["invested"] += trade_val
                mf_holdings[mf_key]["last_price"] = price
            elif t_type == 'sell':
                mf_holdings[mf_key]["qty"] = max(0.0, mf_holdings[mf_key]["qty"] - qty)
                mf_holdings[mf_key]["invested"] = max(0.0, mf_holdings[mf_key]["invested"] - trade_val)

    updated_portfolio = df_portfolio_base.copy()
    # Reset accumulated units and invested values before accumulating from scratch
    updated_portfolio["Units_Accumulated"] = 0.0
    updated_portfolio["Invested_Value"] = 0.0

    for idx, row in updated_portfolio.iterrows():
        cat = row["Category"]
        if cat in etf_categories:
            updated_portfolio.at[idx, "Units_Accumulated"] = etf_categories[cat]["qty"]
            updated_portfolio.at[idx, "Invested_Value"] = etf_categories[cat]["invested"]

    mf_rows = []
    for mf_name, data in mf_holdings.items():
        if data["invested"] > 0 and data["qty"] > 0:
            mf_rows.append({
                "Category": mf_name,
                "Units_Accumulated": data["qty"],
                "Current_LTP": data["last_price"],
                "Invested_Value": data["invested"],
                "Current_Value": data["qty"] * data["last_price"],
                "P&L (₹)": (data["qty"] * data["last_price"]) - data["invested"]
            })
    df_mf_portfolio = pd.DataFrame(mf_rows)

    etf_val = (updated_portfolio["Units_Accumulated"] * updated_portfolio["Current_LTP"]).sum()
    mf_val = df_mf_portfolio["Current_Value"].sum() if not df_mf_portfolio.empty else 0.0
    total_val = etf_val + mf_val

    if cash_flows:
        cash_flows.append(float(total_val if total_val > 0 else 1.0))
        dates.append(datetime.now())
        computed_xirr = solve_xirr(cash_flows, dates)
    else:
        computed_xirr = None

    return computed_xirr, updated_portfolio, df_mf_portfolio

TICKERS = {
    "Next 50": "NEXT50.NS", 
    "NIFTY 50": "NIFTYBEES.NS", 
    "GOLD": "GOLDBEES.NS", 
    "Liquid": "LIQUIDBEES.NS"
}
INITIAL_LOAN = 4890000.0
LOAN_TENURE_YEARS = 30

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try: df_loan = conn.read(worksheet="Loan_Tracker", ttl=0)
    except Exception: df_loan = pd.DataFrame()
        
    try: df_portfolio = conn.read(worksheet="Portfolio_Tracker", ttl=0)
    except Exception: df_portfolio = pd.DataFrame()
        
    try: df_raw_trades = conn.read(worksheet="Raw_Trades", ttl=0)
    except Exception: df_raw_trades = pd.DataFrame()

    if df_portfolio.empty:
        df_portfolio = pd.DataFrame({
            "Category": ["Next 50", "NIFTY 50", "GOLD", "Liquid"],
            "Units_Accumulated": [0.0, 0.0, 0.0, 0.0],
            "Current_LTP": [0.0, 0.0, 0.0, 0.0],
            "Invested_Value": [0.0, 0.0, 0.0, 0.0]
        })
    return df_loan, df_portfolio, df_raw_trades

df_loan, df_portfolio, df_raw_trades = load_data()

for idx, row in df_portfolio.iterrows():
    cat = row["Category"]
    if cat in TICKERS:
        fetched_ltp = fetch_live_ltp(TICKERS[cat])
        if fetched_ltp is not None and fetched_ltp > 0:
            df_portfolio.at[idx, "Current_LTP"] = fetched_ltp

df_portfolio["Units_Accumulated"] = pd.to_numeric(df_portfolio["Units_Accumulated"], errors='coerce').fillna(0.0)
df_portfolio["Current_LTP"] = pd.to_numeric(df_portfolio["Current_LTP"], errors='coerce').fillna(0.0)
df_portfolio["Invested_Value"] = pd.to_numeric(df_portfolio["Invested_Value"], errors='coerce').fillna(0.0)

computed_xirr, df_portfolio, df_mf_portfolio = process_raw_trades_tab(df_raw_trades, df_portfolio)

df_portfolio["Current_Value"] = df_portfolio["Units_Accumulated"] * df_portfolio["Current_LTP"]
df_portfolio["P&L (₹)"] = df_portfolio["Current_Value"] - df_portfolio["Invested_Value"]

etf_val = df_portfolio["Current_Value"].sum()
etf_inv = df_portfolio["Invested_Value"].sum()
mf_val = df_mf_portfolio["Current_Value"].sum() if not df_mf_portfolio.empty else 0.0
mf_inv = df_mf_portfolio["Invested_Value"].sum() if not df_mf_portfolio.empty else 0.0

total_portfolio_val = etf_val + mf_val
total_portfolio_invested = etf_inv + mf_inv
overall_pnl = total_portfolio_val - total_portfolio_invested
overall_pnl_pct = (overall_pnl / total_portfolio_invested * 100) if total_portfolio_invested > 0 else 0.0

st.title("🏡 Home Loan & 📈 Investment Tracker")

with st.container(border=True):
    st.subheader("🎯 Net-Debt-Zero Visualizer")
    net_debt = max(0.0, INITIAL_LOAN - total_portfolio_val)
    nd_covered_pct = (total_portfolio_val / INITIAL_LOAN * 100) if INITIAL_LOAN > 0 else 100.0
    
    nd_col1, nd_col2 = st.columns([3, 1])
    with nd_col1:
        st.progress(min(total_portfolio_val / INITIAL_LOAN, 1.0))
        st.caption(f"**{nd_covered_pct:.1f}% Covered** towards Net-Debt-Zero target")
    with nd_col2:
        st.metric("Net Debt Pending", format_inr(net_debt))

    st.divider()

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    s_col1.metric("Initial Loan", format_inr(INITIAL_LOAN))
    s_col2.metric("Portfolio Value", format_inr(total_portfolio_val))
    s_col3.metric("Total Invested", format_inr(total_portfolio_invested))
    s_col4.metric("Overall Net P&L", format_inr(overall_pnl), f"{overall_pnl_pct:+.2f}%")

st.divider()

st.subheader("2. Live Portfolio Holdings")

st.markdown("#### 📊 ETF Holdings")
active_etfs = df_portfolio[df_portfolio["Invested_Value"] > 0]
if active_etfs.empty:
    st.info("No active ETF holdings found.")
else:
    for _, row in active_etfs.iterrows():
        cat = row["Category"]
        units = row["Units_Accumulated"]
        ltp = row["Current_LTP"]
        inv = row["Invested_Value"]
        curr = row["Current_Value"]
        pnl = row["P&L (₹)"]
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0.0
        
        with st.container(border=True):
            st.markdown(f"**{cat}** &nbsp; <span style='color:#808495; font-size:13px;'>{units:.4f} Units @ {format_inr(ltp)}</span>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", format_inr(inv))
            m2.metric("Current Value", format_inr(curr))
            m3.metric("Net P&L", format_inr(pnl), f"{pnl_pct:+.2f}%")

if not df_mf_portfolio.empty:
    st.markdown("#### 💼 Mutual Fund Holdings")
    for _, row in df_mf_portfolio.iterrows():
        cat = row["Category"]
        units = row["Units_Accumulated"]
        ltp = row["Current_LTP"]
        inv = row["Invested_Value"]
        curr = row["Current_Value"]
        pnl = row["P&L (₹)"]
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0.0
        
        with st.container(border=True):
            st.markdown(f"**{cat}** &nbsp; <span style='color:#808495; font-size:13px;'>{units:.4f} Units @ {format_inr(ltp)} NAV</span>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", format_inr(inv))
            m2.metric("Current Value", format_inr(curr))
            m3.metric("Net P&L", format_inr(pnl), f"{pnl_pct:+.2f}%")
