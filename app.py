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
    """Returns `True` if the user enters the correct password."""
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
    "NEXT50.NS": "Next 50",
    "NIFTYBEES": "NIFTY 50",
    "NIFTYBEES.NS": "NIFTY 50",
    "GOLDBEES": "GOLD",
    "GOLDBEES.NS": "GOLD",
    "LIQUIDBEES": "Liquid",
    "LIQUIDBEES.NS": "Liquid",
    "MIRAE": "Mirae ELSS",
    "MIRAE ELSS": "Mirae ELSS"
}

# --- LIVE LTP FETCHING (ETF VIA YFINANCE & MUTUAL FUND VIA AMFI API) ---
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

# --- MULTI-ACCOUNT ZERODHA TRADEBOOK PARSER (XIRR + AUTO HOLDINGS SYNC) ---
def process_zerodha_tradebooks(uploaded_files, df_portfolio_base):
    all_cash_flows = []
    all_dates = []
    category_holdings = {cat: {"qty": 0.0, "invested": 0.0} for cat in df_portfolio_base["Category"].tolist()}

    for file in uploaded_files:
        try:
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

            date_col = next((c for c in df.columns if 'date' in c), None)
            symbol_col = next((c for c in df.columns if 'symbol' in c or 'tradingsymbol' in c), None)
            type_col = next((c for c in df.columns if 'type' in c), None)
            qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), None)
            price_col = next((c for c in df.columns if 'price' in c or 'rate' in c or 'value' in c), None)

            if date_col and type_col and qty_col and price_col:
                df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                df = df.dropna(subset=[date_col])

                for _, row in df.iterrows():
                    t_type = str(row[type_col]).strip().lower()
                    qty = float(row[qty_col]) if not pd.isna(row[qty_col]) else 0.0
                    price = float(row[price_col]) if not pd.isna(row[price_col]) else 0.0
                    trade_val = qty * price

                    if trade_val > 0:
                        if t_type == 'buy':
                            all_cash_flows.append(-trade_val)
                            all_dates.append(row[date_col])
                        elif t_type == 'sell':
                            all_cash_flows.append(trade_val)
                            all_dates.append(row[date_col])

                    # Aggregate Holdings by Category
                    if symbol_col and not pd.isna(row[symbol_col]):
                        sym = str(row[symbol_col]).strip().upper()
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
        except Exception:
            pass

    if not all_cash_flows:
        return None, None

    # Construct Updated Portfolio DataFrame
    updated_portfolio = df_portfolio_base.copy()
    for idx, row in updated_portfolio.iterrows():
        cat = row["Category"]
        if cat in category_holdings:
            updated_portfolio.at[idx, "Units_Accumulated"] = category_holdings[cat]["qty"]
            updated_portfolio.at[idx, "Invested_Value"] = category_holdings[cat]["invested"]

    # Calculate live valuation for XIRR terminal cash flow
    temp_val = (updated_portfolio["Units_Accumulated"] * updated_portfolio["Current_LTP"]).sum()

    combined_df = pd.DataFrame({"Date": all_dates, "CF": all_cash_flows}).sort_values("Date")
    sorted_cfs = combined_df["CF"].tolist()
    sorted_dates = combined_df["Date"].tolist()

    sorted_cfs.append(float(temp_val if temp_val > 0 else 1.0))
    sorted_dates.append(datetime.now())

    computed_xirr = solve_xirr(sorted_cfs, sorted_dates)
    return computed_xirr, updated_portfolio

# --- FALLBACK XIRR FROM INVESTMENT LOG ---
def calculate_fallback_xirr(df_inv_log, current_portfolio_val):
    try:
        if df_inv_log.empty or current_portfolio_val <= 0:
            return 0.12

        df_temp = df_inv_log.copy()
        df_temp["Date_DT"] = pd.to_datetime(df_temp["Date"], errors="coerce")
        df_temp["Actual_SIP"] = pd.to_numeric(df_temp["Actual_SIP"], errors="coerce").fillna(0.0)
        df_temp = df_temp.dropna(subset=["Date_DT"]).sort_values("Date_DT")

        cash_flows = []
        dates = []

        for _, row in df_temp.iterrows():
            sip_amt = float(row["Actual_SIP"])
            if sip_amt > 0:
                cash_flows.append(-sip_amt)
                dates.append(row["Date_DT"])

        if len(cash_flows) == 0 and "Total_Invested" in df_temp.columns:
            df_temp["Total_Invested"] = pd.to_numeric(df_temp["Total_Invested"], errors="coerce").fillna(0.0)
            prev_inv = 0.0
            for _, row in df_temp.iterrows():
                curr_inv = float(row["Total_Invested"])
                delta = curr_inv - prev_inv
                if delta > 0:
                    cash_flows.append(-delta)
                    dates.append(row["Date_DT"])
                prev_inv = curr_inv

        cash_flows.append(float(current_portfolio_val))
        dates.append(datetime.now())

        return solve_xirr(cash_flows, dates)
    except Exception:
        return 0.12

