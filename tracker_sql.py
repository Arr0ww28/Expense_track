import streamlit as st
import pandas as pd
import datetime
import os
import json
import uuid
import sqlite3
import ollama
import yfinance as yf
import re
import requests
import urllib3
from curl_cffi import requests as curl_requests
import plotly.express as px
import plotly.graph_objects as go
import io

# ── Configuration ──────────────────────────────────────────────────────────────
DB_FILE               = "finance_tracker.db"
LLM_MODEL             = "qwen3-coder:latest"
CUSTOM_CATS_INC_FILE  = "custom_categories_income.json"
CUSTOM_CATS_EXP_FILE  = "custom_categories_expense.json"
GOALS_FILE            = "goals.json"
BUDGETS_FILE          = "budgets.json"
RECURRING_FILE        = "recurring.json"
WATCHLIST_FILE        = "watchlist.json"

# Maps the app's display names → SQLite table names
TABLE_MAP = {
    "MAIN SALARY":      "main_salary",
    "OTHER INCOME":     "other_income",
    "EXPENSES":         "expenses",
    "SHARES and FUNDS": "investments",
    "BANK DEPOSITS":    "bank_deposits",
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── JSON helpers ───────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ── SQLite helpers ─────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist yet."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS main_salary (
            sr_no       INTEGER,
            date        TEXT,
            salary      REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS other_income (
            sr_no       INTEGER,
            date        TEXT,
            income_type TEXT,
            amount      REAL,
            notes       TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            sr_no        INTEGER,
            date         TEXT,
            expense_type TEXT,
            amount       REAL,
            notes        TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investments (
            sr_no    INTEGER,
            date     TEXT,
            name     TEXT,
            ticker   TEXT,
            quantity REAL,
            cost     REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_deposits (
            sr_no         INTEGER,
            date          TEXT,
            bank_name     TEXT,
            deposit_type  TEXT,
            amount        REAL,
            tenure_months INTEGER,
            interest_rate REAL,
            maturity_date TEXT,
            notes         TEXT
        )""")
    conn.commit()
    conn.close()

def load_all_data():
    """Read all five tables from SQLite into DataFrames with display column names."""
    conn = get_conn()
    
    def safe_read(query, col_map):
        """Safely read SQL, returning empty DataFrame with correct columns if table doesn't exist."""
        try:
            return pd.read_sql(query, conn)
        except Exception:
            return pd.DataFrame(columns=col_map.keys())
    
    data = {
        "MAIN SALARY": safe_read(
            "SELECT sr_no AS 'Sr No', date AS 'Date', salary AS 'Salary Credited' FROM main_salary",
            {"Sr No": None, "Date": None, "Salary Credited": None}),
        "OTHER INCOME": safe_read(
            "SELECT sr_no AS 'Sr No', date AS 'Date', income_type AS 'Income Type', "
            "amount AS 'Amount', notes AS 'Notes' FROM other_income",
            {"Sr No": None, "Date": None, "Income Type": None, "Amount": None, "Notes": None}),
        "EXPENSES": safe_read(
            "SELECT sr_no AS 'Sr No', date AS 'Date', expense_type AS 'Expense Type', "
            "amount AS 'Amount', notes AS 'Notes' FROM expenses",
            {"Sr No": None, "Date": None, "Expense Type": None, "Amount": None, "Notes": None}),
        "SHARES and FUNDS": safe_read(
            "SELECT sr_no AS 'Sr No', date AS 'Date', name AS 'Share/Fund Name', "
            "ticker AS 'Ticker', quantity AS 'Quantity', cost AS 'Total Amount Invested' "
            "FROM investments",
            {"Sr No": None, "Date": None, "Share/Fund Name": None, "Ticker": None, "Quantity": None, "Total Amount Invested": None}),
        "BANK DEPOSITS": safe_read(
            "SELECT sr_no AS 'Sr No', date AS 'Date', bank_name AS 'Bank Name', "
            "deposit_type AS 'Deposit Type', amount AS 'Amount', tenure_months AS 'Tenure (months)', "
            "interest_rate AS 'Interest Rate (%)', maturity_date AS 'Maturity Date', notes AS 'Notes' "
            "FROM bank_deposits",
            {"Sr No": None, "Date": None, "Bank Name": None, "Deposit Type": None, "Amount": None, 
             "Tenure (months)": None, "Interest Rate (%)": None, "Maturity Date": None, "Notes": None}),
    }
    conn.close()
    return data

# Maps display column names → SQLite column names for each table
_COL_MAP = {
    "MAIN SALARY": {
        "Sr No": "sr_no", "Date": "date", "Salary Credited": "salary",
    },
    "OTHER INCOME": {
        "Sr No": "sr_no", "Date": "date", "Income Type": "income_type",
        "Amount": "amount", "Notes": "notes",
    },
    "EXPENSES": {
        "Sr No": "sr_no", "Date": "date", "Expense Type": "expense_type",
        "Amount": "amount", "Notes": "notes",
    },
    "SHARES and FUNDS": {
        "Sr No": "sr_no", "Date": "date", "Share/Fund Name": "name",
        "Ticker": "ticker", "Quantity": "quantity", "Total Amount Invested": "cost",
    },
    "BANK DEPOSITS": {
        "Sr No": "sr_no", "Date": "date", "Bank Name": "bank_name",
        "Deposit Type": "deposit_type", "Amount": "amount", "Tenure (months)": "tenure_months",
        "Interest Rate (%)": "interest_rate", "Maturity Date": "maturity_date", "Notes": "notes",
    },
}

def _df_to_db(sheet, df):
    """Write a full DataFrame back to the corresponding SQLite table (replace)."""
    col_map   = _COL_MAP[sheet]
    table     = TABLE_MAP[sheet]
    # Only rename columns that exist in the dataframe
    rename    = {k: v for k, v in col_map.items() if k in df.columns}
    df_db     = df.rename(columns=rename)
    conn      = get_conn()
    df_db.to_sql(table, conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

def add_row(sheet, row_dict):
    """Insert one row into the DB and refresh session state."""
    col_map = _COL_MAP[sheet]
    table   = TABLE_MAP[sheet]
    db_row  = {col_map[k]: v for k, v in row_dict.items() if k in col_map}
    cols    = ", ".join(db_row.keys())
    placeholders = ", ".join(["?"] * len(db_row))
    conn = get_conn()
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(db_row.values()))
    conn.commit()
    conn.close()
    st.session_state.data[sheet] = load_all_data()[sheet]

def undo_last_entry(sheet):
    """Delete the last inserted row (by rowid) and refresh session state."""
    table = TABLE_MAP[sheet]
    conn  = get_conn()
    conn.execute(f"DELETE FROM {table} WHERE rowid = (SELECT MAX(rowid) FROM {table})")
    conn.commit()
    conn.close()
    st.session_state.data[sheet] = load_all_data()[sheet]
    st.success(f"Last entry removed from {sheet}.")
    st.rerun()

def get_next_sr_no(sheet):
    df = st.session_state.data[sheet]
    return 1 if df.empty else int(df["Sr No"].max()) + 1

# ── Yahoo Finance ──────────────────────────────────────────────────────────────
def search_ticker(query):
    headers = {"User-Agent": "Mozilla/5.0"}
    def _fetch(q):
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}"
        r = requests.get(url, headers=headers, verify=False, timeout=8)
        r.raise_for_status()
        return [
            f"{qt.get('shortname','Unknown')} ({qt.get('symbol','')}) - {qt.get('exchange','')}"
            for qt in r.json().get("quotes", [])[:10]
            if qt.get("symbol")
        ]
    try:
        results = _fetch(query)
        if not results and " " in query:
            results = _fetch(query.split()[-1])
        return results, None
    except Exception as e:
        return [], str(e)

@st.cache_data(ttl=300, show_spinner=False)
def get_live_stock_data(ticker_symbol):
    try:
        session = curl_requests.Session(impersonate="chrome")
        session.verify = False
        ticker = yf.Ticker(ticker_symbol, session=session)
        hist   = ticker.history(period="5d")
        if len(hist) < 2:
            return None
        cur  = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        chg  = cur - prev
        pct  = chg / prev * 100
        return cur, chg, pct
    except Exception:
        return None

# ── Session state init ─────────────────────────────────────────────────────────
def _example_goals():
    return [{
        "id": "init_1",
        "title": "Honda CB500",
        "desc": "Motorcycle purchase fund",
        "target": 500000.0,
        "include_investments": False,
        "target_date": (datetime.date.today() + datetime.timedelta(days=365)).strftime("%Y-%m-%d"),
    }]

def init_app():
    init_db()

    if "custom_inc_cats" not in st.session_state:
        st.session_state.custom_inc_cats = load_json(CUSTOM_CATS_INC_FILE, [])
    if "custom_exp_cats" not in st.session_state:
        st.session_state.custom_exp_cats = load_json(CUSTOM_CATS_EXP_FILE, [])
    if "goals" not in st.session_state:
        st.session_state.goals = load_json(GOALS_FILE, _example_goals())
    if "budgets" not in st.session_state:
        st.session_state.budgets = load_json(BUDGETS_FILE, {})
    if "recurring" not in st.session_state:
        st.session_state.recurring = load_json(RECURRING_FILE, [])
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_json(WATCHLIST_FILE, ["RELIANCE.NS", "TCS.NS", "AAPL"])
    if "ai_chat_history" not in st.session_state:
        st.session_state.ai_chat_history = []
    if "data" not in st.session_state:
        st.session_state.data = load_all_data()

def save_goals():     save_json(GOALS_FILE,    st.session_state.goals)
def save_budgets():   save_json(BUDGETS_FILE,  st.session_state.budgets)
def save_recurring(): save_json(RECURRING_FILE, st.session_state.recurring)
def save_watchlist(): save_json(WATCHLIST_FILE, st.session_state.watchlist)

def add_income_category(cat):
    if cat and cat not in st.session_state.custom_inc_cats:
        st.session_state.custom_inc_cats.append(cat)
        save_json(CUSTOM_CATS_INC_FILE, st.session_state.custom_inc_cats)

def add_expense_category(cat):
    if cat and cat not in st.session_state.custom_exp_cats:
        st.session_state.custom_exp_cats.append(cat)
        save_json(CUSTOM_CATS_EXP_FILE, st.session_state.custom_exp_cats)

# ── Filters & budget helpers ───────────────────────────────────────────────────
def filter_df(df, date_col="Date", period="All Time"):
    if period == "All Time" or df.empty:
        return df
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    today = datetime.date.today()
    if period == "Current Month":
        return df[(df[date_col].dt.month == today.month) & (df[date_col].dt.year == today.year)]
    if period == "Last 3 Months":
        cutoff = today - datetime.timedelta(days=90)
        return df[df[date_col].dt.date >= cutoff]
    if period == "Current Year":
        return df[df[date_col].dt.year == today.year]
    return df

def budget_status(category):
    """Returns (spent, limit, pct) or None if no budget set for this category."""
    limit = st.session_state.budgets.get(category)
    if not limit:
        return None
    today = datetime.date.today()
    conn  = get_conn()
    row   = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS spent FROM expenses "
        "WHERE expense_type = ? "
        "AND strftime('%Y', date) = ? AND strftime('%m', date) = ?",
        (category, str(today.year), f"{today.month:02d}")
    ).fetchone()
    conn.close()
    spent = float(row["spent"])
    return spent, limit, spent / limit

# ── Recurring transactions ─────────────────────────────────────────────────────
def generate_recurring_entries():
    today     = datetime.date.today()
    generated = 0
    for rec in st.session_state.recurring:
        if not rec.get("active", True):
            continue
        last = datetime.date.fromisoformat(rec["last_generated"]) if rec.get("last_generated") else None
        freq = rec["frequency"]
        due  = False
        if freq == "Monthly":
            due = (last is None) or (today.year > last.year or today.month > last.month)
        elif freq == "Weekly":
            due = (last is None) or ((today - last).days >= 7)
        elif freq == "Quarterly":
            last_q = (last.month - 1) // 3 if last else -1
            cur_q  = (today.month - 1) // 3
            due    = (last is None) or (today.year > last.year) or (cur_q > last_q)
        if not due:
            continue
        date_str = today.strftime("%Y-%m-%d")
        sheet    = rec["sheet"]
        if sheet == "MAIN SALARY":
            add_row(sheet, {"Sr No": get_next_sr_no(sheet), "Date": date_str,
                            "Salary Credited": rec["amount"]})
        elif sheet == "OTHER INCOME":
            add_row(sheet, {"Sr No": get_next_sr_no(sheet), "Date": date_str,
                            "Income Type": rec["label"], "Amount": rec["amount"],
                            "Notes": "Auto-recurring"})
        elif sheet == "EXPENSES":
            add_row(sheet, {"Sr No": get_next_sr_no(sheet), "Date": date_str,
                            "Expense Type": rec["label"], "Amount": rec["amount"],
                            "Notes": "Auto-recurring"})
        rec["last_generated"] = today.isoformat()
        generated += 1
    if generated:
        save_recurring()
    return generated

# ── Bank CSV import ────────────────────────────────────────────────────────────
def parse_bank_csv(uploaded_file, date_col, amount_col, desc_col,
                   cr_dr_col=None, debit_keyword="DR"):
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip()
        df[date_col]   = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
        df[amount_col] = pd.to_numeric(
            df[amount_col].astype(str).str.replace(",", ""), errors="coerce"
        ).fillna(0).abs()
        if cr_dr_col and cr_dr_col in df.columns:
            debits  = df[df[cr_dr_col].astype(str).str.contains(debit_keyword, case=False)]
            credits = df[~df[cr_dr_col].astype(str).str.contains(debit_keyword, case=False)]
        else:
            debits  = df
            credits = pd.DataFrame(columns=df.columns)
        expenses = debits[[date_col, desc_col, amount_col]].rename(
            columns={date_col: "Date", desc_col: "Expense Type", amount_col: "Amount"})
        expenses["Notes"] = "Imported"
        income = credits[[date_col, desc_col, amount_col]].rename(
            columns={date_col: "Date", desc_col: "Income Type", amount_col: "Amount"})
        income["Notes"] = "Imported"
        return expenses, income, None
    except Exception as e:
        return None, None, str(e)

# ── AI helpers ─────────────────────────────────────────────────────────────────
def build_financial_context():
    df_sal = st.session_state.data["MAIN SALARY"]
    df_inc = st.session_state.data["OTHER INCOME"]
    df_exp = st.session_state.data["EXPENSES"]
    df_inv = st.session_state.data["SHARES and FUNDS"]
    df_dep = st.session_state.data["BANK DEPOSITS"]

    total_income   = df_sal["Salary Credited"].sum() + df_inc["Amount"].sum()
    total_expenses = df_exp["Amount"].sum()
    total_invested = df_inv["Total Amount Invested"].sum()
    total_deposits = df_dep["Amount"].sum() if not df_dep.empty else 0
    liquid         = total_income - total_expenses - total_invested - total_deposits
    savings_rate   = (total_income - total_expenses) / total_income * 100 if total_income else 0

    exp_by_cat  = df_exp.groupby("Expense Type")["Amount"].sum().to_dict() if not df_exp.empty else {}
    inv_by_name = df_inv.groupby("Share/Fund Name")["Total Amount Invested"].sum().to_dict() if not df_inv.empty else {}
    
    # Calculate total FD/RD interest
    est_interest = 0
    if not df_dep.empty:
        est_interest = (df_dep["Amount"] * (df_dep["Interest Rate (%)"] / 100) * (df_dep["Tenure (months)"] / 12)).sum()

    monthly = {}
    if not df_exp.empty:
        df_tmp = df_exp.copy()
        df_tmp["Date"] = pd.to_datetime(df_tmp["Date"], errors="coerce")
        df_tmp["YM"]   = df_tmp["Date"].dt.to_period("M").astype(str)
        monthly        = df_tmp.groupby("YM")["Amount"].sum().tail(6).to_dict()

    return f"""
FINANCIAL SNAPSHOT (all-time):
- Total income:   ₹{total_income:,.0f}  (Salary ₹{df_sal['Salary Credited'].sum():,.0f} + Other ₹{df_inc['Amount'].sum():,.0f})
- Total expenses: ₹{total_expenses:,.0f}
- Total invested: ₹{total_invested:,.0f}
- Bank deposits (FD/RD): ₹{total_deposits:,.0f} (Est. Interest: ₹{est_interest:,.0f})
- Liquid balance: ₹{liquid:,.0f}
- Savings rate:   {savings_rate:.1f}%
- Expense breakdown:           {exp_by_cat}
- Investment portfolio:        {inv_by_name}
- Monthly expense trend (6mo): {monthly}
- Active budgets: {st.session_state.budgets}
- Goals: {[{'title': g['title'], 'target': g['target'], 'date': g['target_date']} for g in st.session_state.goals]}
"""

def get_ai_report():
    ctx    = build_financial_context()
    prompt = f"""Act as a Senior Certified Financial Planner (CFP). Analyse this personal financial data and write a concise, actionable report in Markdown.

{ctx}

Structure your report with these sections:
1. **Executive Summary** (2 sentences)
2. **Spending Analysis** — top categories, savings rate assessment
3. **50/30/20 Rule Check** — compare current split vs ideal
4. **Optimisation Strategy** — 3 specific tips tied to the actual data
5. **Investment Critique** — portfolio concentration, diversification
6. **30-Day Action Plan** — 5 bullet checklist

Be direct, data-driven, and specific. Use ₹ values from the data."""
    try:
        response = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}])
        return response["message"]["content"]
    except Exception as e:
        return f"⚠️ Could not connect to Ollama: {e}\n\nMake sure Ollama is running locally with `ollama serve`."

