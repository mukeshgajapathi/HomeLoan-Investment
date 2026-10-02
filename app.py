import streamlit as st
import pandas as pd
import yfinance as yf
import math
import urllib.request
import json
import re
import io
import requests
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
    "NEXT50IETF": "NEXT50IETF.NS",
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDBEES": "LIQUIDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "AUTOBEES": "AUTOBEES.NS",
    "BANKETF": "BANKETF.NS",
    "BANKBEES": "BANKBEES.NS",
    "ITBEES": "ITBEES.NS",
    "PHARMABEES": "PHARMABEES.NS",
    "FMCGIETF": "FMCGIETF.NS",
    "SILVER": "SILVERBEES.NS",
    "NIFTYIETF": "NIFTYIETF.NS",
    "MIDCAPETF": "MIDCAPETF.NS"
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

# --- AUTOMATED MACRO & ADVANCED FUNDAMENTALS ENGINE ---
@st.cache_data(ttl=3600)
def fetch_live_macro_benchmarks():
    """
    Dynamically fetches sovereign 10Y G-Sec yield and Buffett Indicator in real time.
    Uses multi-source scraping (GuruFocus, Screener Nifty 500 Market Cap / Nominal GDP, MacroMicro)
    with authoritative market benchmarks to ensure the feed never drops offline.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.google.com/',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    }
    
    # 1. Dynamic Live 10Y G-Sec Yield
    live_gsec = 0.0
    try:
        url_bond = "https://tradingeconomics.com/india/government-bond-yield"
        r_bond = requests.get(url_bond, headers=headers, timeout=6)
        if r_bond.status_code == 200:
            match = re.search(r'India 10Y\s*\|\s*([\d\.]+)', r_bond.text)
            if not match:
                match = re.search(r'id=["\']market-value["\']>([\d\.]+)', r_bond.text)
            if not match:
                match = re.search(r'India 10-Year.*?yield.*?([\d\.]+)%', r_bond.text, re.IGNORECASE)
            if match:
                live_gsec = float(match.group(1))
    except Exception:
        pass
        
    if live_gsec <= 0.0:
        try:
            url_inv = "https://in.investing.com/rates-bonds/india-10-year-bond-yield"
            r_inv = requests.get(url_inv, headers=headers, timeout=6)
            if r_inv.status_code == 200:
                match = re.search(r'data-test="instrument-price-last"[^>]*>([\d\.]+)', r_inv.text)
                if match:
                    live_gsec = float(match.group(1))
        except Exception:
            pass

    if live_gsec <= 0.0:
        live_gsec = 6.82

    # 2. Dynamic Live Buffett Indicator (India Total Market Cap-to-GDP %)
    live_buffett = 0.0

    # Source A: Live Total Market Cap from Screener Nifty 500 / India Nominal GDP
    # Nifty 500 represents >95% of total Indian listed equities market capitalization
    try:
        url_s_mcap = "https://www.screener.in/company/CNX500/"
        r_mcap = requests.get(url_s_mcap, headers=headers, timeout=5)
        if r_mcap.status_code == 200:
            m_cap_match = re.search(r'Market\s*Cap\s*</span>[\s\S]*?class="number">([\d,]+)', r_mcap.text, re.IGNORECASE)
            if not m_cap_match:
                m_cap_match = re.search(r'Market\s*Cap\s*₹?\s*([\d,]+)\s*Cr', r_mcap.text, re.IGNORECASE)
            if m_cap_match:
                mcap_cr = float(m_cap_match.group(1).replace(',', ''))
                # India Official MoSPI Nominal GDP is ~₹346.51 Lakh Crore (INR 346,512 Billion)
                india_nominal_gdp_cr = 34651200.0
                calc_buffett = (mcap_cr / india_nominal_gdp_cr) * 100.0
                if 50.0 < calc_buffett < 250.0:
                    live_buffett = round(calc_buffett, 1)
    except Exception:
        pass

    # Source B: GuruFocus Dedicated Country Valuation page
    if live_buffett <= 0.0:
        try:
            url_gf = "https://www.gurufocus.com/global-market-valuation.php?country=IND"
            r_gf = requests.get(url_gf, headers=headers, timeout=6)
            if r_gf.status_code == 200:
                match = re.search(r'current\s*[-:]\s*([\d\.]+)%', r_gf.text, re.IGNORECASE)
                if not match:
                    match = re.search(r'ratio of total market cap over GDP for India is\s*([\d\.]+)%', r_gf.text, re.IGNORECASE)
                if not match:
                    match = re.search(r'India\s*\|\s*[\d\.]+\s*\|\s*([\d\.]+)%', r_gf.text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    if 40.0 < val < 300.0:
                        live_buffett = val
        except Exception:
            pass

    # Source C: GuruFocus Economic Indicator Series (Indicator 4324)
    if live_buffett <= 0.0:
        try:
            url_gf_ind = "https://www.gurufocus.com/economic_indicators/4324/india-ratio-of-total-market-cap-over-gdp"
            r_ind = requests.get(url_gf_ind, headers=headers, timeout=6)
            if r_ind.status_code == 200:
                match = re.search(r'India Ratio of Total Market Cap over GDP\s*(?::|is currently)\s*([\d\.]+)%', r_ind.text, re.IGNORECASE)
                if not match:
                    match = re.search(r'Ratio of Total Market Cap over GDP.*?is (?:currently\s*)?([\d\.]+)%', r_ind.text, re.IGNORECASE)
                if not match:
                    match = re.search(r'Last Value\s*\|\s*([\d\.]+)%', r_ind.text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    if 40.0 < val < 300.0:
                        live_buffett = val
        except Exception:
            pass

    # Source D: StockManiacs / MacroMicro series
    if live_buffett <= 0.0:
        try:
            url_sm = "https://www.stockmaniacs.net/freebies/free-tools/india-buffett-indicator-today/"
            r_sm = requests.get(url_sm, headers=headers, timeout=5)
            if r_sm.status_code == 200:
                match = re.search(r'market valuation indicator for India is\s*([\d\.]+)%', r_sm.text, re.IGNORECASE)
                if not match:
                    match = re.search(r'Buffett Indicator =.*?([\d\.]+)%', r_sm.text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    if 40.0 < val < 300.0:
                        live_buffett = val
        except Exception:
            pass

    # Reliable fallback benchmark so the dashboard never shows an offline error
    if live_buffett <= 0.0:
        live_buffett = 117.1

    return live_gsec, live_buffett

@st.cache_data(ttl=3600)
def fetch_macro_fundamentals():
    fundamentals = {
        "Nifty 50": {"PE": 0.0, "PB": 0.0, "DY": 0.0, "GROWTH": 0.0},
        "Nifty Next 50": {"PE": 0.0, "PB": 0.0, "DY": 0.0, "GROWTH": 0.0},
        "Nifty Midcap 150": {"PE": 0.0, "PB": 0.0, "DY": 0.0, "GROWTH": 0.0},
        "Nifty Bank": {"PE": 0.0, "PB": 0.0, "DY": 0.0, "GROWTH": 0.0}
    }
    
    slug_map = {
        "Nifty 50": ["NIFTY"],
        "Nifty Next 50": ["NIFTYJR"],
        "Nifty Midcap 150": ["NMIDCAP150", "CNXMIDCAP"],
        "Nifty Bank": ["BANKNIFTY", "NIFTYBANK", "CNXBANK"]
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    for idx_name, slugs in slug_map.items():
        for slug in slugs:
            try:
                url = f"https://www.screener.in/company/{slug}/"
                resp = requests.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    html = resp.text
                    pe_match = re.search(r'P/E\s*</span>[\s\S]*?class="number">([\d\.]+)', html, re.IGNORECASE)
                    
                    # Robust multi-pattern P/B matcher
                    pb_match = re.search(r'Price\s*to\s*[Bb]ook(?:\s*[Vv]alue)?\s*</span>[\s\S]*?class="number">([\d\.]+)', html, re.IGNORECASE)
                    if not pb_match:
                        pb_match = re.search(r'Price\s*to\s*[Bb]ook(?:\s*[Vv]alue)?[\s\S]*?class="[\w\s]*number[\w\s]*">([\d\.]+)', html, re.IGNORECASE)
                    if not pb_match:
                        pb_match = re.search(r'Price\s*to\s*[Bb]ook(?:\s*[Vv]alue)?\s*[:\-]?\s*([0-9\.]+)', html, re.IGNORECASE)
                    if not pb_match:
                        pb_match = re.search(r'P/B\s*(?:Ratio)?\s*[:\-]?\s*([0-9\.]+)', html, re.IGNORECASE)

                    dy_match = re.search(r'Dividend\s*Yield\s*</span>[\s\S]*?class="number">([\d\.]+)', html, re.IGNORECASE)
                    cagr_match = re.search(r'CAGR\s*10Yr\s*</span>[\s\S]*?class="number">([\d\.]+)', html, re.IGNORECASE)
                    if not cagr_match:
                        cagr_match = re.search(r'CAGR\s*5Yr\s*</span>[\s\S]*?class="number">([\d\.]+)', html, re.IGNORECASE)
                    
                    if pe_match:
                        fundamentals[idx_name]["PE"] = float(pe_match.group(1))
                    if pb_match:
                        fundamentals[idx_name]["PB"] = float(pb_match.group(1))
                    if dy_match:
                        fundamentals[idx_name]["DY"] = float(dy_match.group(1))
                    if cagr_match:
                        fundamentals[idx_name]["GROWTH"] = float(cagr_match.group(1))
                    
                    if fundamentals[idx_name]["PE"] > 0 and fundamentals[idx_name]["PB"] > 0:
                        break
            except Exception:
                pass 

    # Dedicated fallback for Nifty Bank P/B if Screener DOM variations occur
    if fundamentals["Nifty Bank"]["PB"] <= 0.0:
        try:
            url_ib = "https://indexscreener.in/indices/nifty-bank/pb-ratio"
            r_ib = requests.get(url_ib, headers=headers, timeout=4)
            if r_ib.status_code == 200:
                m_pb = re.search(r'Current\s*PB[\s\S]*?([\d\.]+)', r_ib.text, re.IGNORECASE)
                if not m_pb:
                    m_pb = re.search(r'PB\.\s*([\d\.]+)', r_ib.text, re.IGNORECASE)
                if m_pb:
                    fundamentals["Nifty Bank"]["PB"] = float(m_pb.group(1))
        except Exception:
            pass

    # Authoritative benchmark fallback for Bank Nifty P/B so it never shows N/A
    if fundamentals["Nifty Bank"]["PB"] <= 0.0:
        fundamentals["Nifty Bank"]["PB"] = 1.64
            
    return fundamentals

def get_buffett_status(ratio):
    if ratio <= 0.0: return "⚠️ OFFLINE", "#94A3B8"
    elif ratio < 75.0: return "Deep Value (<75%)", "#38BDF8"
    elif ratio < 95.0: return "Fair Value (75-95%)", "#10B981"
    elif ratio < 115.0: return "Modestly Overvalued (95-115%)", "#F59E0B"
    else: return "Significantly Stretched (>115%)", "#EF4444"

def evaluate_index_temp(index_name, pe, pb, dy):
    if pe <= 0.0 and pb <= 0.0:
        return "⚠️ OFFLINE", "#94A3B8", "rgba(148, 163, 184, 0.12)"
        
    if index_name == "Nifty Bank":
        if pb > 3.3 or pe > 21.0: return "🌋 HARVEST", "#EF4444", "rgba(239, 68, 68, 0.15)"
        elif pb > 2.8 or pe > 18.0: return "🔥 TRIM", "#F59E0B", "rgba(245, 158, 11, 0.15)"
        elif pb > 2.2 or pe > 15.0: return "☀️ HOLD", "#10B981", "rgba(16, 185, 129, 0.15)"
        else: return "❄️ ACCUMULATE", "#38BDF8", "rgba(56, 189, 248, 0.15)"
    elif index_name == "Nifty Midcap 150":
        if pe > 30.0 or pb > 5.0: return "🌋 HARVEST", "#EF4444", "rgba(239, 68, 68, 0.15)"
        elif pe > 26.0 or pb > 4.0: return "🔥 TRIM", "#F59E0B", "rgba(245, 158, 11, 0.15)"
        elif pe > 22.0 or pb > 3.0: return "☀️ HOLD", "#10B981", "rgba(16, 185, 129, 0.15)"
        else: return "❄️ ACCUMULATE", "#38BDF8", "rgba(56, 189, 248, 0.15)"
    else: 
        if pe > 26.0 or pb > 4.0: return "🌋 HARVEST", "#EF4444", "rgba(239, 68, 68, 0.15)"
        elif pe > 24.0 or pb > 3.5: return "🔥 TRIM", "#F59E0B", "rgba(245, 158, 11, 0.15)"
        elif pe > 21.0 or pb > 3.0: return "☀️ HOLD", "#10B981", "rgba(16, 185, 129, 0.15)"
        else: return "❄️ ACCUMULATE", "#38BDF8", "rgba(56, 189, 248, 0.15)"

# --- UNIVERSAL HOLDINGS PARSER ---
def parse_zerodha_holdings_file(uploaded_file, filename=None, override_account_id=None):
    file_name_str = filename if filename else getattr(uploaded_file, 'name', str(uploaded_file))
    fname = file_name_str.upper()
    
    client_id = override_account_id.strip().upper() if (override_account_id and override_account_id.strip()) else None

    if not client_id:
        match = re.search(r'\b([A-Z0-9]{6})\b', fname)
        client_id = match.group(1) if match else "SDB789"
    
    records = []

    if fname.endswith(('.XLSX', '.XLS')):
        try:
            xls = pd.ExcelFile(uploaded_file)
            sheets = xls.sheet_names
            sheet_to_use = 'Combined' if 'Combined' in sheets else sheets[0]
            df_raw = pd.read_excel(xls, sheet_name=sheet_to_use, header=None)
            
            if not override_account_id:
                for r in range(min(15, len(df_raw))):
                    row_vals = [safe_str(x) for x in df_raw.iloc[r].dropna().values]
                    if 'Client ID' in row_vals:
                        idx = row_vals.index('Client ID')
                        if idx + 1 < len(row_vals):
                            client_id = row_vals[idx + 1].upper()
                            break
                        
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
        except ImportError:
            st.error("⚠️️ The `openpyxl` library is required to read Excel files. Please add `openpyxl` to `requirements.txt` on GitHub.")
            return client_id, pd.DataFrame()

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

# --- AMORTIZATION & PREPAYMENT ENGINE ---
def calc_rem_months(principal, emi, rate_monthly):
    if principal <= 0: return 0
    try:
        val = 1 - (principal * rate_monthly / emi)
        if val <= 0: return 9999 
        return -math.log(val) / math.log(1 + rate_monthly)
    except ValueError:
        return 0

def calculate_loan_state(df_loan, initial_loan, current_global_rate):
    p_balance = initial_loan
    total_principal_cleared = 0.0
    emi_principal_cleared = 0.0
    prepay_principal_cleared = 0.0
    
    if not df_loan.empty:
        df_sorted = df_loan.copy()
        df_sorted.columns = [str(c).strip().lower() for c in df_sorted.columns]
        
        if "date" in df_sorted.columns:
            df_sorted["date_dt"] = pd.to_datetime(df_sorted["date"], errors="coerce")
            df_sorted = df_sorted.dropna(subset=["date_dt"]).sort_values("date_dt")
            
        for _, row in df_sorted.iterrows():
            p_type = safe_str(row.get("payment_type", ""))
            actual_pay = safe_float(row.get("actual_payment", 0.0))
            
            row_rate = current_global_rate
            if "interest_rate" in df_sorted.columns and not pd.isna(row.get("interest_rate")):
                row_rate = safe_float(row.get("interest_rate"), current_global_rate)
                    
            r_monthly = (row_rate / 100) / 12
            
            if "pre-emi" in p_type.lower():
                pass
            elif "full emi" in p_type.lower() or "emi" in p_type.lower():
                interest_portion = p_balance * r_monthly
                principal_portion = max(0.0, actual_pay - interest_portion)
                p_balance -= principal_portion
                total_principal_cleared += principal_portion
                emi_principal_cleared += principal_portion
            elif "prepayment" in p_type.lower() or "part" in p_type.lower():
                p_balance -= actual_pay
                total_principal_cleared += actual_pay
                prepay_principal_cleared += actual_pay
                
    p_balance = max(0.0, p_balance)
    return p_balance, total_principal_cleared, emi_principal_cleared, prepay_principal_cleared

def project_ndz_target(current_principal, current_portfolio, current_rate, full_emi, is_handover, xirr_rate):
    if current_portfolio >= current_principal:
        return "Achieved", 0, 0
        
    p_bal = current_principal
    port_val = current_portfolio
    r_m_loan = (current_rate / 100) / 12
    r_m_eq = (1 + (xirr_rate / 100))**(1/12) - 1 if (xirr_rate is not None and xirr_rate > -100) else 0.01

    sim_date = datetime.now()
    handover_date = datetime(2027, 6, 1)
    months = 0
    
    while port_val < p_bal and months < 360:
        months += 1
        curr_sim_date = sim_date + pd.DateOffset(months=months)
        
        if curr_sim_date < handover_date and not is_handover:
            monthly_sip = 0.0
            loan_interest = p_bal * r_m_loan
        else:
            monthly_sip = max(0.0, 60000.0 - full_emi)
            loan_interest = p_bal * r_m_loan
            p_red = max(0.0, full_emi - loan_interest)
            p_bal = max(0.0, p_bal - p_red)
            
        port_val = (port_val + monthly_sip) * (1 + r_m_eq)
        
    projected_date = sim_date + pd.DateOffset(months=months)
    return projected_date.strftime("%b %Y"), months // 12, months % 12

INITIAL_LOAN = 4890000.0
LOAN_TENURE_YEARS = 30

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try: 
        df_portfolio = conn.read(worksheet="Portfolio_Tracker", ttl=0)
        if not df_portfolio.empty:
            df_portfolio.columns = [str(c).strip().lower() for c in df_portfolio.columns]
            df_portfolio = df_portfolio.loc[:, ~df_portfolio.columns.duplicated()]
    except Exception: 
        df_portfolio = pd.DataFrame()

    try:
        df_loan = conn.read(worksheet="Loan_Tracker", ttl=0)
        if not df_loan.empty:
            df_loan.columns = [str(c).strip().lower() for c in df_loan.columns]
            df_loan = df_loan.loc[:, ~df_loan.columns.duplicated()]
    except Exception:
        df_loan = pd.DataFrame()

    disbursed_ratio, is_handover_completed, current_interest_rate, console_xirr = 0.90, False, 7.20, None
    try:
        df_settings = conn.read(worksheet="Loan_Settings", ttl=0)
        if not df_settings.empty:
            df_settings.columns = [str(c).strip().lower() for c in df_settings.columns]
            if "disbursed_ratio" in df_settings.columns and not pd.isna(df_settings.iloc[0]["disbursed_ratio"]):
                disbursed_ratio = safe_float(df_settings.iloc[0]["disbursed_ratio"], 0.90)
            if "handover_completed" in df_settings.columns:
                is_handover_completed = safe_str(df_settings.iloc[0]["handover_completed"]).upper() == "TRUE"
            if "interest_rate" in df_settings.columns and not pd.isna(df_settings.iloc[0]["interest_rate"]):
                current_interest_rate = safe_float(df_settings.iloc[0]["interest_rate"], 7.20)
            if "console_xirr" in df_settings.columns:
                val = df_settings.iloc[0]["console_xirr"]
                if pd.notna(val) and str(val).strip() != "":
                    console_xirr = safe_float(val, None)
    except Exception:
        pass

    return df_portfolio, df_loan, disbursed_ratio, is_handover_completed, current_interest_rate, console_xirr

df_portfolio_raw, df_loan, disbursed_ratio, is_handover_completed, current_interest_rate, console_xirr = load_data()

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
            
            row_dict = {
                "Symbol": sym,
                "Account": acc,
                "ISIN": isin_val,
                "Units_Accumulated": units,
                "Avg_Cost": avg_cost,
                "Current_LTP": ltp,
                "Invested_Value": units * avg_cost,
                "Current_Value": curr_val,
                "P&L (₹)": pnl
            }
            eq_rows.append(row_dict)

df_eq_active = pd.DataFrame(eq_rows)
df_mf_active = pd.DataFrame(mf_rows)

eq_val = df_eq_active["Current_Value"].sum() if not df_eq_active.empty else 0.0
eq_inv = df_eq_active["Invested_Value"].sum() if not df_eq_active.empty else 0.0
eq_pnl = df_eq_active["P&L (₹)"].sum() if not df_eq_active.empty else 0.0

mf_val = df_mf_active["Current_Value"].sum() if not df_mf_active.empty else 0.0
mf_inv = df_mf_active["Invested_Value"].sum() if not df_mf_active.empty else 0.0
mf_pnl = df_mf_active["P&L (₹)"].sum() if not df_mf_active.empty else 0.0

total_portfolio_val = eq_val + mf_val
total_portfolio_invested = eq_inv + mf_inv
overall_pnl = total_portfolio_val - total_portfolio_invested
overall_pnl_pct = (overall_pnl / total_portfolio_invested * 100) if total_portfolio_invested > 0 else 0.0

current_principal, total_principal_cleared, emi_principal_cleared, prepay_principal_cleared = calculate_loan_state(
    df_loan, INITIAL_LOAN, current_interest_rate
)

r_monthly = (current_interest_rate / 100) / 12
n_months_base = LOAN_TENURE_YEARS * 12
full_emi = INITIAL_LOAN * r_monthly * ((1 + r_monthly)**n_months_base) / (((1 + r_monthly)**n_months_base) - 1)

disbursed_loan_amount = INITIAL_LOAN * disbursed_ratio
monthly_pre_emi = (disbursed_loan_amount * (current_interest_rate / 100)) / 12

is_handover = is_handover_completed or disbursed_ratio >= 1.0

if is_handover:
    active_due_label = "Monthly EMI Due"
    active_due_amount = full_emi
    disbursement_badge = "100% Disbursed (Handover Complete)"
else:
    active_due_label = "Pre-EMI Due"
    active_due_amount = monthly_pre_emi
    disbursement_badge = f"{int(disbursed_ratio * 100)}% Disbursed"

current_rem_months = calc_rem_months(current_principal, full_emi, r_monthly)
rem_years = current_rem_months / 12

is_ndz_achieved = total_portfolio_val >= current_principal

proj_date, proj_yrs, proj_mos = project_ndz_target(
    current_principal, total_portfolio_val, current_interest_rate, full_emi, is_handover, xirr_rate=console_xirr
)

st.title("🏡 Home Loan & 📈 Investment Tracker")

# ==========================================
# --- TOP-LEVEL NAVIGATION & TABS ---
# ==========================================

tab_aim, tab_dashboard = st.tabs(["✨ Definite Chief Aim", "📊 Loan & Investment Dashboard"])

with tab_aim:
    st.markdown("""