# --- AMORTIZATION ENGINE: DYNAMIC PRINCIPAL REDUCTION ---
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

# --- PREPAYMENT TRACKER HELPER ---
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

# --- FORWARD NDZ PROJECTION ENGINE ---
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
    return df_loan, df_portfolio, df_inv_log, disbursed_ratio, is_handover_completed, current_interest_rate

df_loan, df_portfolio, df_inv_log, disbursed_ratio, is_handover_completed, current_interest_rate = load_data()

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
if "tradebook_xirr" in st.session_state:
    calculated_xirr = st.session_state["tradebook_xirr"]
    xirr_source = "Combined Zerodha Tradebooks"
else:
    calculated_xirr = calculate_fallback_xirr(df_inv_log, total_portfolio_val)
    xirr_source = "Investment Log"

# --- DERIVED PRIOR INVESTED BASELINE FOR SIP / SWP CALCULATION ---
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
            
            if st.button("💾 Save Disbursement Settings", type="primary", width="stretch"):
                updated_settings = pd.DataFrame([{
                    "Disbursed_Ratio": new_ratio,
                    "Handover_Completed": (new_ratio == 1.0),
                    "Interest_Rate": current_interest_rate
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
        if st.button("💾 Save New Rate", type="primary", width="stretch"):
            updated_settings = pd.DataFrame([{
                "Disbursed_Ratio": disbursed_ratio,
                "Handover_Completed": is_handover_completed,
                "Interest_Rate": new_rate
            }])
            conn.update(worksheet="Loan_Settings", data=updated_settings)
            st.success(f"Interest rate dynamically updated to {new_rate}%!")
            st.rerun()

with m_col3:
    st.metric("Current Tenure Remaining", f"{rem_years:.1f} Yrs", f"{int(current_rem_months)} Mos left")

if not df_loan.empty and "Month_Year" in df_loan.columns:
    emi_records = df_loan[df_loan["Payment_Type"].isin(["Pre-EMI", "Full EMI"])]
    is_current_month_paid = current_month_str in emi_records["Month_Year"].values
else:
    is_current_month_paid = False

with st.form("emi_form", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    c1.text_input("Month-Year", value=current_month_str, disabled=True)
    
    payment_type = "Full EMI" if is_handover else "Pre-EMI"
    expected_loan = full_emi if is_handover else monthly_pre_emi
    c2.text_input("Actual Loan Payment Made", value=format_inr(expected_loan), disabled=True)
    
    with c3:
        st.markdown("**Loan Payment Status**")
        if is_current_month_paid:
            st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 PAID</span>", unsafe_allow_html=True)
        else:
            st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 UNPAID</span>", unsafe_allow_html=True)

    if st.form_submit_button("Log Monthly Loan Payment", disabled=is_current_month_paid, width="stretch"):
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
    st.info(f"✅ Loan payment for **{current_month_str}** is logged. Duplicate entries blocked.")

# --- PRINCIPAL CLEARED VISUALIZER CARD ---
with st.container(border=True):
    pct_loan_cleared = (total_principal_cleared / INITIAL_LOAN) if INITIAL_LOAN > 0 else 0.0
    st.markdown(f"**📉 Principal Cleared Tracker** ({pct_loan_cleared * 100:.2f}% of Initial Loan Paid)")
    st.progress(min(pct_loan_cleared, 1.0))
    
    p_col1, p_col2, p_col3 = st.columns(3)
    p_col1.metric("Total Principal Cleared", format_inr(total_principal_cleared), f"{pct_loan_cleared*100:.1f}% Cleared")
    p_col2.metric("Cleared via Regular EMIs", format_inr(emi_principal_cleared))
    p_col3.metric("Cleared via Part Payments", format_inr(prepay_principal_cleared))

st.divider()

# --- PART 2: LIVE PORTFOLIO HOLDINGS & DYNAMIC SIP / SWP TRACKER ---
sec2_col1, sec2_col2 = st.columns([2, 1])

with sec2_col1:
    st.subheader("2. Live Portfolio Holdings & Capital Flow")
with sec2_col2:
    p_c1, p_c2 = st.columns(2)
    with p_c1:
        with st.popover("📁 Import Tradebooks", width="stretch"):
            st.markdown("### 📥 Import Zerodha Tradebook CSVs")
            st.caption("Upload Zerodha Console Tradebook CSVs for **both you and your wife** to auto-sync holdings & calculate exact combined XIRR:")
            
            uploaded_tb_files = st.file_uploader(
                "Select Tradebook CSVs", 
                type=["csv"], 
                accept_multiple_files=True,
                key="tradebook_uploader"
            )
            
            if uploaded_tb_files and st.button("⚡ Sync Holdings & Calculate XIRR", type="primary", width="stretch"):
                tb_xirr, updated_tb_portfolio = process_zerodha_tradebooks(uploaded_tb_files, df_portfolio)
                if tb_xirr is not None and updated_tb_portfolio is not None:
                    st.session_state["tradebook_xirr"] = tb_xirr
                    
                    # Update Google Sheets Portfolio Tracker automatically
                    df_to_save = updated_tb_portfolio[["Category", "Units_Accumulated", "Current_LTP", "Invested_Value"]].copy()
                    conn.update(worksheet="Portfolio_Tracker", data=df_to_save)
                    
                    st.success(f"Successfully synced holdings and computed combined XIRR: {tb_xirr*100:.2f}%!")
                    st.rerun()
                else:
                    st.error("Could not parse tradebook CSVs. Ensure you uploaded valid Zerodha Tradebook CSV files.")

    with p_c2:
        with st.popover("✏️ Edit Holdings", width="stretch"):
            st.markdown("### 📊 Update Asset Holdings")
            st.caption("Editing Qty & Invested Amount dynamically calculates your monthly SIP / SWP:")
            
            editor_df = df_portfolio[["Category", "Units_Accumulated", "Invested_Value"]].copy()
            
            edited_data = st.data_editor(
                editor_df,
                column_config={
                    "Category": st.column_config.TextColumn("Holding Name", disabled=True),
                    "Units_Accumulated": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0, format="%.4f"),
                    "Invested_Value": st.column_config.NumberColumn("Invested Amount (₹)", min_value=0.0, step=1000.0, format="%.2f")
                },
                hide_index=True,
                width="stretch"
            )
            
            if st.button("💾 Save All Holdings Updates", type="primary", width="stretch"):
                df_portfolio["Units_Accumulated"] = edited_data["Units_Accumulated"]
                df_portfolio["Invested_Value"] = edited_data["Invested_Value"]
                
                for i, r in df_portfolio.iterrows():
                    if r["Current_LTP"] <= 0 and r["Units_Accumulated"] > 0 and r["Invested_Value"] > 0:
                        df_portfolio.at[i, "Current_LTP"] = r["Invested_Value"] / r["Units_Accumulated"]

                df_portfolio["Current_Value"] = df_portfolio["Units_Accumulated"] * df_portfolio["Current_LTP"]
                
                new_total_val = round(float(df_portfolio["Current_Value"].sum()), 2)
                new_total_inv = round(float(df_portfolio["Invested_Value"].sum()), 2)

                new_derived_sip = round(new_total_inv - prior_invested, 2)

                df_to_save = df_portfolio[["Category", "Units_Accumulated", "Current_LTP", "Invested_Value"]].copy()
                conn.update(worksheet="Portfolio_Tracker", data=df_to_save)
                
                snapshot_row = pd.DataFrame([{
                    "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Month_Year": current_month_str,
                    "Actual_SIP": new_derived_sip,
                    "Total_Invested": new_total_inv,
                    "Total_Value": new_total_val
                }])
                
                updated_inv_log = pd.concat([df_inv_log, snapshot_row], ignore_index=True)
                conn.update(worksheet="Investment_Log", data=updated_inv_log)
                
                st.success(f"Holdings updated! Auto-calculated monthly flow: {format_inr(new_derived_sip)}")
                st.rerun()

# --- INTEGRATED DYNAMIC EQUITY SIP / SWP TRACKER CARD ---
with st.container(border=True):
    if not is_ndz_achieved:
        st.markdown(f"### 📈 Equity SIP Allocation Tracker ({current_month_str})")
        
        sip_c1, sip_c2, sip_c3 = st.columns(3)
        sip_c1.metric("Expected Monthly SIP", format_inr(expected_sip), "Paused for Interior" if not is_handover else "₹60k - Full EMI")
        sip_c2.metric("Actual Monthly SIP (Calculated)", format_inr(derived_actual_sip), "Auto-derived from Holdings")
        
        with sip_c3:
            st.markdown("**SIP Status**")
            if not is_handover:
                st.markdown("<span style='color:#808495; font-weight:bold; font-size:18px;'>⏳ PAUSED (Interior Works Accumulation)</span>", unsafe_allow_html=True)
            elif derived_actual_sip >= expected_sip:
                st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 TARGET MET</span>", unsafe_allow_html=True)
            else:
                deficit = expected_sip - derived_actual_sip
                st.markdown(f"<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 DEFICIT ({format_inr(deficit)})</span>", unsafe_allow_html=True)
    else:
        st.markdown(f"### 🔄 Equity SWP & Corpus Flow Tracker ({current_month_str})")
        
        swp_c1, swp_c2, swp_c3 = st.columns(3)
        swp_c1.metric("Salary EMI Status", "OFFLOADED", "Zero Salary Contribution")
        
        if derived_actual_sip < 0:
            swp_c2.metric("Monthly SWP Executed", format_inr(abs(derived_actual_sip)), "Capital Withdrawn for Debt")
            with swp_c3:
                st.markdown("**Corpus Flow Status**")
                st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 SWP ACTIVE (Servicing Debt)</span>", unsafe_allow_html=True)
        else:
            swp_c2.metric("Monthly Net Addition", format_inr(derived_actual_sip), "Corpus Reinvested")
            with swp_c3:
                st.markdown("**Corpus Flow Status**")
                st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 CORPUS COMPOUNDING</span>", unsafe_allow_html=True)

# READONLY CARDS VIEW
active_holdings = df_portfolio[df_portfolio["Invested_Value"] > 0]

if active_holdings.empty:
    st.info("No active investments logged yet. Click '✏️ Edit Holdings' or '📁 Import Tradebooks' to sync your asset holdings.")
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

st.divider()

# --- PART 3: PART PAYMENT & PREPAYMENT ENGINE ---
st.subheader("3. Part Payment & Prepayment Engine")

curr_year_num, has_4pct_executed = get_current_year_prepayment_status(df_loan)

if not is_ndz_achieved:
    st.warning(
        f"🔒 **PREPAYMENTS STRICTLY LOCKED (Capital Stacking Phase Active):** "
        f"Your current portfolio value ({format_inr(total_portfolio_val)}) has not yet reached "
        f"your pending principal ({format_inr(current_principal)}). Prepayments are disabled to maximize "
        f"equity compounding velocity until Net-Debt-Zero is achieved."
    )
else:
    st.success("🎉 **NET-DEBT-ZERO ACHIEVED!** Conditional 4% Corpus Rule prepayments are now active.")
    
    with st.container(border=True):
        st.markdown("### 🚦 4% Portfolio Corpus Rule Conditions")
        st.caption("ℹ️ **Annual Limit:** Tapping the portfolio corpus under this rule is strictly limited to **1 time per loan year**, provided XIRR > 10.0%.")
        
        rule_col1, rule_col2, rule_col3 = st.columns(3)
        rule_col1.metric("4% Corpus Allocation", format_inr(corpus_4_pct))
        rule_col2.metric("2x EMI Minimum Threshold", format_inr(min_prepayment_allowed))
        
        is_corpus_sufficient = corpus_4_pct >= min_prepayment_allowed
        with rule_col3:
            st.markdown("**Corpus Requirement**")
            if has_4pct_executed:
                st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 EXECUTED THIS YEAR (1/1 Used)</span>", unsafe_allow_html=True)
            elif is_corpus_sufficient:
                st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 MET (≥ 2x EMI)</span>", unsafe_allow_html=True)
            else:
                st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 LOCKED (< 2x EMI)</span>", unsafe_allow_html=True)

    st.markdown("### 💸 Execute 4% Corpus Part Payment")
    pp_input_col1, pp_input_col2 = st.columns(2)

    with pp_input_col1:
        user_xirr = st.number_input(
            f"Portfolio XIRR (%) [{xirr_source}]", 
            value=float(round(calculated_xirr * 100, 2)), 
            step=0.5, 
            help="Auto-calculated from tradebooks/logs. You can also override manually."
        )

    is_xirr_valid = user_xirr > 10.0
    
    if has_4pct_executed:
        st.warning(f"🔒 **Part Payment Locked (Annual Limit Reached):** You have already executed your 1-time 4% Corpus Rule prepayment for Loan Year {curr_year_num}.")
        default_pp_val = float(min_prepayment_allowed)
        enable_pp = False
    elif not is_xirr_valid:
        st.warning(f"🔒 **Part Payment Locked:** XIRR must be > 10.0% to unlock corpus prepayments (Current: {user_xirr:.2f}%).")
        default_pp_val = float(min_prepayment_allowed)
        enable_pp = False
    elif not is_corpus_sufficient:
        st.info(f"⏳ **Corpus Growth Required:** Your 4% corpus allocation (**{format_inr(corpus_4_pct)}**) is less than 2x EMI (**{format_inr(min_prepayment_allowed)}**).")
        default_pp_val = float(min_prepayment_allowed)
        enable_pp = False
    else:
        st.success(f"✅ **Prepayment Unlocked:** XIRR > 10% and 4% portfolio cap meets minimum 2x EMI requirements.")
        default_pp_val = float(corpus_4_pct)
        enable_pp = True

    with pp_input_col2:
        pp_amount = st.number_input(
            "Part Payment Amount (₹)", 
            value=default_pp_val, 
            step=5000.0, 
            disabled=not enable_pp,
            help="Defaulted to 4% of actual corpus value when unlocked."
        )

    new_rem_months = calc_rem_months(current_principal - (pp_amount if enable_pp else 0.0), full_emi, r_monthly)
    months_saved = max(0, current_rem_months - new_rem_months)

    st.metric("Tenure Reduced By", f"{int(months_saved)} Months", f"~ {months_saved/12:.1f} Years saved")

    if st.button("Execute Part Payment & Log to Sheet", disabled=not enable_pp, type="primary", width="stretch"):
        new_row = pd.DataFrame([{
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
            "Month_Year": datetime.now().strftime("%b %Y"), 
            "Expected_Payment": 0.0, 
            "Actual_Payment": pp_amount, 
            "Payment_Type": "Prepayment (4% Corpus)", 
            "Confirmed": True,
            "Interest_Rate": current_interest_rate
        }])
        conn.update(worksheet="Loan_Tracker", data=pd.concat([df_loan, new_row], ignore_index=True))
        st.success(f"Part payment of {format_inr(pp_amount)} applied! Tenure reduced by {int(months_saved)} months.")
        st.rerun()

st.divider()

# --- PART 4: HISTORICAL PORTFOLIO GROWTH TIMELINE ---
st.subheader("📈 Portfolio Valuation & Growth Timeline")

if not df_inv_log.empty:
    try:
        df_chart = df_inv_log.copy()
        
        df_chart["Total_Invested"] = pd.to_numeric(df_chart["Total_Invested"], errors='coerce').round(2)
        df_chart["Total_Value"] = pd.to_numeric(df_chart["Total_Value"], errors='coerce').round(2)
        
        df_monthly = df_chart.groupby("Month_Year", sort=False).last().reset_index()
        
        df_monthly_chart = df_monthly.set_index("Month_Year")[["Total_Invested", "Total_Value"]]
        
        st.line_chart(
            df_monthly_chart,
            color=["#FF4B4B", "#00CC96"],
            width="stretch"
        )
    except Exception:
        st.info("Log your portfolio updates to start building your historical growth chart!")
else:
    st.info("No historical snapshots found yet. Click 'Save All Holdings Updates' above to record your first snapshot.")
