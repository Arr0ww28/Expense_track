import streamlit as st
import pandas as pd
import datetime
import os
import ollama
import yfinance as yf
import re
import requests
import urllib3
from curl_cffi import requests


# Configuration
EXCEL_FILE = "finance_tracker.xlsx"
LLM_MODEL = "qwen3-coder:latest"


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        # Try the exact user query first
        results = fetch_results(query)
        
        # Fallback: If empty, try searching just the last word (e.g., 'Goldbees' from 'Nippon Goldbees')
        if not results and " " in query:
            fallback_query = query.split()[-1] 
            results = fetch_results(fallback_query)
            
        return results, None
    except Exception as e:
        return [], str(e)
    
#live stock rates
def init_watchlist():
    if 'watchlist' not in st.session_state:
        st.session_state.watchlist = ["RELIANCE.NS", "TCS.NS", "AAPL"]

def get_live_stock_data(ticker_symbol):
    try:
        # Create a curl_cffi session that ignores SSL verification
        session = requests.Session(impersonate="chrome")
        session.verify = False
        
        # Pass the curl_cffi session to yfinance
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
        print(f"Error fetching {ticker_symbol}: {e}")
        return None

# Call initialization
init_watchlist()
    
# --- STATE & EXCEL MANAGEMENT ---
CUSTOM_CATEGORIES_FILE = "custom_categories.json"

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
            pd.DataFrame(columns=["Sr No", "Date", "Expense Type", "Amount", "Notes"]).to_excel(writer, sheet_name="EXPENSES", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Share/Fund Name", "Quantity", "Total Amount Invested"]).to_excel(writer, sheet_name="SHARES and FUNDS", index=False)
    
    load_custom_categories()
    
    if 'data' not in st.session_state:
        st.session_state.data = {
            "MAIN SALARY": pd.read_excel(EXCEL_FILE, sheet_name="MAIN SALARY"),
            "EXPENSES": pd.read_excel(EXCEL_FILE, sheet_name="EXPENSES"),
            "SHARES and FUNDS": pd.read_excel(EXCEL_FILE, sheet_name="SHARES and FUNDS")
        }

        # --- FIX: Robust Date Cleaning ---
        for sheet in st.session_state.data:
            df = st.session_state.data[sheet]
            if "Date" in df.columns and not df.empty:
                # 'format=mixed' handles different strings, 'errors=coerce' prevents crashing on bad data
                df["Date"] = pd.to_datetime(df["Date"], errors='coerce', format='mixed').dt.strftime('%Y-%m-%d')
                # Fill any failed conversions with today's date so the app doesn't break
                df["Date"] = df["Date"].fillna(datetime.date.today().strftime("%Y-%m-%d"))
                st.session_state.data[sheet] = df
def save_all_to_excel():
    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="w") as writer:
            st.session_state.data["MAIN SALARY"].to_excel(writer, sheet_name="MAIN SALARY", index=False)
            st.session_state.data["EXPENSES"].to_excel(writer, sheet_name="EXPENSES", index=False)
            st.session_state.data["SHARES and FUNDS"].to_excel(writer, sheet_name="SHARES and FUNDS", index=False)
        return True, "Data synced to Excel successfully."
    except PermissionError:
        return False, "Permission Denied: Please close the Excel file to allow background saving. Data saved in memory."
    except Exception as e:
        return False, f"Error saving to Excel: {str(e)}"

def get_next_sr_no(df_name):
    df = st.session_state.data[df_name]
    return 1 if df.empty else int(df["Sr No"].max()) + 1

# --- AI INSIGHTS ---
def get_ai_insights():
    df_salary = st.session_state.data["MAIN SALARY"]
    df_exp = st.session_state.data["EXPENSES"]
    df_shares = st.session_state.data["SHARES and FUNDS"]
    
    total_salary = df_salary["Salary Credited"].sum()
    total_expenses = df_exp["Amount"].sum()
    total_investments = df_shares["Total Amount Invested"].sum()
    
    # Enhanced Context
    df_exp['Date'] = pd.to_datetime(df_exp['Date'])
    recent_expenses = df_exp.sort_values(by='Date', ascending=False).head(5)[['Date', 'Expense Type', 'Amount']].to_string(index=False) if not df_exp.empty else "No recent expenses."
    
    current_month = datetime.date.today().month
    this_month_exp = df_exp[df_exp['Date'].dt.month == current_month]['Amount'].sum() if not df_exp.empty else 0
    
    top_categories = df_exp.groupby('Expense Type')['Amount'].sum().sort_values(ascending=False).head(3).to_string() if not df_exp.empty else "None"
    
    prompt = f"""You are an expert financial advisor. Analyze the following financial data and provide direct, actionable advice. 

### FINANCIAL DATA (All values in ₹ INR)
* **Total Salary:** ₹{total_salary}
* **Total Expenses:** ₹{total_expenses}
* **Total Investments:** ₹{total_investments}
* **Expenses This Month:** ₹{this_month_exp}

### TOP 3 EXPENSE CATEGORIES
{top_categories}

### 5 MOST RECENT TRANSACTIONS
{recent_expenses}

### OUTPUT INSTRUCTIONS
1.Analyze the user's financial data and provide insights on their spending habits and how they can optimize their finances.
2.Advice on how to save better with their given salary and expenses.
3.Help them invest wisely based on the current market trends and their financial behavior.
4. Use **bold text** to highlight key metrics, specific categories, or critical actions to ensure high readability.
5. Structure it like a finance report for easy understanding. Avoid generic advice and focus on personalized, actionable insights based on the provided data.
6. Ensure the report is easy to read and understand, even for someone without a financial background. Use simple language and avoid jargon.
    """
    
    try:
        response = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}])
        return response['message']['content']
    except Exception as e:
        return f"Error connecting to Ollama: {str(e)}. Ensure Ollama is running."

