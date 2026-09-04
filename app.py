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
            elif p_type == "Prepayment":
                p_balance -= actual_pay
                total_principal_cleared += actual_pay
                prepay_principal_cleared += actual_pay
                
    p_balance = max(0.0, p_balance)
    return p_balance, total_principal_cleared, emi_principal_cleared, prepay_principal_cleared

# --- CURRENT YEAR PREPAYMENT TRACKER HELPER ---
def get_current_year_prepayment_status(df_loan, full_emi):
    base_2x = 2 * full_emi
    if not df_loan.empty and "Date" in df_loan.columns:
        df_temp = df_loan.copy()
        df_temp["Date_DT"] = pd.to_datetime(df_temp["Date"], errors="coerce")
        df_temp = df_temp.dropna(subset=["Date_DT"]).sort_values("Date_DT")
        
        if not df_temp.empty:
            start_date = df_temp.iloc[0]["Date_DT"]
            now = datetime.now()
            
            # Calculate current loan year index based on elapsed months
            elapsed_months = (now.year - start_date.year) * 12 + (now.month - start_date.month)
            current_year_num = max(1, (elapsed_months // 12) + 1)
            
            # Calculate target 2x stepped prepayment for the current loan year
            target_prepay = base_2x * (1.10 ** (current_year_num - 1))
            
            # Calculate start date of current 12-month loan year cycle
            year_start_date = start_date + pd.DateOffset(months=(current_year_num - 1) * 12)
            
            # Sum prepayments logged within current 12-month loan year
            prepays_this_year = df_temp[
                (df_temp["Payment_Type"] == "Prepayment") & 
                (df_temp["Date_DT"] >= year_start_date)
            ]["Actual_Payment"].astype(float).sum()
            
            pending_prepay = max(0.0, target_prepay - prepays_this_year)
            return current_year_num, target_prepay, prepays_this_year, pending_prepay

    return 1, base_2x, 0.0, base_2x

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
        df_loan = conn.read(worksheet="Loan_Tracker", ttl="0")
    except Exception:
        df_loan = pd.DataFrame(columns=["Date", "Month_Year", "Expected_Payment", "Actual_Payment", "Payment_Type", "Confirmed", "Interest_Rate"])
        
    try:
        df_portfolio = conn.read(worksheet="Portfolio_Tracker", ttl="0")
    except Exception:
        df_portfolio = pd.DataFrame(columns=["Category", "Units_Accumulated", "Current_LTP", "Invested_Value"])
        
    try:
        df_inv_log = conn.read(worksheet="Investment_Log", ttl="0")
    except Exception:
        df_inv_log = pd.DataFrame(columns=["Date", "Month_Year", "Total_Invested", "Total_Value"])

    try:
        df_settings = conn.read(worksheet="Loan_Settings", ttl="0")
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

# --- DERIVED LOAN CALCULATIONS ---
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

min_prepayment_allowed = 2 * full_emi
corpus_4_pct = 0.04 * total_portfolio_val

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
        if net_debt <= 0: 
            st.success("🎉 Zero Debt Achieved!")
        else: 
            st.metric("Net Debt Pending", format_inr(net_debt))

    st.divider()

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    pct_principal_cleared = (total_principal_cleared / INITIAL_LOAN * 100) if INITIAL_LOAN > 0 else 0.0
    
    s_col1.metric("Principal Pending", format_inr(current_principal), f"{pct_principal_cleared:.1f}% Loan Cleared")
    s_col2.metric("Portfolio Value", format_inr(total_portfolio_val))
    s_col3.metric("Total Invested", format_inr(total_portfolio_invested))
    s_col4.metric("Overall Net P&L", format_inr(overall_pnl), f"{overall_pnl_pct:+.2f}%")

st.divider()

# --- PART 1: MONTHLY EMI LOGGING & CURRENT MONTH PAYMENT STATUS ---
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
            
            if st.button("💾 Save Disbursement Settings", disabled=not can_save, type="primary", use_container_width=True):
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
        if st.button("💾 Save New Rate", type="primary", use_container_width=True):
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

current_month_str = datetime.now().strftime("%b %Y")

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
    c2.text_input("Actual Payment Made", value=format_inr(expected_loan), disabled=True)
    
    with c3:
        st.markdown("**Payment Status**")
        if is_current_month_paid:
            st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 PAID</span>", unsafe_allow_html=True)
        else:
            st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 UNPAID</span>", unsafe_allow_html=True)

    if st.form_submit_button("Log Monthly Payment", disabled=is_current_month_paid, use_container_width=True):
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

# --- PART 2: PORTFOLIO HOLDINGS ---
sec2_col1, sec2_col2 = st.columns([3, 1])

with sec2_col1:
    st.subheader("2. Live Portfolio Holdings")
with sec2_col2:
    with st.popover("✏️ Edit Holdings", use_container_width=True):
        st.markdown("### 📊 Update Asset Holdings")
        
        selected_cat = st.selectbox(
            "Select Asset:",
            df_portfolio["Category"].tolist()
        )
        
        selected_row = df_portfolio[df_portfolio["Category"] == selected_cat].iloc[0]
        curr_units = float(selected_row["Units_Accumulated"])
        curr_invested = float(selected_row["Invested_Value"])
        
        new_units = st.number_input(
            "Units Accumulated", 
            value=curr_units, 
            min_value=0.0, 
            step=1.0, 
            format="%.4f"
        )
        new_invested = st.number_input(
            "Total Amount Invested (₹)", 
            value=curr_invested
