import streamlit as st
import pandas as pd
import yfinance as yf
import math
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

st.set_page_config(
    page_title="Home Loan + Investment Tracker", 
    page_icon="🏡", 
    layout="wide"
)
# Initialize Session State for Edit Mode
if "edit_portfolio" not in st.session_state:
    st.session_state.edit_portfolio = False

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

# --- ROBUST LIVE LTP FETCHING ---
@st.cache_data(ttl=1800)
def fetch_live_ltp(ticker):
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
def calculate_loan_state(df_loan, initial_loan, rate_annual):
    r_monthly = (rate_annual / 100) / 12
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

# --- PARAMETERS & CONNECTION ---
TICKERS = {
    "Next 50": "NEXT50.NS", 
    "NIFTY 50": "NIFTYBEES.NS", 
    "GOLD": "GOLDBEES.NS", 
    "Liquid": "LIQUIDBEES.NS"
}
INITIAL_LOAN = 4890000.0
INTEREST_RATE_ANNUAL = 7.20
LOAN_TENURE_YEARS = 20

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try:
        df_loan = conn.read(worksheet="Loan_Tracker", ttl="0")
    except Exception:
        df_loan = pd.DataFrame(columns=["Date", "Month_Year", "Expected_Payment", "Actual_Payment", "Payment_Type", "Confirmed"])
        
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
            disbursed_ratio = float(df_settings.iloc[0]["Disbursed_Ratio"])
            is_handover_completed = str(df_settings.iloc[0]["Handover_Completed"]).strip().upper() == "TRUE"
        else:
            disbursed_ratio, is_handover_completed = 0.90, False
    except Exception:
        disbursed_ratio, is_handover_completed = 0.90, False

    if df_portfolio.empty:
        df_portfolio = pd.DataFrame({
            "Category": ["Next 50", "NIFTY 50", "GOLD", "Liquid"],
            "Units_Accumulated": [0.0, 0.0, 0.0, 0.0],
            "Current_LTP": [0.0, 0.0, 0.0, 0.0],
            "Invested_Value": [0.0, 0.0, 0.0, 0.0]
        })
    return df_loan, df_portfolio, df_inv_log, disbursed_ratio, is_handover_completed

df_loan, df_portfolio, df_inv_log, disbursed_ratio, is_handover_completed = load_data()

# Update Portfolio with Live LTPs
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

df_portfolio["Current_Value"] = df_portfolio["Units_Accumulated"] * df_portfolio["Current_LTP"]
df_portfolio["P&L (₹)"] = df_portfolio["Current_Value"] - df_portfolio["Invested_Value"]

total_portfolio_val = df_portfolio["Current_Value"].sum()
total_portfolio_invested = df_portfolio["Invested_Value"].sum()

# --- DERIVED LOAN CALCULATIONS via AMORTIZATION ENGINE ---
current_principal, total_principal_cleared, emi_principal_cleared, prepay_principal_cleared = calculate_loan_state(
    df_loan, INITIAL_LOAN, INTEREST_RATE_ANNUAL
)

r_monthly = (INTEREST_RATE_ANNUAL / 100) / 12
n_months_base = LOAN_TENURE_YEARS * 12
full_emi = INITIAL_LOAN * r_monthly * ((1 + r_monthly)**n_months_base) / (((1 + r_monthly)**n_months_base) - 1)

disbursed_loan_amount = INITIAL_LOAN * disbursed_ratio
monthly_pre_emi = (disbursed_loan_amount * (INTEREST_RATE_ANNUAL / 100)) / 12

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

st.subheader("🎯 Net-Debt-Zero Visualizer")
net_debt = current_principal - total_portfolio_val
nd_col1, nd_col2 = st.columns([3, 1])
with nd_col1:
    st.progress(min(total_portfolio_val / current_principal, 1.0) if current_principal > 0 else 1.0)
with nd_col2:
    if net_debt <= 0: st.success("🎉 Zero Debt Achieved!")
    else: st.caption(f"Net Debt: **{format_inr(net_debt)}**")

st.divider()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Loan Balance", format_inr(current_principal))

with col2:
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
                st.warning("⚠️ **Warning:** Setting disbursement to 100% marks handover complete. Dues permanently switch to **Full EMI** (₹38,501) and this edit option will be **permanently locked**.")
                confirm_handover = st.checkbox("I confirm handover is completed and agree to lock settings.")
            
            can_save = (new_ratio < 1.0) or (new_ratio == 1.0 and confirm_handover)
            
            if st.button("💾 Save Disbursement Settings", disabled=not can_save, type="primary", use_container_width=True):
                updated_settings = pd.DataFrame([{
                    "Disbursed_Ratio": new_ratio,
                    "Handover_Completed": (new_ratio == 1.0)
                }])
                conn.update(worksheet="Loan_Settings", data=updated_settings)
                st.success("Loan settings updated successfully!")
                st.rerun()

