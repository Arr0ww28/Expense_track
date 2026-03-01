import streamlit as st
import pandas as pd
import datetime
import os
import ollama

# Configuration
EXCEL_FILE = "finance_tracker.xlsx"
LLM_MODEL = "qwen3-coder:latest"

# --- EXCEL INITIALIZATION ---
def init_excel():
    if not os.path.exists(EXCEL_FILE):
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            pd.DataFrame(columns=["Sr No", "Date", "Salary Credited"]).to_excel(writer, sheet_name="MAIN SALARY", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Expense Type", "Amount"]).to_excel(writer, sheet_name="EXPENSES", index=False)
            pd.DataFrame(columns=["Sr No", "Date", "Share/Fund Name", "Quantity", "Total Amount Invested"]).to_excel(writer, sheet_name="SHARES and FUNDS", index=False)

def get_next_sr_no(df):
    return 1 if df.empty else int(df["Sr No"].max()) + 1

def load_data(sheet_name):
    return pd.read_excel(EXCEL_FILE, sheet_name=sheet_name)

def save_data(df, sheet_name):
    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)

# --- AI INSIGHTS ---
def get_ai_insights(salary_df, expenses_df, shares_df):
    total_salary = salary_df["Salary Credited"].sum()
    total_expenses = expenses_df["Amount"].sum()
    total_investments = shares_df["Total Amount Invested"].sum()
    
    expense_breakdown = expenses_df.groupby('Expense Type')['Amount'].sum().to_string() if not expenses_df.empty else "No expenses logged."
    
    prompt = f"""
    You are an expert financial advisor. Analyze the following financial summary:
    - Total Salary: {total_salary}
    - Total Expenses: {total_expenses}
    - Total Investments: {total_investments}
    
    Expense Categories Breakdown:
    {expense_breakdown}
    
    Provide 3-4 bullet points of brief, actionable financial advice. Keep the response direct and analytical.
    """
    
    try:
        response = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}])
        return response['message']['content']
    except Exception as e:
        return f"Error connecting to Ollama: {str(e)}. Ensure Ollama is running with the '{LLM_MODEL}' model locally."

# --- UI LAYOUT ---
st.set_page_config(page_title="AI Finance Tracker", layout="wide")
init_excel()

st.title("Salary & Expense Tracker")

tab1, tab2, tab3, tab4 = st.tabs(["Main Salary", "Expenses", "Shares & Funds", "Dashboard & AI Insights"])

system_date = datetime.date.today().strftime("%Y-%m-%d")

# TAB 1: MAIN SALARY
with tab1:
    st.subheader("Credit Salary")
    salary_amount = st.number_input("Salary Credited Amount", min_value=0.0, format="%.2f", step=100.0)
    
    if st.button("Add Salary"):
        df_salary = load_data("MAIN SALARY")
        new_row = {"Sr No": get_next_sr_no(df_salary), "Date": system_date, "Salary Credited": salary_amount}
        df_salary = pd.concat([df_salary, pd.DataFrame([new_row])], ignore_index=True)
        save_data(df_salary, "MAIN SALARY")
        st.success("Salary entry added successfully.")

# TAB 2: EXPENSES
with tab2:
    st.subheader("Log an Expense")
    use_custom_date = st.checkbox("Use custom date")
    exp_date = st.date_input("Expense Date").strftime("%Y-%m-%d") if use_custom_date else system_date
    
    expense_categories = ["Rent", "Groceries", "Utilities", "Transport", "Entertainment", "Custom..."]
    exp_type = st.selectbox("Type of Expense", expense_categories)
    
    if exp_type == "Custom...":
        exp_type = st.text_input("Enter Custom Category")
        
    exp_amount = st.number_input("Amount Spent", min_value=0.0, format="%.2f", step=10.0)
    
    if st.button("Add Expense"):
        if exp_type:
            df_exp = load_data("EXPENSES")
            new_row = {"Sr No": get_next_sr_no(df_exp), "Date": exp_date, "Expense Type": exp_type, "Amount": exp_amount}
            df_exp = pd.concat([df_exp, pd.DataFrame([new_row])], ignore_index=True)
            save_data(df_exp, "EXPENSES")
            st.success("Expense logged successfully.")
        else:
            st.warning("Please specify a valid expense category.")

# TAB 3: SHARES & FUNDS
with tab3:
    st.subheader("Log Investment")
    share_name = st.text_input("Share / Fund Name")
    share_qty = st.number_input("Quantity", min_value=0.0, format="%.4f", step=1.0)
    share_amount = st.number_input("Total Amount Invested", min_value=0.0, format="%.2f", step=50.0)
    
    if st.button("Add Investment"):
        if share_name:
            df_shares = load_data("SHARES and FUNDS")
            new_row = {"Sr No": get_next_sr_no(df_shares), "Date": system_date, "Share/Fund Name": share_name, "Quantity": share_qty, "Total Amount Invested": share_amount}
            df_shares = pd.concat([df_shares, pd.DataFrame([new_row])], ignore_index=True)
            save_data(df_shares, "SHARES and FUNDS")
            st.success("Investment logged successfully.")
        else:
            st.warning("Please enter the name of the share or fund.")

# TAB 4: DASHBOARD & AI
with tab4:
    st.subheader("Financial Overview")
    df_salary = load_data("MAIN SALARY")
    df_exp = load_data("EXPENSES")
    df_shares = load_data("SHARES and FUNDS")
    
    total_salary = df_salary["Salary Credited"].sum()
    total_exp = df_exp["Amount"].sum()
    total_inv = df_shares["Total Amount Invested"].sum()
    balance = total_salary - total_exp - total_inv
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Salary", f"{total_salary:,.2f}")
    col2.metric("Total Expenses", f"{total_exp:,.2f}")
    col3.metric("Total Investments", f"{total_inv:,.2f}")
    col4.metric("Available Balance", f"{balance:,.2f}")
    
    st.divider()
    
    if not df_exp.empty:
        st.write("**Expense Distribution**")
        cat_exp = df_exp.groupby("Expense Type")["Amount"].sum().reset_index()
        st.bar_chart(cat_exp.set_index("Expense Type"))
        
    st.divider()
    
    st.subheader("Qwen3 Coder AI Insights")
    if st.button("Analyze My Finances"):
        with st.spinner("Generating insights..."):
            insights = get_ai_insights(df_salary, df_exp, df_shares)
            st.markdown(insights)