# --- UI LAYOUT ---
st.set_page_config(page_title="AI Finance Tracker", layout="wide")
init_data()

st.title("Salary & Expense Tracker")

tab1, tab2, tab3, tab5, tab6 = st.tabs(["Main Salary", "Expenses", "Shares & Funds", "Manage Data (CRUD)", "Dashboard & AI"])

system_date = datetime.date.today().strftime("%Y-%m-%d")

with st.sidebar:
    # Using st.popover creates a clickable button (like an icon) that expands to show content
        st.info("Session-only data fetched via Yahoo Finance.")
        
        search_query = st.text_input("Search Company/Fund")
        if st.button("Search"):
            if search_query:
                results, error_msg = search_ticker(search_query)
                if error_msg:
                    st.error(f"Error: {error_msg}")
                elif not results:
                    st.warning("No results.")
                else:
                    st.session_state.search_results = results

        if 'search_results' in st.session_state and st.session_state.search_results:
            selected_option = st.selectbox("Select ticker:", st.session_state.search_results)
            if st.button("Add to Watchlist"):
                import re
                match = re.search(r'\((.*?)\)', selected_option)
                if match:
                    extracted_ticker = match.group(1)
                    if extracted_ticker not in st.session_state.watchlist:
                        st.session_state.watchlist.append(extracted_ticker)
                        st.session_state.search_results = [] 
                        st.rerun()
                    else:
                        st.warning("Already in watchlist.")
                        
        st.divider()

        if st.button("Refresh Prices", use_container_width=True):
            st.rerun()

        if not st.session_state.watchlist:
            st.write("Watchlist is empty.")
        else:
            for ticker in st.session_state.watchlist:
                container = st.container(border=True)
                with container:
                    st.write(f"**{ticker}**")
                    data = get_live_stock_data(ticker)
                    
                    if data:
                        current_price, change, pct_change = data
                        st.metric(
                            label="Price",
                            value=f"{current_price:.2f}",
                            delta=f"{change:.2f} ({pct_change:.2f}%)"
                        )
                    else:
                        st.error("Data unavailable")
                    
                    if st.button("Remove", key=f"remove_{ticker}", use_container_width=True):
                        st.session_state.watchlist.remove(ticker)
                        st.rerun()

# TAB 1: MAIN SALARY
with tab1:
    st.subheader("Credit Salary")
    salary_amount = st.number_input("Salary Credited Amount", min_value=0.0, format="%.2f", step=100.0)
    
    if st.button("Add Salary"):
        new_row = {"Sr No": get_next_sr_no("MAIN SALARY"), "Date": system_date, "Salary Credited": salary_amount}
        st.session_state.data["MAIN SALARY"] = pd.concat([st.session_state.data["MAIN SALARY"], pd.DataFrame([new_row])], ignore_index=True)
        success, msg = save_all_to_excel()
        st.success("Added! " + msg) if success else st.warning("Added! " + msg)

