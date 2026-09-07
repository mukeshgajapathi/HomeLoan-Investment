import streamlit as st
import pandas as pd
import yfinance as yf
import math
import urllib.request
import json
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

# ==========================================
# --- APP LOGIC (RUNS IF AUTHENTICATED) ---
# ==========================================

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

# --- ZERODHA SYMBOL TO DASHBOARD CATEGORY MAPPER ---
SYMBOL_MAP = {
    "NEXT50": "Next 50",
    "NIFTYBEES": "NIFTY 50",
    "HDFCNIFETF": "NIFTY 50",
    "GOLDBEES": "GOLD",
    "LIQUIDBEES": "Liquid",
    "MIRAE": "Mirae ELSS"
}

# Filter to exclude Derivatives & Commodities
EXCLUDE_KEYWORDS = ["FUT", "CE", "PE", "MCX", "GOLDPETAL", "GOLDGUINEA", "CRUDEOIL"]

def is_equity_or_etf(symbol_str):
    sym = str(symbol_str).upper()
    for kw in EXCLUDE_KEYWORDS:
        if kw in sym:
            return False
    return True

# --- LIVE LTP FETCHING ---
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

# --- NEWTON-RAPHSON XIRR SOLVER ---
def solve_xirr(cash_flows, dates, guess=0.12):
    try:
        if len(cash_flows) < 2 or sum(cash_flows) == 0:
            return 0.12

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

# --- PROCESS RAW TRADES FOR HOLDINGS & XIRR ---
def process_raw_trades_tab(df_raw_trades, df_portfolio_base):
    if df_raw_trades.empty:
        return None, df_portfolio_base

    category_holdings = {cat: {"qty": 0.0, "invested": 0.0} for cat in df_portfolio_base["Category"].tolist()}
    cash_flows = []
    dates = []

    # Clean & sort trade dates
    df_raw = df_raw_trades.copy()
    df_raw["Date_DT"] = pd.to_datetime(df_raw["Date"], errors="coerce")
    df_raw = df_raw.dropna(subset=["Date_DT"]).sort_values("Date_DT")

    for _, row in df_raw.iterrows():
        sym = str(row.get("Symbol", "")).strip().upper()
        t_type = str(row.get("Type", "")).strip().lower()
        qty = float(row.get("Quantity", 0.0))
        price = float(row.get("Price", 0.0))
        trade_val = float(row.get("Value", qty * price))

        # Filter out F&O / Commodities
        if not is_equity_or_etf(sym) or trade_val <= 0:
            continue

        # Cash flows for XIRR
        if t_type == 'buy':
            cash_flows.append(-trade_val)
            dates.append(row["Date_DT"])
        elif t_type == 'sell':
            cash_flows.append(trade_val)
            dates.append(row["Date_DT"])

        # Match symbol to dashboard category
        matched_cat = None
        for s_key, c_val in SYMBOL_MAP.items():
            if s_key in sym:
                matched_cat = c_val
                break

        if matched_cat in category_holdings:
            if t_type == 'buy':
                category_holdings[matched_cat]["qty"] += qty
                category_holdings[matched_cat]["invested"] += trade_val
            elif t_type == 'sell':
                category_holdings[matched_cat]["qty"] = max(0.0, category_holdings[matched_cat]["qty"] - qty)
                category_holdings[matched_cat]["invested"] = max(0.0, category_holdings[matched_cat]["invested"] - trade_val)

    # Build updated portfolio DataFrame
    updated_portfolio = df_portfolio_base.copy()
    for idx, row in updated_portfolio.iterrows():
        cat = row["Category"]
        if cat in category_holdings:
            updated_portfolio.at[idx, "Units_Accumulated"] = category_holdings[cat]["qty"]
            updated_portfolio.at[idx, "Invested_Value"] = category_holdings[cat]["invested"]

    # Current terminal cash flow for XIRR
    temp_val = (updated_portfolio["Units_Accumulated"] * updated_portfolio["Current_LTP"]).sum()

    if cash_flows:
        cash_flows.append(float(temp_val if temp_val > 0 else 1.0))
        dates.append(datetime.now())
        computed_xirr = solve_xirr(cash_flows, dates)
    else:
        computed_xirr = None

    return computed_xirr, updated_portfolio