def get_ai_chat_response(user_message):
    ctx    = build_financial_context()
    system = (f"You are a friendly, expert personal finance advisor. "
              f"You have access to the user's complete financial data below.\n"
              f"Answer questions specifically using their numbers. Be concise but insightful.\n\n{ctx}")
    history = [{"role": "system", "content": system}]
    for msg in st.session_state.ai_chat_history[-10:]:
        history.append({"role": msg["role"], "content": msg["content"]})
    history.append({"role": "user", "content": user_message})
    try:
        response = ollama.chat(model=LLM_MODEL, messages=history)
        return response["message"]["content"]
    except Exception as e:
        return f"⚠️ Ollama error: {e}"

# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Finance Tracker", layout="wide", page_icon="₹")
init_app()

# Auto-generate recurring entries once per session
if "recurring_checked" not in st.session_state:
    n = generate_recurring_entries()
    st.session_state.recurring_checked = True
    if n:
        st.toast(f"✅ {n} recurring transaction(s) auto-added.", icon="🔄")

today_str = datetime.date.today().strftime("%Y-%m-%d")
st.title("Finance Tracker")

(tab_salary, tab_income, tab_expenses, tab_invest, tab_deposits,
 tab_goals, tab_budget, tab_recurring, tab_import,
 tab_manage, tab_dashboard, tab_ai) = st.tabs([
    "Salary", "Other Income", "Expenses",
    "Investments", "Bank Deposits", "Goals", "Budgets",
    "Recurring", "Import", "Manage Data",
    "Dashboard", "AI Advisor",
])

