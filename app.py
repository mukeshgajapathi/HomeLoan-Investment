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

# --- SECURITY / LOGIN WRAPPER ---
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

# --- HELPER: INDIAN CURRENCY FORMATTER ---
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

# --- YAHOO FINANCE TICKER MAP FOR ETFS ---
TICKER_MAP = {
    "NIFTYBEES": "NIFTYBEES.NS",
    "HDFCNIFETF": "HDFCNIFETF.NS",
    "JUNIORBEES": "JUNIORBEES.NS",
    "NEXT50": "NEXT50.NS",
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDBEES": "LIQUIDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "AUTOBEES": "AUTOBEES.NS",
    "BANKETF": "BANKETF.NS",
    "ITBEES": "ITBEES.NS",
    "PHARMABEES": "PHARMABEES.NS",
    "FMCGIETF": "FMCGIETF.NS",
    "SILVER": "SILVERBEES.NS",
    "NIFTYIETF": "NIFTYIETF.NS",
    "MIDCAPETF": "MID150BEES.NS"
}

EXCLUDE_KEYWORDS = ["FUT", "CE", "PE", "MCX", "GOLDPETAL", "GOLDGUINEA", "CRUDEOIL", "CALL", "PUT", "OPT", "FUTURES"]

def is_equity_or_etf(symbol_str):
    sym = str(symbol_str).upper()
    if re.search(r'\b\d{2}[A-Z]{3}\b', sym) or re.search(r'\d+(CE|PE)\b', sym):
        return False
    for kw in EXCLUDE_KEYWORDS:
        if kw in sym:
            return False
    return True

# --- FETCH LIVE ETF / STOCK PRICES ---
@st.cache_data(ttl=1800)
def fetch_live_ltp(ticker, default_price=0.0):
    if not ticker: return default_price
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
    return default_price

# --- FETCH LIVE MUTUAL FUND NAV VIA ISIN (MFAPI.IN) ---
@st.cache_data(ttl=3600)
def fetch_mf_nav_by_isin(isin, default_nav=0.0):
    if not isin or str(isin).strip().upper() in ["NAN", "NONE", ""]:
        return default_nav
    try:
        url_search = f"https://api.mfapi.in/mf/search?q={isin.strip()}"
        req = urllib.request.Request(url_search, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, list) and len(data) > 0:
                scheme_code = data[0]['schemeCode']
                url_nav = f"https://api.mfapi.in/mf/{scheme_code}"
                req_nav = urllib.request.Request(url_nav, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_nav, timeout=5) as resp_nav:
                    nav_json = json.loads(resp_nav.read().decode())
                    if "data" in nav_json and len(nav_json["data"]) > 0:
                        return float(nav_json["data"][0]["nav"])
    except Exception:
        pass
    return default_nav

# --- NEWTON-RAPHSON XIRR SOLVER ---
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

