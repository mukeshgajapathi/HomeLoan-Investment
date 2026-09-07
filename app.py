import streamlit as st
import pandas as pd
import yfinance as yf
import math
import urllib.request
import json
import re
import io
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

# --- SAFE CONVERSION HELPERS ---
def safe_float(val, default=0.0):
    if isinstance(val, pd.Series):
        val = val.iloc[0] if not val.empty else default
    if pd.isna(val) or val is None:
        return default
    try:
        clean_val = str(val).replace(',', '').replace('(', '').replace(')', '').replace('%', '').strip()
        return float(clean_val)
    except (ValueError, TypeError):
        return default

def safe_str(val, default=""):
    if isinstance(val, pd.Series):
        val = val.iloc[0] if not val.empty else default
    if pd.isna(val) or val is None:
        return default
    return str(val).strip()

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

# --- UNIVERSAL HOLDINGS PARSER (KITE CSV & CONSOLE EXCEL) ---
def parse_zerodha_holdings_file(uploaded_file):
    fname = uploaded_file.name.upper()
    match = re.search(r'\b([A-Z0-9]{6})\b', fname)
    filename_acc_id = match.group(1) if match else "SDB789"
    
    records = []
    client_id = filename_acc_id

    # CASE A: Zerodha Console Excel Statement (.xlsx / .xls)
    if fname.endswith(('.XLSX', '.XLS')):
        xls = pd.ExcelFile(uploaded_file)
        sheets = xls.sheet_names
        sheet_to_use = 'Combined' if 'Combined' in sheets else sheets[0]
        df_raw = pd.read_excel(xls, sheet_name=sheet_to_use, header=None)
        
        for r in range(min(15, len(df_raw))):
            row_vals = [safe_str(x) for x in df_raw.iloc[r].dropna().values]
            if 'Client ID' in row_vals:
                idx = row_vals.index('Client ID')
                if idx + 1 < len(row_vals):
                    client_id = row_vals[idx + 1].upper()
                    
        header_idx = -1
        for r in range(len(df_raw)):
            row_vals = [safe_str(x).upper() for x in df_raw.iloc[r].dropna().values]
            if 'SYMBOL' in row_vals and 'QUANTITY AVAILABLE' in row_vals:
                header_idx = r
                break
                
        if header_idx != -1:
            headers = [safe_str(x) for x in df_raw.iloc[header_idx].values]
            df_data = df_raw.iloc[header_idx+1:].copy()
            df_data.columns = headers
            
            for _, row in df_data.iterrows():
                sym = safe_str(row.get('Symbol', ''))
                if not sym or sym.upper() == 'NAN' or 'SUMMARY' in sym.upper():
                    continue
                    
                qty = safe_float(row.get('Quantity Available', 0.0))
                avg_price = safe_float(row.get('Average Price', 0.0))
                ltp = safe_float(row.get('Previous Closing Price', 0.0))
                isin = safe_str(row.get('ISIN', ''))
                inst_type = safe_str(row.get('Instrument Type', ''))
                
                asset_class = "Mutual Fund" if (inst_type != '-' and ('DEBT' in inst_type.upper() or 'MUTUAL' in inst_type.upper() or 'EQUITY' in inst_type.upper())) else "Equity / ETF"
                clean_sym = sym.replace('-E', '').strip()
                
                if qty > 0:
                    records.append({
                        "Account": client_id,
                        "Symbol": clean_sym,
                        "ISIN": isin,
                        "Asset_Class": asset_class,
                        "Units_Accumulated": qty,
                        "Avg_Cost": avg_price,
                        "Current_LTP": ltp,
                        "Invested_Value": round(qty * avg_price, 2),
                        "Current_Value": round(qty * ltp, 2),
                        "P&L (₹)": round(qty * (ltp - avg_price), 2)
                    })

    # CASE B: Zerodha Kite / Console Holdings CSV (.csv)
    elif fname.endswith('.CSV'):
        df = pd.read_csv(uploaded_file)
        df.columns = [str(c).strip().replace('.', '').lower() for c in df.columns]
        
        sym_col = next((c for c in df.columns if 'instrument' in c or 'symbol' in c or 'tradingsymbol' in c), df.columns[0])
        qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), None)
        avg_col = next((c for c in df.columns if 'avg' in c or 'average' in c or 'cost' in c), None)
        ltp_col = next((c for c in df.columns if 'ltp' in c or 'last' in c or 'price' in c or 'close' in c), None)
        isin_col = next((c for c in df.columns if 'isin' in c), None)
        
        for _, row in df.iterrows():
            sym = safe_str(row.get(sym_col, ''))
            if not sym or sym.upper() == 'NAN' or 'TOTAL' in sym.upper() or 'SUMMARY' in sym.upper():
                continue
                
            qty = safe_float(row.get(qty_col, 0.0)) if qty_col else 0.0
            avg_price = safe_float(row.get(avg_col, 0.0)) if avg_col else 0.0
            ltp = safe_float(row.get(ltp_col, 0.0)) if ltp_col else avg_price
            isin = safe_str(row.get(isin_col, '')) if isin_col else ""
            
            clean_sym = sym.replace('-E', '').strip()
            
            if any(kw in clean_sym.upper() for kw in ['DIRECT', 'GROWTH', 'MUTUAL', 'FUND', 'LIQUID', 'MONEY MARKET']) or (isin and isin.startswith('INF') and not clean_sym.endswith('BEES') and 'ETF' not in clean_sym.upper()):
                asset_class = "Mutual Fund"
            else:
                asset_class = "Equity / ETF"
                
            if qty > 0:
                records.append({
                    "Account": client_id,
                    "Symbol": clean_sym,
                    "ISIN": isin,
                    "Asset_Class": asset_class,
                    "Units_Accumulated": qty,
                    "Avg_Cost": avg_price,
                    "Current_LTP": ltp,
                    "Invested_Value": round(qty * avg_price, 2),
                    "Current_Value": round(qty * ltp, 2),
                    "P&L (₹)": round(qty * (ltp - avg_price), 2)
                })

    return client_id, pd.DataFrame(records)

