import streamlit as st
import pandas as pd
import datetime
import os
import ollama
import yfinance as yf
import re
import requests
import urllib3
from curl_cffi import requests as curl_requests
import plotly.express as px
import plotly.graph_objects as go

# Configuration
EXCEL_FILE = "finance_tracker.xlsx"
LLM_MODEL = "qwen3-coder:latest"
CUSTOM_CATEGORIES_FILE = "custom_categories.json"
GOALS_FILE ="goals.json"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- UTILITIES ---
def search_ticker(query):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'} 
    def fetch_results(search_term):
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={search_term}"
        response = requests.get(url, headers=headers, verify=False)
        response.raise_for_status()
        data = response.json()
        results = []
        for quote in data.get('quotes', [])[:10]:
            symbol = quote.get('symbol', '')
            if symbol:
                results.append(f"{quote.get('shortname', 'Unknown')} ({symbol}) - {quote.get('exchange', 'Unknown')}")
        return results
    try:
        results = fetch_results(query)
        if not results and " " in query:
            fallback_query = query.split()[-1] 
            results = fetch_results(fallback_query)
        return results, None
    except Exception as e:
        return [], str(e)

@st.cache_data(ttl=300, show_spinner=False)
def get_live_stock_data(ticker_symbol):
    try:
        session = curl_requests.Session(impersonate="chrome")
        session.verify = False
        ticker = yf.Ticker(ticker_symbol, session=session)
        todays_data = ticker.history(period='5d')
        if len(todays_data) < 2:
            return None
        current_price = todays_data['Close'].iloc[-1]
        prev_close = todays_data['Close'].iloc[-2]
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100
        return current_price, change, pct_change
    except Exception as e:
        return None

# --- DATA MANAGEMENT ---
def load_goals():
    if 'goals' not in st.session_state:
        if os.path.exists(GOALS_FILE):
            import json
            with open(GOALS_FILE, 'r') as f:
                st.session_state.goals = json.load(f)
        else:
            # Initialize with an example goal
            st.session_state.goals = [{
                "id": "init_1", 
                "title": "Honda CB500", 
                "desc": "Motorcycle purchase fund", 
                "target": 500000.0, 
                "include_investments": False,
                "target_date": (datetime.date.today() + datetime.timedelta(days=365)).strftime("%Y-%m-%d")
            }]
            save_goals()

def save_goals():
    import json
    with open(GOALS_FILE, 'w') as f:
        json.dump(st.session_state.goals, f)
def undo_last_entry(sheet_name):
    if not st.session_state.data[sheet_name].empty:
        st.session_state.data[sheet_name] = st.session_state.data[sheet_name].iloc[:-1]
        save_all_to_excel()
        st.success(f"Last entry removed from {sheet_name}!")
        st.rerun()
def add_new_category(new_cat):
    if new_cat and new_cat not in st.session_state.custom_categories:
        st.session_state.custom_categories.append(new_cat)
        save_custom_categories()
def load_custom_categories():
    if 'custom_categories' not in st.session_state:
        if os.path.exists(CUSTOM_CATEGORIES_FILE):
            import json
            with open(CUSTOM_CATEGORIES_FILE, 'r') as f:
                st.session_state.custom_categories = json.load(f)
        else:
            st.session_state.custom_categories = []

def save_custom_categories():
    import json
    with open(CUSTOM_CATEGORIES_FILE, 'w') as f:
        json.dump(st.session_state.custom_categories, f)