# ── SIDEBAR: Watchlist ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Watchlist")
    query = st.text_input("Search company / fund")
    if st.button("Search", key="wl_search"):
        if query:
            results, err = search_ticker(query)
            st.session_state.search_results = results
            if err:
                st.error(err)

    if st.session_state.get("search_results"):
        sel = st.selectbox("Select:", st.session_state.search_results)
        if st.button("Add to Watchlist"):
            m = re.search(r"\((.*?)\)", sel)
            if m:
                sym = m.group(1)
                if sym not in st.session_state.watchlist:
                    st.session_state.watchlist.append(sym)
                    save_watchlist()
                    st.rerun()

    st.divider()
    for ticker in list(st.session_state.watchlist):
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.write(f"**{ticker}**")
            if c2.button("✕", key=f"rm_{ticker}"):
                st.session_state.watchlist.remove(ticker)
                save_watchlist()
                st.rerun()
            data = get_live_stock_data(ticker)
            if data:
                price, chg, pct = data
                st.metric("Price", f"{price:.2f}", f"{chg:+.2f} ({pct:+.2f}%)")
            else:
                st.caption("Unable to fetch price")

# ── TAB: SALARY ────────────────────────────────────────────────────────────────
with tab_salary:
    st.subheader("Credit Salary")
    col1, col2 = st.columns(2)
    sal_amt  = col1.number_input("Amount (₹)", min_value=0.0, step=100.0, key="sal_amt")
    sal_date = col2.date_input("Date", value=datetime.date.today(), key="sal_dt").strftime("%Y-%m-%d")
    if st.button("Add Salary Entry"):
        add_row("MAIN SALARY", {"Sr No": get_next_sr_no("MAIN SALARY"),
                                 "Date": sal_date, "Salary Credited": sal_amt})
        st.success(f"Salary ₹{sal_amt:,.0f} credited!")
        st.rerun()
    if not st.session_state.data["MAIN SALARY"].empty:
        if st.button("↩ Undo Last", key="undo_sal"):
            undo_last_entry("MAIN SALARY")
        st.dataframe(st.session_state.data["MAIN SALARY"].tail(10), use_container_width=True)