INITIAL_LOAN = 4890000.0

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try: 
        df_portfolio = conn.read(worksheet="Portfolio_Tracker", ttl=0)
        if not df_portfolio.empty:
            df_portfolio.columns = [str(c).strip().lower() for c in df_portfolio.columns]
            df_portfolio = df_portfolio.loc[:, ~df_portfolio.columns.duplicated()]
    except Exception: 
        df_portfolio = pd.DataFrame()

    console_xirr = None
    try:
        df_settings = conn.read(worksheet="Loan_Settings", ttl=0)
        if not df_settings.empty and "Console_XIRR" in df_settings.columns:
            val = df_settings.iloc[0]["Console_XIRR"]
            if pd.notna(val) and str(val).strip() != "":
                console_xirr = safe_float(val, None)
    except Exception:
        console_xirr = None

    return df_portfolio, console_xirr

df_portfolio_raw, console_xirr = load_data()

# Process Holdings Data
eq_rows = []
mf_rows = []

if not df_portfolio_raw.empty:
    for _, row in df_portfolio_raw.iterrows():
        sym = safe_str(row.get('symbol', ''))
        acc = safe_str(row.get('account', 'SDB789')).upper()
        isin_val = safe_str(row.get('isin', ''))
        asset_class = safe_str(row.get('asset_class', 'Equity / ETF'))
        units = safe_float(row.get('units_accumulated', 0.0))
        avg_cost = safe_float(row.get('avg_cost', 0.0))
        last_ltp = safe_float(row.get('current_ltp', 0.0))
        
        if units <= 0: continue
        
        if asset_class == "Mutual Fund":
            live_nav = fetch_mf_nav_by_isin(isin_val, default_nav=last_ltp)
            curr_val = units * live_nav
            pnl = curr_val - (units * avg_cost)
            
            mf_rows.append({
                "Symbol": sym,
                "Account": acc,
                "ISIN": isin_val,
                "Units_Accumulated": units,
                "Avg_Cost": avg_cost,
                "Current_LTP": live_nav,
                "Invested_Value": units * avg_cost,
                "Current_Value": curr_val,
                "P&L (₹)": pnl
            })
        else:
            ticker = TICKER_MAP.get(sym, f"{sym}.NS")
            ltp = fetch_live_ltp(ticker, default_price=last_ltp)
            curr_val = units * ltp
            pnl = curr_val - (units * avg_cost)
            
            eq_rows.append({
                "Symbol": sym,
                "Account": acc,
                "ISIN": isin_val,
                "Units_Accumulated": units,
                "Avg_Cost": avg_cost,
                "Current_LTP": ltp,
                "Invested_Value": units * avg_cost,
                "Current_Value": curr_val,
                "P&L (₹)": pnl
            })