def init_data():
    if not os.path.exists(EXCEL_FILE):
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            pd.DataFrame(columns=["Sr No", "Date", "Salary Credited"]).to_excel(writer, sheet_name="MAIN SALARY", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Income Type", "Amount", "Notes"]).to_excel(writer, sheet_name="OTHER INCOME", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Expense Type", "Amount", "Notes"]).to_excel(writer, sheet_name="EXPENSES", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Share/Fund Name", "Quantity", "Total Amount Invested"]).to_excel(writer, sheet_name="SHARES and FUNDS", index=False)
    
    load_custom_categories()
    load_goals()
    
    if 'data' not in st.session_state:
        st.session_state.data = {
            "MAIN SALARY": pd.read_excel(EXCEL_FILE, sheet_name="MAIN SALARY"),
            "OTHER INCOME": pd.read_excel(EXCEL_FILE, sheet_name="OTHER INCOME"),
            "EXPENSES": pd.read_excel(EXCEL_FILE, sheet_name="EXPENSES"),
            "SHARES and FUNDS": pd.read_excel(EXCEL_FILE, sheet_name="SHARES and FUNDS")
        }

        for sheet in st.session_state.data:
            df = st.session_state.data[sheet]
            if "Date" in df.columns and not df.empty:
                df["Date"] = pd.to_datetime(df["Date"], errors='coerce', format='mixed').dt.strftime('%Y-%m-%d')
                df["Date"] = df["Date"].fillna(datetime.date.today().strftime("%Y-%m-%d"))
                st.session_state.data[sheet] = df

def save_all_to_excel():
    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="w") as writer:
            for sheet_name, df in st.session_state.data.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        return True, "Data synced to Excel successfully."
    except PermissionError:
        return False, "Permission Denied: Please close the Excel file."
    except Exception as e:
        return False, f"Error: {str(e)}"

def get_next_sr_no(df_name):
    df = st.session_state.data[df_name]
    return 1 if df.empty else int(df["Sr No"].max()) + 1

# --- AI ---
def get_ai_insights():
    df_salary = st.session_state.data["MAIN SALARY"]
    df_other_inc = st.session_state.data["OTHER INCOME"]
    df_exp = st.session_state.data["EXPENSES"]
    df_shares = st.session_state.data["SHARES and FUNDS"]
    
    total_revenue = df_salary["Salary Credited"].sum() + df_other_inc["Amount"].sum()
    total_expenses = df_exp["Amount"].sum()
    total_investments = df_shares["Total Amount Invested"].sum()
    
    prompt = f"""You are an expert financial advisor.
    * **Total Revenue (Salary + Other):** ₹{total_revenue}
    * **Total Expenses:** ₹{total_expenses}
    * **Total Investments:** ₹{total_investments}
    Analyze spending, suggest savings, and advise on investments based on these totals."""
    
    try:
        response = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}])
        return response['message']['content']
    except Exception as e:
        return f"Error connecting to Ollama: {str(e)}"

# --- UI ---
st.set_page_config(page_title="AI Finance Tracker", layout="wide")
init_data()
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = ["RELIANCE.NS", "TCS.NS", "AAPL"]

st.title("Salary & Expense Tracker")

tab1, tab_inc, tab2, tab3, tab_goals,tab5, tab6 = st.tabs(["Main Salary", "Other Income", "Expenses", "Shares & Funds", "Goals","Manage Data", "Dashboard & AI"])
system_date = datetime.date.today().strftime("%Y-%m-%d")

# Sidebar
with st.sidebar:
    st.info("Yahoo Finance Watchlist")
    search_query = st.text_input("Search Company/Fund")
    if st.button("Search"):
        if search_query:
            results, error_msg = search_ticker(search_query)
            if not error_msg: st.session_state.search_results = results

    if 'search_results' in st.session_state and st.session_state.search_results:
        selected_option = st.selectbox("Select ticker:", st.session_state.search_results)
        if st.button("Add to Watchlist"):
            match = re.search(r'\((.*?)\)', selected_option)
            if match:
                st.session_state.watchlist.append(match.group(1))
                st.rerun()

    st.divider()
    for ticker in st.session_state.watchlist:
        if st.button("Refresh prices", key=f"refresh_{ticker}"):
            st.rerun()
        with st.container(border=True):
            st.write(f"**{ticker}**")
            data = get_live_stock_data(ticker)
            if data:
                current_price, change, pct_change = data
                st.metric(label="Price", value=f"{current_price:.2f}", delta=f"{change:.2f} ({pct_change:.2f}%)")
            if st.button("Remove", key=f"rm_{ticker}"):
                st.session_state.watchlist.remove(ticker)
                st.rerun()