# ── TAB: OTHER INCOME ──────────────────────────────────────────────────────────
with tab_income:
    st.subheader("Log Other Income")
    c1, c2   = st.columns(2)
    inc_date = c1.date_input("Date", value=datetime.date.today(), key="inc_dt").strftime("%Y-%m-%d")
    base_inc = ["Bonus", "Freelance", "Dividends", "Gift", "Rental", "Interest"]
    all_inc  = sorted(set(base_inc + st.session_state.custom_inc_cats)) + ["Custom…"]
    inc_type = c2.selectbox("Income Type", all_inc, key="inc_tp")
    custom_inc = ""
    if inc_type == "Custom…":
        custom_inc = st.text_input("New category name", key="custom_inc")
    c3, c4    = st.columns(2)
    inc_amt   = c3.number_input("Amount (₹)", min_value=0.0, step=10.0, key="inc_amt")
    inc_notes = c4.text_input("Notes (optional)", key="inc_notes")
    if st.button("Add Income"):
        final = custom_inc if inc_type == "Custom…" else inc_type
        if inc_type == "Custom…" and custom_inc:
            add_income_category(custom_inc)
        add_row("OTHER INCOME", {"Sr No": get_next_sr_no("OTHER INCOME"),
                                  "Date": inc_date, "Income Type": final,
                                  "Amount": inc_amt, "Notes": inc_notes})
        st.success(f"Income ₹{inc_amt:,.0f} logged under {final}!")
        st.rerun()
    if not st.session_state.data["OTHER INCOME"].empty:
        if st.button("↩ Undo Last", key="undo_inc"):
            undo_last_entry("OTHER INCOME")
        st.dataframe(st.session_state.data["OTHER INCOME"].tail(10), use_container_width=True)

# ── TAB: EXPENSES ──────────────────────────────────────────────────────────────
with tab_expenses:
    st.subheader("Log Expense")
    c1, c2   = st.columns(2)
    exp_date = c1.date_input("Date", value=datetime.date.today(), key="exp_dt").strftime("%Y-%m-%d")
    base_exp = ["Rent", "Groceries", "Utilities", "Transport", "Entertainment",
                "Healthcare", "Food", "Clothing", "Education", "Subscriptions"]
    all_exp  = sorted(set(base_exp + st.session_state.custom_exp_cats)) + ["Custom…"]
    exp_type = c2.selectbox("Expense Type", all_exp, key="exp_tp")
    custom_exp = ""
    if exp_type == "Custom…":
        custom_exp = st.text_input("New category name", key="custom_exp")
    c3, c4    = st.columns(2)
    exp_amt   = c3.number_input("Amount (₹)", min_value=0.0, step=10.0, key="exp_amt")
    exp_notes = c4.text_input("Notes (optional)", key="exp_notes")
    if st.button("Add Expense"):
        final = custom_exp if exp_type == "Custom…" else exp_type
        if exp_type == "Custom…" and custom_exp:
            add_expense_category(custom_exp)
        add_row("EXPENSES", {"Sr No": get_next_sr_no("EXPENSES"),
                              "Date": exp_date, "Expense Type": final,
                              "Amount": exp_amt, "Notes": exp_notes})
        status = budget_status(final)
        if status:
            spent, limit, pct = status
            if pct >= 1.0:
                st.error(f"🚨 Budget EXCEEDED for {final}! Spent ₹{spent:,.0f} / ₹{limit:,.0f}")
            elif pct >= 0.8:
                st.warning(f"⚠️ {final} budget at {pct*100:.0f}% — ₹{limit-spent:,.0f} remaining")
            else:
                st.success(f"Expense logged. {final}: ₹{spent:,.0f} / ₹{limit:,.0f} ({pct*100:.0f}%)")
        else:
            st.success(f"Expense ₹{exp_amt:,.0f} logged under {final}!")
        st.rerun()

    # Budget mini-dashboard
    if st.session_state.budgets:
        st.markdown("#### This Month's Budget Status")
        for cat, limit in st.session_state.budgets.items():
            s = budget_status(cat)
            if s:
                spent, lim, pct = s
                color = "🔴" if pct >= 1 else ("🟡" if pct >= 0.8 else "🟢")
                st.markdown(f"{color} **{cat}** — ₹{spent:,.0f} / ₹{lim:,.0f}")
                st.progress(min(pct, 1.0))

    if not st.session_state.data["EXPENSES"].empty:
        if st.button("↩ Undo Last", key="undo_exp"):
            undo_last_entry("EXPENSES")
        st.dataframe(st.session_state.data["EXPENSES"].tail(10), use_container_width=True)