df_eq_active = pd.DataFrame(eq_rows)
df_mf_active = pd.DataFrame(mf_rows)

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
    
    xirr_label = f"**{console_xirr:.2f}%**" if console_xirr is not None else "*Not Set (Import Holdings to Set)*"

    nd_col1, nd_col2 = st.columns([3, 1])
    with nd_col1:
        st.progress(min(total_portfolio_val / INITIAL_LOAN, 1.0))
        st.caption(f"**{nd_covered_pct:.1f}% Covered** towards Net-Debt-Zero target | Active XIRR: {xirr_label}")
    with nd_col2:
        st.metric("Net Debt Pending", format_inr(net_debt))

    st.divider()

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    s_col1.metric("Initial Loan", format_inr(INITIAL_LOAN))
    s_col2.metric("Portfolio Value", format_inr(total_portfolio_val))
    s_col3.metric("Total Invested", format_inr(total_portfolio_invested))
    s_col4.metric("Overall Net P&L", format_inr(overall_pnl), f"{overall_pnl_pct:+.2f}%")

st.divider()

# --- SECTION 2: LIVE PORTFOLIO HOLDINGS & ACTION HEADER ---
sec2_hdr_col, sec2_act_col = st.columns([3, 1])

with sec2_hdr_col:
    st.subheader("2. Live Portfolio Holdings")

with sec2_act_col:
    with st.popover("📥 Import Holdings File(s)", use_container_width=True):
        st.markdown("**Import Zerodha Holdings (CSV or Excel)**")
        
        uploaded_files = st.file_uploader(
            "Select Holdings File(s)", 
            type=["csv", "xlsx", "xls"], 
            accept_multiple_files=True,
            key="holdings_uploader",
            help="Upload multiple files at once (e.g. holdings-HEK312.csv, holdings-SDB789.xlsx)."
        )

        input_xirr = st.number_input(
            "Console Overall XIRR (%)", 
            value=None,
            min_value=-100.0, 
            max_value=500.0, 
            step=0.1,
            placeholder="e.g. 14.5 or -2.5 (Mandatory)",
            help="Enter overall portfolio XIRR % from Zerodha Console. Negative, zero, and positive values are allowed."
        )

        if st.button("Sync Holdings & XIRR to Google Sheets", key="btn_sync_holdings", use_container_width=True):
            if input_xirr is None:
                st.error("⚠️ Overall Console XIRR (%) is mandatory. Please enter your XIRR percentage before syncing.")
            elif not uploaded_files:
                st.error("⚠️ Please select at least one holdings CSV or Excel file to upload.")
            else:
                parsed_records = []
                for file in uploaded_files:
                    cid, df_parsed = parse_zerodha_holdings_file(file, file.name)
                    if not df_parsed.empty:
                        parsed_records.append(df_parsed)
                        st.info(f"Loaded **{len(df_parsed)} active holdings** for account **{cid}** from `{file.name}`")

                if parsed_records:
                    df_new_holdings = pd.concat(parsed_records, ignore_index=True)
                    df_new_holdings.columns = [str(c).strip().lower() for c in df_new_holdings.columns]
                    
                    # Deduplicate holdings by Symbol and Account
                    df_deduped_holdings = df_new_holdings.drop_duplicates(subset=["symbol", "account"]).reset_index(drop=True)

                    # Write Holdings to 'Portfolio_Tracker' tab
                    try:
                        conn.update(worksheet="Portfolio_Tracker", data=df_deduped_holdings)
                        
                        # Save XIRR to 'Loan_Settings' tab
                        try:
                            df_settings = conn.read(worksheet="Loan_Settings", ttl=0)
                            if df_settings.empty:
                                df_settings = pd.DataFrame([{"Disbursed_Ratio": 0.90, "Handover_Completed": "FALSE", "Interest_Rate": 7.20, "Console_XIRR": input_xirr}])
                            else:
                                df_settings.at[0, "Console_XIRR"] = input_xirr
                            conn.update(worksheet="Loan_Settings", data=df_settings)
                        except Exception:
                            pass

                        st.success("🎉 Successfully synced active holdings and Console XIRR!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to update Google Sheets: {e}")

# Section 2A: Equity & ETF Holdings
st.markdown("#### 📊 Equity & ETF Holdings")
if df_eq_active.empty:
    st.info("No active Equity/ETF holdings found in 'Portfolio_Tracker' tab.")
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
    st.info("No active Mutual Fund holdings found in 'Portfolio_Tracker' tab.")
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