col3.metric("Current Tenure", f"{rem_years:.1f} Yrs", f"{int(current_rem_months)} Mos left")
col4.metric("Portfolio Value", format_inr(total_portfolio_val), f"Invested: {format_inr(total_portfolio_invested)}")

st.divider()

# --- PART 1: MONTHLY EMI LOGGING & CURRENT MONTH PAYMENT STATUS ---
st.subheader(f"1. Standard Monthly Payments ({active_due_label})")

current_month_str = datetime.now().strftime("%b %Y")

# Check if current month is already paid
if not df_loan.empty and "Month_Year" in df_loan.columns:
    emi_records = df_loan[df_loan["Payment_Type"].isin(["Pre-EMI", "Full EMI"])]
    is_current_month_paid = current_month_str in emi_records["Month_Year"].values
else:
    is_current_month_paid = False

with st.form("emi_form", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    
    # Auto-pick current month (Read-only)
    c1.text_input("Month-Year", value=current_month_str, disabled=True)
    
    # Payment type & amount
    payment_type = "Full EMI" if is_handover else "Pre-EMI"
    expected_loan = full_emi if is_handover else monthly_pre_emi
    c2.text_input("Actual Payment Made", value=format_inr(expected_loan), disabled=True)
    
    # Current Month Status Badge (Green PAID vs Red UNPAID)
    with c3:
        st.markdown("**Payment Status**")
        if is_current_month_paid:
            st.markdown("<span style='color:#00CC96; font-weight:bold; font-size:18px;'>🟢 PAID</span>", unsafe_allow_html=True)
        else:
            st.markdown("<span style='color:#FF4B4B; font-weight:bold; font-size:18px;'>🔴 UNPAID</span>", unsafe_allow_html=True)

    with c4:
        confirmed = st.checkbox("Confirm Payment", value=True, disabled=is_current_month_paid)

    if st.form_submit_button("Log Monthly Payment", disabled=is_current_month_paid, use_container_width=True):
        new_row = pd.DataFrame([{
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
            "Month_Year": current_month_str, 
            "Expected_Payment": expected_loan, 
            "Actual_Payment": expected_loan, 
            "Payment_Type": payment_type, 
            "Confirmed": confirmed
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

# --- PART 2: PORTFOLIO HOLDINGS (READONLY WITH TOGGLE EDIT) ---
sec2_col1, sec2_col2 = st.columns([4, 1])

with sec2_col1:
    st.subheader("2. Live Portfolio Holdings")
with sec2_col2:
    if not st.session_state.edit_portfolio:
        if st.button("✏️ Edit Holdings", use_container_width=True):
            st.session_state.edit_portfolio = True
            st.rerun()
    else:
        if st.button("❌ Cancel Edit", use_container_width=True):
            st.session_state.edit_portfolio = False
            st.rerun()

# READONLY MODE
if not st.session_state.edit_portfolio:
    st.dataframe(
        df_portfolio[["Category", "Units_Accumulated", "Current_LTP", "Invested_Value", "Current_Value", "P&L (₹)"]],
        column_config={
            "Category": "Asset Class",
            "Units_Accumulated": st.column_config.NumberColumn("Total Units Held", format="%.4f"),
            "Current_LTP": st.column_config.NumberColumn("Live LTP (₹)", format="₹%.2f"),
            "Invested_Value": st.column_config.NumberColumn("Invested Capital (₹)", format="₹%d"),
            "Current_Value": st.column_config.NumberColumn("Current Value (₹)", format="₹%d"),
            "P&L (₹)": st.column_config.NumberColumn("Net P&L (₹)", format="₹%d"),
        },
        hide_index=True,
        use_container_width=True
    )

# EDITABLE MODE
else:
    st.info("💡 Edit your **Units Accumulated** or **Invested Value** below, then click **Save Portfolio Updates**.")
    edited_portfolio = st.data_editor(
        df_portfolio[["Category", "Units_Accumulated", "Current_LTP", "Invested_Value", "Current_Value", "P&L (₹)"]],
        column_config={
            "Category": st.column_config.TextColumn("Asset Class", disabled=True),
            "Units_Accumulated": st.column_config.NumberColumn("Total Units Held", format="%.4f", min_value=0.0),
            "Current_LTP": st.column_config.NumberColumn("Live LTP (₹)", format="₹%.2f", disabled=True),
            "Invested_Value": st.column_config.NumberColumn("Invested Capital (₹)", format="₹%d", min_value=0.0),
            "Current_Value": st.column_config.NumberColumn("Current Value (₹)", format="₹%d", disabled=True),
            "P&L (₹)": st.column_config.NumberColumn("Net P&L (₹)", format="₹%d", disabled=True),
        },
        hide_index=True,
        use_container_width=True
    )

    if st.button("💾 Save Portfolio Updates & Record Snapshot", type="primary", use_container_width=True):
        df_to_save = edited_portfolio[["Category", "Units_Accumulated", "Current_LTP", "Invested_Value"]].copy()
        conn.update(worksheet="Portfolio_Tracker", data=df_to_save)
        
        new_curr_val = (edited_portfolio["Units_Accumulated"] * edited_portfolio["Current_LTP"]).sum()
        new_inv_val = edited_portfolio["Invested_Value"].sum()
        
        snapshot_row = pd.DataFrame([{
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Month_Year": datetime.now().strftime("%b %Y"),
            "Total_Invested": new_inv_val,
            "Total_Value": new_curr_val
        }])
        
        updated_inv_log = pd.concat([df_inv_log, snapshot_row], ignore_index=True)
        conn.update(worksheet="Investment_Log", data=updated_inv_log)
        
        st.session_state.edit_portfolio = False
        st.success("Portfolio updated and timeline snapshot recorded successfully!")
        st.rerun()

st.divider()

# --- PART 3: PART PAYMENT (PREPAYMENT RULES & SIMULATOR) ---
st.subheader("3. Part Payment (Tenure Reduction Simulator)")

pp_input_col1, pp_input_col2 = st.columns(2)

with pp_input_col1:
    user_xirr = st.number_input("Enter Zerodha Console XIRR (%)", value=0.0, step=0.5, help="Check your accurate XIRR directly from Zerodha Console.")

# Evaluate Prepayment Conditions
is_xirr_valid = user_xirr > 10.0
is_corpus_sufficient = corpus_4_pct >= min_prepayment_allowed

if not is_xirr_valid:
    st.warning(f"🔒 **Part Payment Greyed Out:** Zerodha Console XIRR must be > 10.0% to unlock prepayments (Current: {user_xirr:.1f}%).")
    default_pp_val = float(min_prepayment_allowed)
    enable_pp = False
elif not is_corpus_sufficient:
    st.info(f"⏳ **Corpus Growth Required:** Your 4% corpus allocation (**{format_inr(corpus_4_pct)}**) is less than the minimum required 2x EMI (**{format_inr(min_prepayment_allowed)}**). Please wait for your corpus to grow further.")
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

# Dynamic Tenure Reduction Display
new_rem_months = calc_rem_months(current_principal - (pp_amount if enable_pp else 0.0), full_emi, r_monthly)
months_saved = max(0, current_rem_months - new_rem_months)

st.metric("Tenure Reduced By", f"{int(months_saved)} Months", f"~ {months_saved/12:.1f} Years saved")

if st.button("Execute Part Payment & Log to Sheet", disabled=not enable_pp, type="primary"):
    new_row = pd.DataFrame([{
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M"), 
        "Month_Year": datetime.now().strftime("%b %Y"), 
        "Expected_Payment": 0.0, 
        "Actual_Payment": pp_amount, 
        "Payment_Type": "Prepayment", 
        "Confirmed": True
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
        df_chart["Date_DT"] = pd.to_datetime(df_chart["Date"])
        df_chart["Total_Invested"] = pd.to_numeric(df_chart["Total_Invested"], errors='coerce')
        df_chart["Total_Value"] = pd.to_numeric(df_chart["Total_Value"], errors='coerce')
        
        df_chart = df_chart.sort_values("Date_DT")
        df_monthly = df_chart.groupby("Month_Year", sort=False).last().reset_index()
        
        df_monthly_chart = df_monthly.set_index("Month_Year")[["Total_Invested", "Total_Value"]]
        
        st.line_chart(
            df_monthly_chart,
            color=["#FF4B4B", "#00CC96"],
            use_container_width=True
        )
    except Exception:
        st.info("Log your portfolio updates to start building your historical growth chart!")
else:
    st.info("No historical snapshots found yet. Click 'Save Portfolio Updates & Record Snapshot' above to record your first snapshot.")
