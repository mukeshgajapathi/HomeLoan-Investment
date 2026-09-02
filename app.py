import streamlit as st
import pandas as pd
try:
    from kiteconnect import KiteConnect
except ImportError:
    st.error("The 'kiteconnect' package is missing. Install it using: pip install kiteconnect")

st.set_page_config(
    page_title="Wealth Tracker & Kite API Sandbox",
    page_icon="📈",
    layout="wide"
)

# Create top-level Navigation Tabs
tab_dashboard, tab_kite = st.tabs(["📊 Main Wealth & Loan Dashboard", "🔌 Zerodha Kite API Sandbox"])

# ==============================================================================
# TAB 1: MAIN WEALTH & LOAN DASHBOARD
# ==============================================================================
with tab_dashboard:
    st.title("📊 Financial Planning & Investment Dashboard")
    st.info("💡 **Developer Note:** Place your existing dashboard visualizations, loan tables, and equity step-up calculators here.")
    
    # --------------------------------------------------------------------------
    # YOUR EXISTING DASHBOARD CODE GOES BELOW THIS LINE
    # --------------------------------------------------------------------------
    st.markdown("### Portfolio & Loan Summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Target Liquid Corpus (2040)", "₹1.00 Cr+", "On Track")
    col2.metric("Home Loan Status", "30-Yr Tenure", "25% Salary SIP")
    col3.metric("Equity & Gold Asset Split", "45% N50 / 35% N50 / 20% Gold", "Blended 12.08% CAGR")
    
    # Example placeholder for your existing charts/tables
    st.divider()
    st.caption("Your existing interactive charts, loan schedules, and step-up calculators will render in this tab without interference from the API sandbox.")

# ==============================================================================
# TAB 2: ZERODHA KITE API SANDBOX
# ==============================================================================
with tab_kite:
    st.title("🔌 Zerodha Kite API Testing Sandbox")
    st.caption("Test user profile access, live portfolio holdings retrieval, open positions, and quote feeds.")

    # 1. API Credentials Input
    st.subheader("1. Enter API Credentials")
    c1, c2 = st.columns(2)
    with c1:
        api_key = st.text_input("Kite API Key", type="password", value=st.session_state.get("api_key", ""), key="input_api_key")
    with c2:
        api_secret = st.text_input("Kite API Secret", type="password", value=st.session_state.get("api_secret", ""), key="input_api_secret")

    if api_key and api_secret:
        st.session_state["api_key"] = api_key
        st.session_state["api_secret"] = api_secret
        
        # Initialize KiteConnect instance
        kite = KiteConnect(api_key=api_key)

        st.divider()

        # 2. Daily Session Authentication
        st.subheader("2. Daily Login Authentication")
        
        login_url = kite.login_url()
        st.markdown(f"**Step 1:** [👉 Click here to log in via Zerodha]({login_url})")
        st.caption("After logging in, Zerodha will redirect your browser. Copy the `request_token=XXXXXX` string from your browser URL bar.")
        
        req_token_input = st.text_input("Step 2: Paste Request Token Here", key="input_req_token")
        
        if st.button("🔑 Generate Daily Access Token"):
            if req_token_input:
                try:
                    session_data = kite.generate_session(req_token_input, api_secret=api_secret)
                    st.session_state["access_token"] = session_data["access_token"]
                    st.success("✅ Access token generated successfully! You are logged in for the session.")
                except Exception as e:
                    st.error(f"❌ Authentication Error: {e}")
            else:
                st.warning("Please paste the request token before generating session.")

        # 3. API Capability Testers (Unlocked after login)
        if "access_token" in st.session_state:
            kite.set_access_token(st.session_state["access_token"])
            
            st.divider()
            st.subheader("3. Test API Capabilities")

            subtab_profile, subtab_holdings, subtab_positions, subtab_quotes = st.tabs(
                ["User Profile", "Portfolio Holdings", "Positions", "Live Market Quotes"]
            )

            # Test 1: User Profile
            with subtab_profile:
                if st.button("Fetch User Profile"):
                    try:
                        profile_data = kite.profile()
                        st.json(profile_data)
                    except Exception as e:
                        st.error(f"Failed to fetch profile: {e}")

            # Test 2: Portfolio Holdings
            with subtab_holdings:
                if st.button("Fetch Real-Time Holdings"):
                    try:
                        holdings = kite.holdings()
                        if holdings:
                            df_holdings = pd.DataFrame(holdings)
                            st.dataframe(
                                df_holdings[['tradingsymbol', 'exchange', 'quantity', 'average_price', 'last_price', 'pnl']],
                                use_container_width=True
                            )
                        else:
                            st.info("No holdings found in this Zerodha account.")
                    except Exception as e:
                        st.error(f"Failed to fetch holdings: {e}")

            # Test 3: Day Positions
            with subtab_positions:
                if st.button("Fetch Open Positions"):
                    try:
                        positions = kite.positions()
                        st.json(positions)
                    except Exception as e:
                        st.error(f"Failed to fetch positions: {e}")

            # Test 4: Live Quotes / LTP
            with subtab_quotes:
                col_sym, col_btn = st.columns([3, 1])
                with col_sym:
                    symbol = st.text_input("Trading Symbol (Format: EXCHANGE:SYMBOL)", value="NSE:NIFTY 50")
                with col_btn:
                    st.write("")
                    st.write("")
                    fetch_quote = st.button("Fetch LTP")

                if fetch_quote:
                    try:
                        quote = kite.ltp(symbol)
                        st.write(quote)
                    except Exception as e:
                        st.error(f"Failed to fetch quote: {e}")
    else:
        st.info("👈 Please enter your API Key and API Secret above to unlock authentication and API testing.")