# --- AMORTIZATION ENGINE ---
def calculate_loan_state(df_loan, initial_loan, current_global_rate):
    p_balance = initial_loan
    total_principal_cleared = 0.0
    emi_principal_cleared = 0.0
    prepay_principal_cleared = 0.0
    
    if not df_loan.empty:
        df_sorted = df_loan.copy()
        if "Date" in df_sorted.columns:
            df_sorted["Date_DT"] = pd.to_datetime(df_sorted["Date"], errors="coerce")
            df_sorted = df_sorted.sort_values("Date_DT")
            
        for _, row in df_sorted.iterrows():
            p_type = str(row.get("Payment_Type", ""))
            actual_pay = float(row.get("Actual_Payment", 0.0))
            
            row_rate = current_global_rate
            if "Interest_Rate" in df_sorted.columns and not pd.isna(row.get("Interest_Rate")):
                try:
                    row_rate = float(row.get("Interest_Rate"))
                except ValueError:
                    pass
                    
            r_monthly = (row_rate / 100) / 12
            
            if p_type == "Pre-EMI":
                pass
            elif p_type == "Full EMI":
                interest_portion = p_balance * r_monthly
                principal_portion = max(0.0, actual_pay - interest_portion)
                p_balance -= principal_portion
                total_principal_cleared += principal_portion
                emi_principal_cleared += principal_portion
            elif "Prepayment" in p_type:
                p_balance -= actual_pay
                total_principal_cleared += actual_pay
                prepay_principal_cleared += actual_pay
                
    p_balance = max(0.0, p_balance)
    return p_balance, total_principal_cleared, emi_principal_cleared, prepay_principal_cleared