# TAB 1: SALARY
with tab1:
    st.subheader("Credit Salary")
    salary_amount = st.number_input("Amount", min_value=0.0, step=100.0)
    if st.button("Add Salary"):
        new_row = {"Sr No": get_next_sr_no("MAIN SALARY"), "Date": system_date, "Salary Credited": salary_amount}
        st.session_state.data["MAIN SALARY"] = pd.concat([st.session_state.data["MAIN SALARY"], pd.DataFrame([new_row])], ignore_index=True)
        save_all_to_excel()
        st.success("Added!")

# TAB: OTHER INCOME
with tab_inc:
    st.subheader("Log Other Income")
    inc_date = st.date_input("Date", value=datetime.date.today(), key="inc_dt").strftime("%Y-%m-%d")
    
    # Combine default income types with your saved custom categories
    base_inc_cats = ["Bonus", "Freelance", "Dividends", "Gift"]
    inc_cats = sorted(list(set(base_inc_cats + st.session_state.custom_categories))) + ["Custom..."]
    
    inc_type = st.selectbox("Type", inc_cats, key="inc_tp")
    custom_inc_name = ""
    if inc_type == "Custom...":
        custom_inc_name = st.text_input("Category Name", key="custom_inc_input")

    inc_amt = st.number_input("Amount", min_value=0.0, step=10.0, key="inc_val")
    inc_notes = st.text_area("Notes", key="inc_nt")

    if st.button("Add Income"):
        final_type = custom_inc_name if inc_type == "Custom..." else inc_type
        if inc_type == "Custom..." and custom_inc_name:
            add_new_category(custom_inc_name)
        
        new_row = {"Sr No": get_next_sr_no("OTHER INCOME"), "Date": inc_date, "Income Type": final_type, "Amount": inc_amt, "Notes": inc_notes}
        st.session_state.data["OTHER INCOME"] = pd.concat([st.session_state.data["OTHER INCOME"], pd.DataFrame([new_row])], ignore_index=True)
        save_all_to_excel()
        st.success(f"Income logged under {final_type}!")
        st.rerun()

# TAB 2: EXPENSES
with tab2:
    st.subheader("Log Expense")
    exp_date = st.date_input("Date", value=datetime.date.today(), key="exp_dt").strftime("%Y-%m-%d")
    
    # Combine default expense types with your saved custom categories
    base_exp_cats = ["Rent", "Groceries", "Utilities", "Transport", "Entertainment"]
    exp_cats = sorted(list(set(base_exp_cats + st.session_state.custom_categories))) + ["Custom..."]
    
    exp_type = st.selectbox("Type", exp_cats, key="exp_select")
    custom_exp_name = ""
    if exp_type == "Custom...":
        custom_exp_name = st.text_input("New Category", key="custom_exp_input")
        
    exp_amt = st.number_input("Amount", min_value=0.0, step=10.0, key="exp_amt_input")
    exp_notes = st.text_area("Notes", key="exp_notes_input")

    if st.button("Add Expense"):
        final_type = custom_exp_name if exp_type == "Custom..." else exp_type
        if exp_type == "Custom..." and custom_exp_name:
            add_new_category(custom_exp_name)
            
        new_row = {"Sr No": get_next_sr_no("EXPENSES"), "Date": exp_date, "Expense Type": final_type, "Amount": exp_amt, "Notes": exp_notes}
        st.session_state.data["EXPENSES"] = pd.concat([st.session_state.data["EXPENSES"], pd.DataFrame([new_row])], ignore_index=True)
        save_all_to_excel()
        st.success(f"Expense logged under {final_type}!")
        st.rerun()
    # Add to bottom of EXPENSES tab
    if not st.session_state.data["EXPENSES"].empty:
        if st.button("↩️ Undo Last Expense", key="undo_exp"):
            undo_last_entry("EXPENSES")