# --- PROCESS TRADEBOOK WITH ACCOUNT ID, SEGMENT & ISIN ---
def process_tradebook_tab(df_tradebook):
    if df_tradebook.empty:
        return None, pd.DataFrame(), pd.DataFrame()

    df_raw = df_tradebook.copy()
    df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]

    sym_col = 'symbol' if 'symbol' in df_raw.columns else df_raw.columns[0]
    date_col = 'trade_date' if 'trade_date' in df_raw.columns else 'date'
    type_col = 'trade_type' if 'trade_type' in df_raw.columns else 'type'
    qty_col = 'quantity' if 'quantity' in df_raw.columns else 'qty'
    price_col = 'price' if 'price' in df_raw.columns else 'rate'
    seg_col = 'segment' if 'segment' in df_raw.columns else None
    isin_col = 'isin' if 'isin' in df_raw.columns else None

    # Detect Account ID column
    acc_col = None
    for possible_acc in ['account', 'account_id', 'client_id', 'user', 'owner']:
        if possible_acc in df_raw.columns:
            acc_col = possible_acc
            break

    if date_col in df_raw.columns:
        df_raw["Date_DT"] = pd.to_datetime(df_raw[date_col], errors="coerce")
        df_raw = df_raw.dropna(subset=["Date_DT"]).sort_values("Date_DT")

    holdings = {}
    cash_flows = []
    dates = []

    for _, row in df_raw.iterrows():
        sym = str(row.get(sym_col, "")).strip().upper()
        t_type = str(row.get(type_col, "")).strip().lower()
        qty = float(row.get(qty_col, 0.0))
        price = float(row.get(price_col, 0.0))
        trade_val = qty * price
        
        raw_seg = str(row.get(seg_col, "")).strip().upper() if seg_col else ""
        isin_val = str(row.get(isin_col, "")).strip() if isin_col else ""
        
        # Read raw Account ID (e.g. HEK312 or SDB789)
        acc_val = str(row.get(acc_col, "SDB789")).strip().upper() if acc_col else "SDB789"
        if acc_val in ["NAN", "NONE", ""]: acc_val = "SDB789"

        if raw_seg == 'MF' or any(kw in sym for kw in ['DIRECT', 'GROWTH', 'MUTUAL', 'FUND', 'OPTION']):
            asset_class = "Mutual Fund"
        else:
            asset_class = "Equity / ETF"

        if not is_equity_or_etf(sym) or trade_val <= 0:
            continue

        if t_type == 'buy':
            cash_flows.append(-trade_val)
            dates.append(row["Date_DT"])
        elif t_type == 'sell':
            cash_flows.append(trade_val)
            dates.append(row["Date_DT"])

        holding_key = (sym, acc_val)

        if holding_key not in holdings:
            holdings[holding_key] = {
                "symbol": sym,
                "account": acc_val,
                "qty": 0.0, 
                "invested": 0.0, 
                "avg_cost": 0.0, 
                "last_price": price,
                "asset_class": asset_class,
                "isin": isin_val
            }

        h = holdings[holding_key]
        h["last_price"] = price

        if t_type == 'buy':
            h["qty"] += qty
            h["invested"] += trade_val
            if h["qty"] > 0:
                h["avg_cost"] = h["invested"] / h["qty"]
        elif t_type == 'sell':
            if h["qty"] > 0:
                h["qty"] = max(0.0, h["qty"] - qty)
                if h["qty"] == 0:
                    h["invested"] = 0.0
                    h["avg_cost"] = 0.0
                else:
                    h["invested"] = h["qty"] * h["avg_cost"]

    eq_rows = []
    mf_rows = []
    total_active_val = 0.0

    for (sym, acc), data in holdings.items():
        if data["qty"] > 0 and data["invested"] > 0:
            if data["asset_class"] == "Mutual Fund":
                live_nav = fetch_mf_nav_by_isin(data["isin"], default_nav=data["last_price"])
                curr_val = data["qty"] * live_nav
                pnl = curr_val - data["invested"]
                total_active_val += curr_val

                mf_rows.append({
                    "Symbol": sym,
                    "Account": data["account"],
                    "ISIN": data["isin"],
                    "Units_Accumulated": data["qty"],
                    "Avg_Cost": data["avg_cost"],
                    "Current_LTP": live_nav,
                    "Invested_Value": data["invested"],
                    "Current_Value": curr_val,
                    "P&L (₹)": pnl
                })
            else:
                ticker = TICKER_MAP.get(sym, f"{sym}.NS")
                ltp = fetch_live_ltp(ticker, default_price=data["last_price"])
                curr_val = data["qty"] * ltp
                pnl = curr_val - data["invested"]
                total_active_val += curr_val

                eq_rows.append({
                    "Symbol": sym,
                    "Account": data["account"],
                    "ISIN": data["isin"],
                    "Units_Accumulated": data["qty"],
                    "Avg_Cost": data["avg_cost"],
                    "Current_LTP": ltp,
                    "Invested_Value": data["invested"],
                    "Current_Value": curr_val,
                    "P&L (₹)": pnl
                })

    df_eq_active = pd.DataFrame(eq_rows)
    df_mf_active = pd.DataFrame(mf_rows)

    if cash_flows:
        cash_flows.append(float(total_active_val if total_active_val > 0 else 1.0))
        dates.append(datetime.now())
        computed_xirr = solve_xirr(cash_flows, dates)
    else:
        computed_xirr = None

    return computed_xirr, df_eq_active, df_mf_active