# TAB 2: EXPENSES
with tab2:
    st.subheader("Log an Expense")
    use_custom_date = st.checkbox("Use custom date")
    
    # Ensure raw_date is always a date object
    if use_custom_date:
        raw_date = st.date_input("Expense Date", value=datetime.date.today())
    else:
        raw_date = datetime.date.today()
    
    # Format strictly as YYYY-MM-DD string
    exp_date = raw_date.strftime("%Y-%m-%d")
    
    expense_categories = ["Rent", "Groceries", "Utilities", "Transport", "Entertainment"] + st.session_state.custom_categories + ["Custom..."]
    exp_type = st.selectbox("Type of Expense", expense_categories)
    
    if exp_type == "Custom...":
        exp_type = st.text_input("Enter Custom Category")
        
    exp_amount = st.number_input("Amount Spent", min_value=0.0, format="%.2f", step=10.0)
    exp_notes = st.text_area("Notes (optional)", placeholder="Add any notes about this expense...")
    
    if st.button("Add Expense"):
        if exp_type:
            if exp_type not in ["Rent", "Groceries", "Utilities", "Transport", "Entertainment"] and exp_type not in st.session_state.custom_categories:
                st.session_state.custom_categories.append(exp_type)
                save_custom_categories()
            
            # Create new row with formatted date string
            new_row = {"Sr No": get_next_sr_no("EXPENSES"), "Date": exp_date, "Expense Type": exp_type, "Amount": exp_amount, "Notes": exp_notes}
            st.session_state.data["EXPENSES"] = pd.concat([st.session_state.data["EXPENSES"], pd.DataFrame([new_row])], ignore_index=True)
            
            # Force the entire column to string format to prevent Excel/Pandas from adding 00:00:00
            st.session_state.data["EXPENSES"]["Date"] = st.session_state.data["EXPENSES"]["Date"].astype(str)
            
            success, msg = save_all_to_excel()
            st.success("Logged! " + msg) if success else st.warning("Logged! " + msg)
        else:
            st.warning("Please specify a valid expense category.")

# TAB 3: SHARES & FUNDS
with tab3:
    st.subheader("Log Investment")
    
    # Get unique names already used in the "SHARES and FUNDS" sheet
    existing_investments = []
    if not st.session_state.data["SHARES and FUNDS"].empty:
        existing_investments = sorted(st.session_state.data["SHARES and FUNDS"]["Share/Fund Name"].unique().tolist())
    
    # Create the selection list
    investment_options = existing_investments + ["New..."]
    
    # Use a selectbox for auto-suggestions
    selected_investment = st.selectbox("Select or Search Investment", investment_options, index=len(investment_options)-1 if not existing_investments else 0)
    
    # If "New..." is selected, show a text input
    if selected_investment == "New...":
        share_name = st.text_input("Enter New Share / Fund Name")
    else:
        share_name = selected_investment

    share_qty = st.number_input("Quantity", min_value=0.0, format="%.4f", step=1.0)
    share_amount = st.number_input("Total Amount Invested", min_value=0.0, format="%.2f", step=50.0)
    
    if st.button("Add Investment"):
        if share_name:
            # Format current date strictly
            clean_date = datetime.date.today().strftime("%Y-%m-%d")

            new_row = {
                "Sr No": get_next_sr_no("SHARES and FUNDS"), 
                "Date": clean_date, 
                "Share/Fund Name": share_name, 
                "Quantity": share_qty, 
                "Total Amount Invested": share_amount
            }
            st.session_state.data["SHARES and FUNDS"] = pd.concat([st.session_state.data["SHARES and FUNDS"], pd.DataFrame([new_row])], ignore_index=True)

            # Prevent conversion back to datetime objects
            st.session_state.data["SHARES and FUNDS"]["Date"] = st.session_state.data["SHARES and FUNDS"]["Date"].astype(str)

            success, msg = save_all_to_excel()
            st.success("Invested! " + msg) if success else st.warning("Invested! " + msg)


# TAB 5: MANAGE DATA (CRUD)
with tab5:
    st.subheader("Edit or Delete Records")
    st.info("Edit cells directly or select rows to delete. Click 'Save Changes' to update the Excel file.")
    
    sheet_choice = st.selectbox("Select Sheet to Edit", ["MAIN SALARY", "EXPENSES", "SHARES and FUNDS"])
    
    edited_df = edited_df = st.data_editor(st.session_state.data[sheet_choice], num_rows="dynamic", width="stretch")
    
    if st.button("Save Changes to Excel"):
        st.session_state.data[sheet_choice] = edited_df
        success, msg = save_all_to_excel()
        st.success(msg) if success else st.error(msg)

# TAB 5: DASHBOARD & AI
with tab6:
    st.subheader("Financial Overview")
    
    total_salary = st.session_state.data["MAIN SALARY"]["Salary Credited"].sum()
    total_exp = st.session_state.data["EXPENSES"]["Amount"].sum()
    total_inv = st.session_state.data["SHARES and FUNDS"]["Total Amount Invested"].sum()
    balance = total_salary - total_exp - total_inv
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Salary", f"{total_salary:,.2f}")
    col2.metric("Total Expenses", f"{total_exp:,.2f}")
    col3.metric("Total Investments", f"{total_inv:,.2f}")
    col4.metric("Available Balance", f"{balance:,.2f}")
    
    st.divider()
    
    if not st.session_state.data["EXPENSES"].empty:
        st.write("**Expense Distribution**")
        cat_exp = st.session_state.data["EXPENSES"].groupby("Expense Type")["Amount"].sum().reset_index()
        st.bar_chart(cat_exp.set_index("Expense Type"))
        
    st.divider()
    
    st.subheader("Qwen3 Coder AI Insights")
    if st.button("Analyze My Finances"):
        with st.spinner("Analyzing recent trends..."):
            insights = get_ai_insights()
            st.markdown(insights)