# TAB 3: INVESTMENTS
with tab3:
    st.subheader("Log Investment")
    inv_name = st.text_input("Share/Fund Name")
    inv_qty = st.number_input("Quantity", min_value=0.0, step=1.0)
    inv_amt = st.number_input("Total Invested", min_value=0.0, step=50.0)
    if st.button("Add Investment"):
        new_row = {"Sr No": get_next_sr_no("SHARES and FUNDS"), "Date": system_date, "Share/Fund Name": inv_name, "Quantity": inv_qty, "Total Amount Invested": inv_amt}
        st.session_state.data["SHARES and FUNDS"] = pd.concat([st.session_state.data["SHARES and FUNDS"], pd.DataFrame([new_row])], ignore_index=True)
        save_all_to_excel()
        st.success("Investment added!")

# TAB 4: GOALS
with tab_goals:
    st.subheader("Financial Goals Tracker")

    global_income = st.session_state.data["MAIN SALARY"]["Salary Credited"].sum() + st.session_state.data["OTHER INCOME"]["Amount"].sum()
    global_exp = st.session_state.data["EXPENSES"]["Amount"].sum()
    global_inv = st.session_state.data["SHARES and FUNDS"]["Total Amount Invested"].sum()
    
    total_liquid = (global_income - global_exp) - global_inv
    total_wealth = global_income - global_exp 

    # --- CREATE ---
    with st.expander("➕ Create New Goal"):
        with st.form("new_goal_form"):
            new_title = st.text_input("Goal Title")
            new_desc = st.text_input("Description")
            
            col_t1, col_t2 = st.columns(2)
            new_target = col_t1.number_input("Target Amount (₹)", min_value=1.0, step=1000.0)
            new_date = col_t2.date_input("Target Date", min_value=datetime.date.today())
            
            include_inv = st.checkbox("Include Investments in Progress?")
            
            if st.form_submit_button("Save Goal"):
                import uuid
                new_goal = {
                    "id": str(uuid.uuid4()),
                    "title": new_title,
                    "desc": new_desc,
                    "target": new_target,
                    "include_investments": include_inv,
                    "target_date": new_date.strftime("%Y-%m-%d")
                }
                st.session_state.goals.append(new_goal)
                save_goals()
                st.success("Goal Added!")
                st.rerun()

    st.divider()

    # --- READ, UPDATE, DELETE ---
    if not st.session_state.goals:
        st.info("No active goals. Create one above!")
    
    today = datetime.date.today()
    
    for i, goal in enumerate(st.session_state.goals):
        with st.container(border=True):
            col_info, col_actions = st.columns([3, 1])
            
            with col_info:
                st.markdown(f"### {goal['title']}")
                if goal['desc']: st.caption(f"{goal['desc']}")
                
                # Progress Math
                current_funds = total_wealth if goal.get("include_investments", False) else total_liquid
                current_funds = max(0, current_funds)
                progress_val = min(current_funds / goal['target'], 1.0) if goal['target'] > 0 else 0.0
                st.progress(progress_val)
                
                # Date & Monthly Savings Math
                target_dt = datetime.datetime.strptime(goal.get('target_date', (today + datetime.timedelta(days=365)).strftime("%Y-%m-%d")), "%Y-%m-%d").date()
                months_remaining = (target_dt.year - today.year) * 12 + target_dt.month - today.month
                months_remaining = max(1, months_remaining) # Prevent division by zero
                
                remaining_amt = max(0, goal['target'] - current_funds)
                monthly_req = remaining_amt / months_remaining
                
                st.metric(label=f"Progress ({'Net Worth' if goal.get('include_investments', False) else 'Liquid Cash'})", 
                          value=f"₹{current_funds:,.2f} / ₹{goal['target']:,.2f}", 
                          delta=f"{(progress_val*100):.1f}% Funded",
                          delta_color="normal" if progress_val < 1.0 else "off")
                
                if progress_val >= 1.0:
                    st.success("🎉 Target Reached!")
                else:
                    st.info(f"📅 Target: **{target_dt.strftime('%b %Y')}** ({months_remaining} months left) | 💡 Required: **₹{monthly_req:,.2f} / month**")

            with col_actions:
                with st.expander("Edit"):
                    edit_title = st.text_input("Title", value=goal['title'], key=f"title_{i}_{goal['id']}")
                    edit_desc = st.text_input("Desc", value=goal['desc'], key=f"desc_{i}_{goal['id']}")
                    edit_target = st.number_input("Target", value=float(goal['target']), step=1000.0, key=f"target_{i}_{goal['id']}")
                    
                    fallback_dt = datetime.datetime.strptime(goal.get('target_date', today.strftime("%Y-%m-%d")), "%Y-%m-%d").date()
                    edit_date = st.date_input("Date", value=fallback_dt, key=f"date_{i}_{goal['id']}")
                    
                    edit_inc_inv = st.checkbox("Include Inv.", value=goal.get('include_investments', False), key=f"inc_{i}_{goal['id']}")
                    
                    c_save, c_del = st.columns(2)
                    if c_save.button("Save", key=f"save_{i}_{goal['id']}"):
                        st.session_state.goals[i].update({
                            "title": edit_title, "desc": edit_desc, 
                            "target": edit_target, "include_investments": edit_inc_inv,
                            "target_date": edit_date.strftime("%Y-%m-%d")
                        })
                        save_goals()
                        st.rerun()
                        
                    if c_del.button("Del", type="primary", key=f"del_{i}_{goal['id']}"):
                        st.session_state.goals.pop(i)
                        save_goals()
                        st.rerun()