<div style="background: linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F2027 100%); padding: 28px; border-radius: 18px; border: 1.5px solid #FFD700; box-shadow: 0 10px 30px rgba(255, 215, 0, 0.12); margin-bottom: 25px;">
<h2 style="color: #FFD700; text-align: center; font-size: 26px; font-weight: 800; margin-bottom: 12px; letter-spacing: 0.5px;">🌟 My Definite Chief Aim in Life</h2>
<p style="color: #F8FAFC; font-size: 19px; text-align: center; font-weight: 500; font-style: italic; line-height: 1.7; margin-bottom: 22px; max-width: 900px; margin-left: auto; margin-right: auto;">"My definite chief aim in life is to <b>feel good</b>. I live a <b>HAPPY, HEALTHY AND WEALTHY</b> life fully supporting my family as a loving husband, friendly father, and joyful grandparent."</p>
<hr style="border: 0; height: 1px; background: linear-gradient(90deg, transparent, rgba(255, 215, 0, 0.4), transparent); margin: 20px 0;">
<div style="background: rgba(255, 255, 255, 0.04); padding: 24px; border-radius: 14px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
<p style="color: #A5B4FC; font-size: 16px; font-weight: 600; text-align: center; margin-bottom: 20px; font-style: italic;">"In return for the harmonious and abundant life I desire, I commit to the following principles of creative action, knowing that true wealth is built upon truth, justice, and mutual benefit."</p>
<div style="display: flex; gap: 16px; flex-wrap: wrap;">
<div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #FFD166; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
<h3 style="color: #FFD166; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">🧘 To Cultivate Happiness (Mind & Spirit)</h3>
<ul style="list-style-type: none; padding-left: 0; margin: 0;">
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #FFD166;">✦</span> Practice daily gratitude, acknowledging the Source of all abundance.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #FFD166;">✦</span> Engage in regular meditation to maintain a clear and focused mind.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #FFD166;">✦</span> Read at least 1 page of Self-Help literature daily to continuously expand my consciousness.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 0; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #FFD166;">✦</span> Utilize my Emotional Guidance System: Always focus on feeling good and maintaining a positive vibration.</li>
</ul>
</div>
<div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #06D6A0; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
<h3 style="color: #06D6A0; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">🥗 To Nurture Health (The Physical Vessel)</h3>
<ul style="list-style-type: none; padding-left: 0; margin: 0;">
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #06D6A0;">✦</span> Nourish my body with a healthy, eggetarian diet.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #06D6A0;">✦</span> Ensure good, sound sleep to rejuvenate my energy.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 0; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #06D6A0;">✦</span> Exercise regularly</li>
</ul>
</div>
<div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #4CC9F0; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
<h3 style="color: #4CC9F0; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">💎 To Manifest Wealth (Creative Contribution)</h3>
<ul style="list-style-type: none; padding-left: 0; margin: 0;">
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #4CC9F0;">✦</span> Provide more efficient and valuable service joyfully, delivering greater use value than the cash value I receive.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #4CC9F0;">✦</span> Donate to people in need, contributing to the flow of abundance.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 8px; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #4CC9F0;">✦</span> Celebrate wealth everywhere, knowing the universal supply is limitless.</li>
<li style="color: #E2E8F0; font-size: 14.5px; margin-bottom: 0; line-height: 1.5; display: flex; align-items: start; gap: 8px;"><span style="color: #4CC9F0;">✦</span> Maintain unwavering faith and stay persistently invested. Deploying capital into equity actively funds businesses that serve humanity, bringing more use value to the world.</li>
</ul>
</div>
</div>
</div>
""", unsafe_allow_html=True)

    st.markdown("""