INITIAL_LOAN = 4890000.0

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try: df_tradebook = conn.read(worksheet="Tradebook", ttl=0)
    except Exception: df_tradebook = pd.DataFrame()
    return df_tradebook

df_tradebook = load_data()
computed_xirr, df_eq_active, df_mf_active = process_tradebook_tab(df_tradebook)

eq_val = df_eq_active["Current_Value"].sum() if not df_eq_active.empty else 0.0
eq_inv = df_eq_active["Invested_Value"].sum() if not df_eq_active.empty else 0.0
mf_val = df_mf_active["Current_Value"].sum() if not df_mf_active.empty else 0.0
mf_inv = df_mf_active["Invested_Value"].sum() if not df_mf_active.empty else 0.0

total_portfolio_val = eq_val + mf_val
total_portfolio_invested = eq_inv + mf_inv
overall_pnl = total_portfolio_val - total_portfolio_invested
overall_pnl_pct = (overall_pnl / total_portfolio_invested * 100) if total_portfolio_invested > 0 else 0.0

st.title("🏡 Home Loan & 📈 Investment Tracker")

# Summary Section
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

# Section 2A: Equity & ETF Holdings
st.markdown("#### 📊 Equity & ETF Holdings")
if df_eq_active.empty:
    st.info("No active Equity/ETF holdings found in 'Tradebook' tab.")
else:
    for _, row in df_eq_active.iterrows():
        sym = row["Symbol"]
        acc = row["Account"]
        units = row["Units_Accumulated"]
        ltp = row["Current_LTP"]
        inv = row["Invested_Value"]
        curr = row["Current_Value"]
        pnl = row["P&L (₹)"]
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0.0
        
        with st.container(border=True):
            st.markdown(
                f"**{sym}** &nbsp; <span style='color:#00D1B2; font-size:11px; background-color:#1E1E1E; padding:2px 8px; border-radius:4px; font-weight:600;'>{acc}</span> &nbsp; <span style='color:#808495; font-size:13px;'>{units:.4f} Units @ {format_inr(ltp)} (Avg: {format_inr(row['Avg_Cost'])})</span>", 
                unsafe_allow_html=True
            )
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", format_inr(inv))
            m2.metric("Current Value", format_inr(curr))
            m3.metric("Net P&L", format_inr(pnl), f"{pnl_pct:+.2f}%")

# Section 2B: Mutual Fund Holdings
st.markdown("#### 💼 Mutual Fund Holdings")
if df_mf_active.empty:
    st.info("No active Mutual Fund holdings found in 'Tradebook' tab.")
else:
    for _, row in df_mf_active.iterrows():
        sym = row["Symbol"]
        acc = row["Account"]
        units = row["Units_Accumulated"]
        ltp = row["Current_LTP"]
        inv = row["Invested_Value"]
        curr = row["Current_Value"]
        pnl = row["P&L (₹)"]
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0.0
        
        with st.container(border=True):
            st.markdown(
                f"**{sym}** &nbsp; <span style='color:#00D1B2; font-size:11px; background-color:#1E1E1E; padding:2px 8px; border-radius:4px; font-weight:600;'>{acc}</span> &nbsp; <span style='color:#808495; font-size:13px;'>{units:.4f} Units @ ₹{ltp:.2f} NAV (Avg: {format_inr(row['Avg_Cost'])})</span>", 
                unsafe_allow_html=True
            )
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", format_inr(inv))
            m2.metric("Current Value", format_inr(curr))
            m3.metric("Net P&L", format_inr(pnl), f"{pnl_pct:+.2f}%")