# TAB 5: MANAGE
with tab5:
    sheet_choice = st.selectbox("Edit Sheet", ["MAIN SALARY", "OTHER INCOME", "EXPENSES", "SHARES and FUNDS"])
    edited_df = st.data_editor(st.session_state.data[sheet_choice], num_rows="dynamic", width="stretch")
    if st.button("Save Changes"):
        st.session_state.data[sheet_choice] = edited_df
        success, msg = save_all_to_excel()
        st.success(msg) if success else st.error(msg)

with tab6:
    st.subheader("Financial Overview")
    
    # --- TIME FILTER ---
    filter_option = st.radio("Timeframe", ["All Time", "Current Month"], horizontal=True)
    
    def filter_df(df, date_col="Date"):
        if filter_option == "All Time" or df.empty: return df
        df[date_col] = pd.to_datetime(df[date_col])
        current_month = datetime.date.today().month
        current_year = datetime.date.today().year
        return df[(df[date_col].dt.month == current_month) & (df[date_col].dt.year == current_year)]

    df_salary = filter_df(st.session_state.data["MAIN SALARY"].copy())
    df_other = filter_df(st.session_state.data["OTHER INCOME"].copy())
    df_exp = filter_df(st.session_state.data["EXPENSES"].copy())
    df_inv = filter_df(st.session_state.data["SHARES and FUNDS"].copy())

    # 1. Calculations (Using filtered data)
    total_salary = df_salary["Salary Credited"].sum()
    total_other_income = df_other["Amount"].sum()
    total_income = total_salary + total_other_income
    total_expenses = df_exp["Amount"].sum()
    total_investments = df_inv["Total Amount Invested"].sum()
    
    net_worth = total_income - total_expenses
    liquid_balance = net_worth - total_investments
    savings_rate = ((total_income - total_expenses) / total_income * 100) if total_income > 0 else 0