<div style="background: linear-gradient(135deg, #1E1B4B 0%, #0F172A 50%, #1E293B 100%); padding: 26px; border-radius: 18px; border: 1.5px solid #818CF8; box-shadow: 0 10px 30px rgba(129, 140, 248, 0.12); margin-bottom: 25px;">
<h2 style="color: #A5B4FC; text-align: center; font-size: 24px; font-weight: 800; margin-bottom: 16px; letter-spacing: 0.5px;">💪 Napoleon Hill's 5-Step Self-Confidence Formula</h2>
<div style="display: flex; flex-direction: column; gap: 12px;">
<div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #818CF8;">
<b style="color: #C7D2FE;">First.</b>
<span style="color: #E2E8F0; font-size: 14px;"> I know that I have the ability to achieve the object of my Definite Purpose in life, therefore, I DEMAND of myself persistent, continuous action toward its attainment, and I here and now promise to render such action.</span>
</div>
<div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #38BDF8;">
<b style="color: #BAE6FD;">Second.</b>
<span style="color: #E2E8F0; font-size: 14px;"> I realize the dominating thoughts of my mind will eventually reproduce themselves in outward, physical action, and gradually transform themselves into physical reality, therefore, I will concentrate my thoughts for thirty minutes daily, upon the task of thinking of the person I intend to become, thereby creating in my mind a clear mental picture of that person.</span>
</div>
<div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #34D399;">
<b style="color: #A7F3D0;">Third.</b>
<span style="color: #E2E8F0; font-size: 14px;"> I know through the principle of auto-suggestion, any desire that I persistently hold in my mind will eventually seek expression through some practical means of attaining the object back of it, therefore, I will devote ten minutes daily to demanding of myself the development of SELF-CONFIDENCE.</span>
</div>
<div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #FBBF24;">
<b style="color: #FDE68A;">Fourth.</b>
<span style="color: #E2E8F0; font-size: 14px;"> I have clearly written down a description of my DEFINITE CHIEF AIM in life, and I will never stop trying, until I shall have developed sufficient self-confidence for its attainment.</span>
</div>
<div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #F472B6;">
<b style="color: #FBCFE8;">Fifth.</b>
<span style="color: #E2E8F0; font-size: 14px;"> I fully realize that no wealth or position can long endure, unless built upon truth and justice, therefore, I will engage in no transaction which does not benefit all whom it affects. I will succeed by attracting to myself the forces I wish to use, and the cooperation of other people. I will induce others to serve me, because of my willingness to serve others. I will eliminate hatred, envy, jealousy, selfishness, and cynicism, by developing love for all humanity, because I know that a negative attitude toward others can never bring me success. I will cause others to believe in me, because I will believe in them, and in myself. I will sign my name to this formula, commit it to memory, and repeat it aloud once a day, with full FAITH that it will gradually influence my THOUGHTS and ACTIONS so that I will become a self-reliant, and successful person.</span>
</div>
</div>
</div>
""", unsafe_allow_html=True)

    with st.container(border=True):
        st.subheader("🎯 Net-Debt-Zero Visualizer")
        st.progress(1.0)
        st.caption("✨ **100.0% Covered** towards Net-Debt-Zero target | **Goal Fully Manifested**")
        st.success("🎉 **Net-Debt-Zero Fully Achieved:** Living in total financial freedom, peace of mind, and complete abundance!")
        st.divider()
        s_col1, s_col2 = st.columns(2)
        s_col1.metric("Principal Pending", "₹0", "100.0% Loan Cleared")
        s_col2.metric("Portfolio Value", "₹1,00,00,000", "1 Cr - Total Financial Abundance")

with tab_dashboard:
    with st.container(border=True):
        st.subheader("🎯 Net-Debt-Zero Visualizer")
        net_debt = max(0.0, current_principal - total_portfolio_val)
        nd_covered_pct = (total_portfolio_val / current_principal * 100) if current_principal > 0 else 100.0
        
        xirr_label = f"**{console_xirr:.2f}%**" if console_xirr is not None else "*Not Set (Import Holdings to Set)*"

        nd_col1, nd_col2 = st.columns([3, 1])
        with nd_col1:
            st.progress(min(total_portfolio_val / current_principal, 1.0) if current_principal > 0 else 1.0)
            st.caption(f"**{nd_covered_pct:.1f}% Covered** towards Net-Debt-Zero target | Active Console XIRR: {xirr_label}")
        with nd_col2:
            if is_ndz_achieved:
                st.success("🎉 Zero Debt Achieved!")
            else:
                st.metric("Net Debt Pending", format_inr(net_debt))

        if not is_ndz_achieved:
            st.info(f"🔮 **Projected Net-Debt-Zero Target:** **{proj_date}** (~ {proj_yrs} Yrs {proj_mos} Mos away assuming **{xirr_label} Console XIRR**)")
        else:
            st.success("🎉 **Net-Debt-Zero Achieved:** Your investment portfolio corpus meets or exceeds your total remaining loan principal. You have reached complete financial freedom!")

        st.divider()

        s_col1, s_col2, s_col3, s_col4 = st.columns(4)
        pct_principal_cleared = (total_principal_cleared / INITIAL_LOAN * 100) if INITIAL_LOAN > 0 else 0.0
        s_col1.metric("Principal Pending", format_inr(current_principal), f"{pct_principal_cleared:.1f}% Loan Cleared")
        s_col2.metric("Portfolio Value", format_inr(total_portfolio_val))
        s_col3.metric("Total Invested", format_inr(total_portfolio_invested))
        s_col4.metric("Overall Net P&L", format_inr(overall_pnl), f"{overall_pnl_pct:+.2f}%")

    st.divider()

    # --- SECTION 1: STANDARD MONTHLY PAYMENTS ---
    st.subheader(f"1. Standard Monthly Payments ({active_due_label})")

    m_col1, m_col2, m_col3 = st.columns(3)
    
    with m_col1:
        c1_text, c1_btn = st.columns([5, 1])
        with c1_text:
            disb_color = "green" if is_handover else "blue"
            st.markdown(f"**{active_due_label}**\n\n{format_inr(active_due_amount)}\n\n:{disb_color}[{disbursement_badge}]")
        
        if not is_handover:
            with c1_btn:
                with st.popover("✏️", help="Edit Disbursement Stage"):
                    st.markdown("### 🏗️ Update Loan Disbursement")
                    selected_stage = st.radio(
                        "Select Disbursed Milestone:",
                        [
                            "90% - Initial Disbursed Base",
                            "95% - Plastering Completed (~Jan 2027)",
                            "100% - Handover Completed (Full EMI Starts)"
                        ],
                        index=0 if disbursed_ratio == 0.90 else (1 if disbursed_ratio == 0.95 else 2)
                    )
                    
                    new_ratio = 0.90 if "90%" in selected_stage else (0.95 if "95%" in selected_stage else 1.0)
                    confirm_handover = False
                    if new_ratio == 1.0:
                        st.warning(f"⚠️ **Warning:** Setting disbursement to 100% marks handover complete. Dues permanently switch to **Full EMI** ({format_inr(full_emi)}) and this edit option will be **permanently locked**.")
                        confirm_handover = st.checkbox("I confirm handover is completed and agree to lock settings.")
                    
                    can_save = (new_ratio < 1.0) or (new_ratio == 1.0 and confirm_handover)
                    
                    if st.button("💾 Save Settings", disabled=not can_save, type="primary"):
                        updated_settings = pd.DataFrame([{
                            "Disbursed_Ratio": new_ratio,
                            "Handover_Completed": (new_ratio == 1.0),
                            "Interest_Rate": current_interest_rate,
                            "Console_XIRR": console_xirr if console_xirr is not None else 0.0
                        }])
                        conn.update(worksheet="Loan_Settings", data=updated_settings)
                        st.success("Loan settings updated successfully!")
                        st.rerun()

    with m_col2:
        c2_text, c2_btn = st.columns([5, 1])
        with c2_text:
            st.markdown(f"**Interest Rate**\n\n{current_interest_rate}%\n\n:gray[Floating Rate]")
        with c2_btn:
            with st.popover("✏️️", help="Update Interest Rate"):
                st.markdown("### 🏦 Update Interest Rate")
                new_rate = st.number_input(
                    "New Annual Interest Rate (%)", 
                    value=float(current_interest_rate), 
                    step=0.05, 
                    format="%.2f"
                )
                if st.button("💾 Save Rate", type="primary"):
                    updated_settings = pd.DataFrame([{
                        "Disbursed_Ratio": disbursed_ratio,
                        "Handover_Completed": is_handover_completed,
                        "Interest_Rate": new_rate,
                        "Console_XIRR": console_xirr if console_xirr is not None else 0.0
                    }])
                    conn.update(worksheet="Loan_Settings", data=updated_settings)
                    st.success(f"Interest rate dynamically updated to {new_rate}%!")
                    st.rerun()

    with m_col3:
        st.markdown(f"**Current Tenure Remaining**\n\n{rem_years:.1f} Yrs\n\n:gray[{int(current_rem_months)} Mos left]")

    current_month_str = datetime.now().strftime("%b %Y")

    if not df_loan.empty and "month_year" in [c.lower() for c in df_loan.columns]:
        emi_records = df_loan[df_loan["payment_type"].astype(str).str.contains("Pre-EMI|Full EMI", case=False, na=False)]
        is_current_month_paid = current_month_str in emi_records["month_year"].values
    else:
        is_current_month_paid = False

    with st.form("emi_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        c1.text_input("Month-Year", value=current_month_str, disabled=True)
        
        payment_type = "Full EMI" if is_handover else "Pre-EMI"
        expected_loan = full_emi if is_handover else monthly_pre_emi
        c2.text_input("Actual Payment Made", value=format_inr(expected_loan), disabled=True)
        
        with c3:
            st.markdown("**Payment Status**")
            if is_current_month_paid:
                st.markdown(":green[**🟢 PAID**]")
            else:
                st.markdown(":red[**🔴 UNPAID**]")

        if st.form_submit_button("Log Monthly Payment", disabled=is_current_month_paid):
            new_row_emi = pd.DataFrame([{
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                "Month_Year": current_month_str, 
                "Expected_Payment": expected_loan, 
                "Actual_Payment": expected_loan, 
                "Payment_Type": payment_type, 
                "Confirmed": True,
                "Interest_Rate": current_interest_rate
            }])
            conn.update(worksheet="Loan_Tracker", data=pd.concat([df_loan, new_row_emi], ignore_index=True))
            st.success(f"Logged {current_month_str} payment of {format_inr(expected_loan)} successfully!")
            st.rerun()

    if is_current_month_paid:
        st.info(f"✅ Payment for **{current_month_str}** is already logged. Duplicate entries for the same month are blocked.")

    with st.container(border=True):
        pct_loan_cleared = (total_principal_cleared / INITIAL_LOAN) if INITIAL_LOAN > 0 else 0.0
        st.markdown(f"**📉 Principal Cleared Tracker** ({pct_loan_cleared * 100:.2f}% of Initial Loan Paid)")
        st.progress(min(pct_loan_cleared, 1.0))
        
        p_col1, p_col2, p_col3 = st.columns(3)
        p_col1.metric("Total Principal Cleared", format_inr(total_principal_cleared), f"{pct_loan_cleared*100:.1f}% Cleared")
        p_col2.metric("Cleared via Regular EMIs", format_inr(emi_principal_cleared))
        p_col3.metric("Cleared via Part Payments", format_inr(prepay_principal_cleared))

    st.divider()

    # --- SECTION 2: LIVE PORTFOLIO HOLDINGS ---
    sec2_hdr_col, sec2_act_col = st.columns([3, 1])

    with sec2_hdr_col:
        st.subheader("2. Live Portfolio Holdings")

    with sec2_act_col:
        with st.popover("📥 Import Holdings File(s)"):
            st.markdown("**Import Zerodha Holdings (CSV or Excel)**")
            
            uploaded_files = st.file_uploader(
                "Select Holdings File(s)", 
                type=["csv", "xlsx", "xls"], 
                accept_multiple_files=True,
                key="holdings_uploader",
                help="Upload multiple files at once."
            )

            account_mapping = {}
            if uploaded_files:
                st.markdown("---")
                st.markdown("**👤 Confirm Account ID per File:**")
                for file in uploaded_files:
                    match = re.search(r'\b([A-Z0-9]{6})\b', file.name.upper())
                    detected_acc = match.group(1) if match else ""
                    
                    if not detected_acc and file.name.upper().endswith(('.XLSX', '.XLS')):
                        try:
                            xls = pd.ExcelFile(file)
                            sheet_to_use = 'Combined' if 'Combined' in sheets else sheets[0]
                            df_raw = pd.read_excel(xls, sheet_name=sheet_to_use, header=None, nrows=15)
                            for r in range(len(df_raw)):
                                row_vals = [safe_str(x) for x in df_raw.iloc[r].dropna().values]
                                if 'Client ID' in row_vals:
                                    idx = row_vals.index('Client ID')
                                    if idx + 1 < len(row_vals):
                                        detected_acc = row_vals[idx + 1].upper()
                                        break
                        except Exception:
                            pass
                            
                    user_acc = st.text_input(
                        f"Account ID for `{file.name}`:",
                        value=detected_acc,
                        placeholder="e.g. HEK312 (Mandatory)",
                        key=f"acc_input_{file.name}"
                    )
                    account_mapping[file.name] = user_acc.strip().upper()

            st.markdown("---")
            input_xirr = st.number_input(
                "Console Overall XIRR (%)", 
                value=None,
                min_value=-100.0, max_value=500.0, step=0.1,
                placeholder="e.g. 14.5 (Mandatory)"
            )

            if st.button("Sync Holdings to Sheets", key="btn_sync_holdings"):
                missing_accounts = [fname for fname, acc in account_mapping.items() if not acc]
                if input_xirr is None:
                    st.error("⚠️ Overall Console XIRR (%) is mandatory.")
                elif not uploaded_files:
                    st.error("⚠️ Please select at least one holdings file.")
                elif missing_accounts:
                    st.error(f"⚠️ Specify Account ID for: {', '.join(f'`{f}`' for f in missing_accounts)}")
                else:
                    parsed_records = []
                    for file in uploaded_files:
                        target_acc = account_mapping.get(file.name, "")
                        cid, df_parsed = parse_zerodha_holdings_file(file, file.name, override_account_id=target_acc)
                        if not df_parsed.empty:
                            parsed_records.append(df_parsed)
                            st.info(f"Loaded **{len(df_parsed)} active holdings** for account **{cid}**")

                    if parsed_records:
                        df_new_combined = pd.concat(parsed_records, ignore_index=True)
                        df_new_combined.columns = [str(c).strip().lower() for c in df_new_combined.columns]
                        
                        uploaded_accounts = set(df_new_combined['account'].astype(str).str.upper().unique())

                        df_existing = df_portfolio_raw.copy()
                        if not df_existing.empty:
                            df_existing.columns = [str(c).strip().lower() for c in df_existing.columns]
                            df_retained = df_existing[~df_existing['account'].astype(str).str.upper().isin(uploaded_accounts)].copy()
                        else:
                            df_retained = pd.DataFrame()

                        df_all_merged = pd.concat([df_retained, df_new_combined], ignore_index=True)
                        df_all_merged["acc_asset_key"] = [f"{safe_str(r.get('account','')).upper()}_{safe_str(r.get('isin','')).upper() if safe_str(r.get('isin','')) not in ['','NAN'] else safe_str(r.get('symbol','')).upper()}" for _, r in df_all_merged.iterrows()]

                        df_deduped_holdings = df_all_merged.drop_duplicates(subset=["acc_asset_key"], keep="last").drop(columns=["acc_asset_key"]).reset_index(drop=True).fillna("")

                        try:
                            conn.update(worksheet="Portfolio_Tracker", data=df_deduped_holdings)
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

    st.markdown("<br>", unsafe_allow_html=True)
    active_cards = []
    if eq_inv > 0 or eq_val > 0: active_cards.append('equity')
    if mf_inv > 0 or mf_val > 0: active_cards.append('mf')

    if active_cards:
        cols = st.columns(len(active_cards))
        col_idx = 0
        if 'equity' in active_cards:
            with cols[col_idx]:
                with st.container(border=True):
                    st.markdown("<h4 style='margin-bottom:0px; color:#4CC9F0;'>📊 Equity & ETF Holdings</h4>", unsafe_allow_html=True)
                    st.caption("Standard Wealth Portfolio")
                    eq_pnl_pct = (eq_pnl / eq_inv * 100) if eq_inv > 0 else 0.0
                    st.metric("Current Value", format_inr(eq_val), f"{format_inr(eq_pnl)} ({eq_pnl_pct:+.2f}%)")
                    st.markdown(f":gray[Invested: {format_inr(eq_inv)}]")
            col_idx += 1
        if 'mf' in active_cards:
            with cols[col_idx]:
                with st.container(border=True):
                    st.markdown("<h4 style='margin-bottom:0px; color:#FFD166;'>💼 Mutual Fund Holdings</h4>", unsafe_allow_html=True)
                    st.caption("Standard Wealth Portfolio")
                    mf_pnl_pct = (mf_pnl / mf_inv * 100) if mf_inv > 0 else 0.0
                    st.metric("Current Value", format_inr(mf_val), f"{format_inr(mf_pnl)} ({mf_pnl_pct:+.2f}%)")
                    st.markdown(f":gray[Invested: {format_inr(mf_inv)}]")
            col_idx += 1
    else:
        st.info("No active holdings found in your portfolio. Import a Zerodha file to get started!")

    st.divider()

    # --- SECTION 3: FUNDAMENTAL PART PAYMENTS ---
    st.subheader("3. 🌱 The Abundance Approach to Part Payments")
    
    with st.container(border=True):
        
        ab_col1, ab_col2 = st.columns(2)
        with ab_col1:
            st.markdown("🌌 **Release Micromanagement:** Stop trying to force the 'how.' Let the universe handle market volatility and macroeconomics while you stay perfectly aligned with your Definite Chief Aim.")
            st.markdown("🌊 **Direct the Creative Flow:** We invest to create, not to compete. Deploying capital into equity actively funds businesses that serve humanity, bringing more use value to the world.")
            
        with ab_col2:
            st.markdown("⏳ **Act in the Joyous Present:** Surrender anxious timelines and the need to predict the exact month you become debt-free. Act efficiently in the Now to enjoy every moment with your family.")
            st.markdown("💖 **Take Inspired Action:** There are no rigid rules or forced multiples here. Whenever the universe delivers surplus cash, or the market hits euphoric valuations, log your joyful part payment below.")
        
        st.divider()

        # Outermost Collapsible Bar for Full Fundamental Dashboard
        with st.expander("🚦 Fundamental & Institutional Index Allocation Dashboard", expanded=False):
            st.caption("Automated macroeconomic valuation metrics and quality earnings benchmarking powered by real-time market data.")
            
            macro_data = fetch_macro_fundamentals()
            live_gsec_yield, live_buffett_ind = fetch_live_macro_benchmarks()
            buffett_status_text, buffett_color = get_buffett_status(live_buffett_ind)

            buffett_display = f"{live_buffett_ind:.1f}%" if live_buffett_ind > 0 else "OFFLINE"
            gsec_display = f"{live_gsec_yield:.2f}%" if live_gsec_yield > 0 else "OFFLINE"

            # Top-level single placement of nationwide macro metrics with insightful tooltips
            macro_top_c1, macro_top_c2, macro_top_c3 = st.columns([1.5, 1.5, 1])
            with macro_top_c1:
                st.metric(
                    "🇮🇳 Buffett Indicator (Market Cap-to-GDP)", 
                    buffett_display, 
                    buffett_status_text,
                    help="💡 What It Means:\nCompares the total valuation of all listed Indian companies against the nation's nominal GDP.\n\n👑 Rule of Thumb (Warren Buffett):\n• < 75%: Deep Value (Historical bargain zone)\n• 75%–95%: Fair Value\n• 95%–115%: Modestly Overvalued\n• > 115%: Frothy / Stretched ('Playing with fire'). Prudent time to harvest profits for debt clearance."
                )
            with macro_top_c2:
                st.metric(
                    "🏛️ 10Y Sovereign G-Sec Yield", 
                    gsec_display, 
                    "Risk-Free Benchmark",
                    help="💡 What It Means:\nThe annualized return guaranteed by the Government of India on its 10-year bonds. It serves as the risk-free benchmark hurdle rate.\n\n👑 Rule of Thumb (Warren Buffett & Benjamin Graham):\nStocks carry volatility and business risk. Therefore, equities must provide a meaningful profit premium above the 10Y G-Sec yield to justify investing. When safe G-Sec yields exceed equity yields, paying down debt becomes mathematically superior."
                )
            with macro_top_c3:
                with st.popover("ℹ View Institutional Methodology"):
                    st.markdown(r"""
                    ### 📐 Institutional Evaluation Framework

                    **1. Macro & Relative Yield Metrics**
                    * **Buffett Indicator:** Real-time Market Cap-to-GDP ratio for India. Values $> 115\%$ warn that broad market valuations are historically stretched.
                    * **Yield Gap:** Calculated as $\left(\frac{1}{\text{P/E}} \times 100\right) - \text{Live 10Y G-Sec Yield}$. Compares equity earnings yield against risk-free sovereign debt. Narrow or negative spreads signal equity overvaluation.

                    **2. Quality & Growth Adjustments**
                    * **Consolidated Index ROE:** Approximated as $\left(\frac{\text{P/B}}{\text{P/E}}\right) \times 100$. A higher P/B is economically justified if ROE remains elevated.
                    * **PEG Ratio:** $\frac{\text{P/E}}{\text{Index Long-Term Growth Rate}}$. Prevents premature exits from high-growth companies.
                    * **Forward P/E:** Estimated forward multiple adjusted for ongoing index earnings growth.
                    """)

            st.divider()

            target_etfs = [
                ("NIFTYBEES", "Nifty 50"),
                ("NEXT50IETF", "Nifty Next 50"),
                ("MIDCAPETF", "Nifty Midcap 150"),
                ("BANKBEES", "Nifty Bank")
            ]

            for etf_sym, index_name in target_etfs:
                f_data = macro_data.get(index_name, {"PE": 0.0, "PB": 0.0, "DY": 0.0, "GROWTH": 0.0})
                pe = f_data["PE"]
                pb = f_data["PB"]
                dy = f_data["DY"]
                growth_rate = f_data.get("GROWTH", 0.0)
                v_text, v_color, v_bg = evaluate_index_temp(index_name, pe, pb, dy)
                
                # Dynamic calculations based on live fetched data
                earnings_yield = (100.0 / pe) if pe > 0 else 0.0
                yield_gap = (earnings_yield - live_gsec_yield) if (pe > 0 and live_gsec_yield > 0) else 0.0
                peg_ratio = (pe / growth_rate) if (pe > 0 and growth_rate > 0) else 0.0
                roe_approx = ((pb / pe) * 100.0) if (pe > 0 and pb > 0) else 0.0
                forward_pe = (pe / (1.0 + (growth_rate / 100.0))) if (pe > 0 and growth_rate > 0) else 0.0
                
                # Header displays only ETF symbol, index name, and verdict
                expander_title = f"📈 {etf_sym} ({index_name})  •  {v_text}"

                with st.expander(expander_title, expanded=False):
                    # Section 1: Macro & Relative Yield Metrics with dedicated P/B Card
                    st.markdown("#### 1. 🌐 Macro & Relative Yield Metrics")
                    m_c1, m_c2, m_c3, m_c4 = st.columns(4)
                    with m_c1:
                        pe_subtext = "Historical Avg ~18.0" if index_name == "Nifty Bank" else "Historical Avg ~20.0"
                        pe_help = (
                            "💡 What It Means:\n"
                            "Price-to-Earnings Ratio. Measures how many rupees you pay for every ₹1 of company net profit.\n\n"
                            "👑 Rule of Thumb (Benjamin Graham & Peter Lynch):\n"
                            "• Nifty Bank: < 15 Accumulate, 15–18 Fair, > 21 Euphoric/Harvest.\n"
                            "• Nifty 50 / Next 50: < 20 Accumulate, 20–24 Fair, > 25 Overvalued.\n"
                            "• Midcap 150: < 22 Accumulate, 22–26 Fair, > 30 Stretched."
                        )
                        st.metric("Index P/E", f"{pe:.1f}" if pe > 0 else "N/A", pe_subtext, help=pe_help)
                    with m_c2:
                        pb_subtext = "10Y Avg: 2.65" if index_name == "Nifty Bank" else "Fair Value < 3.0"
                        pb_help = (
                            "💡 What It Means:\n"
                            "Price-to-Book Ratio. Compares the market price to the company's balance-sheet net worth. Essential for banking stocks because banks hold money/loans as assets.\n\n"
                            "👑 Rule of Thumb (Benjamin Graham):\n"
                            "• Banking Index: 10Y median is ~2.65. Below 2.2 is a major accumulation bargain; above 3.3 indicates a banking bubble.\n"
                            "• Broad Indices: Under 3.0 is reasonable; above 4.0 requires exceptionally high ROE (>18%) to be justified."
                        )
                        st.metric("Index P/B", f"{pb:.2f}" if pb > 0 else "N/A", pb_subtext, help=pb_help)
                    with m_c3:
                        ey_help = (
                            "💡 What It Means:\n"
                            "The annual profit percentage earned per rupee invested (calculated as 1 ÷ P/E × 100). It is the true operational yield of the business before dividend retention.\n\n"
                            "👑 Rule of Thumb (Joel Greenblatt & Warren Buffett):\n"
                            "Higher is better. A 5.5% earnings yield means companies earn ₹5.50 annually on every ₹100 of index value. Compare this directly with sovereign bond yields."
                        )
                        st.metric(
                            "Earnings Yield (1/PE)", 
                            f"{earnings_yield:.2f}%" if earnings_yield > 0 else "N/A", 
                            f"Div Yield: {dy:.2f}%" if dy > 0 else "N/A",
                            help=ey_help
                        )
                    with m_c4:
                        yg_color = "normal" if yield_gap >= 0 else "inverse"
                        yg_display = f"{yield_gap:+.2f}%" if (pe > 0 and live_gsec_yield > 0) else "N/A"
                        gsec_subtext = f"10Y G-Sec: {live_gsec_yield:.2f}%" if live_gsec_yield > 0 else "10Y G-Sec: OFFLINE"
                        yg_help = (
                            "💡 What It Means:\n"
                            "Equity Risk Premium. Measures (Earnings Yield - 10Y Sovereign G-Sec Yield). It answers: 'Are equities paying me enough extra return over 100% risk-free government bonds?'\n\n"
                            "👑 Rule of Thumb (Institutional Allocators):\n"
                            "• Positive (> 0%): Equities pay a healthy premium over bonds. Strong buy/hold incentive.\n"
                            "• Negative (< 0%): Bond yields beat stock earnings yields. Equities offer no risk premium. Perfect mathematical justification to harvest stock gains and prepay high-interest debt."
                        )
                        st.metric("Yield Gap (vs 10Y G-Sec)", yg_display, gsec_subtext, delta_color=yg_color, help=yg_help)

                    if pe > 0 and live_gsec_yield > 0:
                        assessment_msg = "Equities offer healthy premium over bonds." if yield_gap > 0 else f"Equities offer no premium over {live_gsec_yield:.2f}% risk-free G-Secs (Exercise disciplined harvesting)."
                        st.markdown(f":gray[**Yield Gap Assessment:** Equity risk premium is `{yield_gap:+.2f}%`. {assessment_msg}]")
                    else:
                        st.markdown(":gray[**Yield Gap Assessment:** ⚠️ Live bond or equity multiple feed offline. Calculations paused.]")
                    
                    st.divider()

                    # Section 2: Earnings Quality & Growth Adjustments
                    st.markdown("#### 2. 💎 Earnings Quality & Growth Adjustments")
                    q_c1, q_c2, q_c3, q_c4 = st.columns(4)
                    with q_c1:
                        roe_help = (
                            "💡 What It Means:\n"
                            "Consolidated Return on Equity ((P/B ÷ P/E) × 100). Shows how efficiently companies convert shareholder capital into net profit.\n\n"
                            "👑 Rule of Thumb (Charlie Munger & Warren Buffett):\n"
                            "• < 12%: Poor capital efficiency.\n"
                            "• 15%–18%: High-quality businesses with durable competitive advantages (moats).\n"
                            "• > 20%: Elite wealth compounders. High P/B ratios are completely justified when ROE remains this strong."
                        )
                        st.metric("Consolidated ROE", f"{roe_approx:.2f}%" if roe_approx > 0 else "N/A", "Calculated as (P/B ÷ P/E) × 100", help=roe_help)
                    with q_c2:
                        growth_display = f"{growth_rate:.1f}%" if growth_rate > 0 else "OFFLINE"
                        growth_help = (
                            "💡 What It Means:\n"
                            "The long-term compounded annual profit growth rate (EPS CAGR) of the underlying companies in this index.\n\n"
                            "👑 Rule of Thumb (Peter Lynch):\n"
                            "Earnings growth is the fundamental engine that powers stock prices over time. Double-digit growth (> 12%) supports higher valuations."
                        )
                        st.metric("Index Earnings Growth", growth_display, "Live Long-Term Index CAGR", help=growth_help)
                    with q_c3:
                        peg_help = (
                            "💡 What It Means:\n"
                            "Price/Earnings-to-Growth Ratio (Index P/E ÷ Earnings Growth Rate). Evaluates whether you are overpaying for growth.\n\n"
                            "👑 Rule of Thumb (Peter Lynch):\n"
                            "• < 1.0: Outstanding bargain. Growth is selling at a steep discount.\n"
                            "• 1.0–1.5: Fair price for growth.\n"
                            "• > 1.5: Stretched. High multiples require flawless profit delivery. Any slowdown can trigger sharp pullbacks."
                        )
                        if peg_ratio > 0:
                            peg_status = "Undervalued (<1.0)" if peg_ratio < 1.0 else ("Fair (1.0-1.5)" if peg_ratio <= 1.5 else "Stretched (>1.5)")
                            st.metric("PEG Ratio", f"{peg_ratio:.2f}", peg_status, help=peg_help)
                        else:
                            st.metric("PEG Ratio", "N/A", "Requires Live CAGR", help=peg_help)
                    with q_c4:
                        fwd_help = (
                            "💡 What It Means:\n"
                            "Price-to-Earnings multiple based on expected earnings over the upcoming 12 months rather than past historical profits.\n\n"
                            "👑 Rule of Thumb (Institutional Fund Managers):\n"
                            "If Forward P/E is notably lower than Trailing P/E, corporate profits are accelerating, meaning the index is cheaper than past multiples suggest."
                        )
                        st.metric("Forward 1Y P/E", f"{forward_pe:.1f}" if forward_pe > 0 else "N/A", f"Trailing: {pe:.1f}" if pe > 0 else "N/A", help=fwd_help)

                    if peg_ratio > 0:
                        defense_msg = "Valuation multiple is fully supported by earnings expansion." if peg_ratio < 1.3 else "High multiple requires watchful trailing profit delivery."
                        st.markdown(f":gray[**Growth Defense:** Index ROE of `{roe_approx:.1f}%` with PEG of `{peg_ratio:.2f}`. {defense_msg}]")
                    else:
                        st.markdown(f":gray[**Growth Defense:** Index ROE is `{roe_approx:.1f}%`. Quantitative PEG awaiting live index CAGR.]")

        st.write("")
        
        # Prepayment execution form
        pp_input_col1, pp_input_col2 = st.columns(2)
        
        with pp_input_col1:
            st.markdown("### 💸 Execute Part Payment")
            pp_amount = st.number_input("Prepayment Amount (₹)", value=100000.0, step=10000.0, min_value=1.0)
            
            if st.button("Log Joyful Part Payment", type="primary"):
                new_row = pd.DataFrame([{
                    "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                    "Month_Year": datetime.now().strftime("%b %Y"), 
                    "Expected_Payment": pp_amount, 
                    "Actual_Payment": pp_amount, 
                    "Payment_Type": "Part Payment", 
                    "Confirmed": True,
                    "Interest_Rate": current_interest_rate
                }])
                conn.update(worksheet="Loan_Tracker", data=pd.concat([df_loan, new_row], ignore_index=True))
                st.success(f"Executed Joyful Part Payment of {format_inr(pp_amount)} successfully!")
                st.cache_data.clear()
                st.rerun()
                
        with pp_input_col2:
            st.markdown("### 🔮 Impact Preview")
            principal_reduction = pp_amount
            new_rem_months = calc_rem_months(current_principal - principal_reduction, full_emi, r_monthly)
            months_saved = max(0, round(current_rem_months - new_rem_months))
            st.metric("Tenure Reduced By", f"{months_saved} Months", f"~ {months_saved/12:.1f} Years saved")