# ── TAB: INVESTMENTS ───────────────────────────────────────────────────────────
with tab_invest:
    st.subheader("Log Investment")
    c1, c2, c3 = st.columns(3)
    inv_name = c1.text_input("Share / Fund Name", key="inv_name")
    inv_tick = c2.text_input("Ticker (optional, for live P&L)", placeholder="e.g. TCS.NS", key="inv_tick")
    inv_qty  = c3.number_input("Quantity", min_value=0.0, step=1.0, key="inv_qty")
    c4, c5   = st.columns(2)
    inv_amt  = c4.number_input("Total Invested (₹)", min_value=0.0, step=50.0, key="inv_amt")
    inv_date = c5.date_input("Date", value=datetime.date.today(), key="inv_dt").strftime("%Y-%m-%d")
    if st.button("Add Investment"):
        add_row("SHARES and FUNDS", {
            "Sr No": get_next_sr_no("SHARES and FUNDS"),
            "Date": inv_date, "Share/Fund Name": inv_name,
            "Ticker": inv_tick, "Quantity": inv_qty,
            "Total Amount Invested": inv_amt,
        })
        st.success(f"Investment in {inv_name} logged!")
        st.rerun()

    if not st.session_state.data["SHARES and FUNDS"].empty:
        if st.button("↩ Undo Last", key="undo_inv"):
            undo_last_entry("SHARES and FUNDS")

    # ── Live P&L ──
    st.markdown("---")
    st.subheader("Portfolio P&L")
    df_inv_raw = st.session_state.data["SHARES and FUNDS"]

    if df_inv_raw.empty:
        st.info("No investments logged yet.")
    else:
        df_inv_pl = df_inv_raw.copy()
        df_inv_pl["Ticker"] = df_inv_pl["Ticker"].fillna("").astype(str).str.strip()

        grp = df_inv_pl.groupby(["Share/Fund Name", "Ticker"], as_index=False).agg(
            Quantity=("Quantity", "sum"),
            Cost=("Total Amount Invested", "sum"),
        )

        col_ref, col_note = st.columns([1, 3])
        if col_ref.button("🔄 Refresh Live Prices"):
            st.cache_data.clear()
            st.rerun()
        col_note.caption("Add a ticker symbol when logging to enable live price tracking.")

        rows        = []
        fetch_errors = []

        for _, row in grp.iterrows():
            name      = row["Share/Fund Name"]
            ticker    = row["Ticker"]
            qty       = float(row["Quantity"])
            cost      = float(row["Cost"])
            cur_price = None

            if ticker:
                try:
                    session   = curl_requests.Session(impersonate="chrome")
                    session.verify = False
                    yf_ticker = yf.Ticker(ticker, session=session)
                    hist      = yf_ticker.history(period="5d")
                    if not hist.empty:
                        cur_price = float(hist["Close"].iloc[-1])
                except Exception as e:
                    fetch_errors.append(f"{ticker}: {e}")

            if cur_price is not None:
                cur_val = cur_price * qty
                gain    = cur_val - cost
                pct_ret = (gain / cost * 100) if cost else 0.0
                rows.append({"Name": name, "Ticker": ticker or "—", "Qty": qty,
                             "Cost (₹)": cost, "Price": cur_price,
                             "Value (₹)": cur_val, "Gain/Loss": gain, "Return %": pct_ret})
            else:
                rows.append({"Name": name, "Ticker": ticker if ticker else "No ticker",
                             "Qty": qty, "Cost (₹)": cost,
                             "Price": None, "Value (₹)": None,
                             "Gain/Loss": None, "Return %": None})

        def fmt_row(r):
            return {
                "Name":       r["Name"],
                "Ticker":     r["Ticker"],
                "Qty":        r["Qty"],
                "Cost (₹)":   f"₹{r['Cost (₹)']:,.0f}",
                "Cur. Price": f"₹{r['Price']:,.2f}"       if r["Price"]      is not None else "—",
                "Value (₹)":  f"₹{r['Value (₹)']:,.0f}"  if r["Value (₹)"] is not None else "—",
                "Gain/Loss":  f"₹{r['Gain/Loss']:+,.0f}"  if r["Gain/Loss"]  is not None else "—",
                "Return %":   f"{r['Return %']:+.1f}%"    if r["Return %"]   is not None else "—",
            }

        st.dataframe(pd.DataFrame([fmt_row(r) for r in rows]),
                     use_container_width=True, hide_index=True)

        tracked = [r for r in rows if r["Value (₹)"] is not None]
        if tracked:
            total_cost    = sum(r["Cost (₹)"]  for r in tracked)
            total_current = sum(r["Value (₹)"] for r in tracked)
            total_gain    = total_current - total_cost
            pct_overall   = (total_gain / total_cost * 100) if total_cost else 0.0
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Cost",      f"₹{total_cost:,.0f}")
            m2.metric("Current Value",   f"₹{total_current:,.0f}")
            m3.metric("Unrealised P&L",  f"₹{total_gain:+,.0f}",
                      delta=f"{pct_overall:+.1f}%", delta_color="normal")
            m4.metric("Holdings tracked", f"{len(tracked)} / {len(rows)}")
        else:
            st.info("No tickers added yet — add ticker symbols to holdings to enable live prices.")

        if fetch_errors:
            with st.expander(f"⚠️ {len(fetch_errors)} fetch error(s)"):
                for e in fetch_errors:
                    st.caption(e)

# ── TAB: BANK DEPOSITS (FD/RD) ─────────────────────────────────────────────────
with tab_deposits:
    st.subheader("Bank Deposits (FD / RD)")
    st.caption("Track Fixed Deposits and Recurring Deposits across banks. They will appear in the Dashboard.")
    
    c1, c2, c3 = st.columns(3)
    dep_date = c1.date_input("Opening Date", value=datetime.date.today(), key="dep_dt").strftime("%Y-%m-%d")
    dep_bank = c2.text_input("Bank Name", placeholder="e.g. HDFC, ICICI, SBI", key="dep_bank")
    dep_type = c3.selectbox("Deposit Type", ["FD (Fixed Deposit)", "RD (Recurring Deposit)"], key="dep_type")
    
    c4, c5, c6 = st.columns(3)
    dep_amt = c4.number_input("Amount (₹)", min_value=1.0, step=100.0, key="dep_amt")
    dep_tenure = c5.number_input("Tenure (months)", min_value=1, step=1, key="dep_tenure")
    dep_rate = c6.number_input("Interest Rate (%)", min_value=0.0, step=0.1, key="dep_rate")
    
    # Calculate maturity date
    dep_obj = datetime.date.today()
    dep_obj_parsed = datetime.datetime.strptime(dep_date, "%Y-%m-%d").date()
    months_to_add = int(dep_tenure)
    maturity = (dep_obj_parsed + datetime.timedelta(days=months_to_add * 30)).strftime("%Y-%m-%d")
    
    dep_notes = st.text_input("Notes (optional)", key="dep_notes")
    
    if st.button("Add Bank Deposit"):
        # Add to Bank Deposits table
        add_row("BANK DEPOSITS", {
            "Sr No": get_next_sr_no("BANK DEPOSITS"),
            "Date": dep_date,
            "Bank Name": dep_bank,
            "Deposit Type": dep_type,
            "Amount": dep_amt,
            "Tenure (months)": int(dep_tenure),
            "Interest Rate (%)": dep_rate,
            "Maturity Date": maturity,
            "Notes": dep_notes,
        })
        
        # Automatically add to OTHER INCOME as well
        add_row("OTHER INCOME", {
            "Sr No": get_next_sr_no("OTHER INCOME"),
            "Date": dep_date,
            "Income Type": f"{dep_type} - {dep_bank}",
            "Amount": dep_amt,
            "Notes": f"Auto: {dep_notes}" if dep_notes else "Auto-added from Bank Deposits",
        })
        
        est_interest = dep_amt * (dep_rate / 100) * (dep_tenure / 12)
        st.success(f"✅ {dep_type} of ₹{dep_amt:,.0f} added to {dep_bank}! (Est. Interest: ₹{est_interest:,.0f})")
        st.success(f"✅ Also added as income entry in 'Other Income'")
        st.rerun()
    
    st.info("📊 View all deposits in the **Dashboard** tab for a complete overview.")