# --- UPDATED METRICS SECTION ---
    
    # 2. Key Metrics Display
    st.subheader("Financial Overview")
    
    # Row 1: The Three Requested Fields
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Worth", f"₹{net_worth:,.2f}", help="Total Income - Total Expenses")
    m2.metric("Total Income", f"₹{total_income:,.2f}")
    m3.metric("Total Salary", f"₹{total_salary:,.2f}")
    m4.metric("Savings Rate", f"{savings_rate:.1f}%")
    # Row 2: Secondary Metrics
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Other Income", f"₹{total_other_income:,.2f}")
    c2.metric("Total Expenses", f"₹{total_expenses:,.2f}", delta=f"{(total_expenses/total_income*100) if total_income > 0 else 0:.1f}% of Income", delta_color="inverse")
    c3.metric("Total Invested", f"₹{total_investments:,.2f}")
    c4.metric("Liquid Balance", f"₹{liquid_balance:,.2f}", help="Cash remaining after expenses and investments")

    st.divider()

    # --- VISUALIZATIONS (Updated for Salary vs Other Income) ---
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("Income Distribution")
        income_df = pd.DataFrame({
            "Source": ["Main Salary", "Other Income"],
            "Amount": [total_salary, total_other_income]
        })
        fig_inc = px.pie(income_df, values='Amount', names='Source', hole=0.4,
                         color_discrete_sequence=["#2ecc71", "#27ae60"])
        st.plotly_chart(fig_inc, use_container_width=True)

    with col_chart2:
        st.subheader("Expense Breakdown")
        if not st.session_state.data["EXPENSES"].empty:
            df_exp_grouped = st.session_state.data["EXPENSES"].groupby("Expense Type")["Amount"].sum().reset_index()
            fig_pie = px.pie(df_exp_grouped, values='Amount', names='Expense Type', hole=0.4,
                             color_discrete_sequence=px.colors.sequential.RdBu)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No expense data.")

    st.subheader("Investment Portfolio")
    if not st.session_state.data["SHARES and FUNDS"].empty:
        df_inv_grouped = st.session_state.data["SHARES and FUNDS"].groupby("Share/Fund Name")["Total Amount Invested"].sum().reset_index()
        fig_inv = px.bar(df_inv_grouped, x='Share/Fund Name', y='Total Amount Invested', 
                         color='Total Amount Invested', labels={'Total Amount Invested': 'Amount (₹)'})
        st.plotly_chart(fig_inv, use_container_width=True)

    st.divider()

    # --- ROBUST AI INSIGHTS ---
    if st.button("Generate Professional Financial Report"):
        with st.spinner("Analyzing financial patterns..."):
            # Prepare data summaries for the AI
            exp_summary = st.session_state.data["EXPENSES"].groupby("Expense Type")["Amount"].sum().to_dict()
            inv_summary = st.session_state.data["SHARES and FUNDS"].groupby("Share/Fund Name")["Total Amount Invested"].sum().to_dict()
            
            robust_prompt = f"""
            Act as a Senior Certified Financial Planner (CFP). Analyze the following personal financial data and provide a strictly formatted report.

            ### DATA SUMMARY:
            - Total Monthly Income: ₹{total_income}
            - Total Expenses: ₹{total_expenses}
            - Total Invested: ₹{total_investments}
            - Current Liquidity: ₹{liquid_balance}
            - Expense Categories: {exp_summary}
            - Investment Portfolio: {inv_summary}

            ### REPORT REQUIREMENTS:
            1. **Executive Summary**: A 2-sentence overview of the current financial health.
            2. **Spending Analysis**: Identify the top 3 expense categories. Comment on the 'Savings Rate' (calculated as (Income - Expenses)/Income).
            3. **The 50/30/20 Rule Check**: Compare current spending against this rule (50% Needs, 30% Wants, 20% Savings/Investments).
            4. **Optimization Strategy**: Give 3 specific, actionable tips to reduce the highest expense categories found in the data.
            5. **Investment Critique**: Analyze the current portfolio distribution. Suggest if there's an over-concentration in one asset.
            6. **Action Plan**: Provide a 'Next 30 Days' checklist to increase the net balance.

            Use Markdown for formatting. Be direct, professional, and data-driven.
            """
            
            try:
                response = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": robust_prompt}])
                st.markdown("---")
                st.markdown(response['message']['content'])
            except Exception as e:
                st.error(f"AI Analysis Failed: {str(e)}")