def get_current_year_prepayment_status(df_loan):
    if not df_loan.empty and "Date" in df_loan.columns:
        df_temp = df_loan.copy()
        df_temp["Date_DT"] = pd.to_datetime(df_temp["Date"], errors="coerce")
        df_temp = df_temp.dropna(subset=["Date_DT"]).sort_values("Date_DT")
        
        df_full = df_temp[df_temp["Payment_Type"].str.contains("Full EMI|Prepayment", na=False)]
        
        if not df_full.empty:
            start_date = df_full.iloc[0]["Date_DT"]
        else:
            start_date = pd.to_datetime("2027-06-01")
            
        now = datetime.now()
        if now < start_date:
            return 0, False
        
        elapsed_months = (now.year - start_date.year) * 12 + (now.month - start_date.month)
        current_year_num = max(1, (elapsed_months // 12) + 1)
        year_start_date = start_date + pd.DateOffset(months=(current_year_num - 1) * 12)
        
        prepays_this_year_df = df_temp[
            (df_temp["Payment_Type"].str.contains("Prepayment", na=False)) & 
            (df_temp["Date_DT"] >= year_start_date)
        ]
        
        has_4pct_prepay_this_year = prepays_this_year_df["Payment_Type"].str.contains("4% Corpus", na=False).any()
        return current_year_num, has_4pct_prepay_this_year

    return 0, False

def project_ndz_target(current_principal, current_portfolio, current_rate, full_emi, is_handover, xirr_rate):
    if current_portfolio >= current_principal:
        return "Achieved", 0, 0
        
    p_bal = current_principal
    port_val = current_portfolio
    r_m_loan = (current_rate / 100) / 12
    r_m_eq = (1 + xirr_rate)**(1/12) - 1
    
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

# --- PARAMETERS & CONNECTION ---
TICKERS = {
    "Next 50": "NEXT50.NS", 
    "NIFTY 50": "NIFTYBEES.NS", 
    "GOLD": "GOLDBEES.NS", 
    "Liquid": "LIQUIDBEES.NS",
    "Mirae ELSS": "AMFI:135781"
}
INITIAL_LOAN = 4890000.0
LOAN_TENURE_YEARS = 30

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try:
        df_loan = conn.read(worksheet="Loan_Tracker", ttl="10")
    except Exception:
        df_loan = pd.DataFrame(columns=["Date", "Month_Year", "Expected_Payment", "Actual_Payment", "Payment_Type", "Confirmed", "Interest_Rate"])
        
    try:
        df_portfolio = conn.read(worksheet="Portfolio_Tracker", ttl="10")
    except Exception:
        df_portfolio = pd.DataFrame(columns=["Category", "Units_Accumulated", "Current_LTP", "Invested_Value"])
        
    try:
        df_raw_trades = conn.read(worksheet="Raw_Trades", ttl="10")
    except Exception:
        df_raw_trades = pd.DataFrame()

    try:
        df_inv_log = conn.read(worksheet="Investment_Log", ttl="10")
        if "Actual_SIP" not in df_inv_log.columns:
            df_inv_log["Actual_SIP"] = 0.0
    except Exception:
        df_inv_log = pd.DataFrame(columns=["Date", "Month_Year", "Actual_SIP", "Total_Invested", "Total_Value"])

    try:
        df_settings = conn.read(worksheet="Loan_Settings", ttl="10")
        if not df_settings.empty:
            disbursed_ratio = 0.90
            if "Disbursed_Ratio" in df_settings.columns and not pd.isna(df_settings.iloc[0]["Disbursed_Ratio"]):
                disbursed_ratio = float(df_settings.iloc[0]["Disbursed_Ratio"])
                
            is_handover_completed = False
            if "Handover_Completed" in df_settings.columns:
                is_handover_completed = str(df_settings.iloc[0]["Handover_Completed"]).strip().upper() == "TRUE"
                
            current_interest_rate = 7.20
            if "Interest_Rate" in df_settings.columns and not pd.isna(df_settings.iloc[0]["Interest_Rate"]):
                current_interest_rate = float(df_settings.iloc[0]["Interest_Rate"])
        else:
            disbursed_ratio, is_handover_completed, current_interest_rate = 0.90, False, 7.20
    except Exception:
        disbursed_ratio, is_handover_completed, current_interest_rate = 0.90, False, 7.20

    if df_portfolio.empty:
        df_portfolio = pd.DataFrame({
            "Category": ["Next 50", "NIFTY 50", "GOLD", "Liquid", "Mirae ELSS"],
            "Units_Accumulated": [0.0, 0.0, 0.0, 0.0, 0.0],
            "Current_LTP": [0.0, 0.0, 0.0, 0.0, 0.0],
            "Invested_Value": [0.0, 0.0, 0.0, 0.0, 0.0]
        })
    return df_loan, df_portfolio, df_raw_trades, df_inv_log, disbursed_ratio, is_handover_completed, current_interest_rate

df_loan, df_portfolio, df_raw_trades, df_inv_log, disbursed_ratio, is_handover_completed, current_interest_rate = load_data()

# Update Portfolio Items with Live LTPs
for idx, row in df_portfolio.iterrows():
    cat = row["Category"]
    if cat in TICKERS:
        fetched_ltp = fetch_live_ltp(TICKERS[cat])
        if fetched_ltp is not None and fetched_ltp > 0:
            df_portfolio.at[idx, "Current_LTP"] = fetched_ltp

# Clean numeric fields & calculate portfolio metrics
df_portfolio["Units_Accumulated"] = pd.to_numeric(df_portfolio["Units_Accumulated"], errors='coerce').fillna(0.0)
df_portfolio["Current_LTP"] = pd.to_numeric(df_portfolio["Current_LTP"], errors='coerce').fillna(0.0)
df_portfolio["Invested_Value"] = pd.to_numeric(df_portfolio["Invested_Value"], errors='coerce').fillna(0.0)

# Process Raw_Trades tab if available
computed_xirr, df_portfolio = process_raw_trades_tab(df_raw_trades, df_portfolio)

for idx, row in df_portfolio.iterrows():
    if row["Current_LTP"] <= 0 and row["Units_Accumulated"] > 0 and row["Invested_Value"] > 0:
        df_portfolio.at[idx, "Current_LTP"] = row["Invested_Value"] / row["Units_Accumulated"]

df_portfolio["Current_Value"] = df_portfolio["Units_Accumulated"] * df_portfolio["Current_LTP"]
df_portfolio["P&L (₹)"] = df_portfolio["Current_Value"] - df_portfolio["Invested_Value"]

total_portfolio_val = df_portfolio["Current_Value"].sum()
total_portfolio_invested = df_portfolio["Invested_Value"].sum()
overall_pnl = total_portfolio_val - total_portfolio_invested
overall_pnl_pct = (overall_pnl / total_portfolio_invested * 100) if total_portfolio_invested > 0 else 0.0

# --- DETERMINE ACTIVE XIRR RATE ---
if computed_xirr is not None:
    calculated_xirr = computed_xirr
    xirr_source = "Auto-Synced Gmail Contract Notes"
else:
    calculated_xirr = 0.12
    xirr_source = "Default Baseline (12.0%)"

current_month_str = datetime.now().strftime("%b %Y")

if not df_inv_log.empty and "Month_Year" in df_inv_log.columns and "Total_Invested" in df_inv_log.columns:
    prev_logs = df_inv_log[df_inv_log["Month_Year"] != current_month_str]
    if not prev_logs.empty:
        prior_invested = float(prev_logs["Total_Invested"].iloc[-1])
    else:
        prior_invested = float(df_inv_log["Total_Invested"].iloc[0])
else:
    prior_invested = 0.0

derived_actual_sip = total_portfolio_invested - prior_invested

# --- DERIVED LOAN CALCULATIONS via AMORTIZATION ENGINE ---
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
    expected_sip = max(0.0, 60000.0 - full_emi)
else:
    active_due_label = "Pre-EMI Due"
    active_due_amount = monthly_pre_emi
    disbursement_badge = f"{int(disbursed_ratio * 100)}% Disbursed"
    expected_sip = 0.0

current_rem_months = calc_rem_months(current_principal, full_emi, r_monthly)
rem_years = current_rem_months / 12

min_prepayment_allowed = 2 * full_emi
corpus_4_pct = 0.04 * total_portfolio_val
is_ndz_achieved = total_portfolio_val >= current_principal

# Run Forward NDZ Projection
proj_date, proj_yrs, proj_mos = project_ndz_target(
    current_principal, total_portfolio_val, current_interest_rate, full_emi, is_handover, xirr_rate=calculated_xirr
)

# --- DASHBOARD HEADER ---
st.title("🏡 Home Loan & 📈 Investment Tracker")

# --- NET-DEBT-ZERO & OVERALL SUMMARY CARD ---
with st.container(border=True):
    st.subheader("🎯 Net-Debt-Zero Visualizer")
    net_debt = current_principal - total_portfolio_val
    nd_covered_pct = (total_portfolio_val / current_principal * 100) if current_principal > 0 else 100.0
    
    nd_col1, nd_col2 = st.columns([3, 1])
    with nd_col1:
        st.progress(min(total_portfolio_val / current_principal, 1.0) if current_principal > 0 else 1.0)
        st.caption(f"**{nd_covered_pct:.1f}% Covered** towards Net-Debt-Zero target")
    with nd_col2:
        if is_ndz_achieved: 
            st.success("🎉 Zero Debt Achieved!")
        else: 
            st.metric("Net Debt Pending", format_inr(net_debt))

    if not is_ndz_achieved:
        st.info(f"🔮 **Projected Net-Debt-Zero Target:** **{proj_date}** (~ {proj_yrs} Yrs {proj_mos} Mos away assuming **{calculated_xirr*100:.2f}% XIRR** via {xirr_source})")

    st.divider()

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    pct_principal_cleared = (total_principal_cleared / INITIAL_LOAN * 100) if INITIAL_LOAN > 0 else 0.0
    
    s_col1.metric("Principal Pending", format_inr(current_principal), f"{pct_principal_cleared:.1f}% Loan Cleared")
    s_col2.metric("Portfolio Value", format_inr(total_portfolio_val))
    s_col3.metric("Total Invested", format_inr(total_portfolio_invested))
    s_col4.metric("Overall Net P&L", format_inr(overall_pnl), f"{overall_pnl_pct:+.2f}%")

st.divider()

# --- PART 1: MONTHLY EMI LOGGING ---
st.subheader(f"1. Standard Monthly Payments ({active_due_label})")

m_col1, m_col2, m_col3 = st.columns(3)
with m_col1:
    st.metric(active_due_label, format_inr(active_due_amount), disbursement_badge)
with m_col2:
    st.metric("Interest Rate", f"{current_interest_rate}%", "Floating Rate")
with m_col3:
    st.metric("Current Tenure Remaining", f"{rem_years:.1f} Yrs", f"{int(current_rem_months)} Mos left")

st.divider()

# --- PART 2: LIVE PORTFOLIO HOLDINGS ---
st.subheader("2. Live Portfolio Holdings & Capital Flow")

active_holdings = df_portfolio[df_portfolio["Invested_Value"] > 0]

if active_holdings.empty:
    st.info("No active equity/ETF investments parsed yet from the 'Raw_Trades' tab.")
else:
    for _, row in active_holdings.iterrows():
        cat = row["Category"]
        units = row["Units_Accumulated"]
        ltp = row["Current_LTP"]
        inv = row["Invested_Value"]
        curr = row["Current_Value"]
        pnl = row["P&L (₹)"]
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0.0
        
        with st.container(border=True):
            st.markdown(
                f"**{cat}** &nbsp; <span style='color:#808495; font-size:13px;'>{units:.4f} Units @ {format_inr(ltp)}</span>", 
                unsafe_allow_html=True
            )
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", format_inr(inv))
            m2.metric("Current Value", format_inr(curr))
            m3.metric("Net P&L", format_inr(pnl), f"{pnl_pct:+.2f}%")