# ── TAB: GOALS ─────────────────────────────────────────────────────────────────
with tab_goals:
    st.subheader("Financial Goals")
    total_income_all = (st.session_state.data["MAIN SALARY"]["Salary Credited"].sum() +
                        st.session_state.data["OTHER INCOME"]["Amount"].sum())
    total_exp_all    = st.session_state.data["EXPENSES"]["Amount"].sum()
    total_inv_all    = st.session_state.data["SHARES and FUNDS"]["Total Amount Invested"].sum()
    liquid_all       = max(0, total_income_all - total_exp_all - total_inv_all)
    networth_all     = max(0, total_income_all - total_exp_all)

    with st.expander("➕ Create New Goal"):
        with st.form("new_goal_form"):
            g_title  = st.text_input("Goal Title")
            g_desc   = st.text_input("Description")
            c1, c2   = st.columns(2)
            g_target = c1.number_input("Target Amount (₹)", min_value=1.0, step=1000.0)
            g_date   = c2.date_input("Target Date", min_value=datetime.date.today())
            g_inv    = st.checkbox("Include investments in progress?")
            if st.form_submit_button("Save Goal"):
                st.session_state.goals.append({
                    "id": str(uuid.uuid4()), "title": g_title, "desc": g_desc,
                    "target": g_target, "include_investments": g_inv,
                    "target_date": g_date.strftime("%Y-%m-%d"),
                })
                save_goals()
                st.success("Goal saved!")
                st.rerun()

    st.divider()
    today = datetime.date.today()
    if not st.session_state.goals:
        st.info("No goals yet. Create one above!")

    for i, goal in enumerate(st.session_state.goals):
        with st.container(border=True):
            col_info, col_act = st.columns([3, 1])
            with col_info:
                st.markdown(f"### {goal['title']}")
                if goal.get("desc"):
                    st.caption(goal["desc"])
                funds    = networth_all if goal.get("include_investments") else liquid_all
                funds    = max(0, funds)
                progress = min(funds / goal["target"], 1.0) if goal["target"] > 0 else 0.0
                st.progress(progress)
                target_dt   = datetime.datetime.strptime(
                    goal.get("target_date", (today + datetime.timedelta(days=365)).strftime("%Y-%m-%d")),
                    "%Y-%m-%d").date()
                months_left = max(1, (target_dt.year - today.year) * 12 + target_dt.month - today.month)
                remaining   = max(0, goal["target"] - funds)
                monthly_req = remaining / months_left
                st.metric(
                    label=f"Progress ({'Net Worth' if goal.get('include_investments') else 'Liquid'})",
                    value=f"₹{funds:,.0f} / ₹{goal['target']:,.0f}",
                    delta=f"{progress*100:.1f}% funded",
                    delta_color="normal" if progress < 1.0 else "off",
                )
                if progress >= 1.0:
                    st.success("🎉 Goal reached!")
                else:
                    st.info(f"📅 **{target_dt.strftime('%b %Y')}** · {months_left} months · ₹{monthly_req:,.0f}/month needed")
            with col_act:
                with st.expander("Edit / Delete"):
                    e_title  = st.text_input("Title",  value=goal["title"],  key=f"et_{i}")
                    e_desc   = st.text_input("Desc",   value=goal["desc"],   key=f"ed_{i}")
                    e_target = st.number_input("Target", value=float(goal["target"]), step=1000.0, key=f"eg_{i}")
                    e_date   = st.date_input("Date",
                                  value=datetime.datetime.strptime(
                                      goal.get("target_date", today.strftime("%Y-%m-%d")), "%Y-%m-%d").date(),
                                  key=f"edt_{i}")
                    e_inv    = st.checkbox("Include inv.", value=goal.get("include_investments", False), key=f"ei_{i}")
                    cs, cd   = st.columns(2)
                    if cs.button("Save", key=f"gs_{i}"):
                        st.session_state.goals[i].update({
                            "title": e_title, "desc": e_desc, "target": e_target,
                            "include_investments": e_inv,
                            "target_date": e_date.strftime("%Y-%m-%d"),
                        })
                        save_goals()
                        st.rerun()
                    if cd.button("Delete", type="primary", key=f"gd_{i}"):
                        st.session_state.goals.pop(i)
                        save_goals()
                        st.rerun()

# ── TAB: BUDGETS ───────────────────────────────────────────────────────────────
with tab_budget:
    st.subheader("Monthly Budget Limits")
    st.caption("Set a spending ceiling per category. You'll be alerted when you approach or exceed it.")
    all_cats = sorted(set(
        ["Rent", "Groceries", "Utilities", "Transport", "Entertainment",
         "Healthcare", "Food", "Clothing", "Education", "Subscriptions"]
        + st.session_state.custom_exp_cats
    ))
    with st.form("budget_form"):
        st.markdown("**Set / update budget limits (₹/month)**")
        new_budgets = {}
        cols = st.columns(2)
        for idx, cat in enumerate(all_cats):
            current = st.session_state.budgets.get(cat, 0.0)
            val     = cols[idx % 2].number_input(cat, min_value=0.0, value=float(current),
                                                  step=500.0, key=f"bgt_{cat}")
            if val > 0:
                new_budgets[cat] = val
        if st.form_submit_button("💾 Save Budgets"):
            st.session_state.budgets = new_budgets
            save_budgets()
            st.success("Budgets saved!")
            st.rerun()

    st.divider()
    st.subheader("This Month's Overview")
    if st.session_state.budgets:
        for cat, limit in st.session_state.budgets.items():
            s = budget_status(cat)
            if s:
                spent, lim, pct = s
                remaining = lim - spent
                color = "🔴" if pct >= 1 else ("🟡" if pct >= 0.8 else "🟢")
                c1, c2, c3 = st.columns([2.5, 1, 0.5])
                c1.markdown(f"{color} **{cat}**")
                c1.progress(min(pct, 1.0))
                c2.metric("Spent / Limit", f"₹{spent:,.0f}", f"₹{remaining:,.0f} left",
                          delta_color="inverse")
                if c3.button("Delete", key=f"del_bgt_{cat}", help="Delete budget", use_container_width=True):
                    del st.session_state.budgets[cat]
                    save_budgets()
                    st.rerun()
    else:
        st.info("No budgets set yet.")

    st.divider()
    st.subheader("Custom Expense Categories")
    if st.session_state.custom_exp_cats:
        st.markdown("**Your custom categories:**")
        for cat in st.session_state.custom_exp_cats:
            col_cat, col_keep, col_del = st.columns([3, 1, 0.8])
            col_cat.write(f"• {cat}")
            if col_keep.button("✓ Keep", key=f"keep_cat_{cat}", help="Confirm to keep this category"):
                st.toast(f"✓ '{cat}' will be retained", icon="✓")
            if col_del.button("✕", key=f"del_cat_{cat}", help="Remove category"):
                st.session_state.custom_exp_cats.remove(cat)
                save_json(CUSTOM_CATS_EXP_FILE, st.session_state.custom_exp_cats)
                st.success(f"Category '{cat}' removed!")
                st.rerun()
    else:
        st.caption("No custom categories created yet.")

# ── TAB: RECURRING ─────────────────────────────────────────────────────────────
with tab_recurring:
    st.subheader("Recurring Transactions")
    st.caption("These are auto-added when the app starts if the period has elapsed.")
    with st.expander("➕ Add Recurring Entry"):
        with st.form("rec_form"):
            r_label = st.text_input("Label (e.g. Rent, Netflix)")
            c1, c2  = st.columns(2)
            r_sheet = c1.selectbox("Type", ["EXPENSES", "OTHER INCOME", "MAIN SALARY"])
            r_freq  = c2.selectbox("Frequency", ["Monthly", "Weekly", "Quarterly"])
            r_amt   = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
            if st.form_submit_button("Add"):
                st.session_state.recurring.append({
                    "id": str(uuid.uuid4()), "label": r_label, "sheet": r_sheet,
                    "frequency": r_freq, "amount": r_amt,
                    "active": True, "last_generated": None,
                })
                save_recurring()
                st.success(f"Recurring entry '{r_label}' added!")
                st.rerun()

    st.divider()
    if not st.session_state.recurring:
        st.info("No recurring entries yet.")
    else:
        for i, rec in enumerate(st.session_state.recurring):
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
                c1.markdown(f"**{rec['label']}** — {rec['sheet']}")
                c2.write(f"₹{rec['amount']:,.0f} / {rec['frequency']}")
                c3.write(f"Last: {rec.get('last_generated') or 'Never'}")
                active = c4.toggle("Active", value=rec.get("active", True), key=f"rec_active_{i}")
                if active != rec.get("active", True):
                    st.session_state.recurring[i]["active"] = active
                    save_recurring()
                    st.rerun()
                if c4.button("Delete", key=f"rec_del_{i}"):
                    st.session_state.recurring.pop(i)
                    save_recurring()
                    st.rerun()
        if st.button("▶ Generate Now (force run)"):
            for rec in st.session_state.recurring:
                rec["last_generated"] = None
            save_recurring()
            st.session_state.pop("recurring_checked", None)
            st.rerun()

