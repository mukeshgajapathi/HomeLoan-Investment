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
            st.error("⚠️ The `openpyxl` library is required to read Excel files. Please add `openpyxl` to `requirements.txt` on GitHub.")
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

def get_current_year_prepayment_status(df_loan, full_emi):
    base_2x = 2 * full_emi
    if not df_loan.empty and "date" in [c.lower() for c in df_loan.columns]:
        df_temp = df_loan.copy()
        df_temp.columns = [c.lower() for c in df_temp.columns]
        df_temp["date_dt"] = pd.to_datetime(df_temp["date"], errors="coerce")
        df_temp = df_temp.dropna(subset=["date_dt"]).sort_values("date_dt")
        
        df_full = df_temp[df_temp["payment_type"].astype(str).str.contains("Full EMI|Prepayment", case=False, na=False)]
        
        if not df_full.empty:
            start_date = df_full.iloc[0]["date_dt"]
        else:
            start_date = pd.to_datetime("2027-06-01")
            
        now = datetime.now()
        if now < start_date:
            return 0, base_2x, 0.0, base_2x, False
            
        elapsed_months = (now.year - start_date.year) * 12 + (now.month - start_date.month)
        current_year_num = max(1, (elapsed_months // 12) + 1)
        target_prepay = base_2x * (1.10 ** (current_year_num - 1))
        
        year_start_date = start_date + pd.DateOffset(months=(current_year_num - 1) * 12)
        prepays_this_year_df = df_temp[
            (df_temp["payment_type"].astype(str).str.contains("Prepayment", case=False, na=False)) & 
            (df_temp["date_dt"] >= year_start_date)
        ]
        
        prepays_this_year = prepays_this_year_df["actual_payment"].apply(safe_float).sum()
        has_4pct_prepay_this_year = prepays_this_year_df["payment_type"].astype(str).str.contains("4% Corpus", case=False, na=False).any()
        
        pending_prepay = max(0.0, target_prepay - prepays_this_year)
        return current_year_num, target_prepay, prepays_this_year, pending_prepay, has_4pct_prepay_this_year

    return 0, base_2x, 0.0, base_2x, False

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

# Loan calculations
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
    # --- HERO CARD 1: DEFINITE CHIEF AIM IN LIFE ---
    st.markdown("""
    <div style="background: linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F2027 100%); padding: 28px; border-radius: 18px; border: 1.5px solid #FFD700; box-shadow: 0 10px 30px rgba(255, 215, 0, 0.12); margin-bottom: 25px;">
        <h2 style="color: #FFD700; text-align: center; font-size: 26px; font-weight: 800; margin-bottom: 12px; letter-spacing: 0.5px;">
            🌟 My Definite Chief Aim
        </h2>
        <p style="color: #F8FAFC; font-size: 19px; text-align: center; font-weight: 500; font-style: italic; line-height: 1.7; margin-bottom: 22px; max-width: 900px; margin-left: auto; margin-right: auto;">
            "My definite chief aim in life is to <b>feel good</b>. I live a <b>HAPPY, HEALTHY AND WEALTHY</b> life fully supporting my family as a loving husband, friendly father, and joyful grandparent."
        </p>
        <hr style="border: 0; height: 1px; background: linear-gradient(90deg, transparent, rgba(255, 215, 0, 0.4), transparent); margin: 20px 0;">
        <div style="display: flex; gap: 16px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #FFD166; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
                <h3 style="color: #FFD166; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    🧘 For Happiness
                </h3>
                <p style="color: #E2E8F0; font-size: 14.5px; margin: 0; line-height: 1.6; font-weight: 400;">
                    Practice gratitude and meditation.
                </p>
            </div>
            <div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #06D6A0; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
                <h3 style="color: #06D6A0; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    🥗 For Health
                </h3>
                <p style="color: #E2E8F0; font-size: 14.5px; margin: 0; line-height: 1.6; font-weight: 400;">
                    Eat healthy and nourishing food. Get good sound sleep. Appreciate my natural health.
                </p>
            </div>
            <div style="flex: 1; min-width: 260px; background: rgba(255, 255, 255, 0.04); padding: 20px; border-radius: 14px; border-left: 4px solid #4CC9F0; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);">
                <h3 style="color: #4CC9F0; font-size: 17px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    💎 For Wealth
                </h3>
                <p style="color: #E2E8F0; font-size: 14.5px; margin: 0; line-height: 1.6; font-weight: 400;">
                    Provide valuable and efficient service joyfully. Donate to people in need. Celebrate financial abundance around the world.
                </p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- HERO CARD 2: NAPOLEON HILL'S 5-STEP SELF-CONFIDENCE FORMULA ---
    st.markdown("""
    <div style="background: linear-gradient(135deg, #1E1B4B 0%, #0F172A 50%, #1E293B 100%); padding: 26px; border-radius: 18px; border: 1.5px solid #818CF8; box-shadow: 0 10px 30px rgba(129, 140, 248, 0.12); margin-bottom: 25px;">
        <h2 style="color: #A5B4FC; text-align: center; font-size: 24px; font-weight: 800; margin-bottom: 16px; letter-spacing: 0.5px;">
            💪 Napoleon Hill's 5-Step Self-Confidence Formula
        </h2>
        <div style="display: flex; flex-direction: column; gap: 12px;">
            <div style="background: rgba(255, 255, 255, 0.03); padding: 14px 18px; border-radius: 10px; border-left: 4px solid #818CF8;">
                <b style="color: #C7D2FE;">First.</b>
                <span style="color: #E2E8F0; font-size: 14px;"> I know that I have the ability to achieve the object of my Definite Chief Aim in life, therefore, I DEMAND of myself persistent, continuous action toward its attainment, and I here and now promise to render such action.</span>
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
                <span style="color: #E2E8F0; font-size: 14px;"> I fully realize that no wealth or position can long endure, unless built upon truth and justice, therefore, I will engage in no transaction which does not benefit all whom it affects. I will succeed by attracting to myself the forces I wish to use, and the cooperation of other people. I will induce others to serve me, because of my willingness to serve others. I will eliminate hatred, envy, jealousy, selfishness, and cynicism, by developing love for all humanity, because I know that a negative attitude toward others can never bring me success. I will cause others to believe in me, because I will believe in them, and in myself. I will sign my name to this formula, commit it to memory, and repeat it aloud once a day, with full FAITH that it will gradually influence my THOUGHTS and ACTIONS so that I will become a self-reliant, and successful person. I will sign my name to this formula, commit it to memory, and repeat it aloud once a day, with full FAITH that it will gradually influence my THOUGHTS and ACTIONS so that I will become a self-reliant, and successful person.</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- NET-DEBT-ZERO VISUALIZER ON AIM TAB (ALIGNED GOAL STATE) ---
    with st.container(border=True):
        st.markdown("<h3 style='margin-bottom: 0px;'>🎯 Net-Debt-Zero Visualizer</h3>", unsafe_allow_html=True)
        
        st.progress(1.0)
        st.caption("✨ **100.0% Covered** towards Net-Debt-Zero target | **Goal Fully Manifested**")
        
        st.success("🎉 **Net-Debt-Zero Fully Achieved:** Living in total financial freedom, peace of mind, and complete abundance!")
        
        st.divider()
        
        s_col1, s_col2 = st.columns(2)
        s_col1.metric("Principal Pending", "₹0", "100.0% Loan Cleared")
        s_col2.metric("Portfolio Value", "₹1,00,00,000", "1 Cr - Total Financial Abundance")

with tab_dashboard:
    # --- NET-DEBT-ZERO VISUALIZER ON DASHBOARD TAB (CURRENT REALITY) ---
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
            st.success("🎉 **Net-Debt-Zero Achieved:** Your investment portfolio corpus meets or exceeds your total remaining loan principal. You can now service EMIs directly from portfolio withdrawals!")

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
        st.metric(active_due_label, format_inr(active_due_amount), disbursement_badge)
        if not is_handover:
            with st.popover("✏️ Edit Disbursement Stage"):
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
                
                if st.button("💾 Save Disbursement Settings", disabled=not can_save, type="primary"):
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
        st.metric("Interest Rate", f"{current_interest_rate}%", "Floating Rate")
        with st.popover("✏️ Update Interest Rate"):
            st.markdown("### 🏦 Update Interest Rate")
            new_rate = st.number_input(
                "New Annual Interest Rate (%)", 
                value=float(current_interest_rate), 
                step=0.05, 
                format="%.2f"
            )
            if st.button("💾 Save New Rate", type="primary"):
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
        st.metric("Current Tenure Remaining", f"{rem_years:.1f} Yrs", f"{int(current_rem_months)} Mos left")

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
                st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 PAID</span>", unsafe_allow_html=True)
            else:
                st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 UNPAID</span>", unsafe_allow_html=True)

        if st.form_submit_button("Log Monthly Payment", disabled=is_current_month_paid):
            new_row = pd.DataFrame([{
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                "Month_Year": current_month_str, 
                "Expected_Payment": expected_loan, 
                "Actual_Payment": expected_loan, 
                "Payment_Type": payment_type, 
                "Confirmed": True,
                "Interest_Rate": current_interest_rate
            }])
            conn.update(worksheet="Loan_Tracker", data=pd.concat([df_loan, new_row], ignore_index=True))
            st.success(f"Logged {current_month_str} payment of {format_inr(expected_loan)} successfully!")
            st.rerun()

    if is_current_month_paid:
        st.info(f"✅ Payment for **{current_month_str}** is already logged. Duplicate entries for the same month are blocked.")

    # Principal Cleared Visualizer Card
    with st.container(border=True):
        pct_loan_cleared = (total_principal_cleared / INITIAL_LOAN) if INITIAL_LOAN > 0 else 0.0
        st.markdown(f"**📉 Principal Cleared Tracker** ({pct_loan_cleared * 100:.2f}% of Initial Loan Paid)")
        st.progress(min(pct_loan_cleared, 1.0))
        
        p_col1, p_col2, p_col3 = st.columns(3)
        p_col1.metric("Total Principal Cleared", format_inr(total_principal_cleared), f"{pct_loan_cleared*100:.1f}% Cleared")
        p_col2.metric("Cleared via Regular EMIs", format_inr(emi_principal_cleared))
        p_col3.metric("Cleared via Part Payments", format_inr(prepay_principal_cleared))

    st.divider()

    # --- SECTION 2: LIVE PORTFOLIO HOLDINGS & ACTION HEADER ---
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
                help="Upload multiple files at once (e.g. holdings-HEK312.csv, holdings-SDB789.xlsx)."
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
                            sheet_to_use = 'Combined' if 'Combined' in xls.sheet_names else xls.sheet_names[0]
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
                        placeholder="e.g. HEK312 or SDB789 (Mandatory)",
                        key=f"acc_input_{file.name}"
                    )
                    account_mapping[file.name] = user_acc.strip().upper()

            st.markdown("---")
            input_xirr = st.number_input(
                "Console Overall XIRR (%)", 
                value=None,
                min_value=-100.0, 
                max_value=500.0, 
                step=0.1,
                placeholder="e.g. 14.5 or -2.5 (Mandatory)",
                help="Enter overall portfolio XIRR % from Zerodha Console. Negative, zero, and positive values are allowed."
            )

            if st.button("Sync Holdings & XIRR to Google Sheets", key="btn_sync_holdings"):
                missing_accounts = [fname for fname, acc in account_mapping.items() if not acc]
                if input_xirr is None:
                    st.error("⚠️ Overall Console XIRR (%) is mandatory. Please enter your XIRR percentage before syncing.")
                elif not uploaded_files:
                    st.error("⚠️ Please select at least one holdings CSV or Excel file to upload.")
                elif missing_accounts:
                    st.error(f"⚠️ Please specify an Account ID for: {', '.join(f'`{f}`' for f in missing_accounts)}")
                else:
                    parsed_records = []
                    for file in uploaded_files:
                        target_acc = account_mapping.get(file.name, "")
                        cid, df_parsed = parse_zerodha_holdings_file(file, file.name, override_account_id=target_acc)
                        if not df_parsed.empty:
                            parsed_records.append(df_parsed)
                            st.info(f"Loaded **{len(df_parsed)} active holdings** for account **{cid}** from `{file.name}`")

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

                        keys = []
                        for _, row in df_all_merged.iterrows():
                            acc = safe_str(row.get('account', '')).upper()
                            isin = safe_str(row.get('isin', '')).upper()
                            sym = safe_str(row.get('symbol', '')).upper()
                            asset_id = isin if (isin and isin != "NAN") else sym
                            keys.append(f"{acc}_{asset_id}")

                        df_all_merged["acc_asset_key"] = keys

                        df_deduped_holdings = df_all_merged.drop_duplicates(subset=["acc_asset_key"], keep="last").drop(columns=["acc_asset_key"]).reset_index(drop=True)
                        df_deduped_holdings = df_deduped_holdings.fillna("")

                        try:
                            conn.update(worksheet="Portfolio_Tracker", data=df_deduped_holdings)
                            
                            # Save XIRR to Loan_Settings
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

    st.divider()

    # --- SECTION 3: REVISED REPAYMENT & NDZ STRATEGY ENGINE ---
    st.subheader("3. Part Payment & Prepayment Engine")

    curr_year_num, curr_target_prepay, curr_paid_prepay, curr_pending_prepay, has_4pct_executed = get_current_year_prepayment_status(df_loan, full_emi)
    corpus_4_pct = 0.04 * total_portfolio_val
    min_prepayment_allowed = 2 * full_emi

    # NET-DEBT-ZERO PREPAYMENT LOCK CHECK
    if not is_ndz_achieved:
        with st.container(border=True):
            st.markdown("### 🔒 Prepayment Engine Locked (Net-Debt-Zero Pending)")
            st.warning(
                f"⚠️ **Rule Restriction Active:** Prepayments are locked while your portfolio corpus (**{format_inr(total_portfolio_val)}**) is less than your remaining principal pending (**{format_inr(current_principal)}**).\n\n"
                f"**Net Debt Gap:** **{format_inr(current_principal - total_portfolio_val)}** remaining to reach Net-Debt-Zero."
            )
            
            c_l1, c_l2 = st.columns(2)
            with c_l1:
                st.info("🎯 **Primary Objective:** Direct all surplus monthly savings into building your investment portfolio corpus until Net-Debt-Zero is achieved.")
            with c_l2:
                st.success("🔓 **Unlocks Upon Net-Debt-Zero:**\n1. **Corpus EMI Servicing**: Withdraw monthly EMIs directly from corpus without touching salary.\n2. **4% Annual Prepayment**: Execute 4% corpus prepayments when Console XIRR > 10.0%.")

    else:
        # UNLOCKED STATE (Corpus >= Principal Pending)
        st.success("🎉 **Net-Debt-Zero Reached:** Portfolio corpus meets or exceeds remaining loan principal. Prepayment strategies are now fully unlocked!")
        
        with st.container(border=True):
            st.markdown("### 🚦 Live 4% Portfolio Corpus Rule Status")
            st.caption("ℹ️ **Annual Limit:** Tapping the portfolio corpus under this rule is strictly limited to **1 time per loan year**.")
            
            rule_col1, rule_col2, rule_col3 = st.columns(3)
            rule_col1.metric("4% Corpus Allocation", format_inr(corpus_4_pct))
            rule_col2.metric("2x EMI Minimum Threshold", format_inr(min_prepayment_allowed))
            
            is_corpus_sufficient = corpus_4_pct >= min_prepayment_allowed
            is_xirr_valid = (console_xirr is not None) and (console_xirr > 10.0)
            
            with rule_col3:
                st.markdown("**Corpus Requirement**")
                if has_4pct_executed:
                    st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 EXECUTED THIS YEAR (1/1 Used)</span>", unsafe_allow_html=True)
                elif is_corpus_sufficient and is_xirr_valid:
                    st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 UNLOCKED (XIRR > 10%)</span>", unsafe_allow_html=True)
                elif not is_xirr_valid:
                    st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 LOCKED (XIRR ≤ 10%)</span>", unsafe_allow_html=True)
                else:
                    st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 LOCKED (< 2x EMI)</span>", unsafe_allow_html=True)

        st.markdown("### 💸 Execute Strategy Action")

        prepay_strategy_type = st.radio(
            "Select Action Strategy:",
            options=[
                "Service Monthly EMI from Portfolio Corpus (Zero Salary Impact)",
                "Execute 4% Portfolio Corpus Prepayment (Requires XIRR > 10%)"
            ],
            horizontal=True
        )

        pp_input_col1, pp_input_col2 = st.columns(2)

        if "4% Portfolio Corpus" in prepay_strategy_type:
            enable_pp = is_xirr_valid and is_corpus_sufficient and (not has_4pct_executed)
            
            with pp_input_col1:
                st.info(f"Active Console XIRR: **{console_xirr:.2f}%**" if console_xirr is not None else "Active Console XIRR: Not set")
                if not is_xirr_valid:
                    st.warning("🔒 XIRR must be > 10.0% to unlock 4% corpus prepayment.")
                elif has_4pct_executed:
                    st.warning("🔒 4% Corpus Prepayment has already been executed for this loan year.")

            with pp_input_col2:
                pp_amount = st.number_input(
                    "Prepayment Amount (₹)", 
                    value=float(corpus_4_pct), 
                    step=5000.0, 
                    disabled=not enable_pp
                )
            logged_payment_type = "Prepayment (4% Corpus)"
            
            # Action Preview Calculation
            principal_reduction = pp_amount if enable_pp else 0.0
            new_rem_months = calc_rem_months(current_principal - principal_reduction, full_emi, r_monthly)
            months_saved = max(0, round(current_rem_months - new_rem_months))
            st.metric("Tenure Reduced By", f"{months_saved} Months", f"~ {months_saved/12:.1f} Years saved")

        else: # Service Monthly EMI from Corpus
            enable_pp = True
            with pp_input_col1:
                st.info(f"Monthly EMI of **{format_inr(full_emi)}** will be withdrawn from portfolio corpus.")
                
            with pp_input_col2:
                pp_amount = st.number_input(
                    "EMI Amount (₹)", 
                    value=float(full_emi), 
                    disabled=True
                )
            logged_payment_type = "Full EMI (Corpus Withdrawal)"
            
            # Action Preview Calculation for Regular EMI
            interest_portion = current_principal * r_monthly
            principal_reduction = max(0.0, full_emi - interest_portion)
            new_rem_months = calc_rem_months(current_principal - principal_reduction, full_emi, r_monthly)
            months_saved = max(0, round(current_rem_months - new_rem_months))
            st.metric("Tenure Progressed By", f"{months_saved} Month", "Standard 1-Month Amortization Cycle")

        if st.button("Execute Strategy Action & Log to Sheet", disabled=not enable_pp, type="primary"):
            new_row = pd.DataFrame([{
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                "Month_Year": datetime.now().strftime("%b %Y"), 
                "Expected_Payment": expected_loan, 
                "Actual_Payment": pp_amount, 
                "Payment_Type": logged_payment_type, 
                "Confirmed": True,
                "Interest_Rate": current_interest_rate
            }])
            conn.update(worksheet="Loan_Tracker", data=pd.concat([df_loan, new_row], ignore_index=True))
            st.success(f"Executed {logged_payment_type} of {format_inr(pp_amount)} successfully!")
            st.cache_data.clear()
            st.rerun()