# ── TAB: IMPORT ────────────────────────────────────────────────────────────────
with tab_import:
    st.subheader("Import Bank Statement (CSV)")
    st.caption("Upload a CSV export from your bank. Map the columns and import in bulk.")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded:
        preview_df = pd.read_csv(uploaded)
        st.dataframe(preview_df.head(5), use_container_width=True)
        uploaded.seek(0)
        cols       = list(preview_df.columns)
        c1, c2, c3 = st.columns(3)
        date_col   = c1.selectbox("Date column",   cols, key="imp_date")
        amount_col = c2.selectbox("Amount column", cols, key="imp_amt")
        desc_col   = c3.selectbox("Description",   cols, key="imp_desc")
        cr_dr_col  = st.selectbox("Credit/Debit indicator column (optional)", ["None"] + cols)
        cr_dr_col  = None if cr_dr_col == "None" else cr_dr_col
        debit_kw   = st.text_input("Debit keyword in that column", value="DR")
        if st.button("🔍 Preview Import"):
            uploaded.seek(0)
            exp_df, inc_df, err = parse_bank_csv(uploaded, date_col, amount_col,
                                                  desc_col, cr_dr_col, debit_kw)
            if err:
                st.error(f"Parse error: {err}")
            else:
                st.markdown(f"**{len(exp_df)} expense rows · {len(inc_df)} income rows detected**")
                st.dataframe(exp_df.head(10), use_container_width=True)
                if not inc_df.empty:
                    st.dataframe(inc_df.head(10), use_container_width=True)
                st.session_state["import_exp"] = exp_df
                st.session_state["import_inc"] = inc_df
        if "import_exp" in st.session_state:
            if st.button("✅ Confirm Import"):
                exp_df = st.session_state["import_exp"]
                inc_df = st.session_state["import_inc"]
                for _, row in exp_df.iterrows():
                    add_row("EXPENSES", {
                        "Sr No": get_next_sr_no("EXPENSES"),
                        "Date": row["Date"], "Expense Type": row["Expense Type"],
                        "Amount": row["Amount"], "Notes": row.get("Notes", "Imported"),
                    })
                for _, row in inc_df.iterrows():
                    add_row("OTHER INCOME", {
                        "Sr No": get_next_sr_no("OTHER INCOME"),
                        "Date": row["Date"], "Income Type": row["Income Type"],
                        "Amount": row["Amount"], "Notes": row.get("Notes", "Imported"),
                    })
                st.session_state.pop("import_exp", None)
                st.session_state.pop("import_inc", None)
                st.success(f"Imported {len(exp_df)} expenses and {len(inc_df)} income entries!")
                st.rerun()

# ── TAB: MANAGE DATA ──────────────────────────────────────────────────────────
with tab_manage:
    st.subheader("Edit Raw Data")
    sheet_choice = st.selectbox("Sheet", list(TABLE_MAP.keys()))

    edited = st.data_editor(
        st.session_state.data[sheet_choice],
        num_rows="dynamic",          # ➕ add rows | 🗑 delete rows
        use_container_width=True,
        key=f"editor_{sheet_choice}",
    )

    col_save, col_undo, col_info = st.columns([1, 1, 3])

    if col_save.button("💾 Save Changes"):
        # Write edited DataFrame straight back to SQLite
        _df_to_db(sheet_choice, edited)
        # Refresh session state so every other tab sees the updated data
        st.session_state.data[sheet_choice] = load_all_data()[sheet_choice]
        col_info.success(f"✅ {len(edited)} rows saved to database.")

    if col_undo.button("↩ Undo Last Row"):
        undo_last_entry(sheet_choice)

    # ── Export ──
    st.divider()
    st.subheader("Export")
    col_a, col_b = st.columns(2)

    csv_buf = io.StringIO()
    st.session_state.data[sheet_choice].to_csv(csv_buf, index=False)
    col_a.download_button(
        "⬇ Download CSV",
        data=csv_buf.getvalue(),
        file_name=f"{sheet_choice.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )

    excel_buf = io.BytesIO()
    with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
        for sn, df in st.session_state.data.items():
            df.to_excel(writer, sheet_name=sn, index=False)
    col_b.download_button(
        "⬇ Download Full Excel",
        data=excel_buf.getvalue(),
        file_name="finance_tracker_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ── TAB: DASHBOARD ─────────────────────────────────────────────────────────────
with tab_dashboard:
    st.subheader("Financial Overview")
    period = st.radio("Timeframe",
                      ["All Time", "Current Month", "Last 3 Months", "Current Year"],
                      horizontal=True)

    df_sal = filter_df(st.session_state.data["MAIN SALARY"].copy(),    period=period)
    df_inc = filter_df(st.session_state.data["OTHER INCOME"].copy(),   period=period)
    df_exp = filter_df(st.session_state.data["EXPENSES"].copy(),       period=period)
    df_inv = filter_df(st.session_state.data["SHARES and FUNDS"].copy(), period=period)
    df_dep = filter_df(st.session_state.data["BANK DEPOSITS"].copy(),  period=period)

    total_salary    = df_sal["Salary Credited"].sum()
    total_other_inc = df_inc["Amount"].sum()
    total_income    = total_salary + total_other_inc
    total_expenses  = df_exp["Amount"].sum()
    total_invested  = df_inv["Total Amount Invested"].sum()
    net_worth       = total_income - total_expenses
    liquid          = net_worth - total_invested
    savings_rate    = (total_income - total_expenses) / total_income * 100 if total_income else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Worth",      f"₹{net_worth:,.0f}",     help="Income − Expenses")
    m2.metric("Total Income",   f"₹{total_income:,.0f}")
    m3.metric("Total Expenses", f"₹{total_expenses:,.0f}",
              delta=f"{total_expenses/total_income*100:.1f}% of income" if total_income else "",
              delta_color="inverse")
    m4.metric("Savings Rate",   f"{savings_rate:.1f}%")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Liquid Balance",f"₹{liquid:,.0f}")
    c2.metric("Salary",        f"₹{total_salary:,.0f}")
    c3.metric("Other Income",  f"₹{total_other_inc:,.0f}")
    c4.metric("Bank Deposits",       f"₹{df_dep['Amount'].sum():,.0f}")

    st.divider()

    ch1, ch2 = st.columns(2)
    with ch1:
        st.markdown("#### Income breakdown")
        fig = px.pie(
            pd.DataFrame({"Source": ["Salary", "Other"], "Amount": [total_salary, total_other_inc]}),
            values="Amount", names="Source", hole=0.4,
            color_discrete_sequence=["#03e6ff", "#4aef8f"])
        fig.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with ch2:
        st.markdown("#### Expense breakdown")
        if not df_exp.empty:
            grp = df_exp.groupby("Expense Type")["Amount"].sum().reset_index()
            fig = px.pie(grp, values="Amount", names="Expense Type", hole=0.4,
                         color_discrete_sequence=px.colors.qualitative.Pastel)
            fig.update_layout(margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No expense data for selected period.")

    # Monthly trend (always all-time so the chart is meaningful)
    st.markdown("#### Monthly income vs expenses trend")
    df_sal_all = st.session_state.data["MAIN SALARY"].copy()
    df_inc_all = st.session_state.data["OTHER INCOME"].copy()
    df_exp_all = st.session_state.data["EXPENSES"].copy()

    if not df_sal_all.empty or not df_exp_all.empty:
        for _df in [df_sal_all, df_inc_all, df_exp_all]:
            if not _df.empty:
                _df["Date"] = pd.to_datetime(_df["Date"], errors="coerce")

        def _monthly(df, col):
            if df.empty:
                return pd.DataFrame(columns=["Month", "Amount"])
            m = df.set_index("Date")[col].resample("ME").sum().reset_index()
            m.columns = ["Month", "Amount"]
            return m

        sal_m = _monthly(df_sal_all, "Salary Credited"); sal_m["Type"] = "Salary"
        inc_m = _monthly(df_inc_all, "Amount");          inc_m["Type"] = "Other Income"
        exp_m = _monthly(df_exp_all, "Amount");          exp_m["Type"] = "Expenses"

        trend_df = pd.concat([sal_m, inc_m, exp_m])
        trend_df = trend_df.sort_values("Month")
        trend_df["Month"] = trend_df["Month"].dt.strftime("%b %Y")
        fig = px.bar(trend_df, x="Month", y="Amount", color="Type", barmode="group",
                     color_discrete_map={"Salary": "#03e6ff",
                                         "Other Income": "#4aef8f",
                                         "Expenses": "#ff6b6b"})
        fig.update_layout(margin=dict(t=10, b=10), xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)

    # Investment portfolio bar chart
    st.markdown("#### Investment portfolio")
    if not df_inv.empty:
        grp = df_inv.groupby("Share/Fund Name")["Total Amount Invested"].sum().reset_index()
        fig = px.bar(grp, x="Share/Fund Name", y="Total Amount Invested",
                     color="Total Amount Invested", color_continuous_scale="Teal",
                     labels={"Total Amount Invested": "Amount (₹)"})
        fig.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    # Bank Deposits (FD/RD) breakdown
    st.markdown("#### Bank Deposits (FD/RD)")
    if not df_dep.empty:
        bank_grp = df_dep.groupby("Bank Name")["Amount"].sum().reset_index().sort_values("Amount", ascending=False)
        fig = px.pie(bank_grp, values="Amount", names="Bank Name", hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                    labels={"Amount": "Amount (₹)"})
        fig.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        
    else:
        st.info("No bank deposits logged yet.")

    # Budget vs Actual
    if st.session_state.budgets:
        st.markdown("#### Budget vs Actual (this month)")
        bgt_rows = []
        for cat, lim in st.session_state.budgets.items():
            s = budget_status(cat)
            bgt_rows.append({"Category": cat, "Budget": lim, "Spent": s[0] if s else 0})
        bgt_df = pd.DataFrame(bgt_rows)
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Budget", x=bgt_df["Category"], y=bgt_df["Budget"],
                             marker_color="#3498db"))
        fig.add_trace(go.Bar(name="Spent",  x=bgt_df["Category"], y=bgt_df["Spent"],
                             marker_color="#e74c3c"))
        fig.update_layout(barmode="group", margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

# ── TAB: AI ADVISOR ────────────────────────────────────────────────────────────
with tab_ai:
    st.subheader("AI Financial Advisor")
    ai_tab1, ai_tab2 = st.tabs(["Chat", "Full Report"])

    with ai_tab1:
        st.caption("Ask anything about your finances. The AI has full access to your data.")
        for msg in st.session_state.ai_chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if not st.session_state.ai_chat_history:
            st.markdown("**Suggested questions:**")
            suggestions = [
                "What is my savings rate and is it healthy?",
                "Which category am I overspending on?",
                "Can I afford to invest ₹10,000 more per month?",
                "How long will it take to reach my goals at this rate?",
                "What's my biggest financial risk right now?",
            ]
            s_cols = st.columns(len(suggestions))
            for i, q in enumerate(suggestions):
                if s_cols[i].button(q, key=f"sugg_{i}"):
                    st.session_state.ai_chat_history.append({"role": "user", "content": q})
                    with st.spinner("Thinking…"):
                        reply = get_ai_chat_response(q)
                    st.session_state.ai_chat_history.append({"role": "assistant", "content": reply})
                    st.rerun()

        if prompt := st.chat_input("Ask your advisor…"):
            st.session_state.ai_chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    reply = get_ai_chat_response(prompt)
                st.markdown(reply)
            st.session_state.ai_chat_history.append({"role": "assistant", "content": reply})

        if st.session_state.ai_chat_history:
            if st.button("🗑 Clear Chat"):
                st.session_state.ai_chat_history = []
                st.rerun()

    with ai_tab2:
        st.caption("A comprehensive CFP-style analysis of your full financial picture.")
        if st.button("📊 Generate Full Report"):
            with st.spinner("Analysing your finances…"):
                report = get_ai_report()
            st.session_state["last_report"] = report
 
        if "last_report" in st.session_state:
            report = st.session_state["last_report"]
            st.markdown("---")
            # Convert markdown to HTML so tables and checkboxes render correctly
            try:
                import markdown as md_lib
                html = md_lib.markdown(
                    report,
                    extensions=["tables", "nl2br", "sane_lists"],
                )
                # Style the table and checkboxes to match Streamlit's look
                styled = f"""
                <style>
                  .report-body table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 1rem 0;
                    font-size: 0.9rem;
                  }}
                  .report-body th, .report-body td {{
                    border: 1px solid rgba(128,128,128,0.3);
                    padding: 8px 12px;
                    text-align: left;
                  }}
                  .report-body th {{
                    background: rgba(128,128,128,0.15);
                    font-weight: 600;
                  }}
                  .report-body tr:nth-child(even) {{
                    background: rgba(128,128,128,0.05);
                  }}
                  .report-body li input[type=checkbox] {{
                    margin-right: 6px;
                  }}
                  .report-body h1 {{ font-size: 1.6rem; margin-top: 1.2rem; }}
                  .report-body h2 {{ font-size: 1.3rem; margin-top: 1rem; }}
                  .report-body h3 {{ font-size: 1.1rem; }}
                </style>
                <div class="report-body">{html}</div>
                """
                st.html(styled)
            except ImportError:
                # Fallback: plain st.markdown if the markdown package isn't installed
                st.warning(
                    "Install the `markdown` package for full table rendering: "
                    "`pip install markdown`"
                )
                st.markdown(report, unsafe_allow_html=True)
 
            # Download button for the report
            st.download_button(
                "⬇ Download Report (Markdown)",
                data=report,
                file_name=f"financial_report_{datetime.date.today()}.md",
                mime="text/markdown",
            )