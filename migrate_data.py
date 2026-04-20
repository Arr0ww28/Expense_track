"""
Run this ONCE from your project folder to migrate finance_tracker.xlsx → finance_tracker.db
    python migrate_to_sqlite.py
Your Excel file is never modified — it stays as a backup.
"""
import os
import sqlite3
import pandas as pd

EXCEL_FILE = "finance_tracker.xlsx"
DB_FILE    = "finance_tracker.db"

if not os.path.exists(EXCEL_FILE):
    print(f"ERROR: {EXCEL_FILE} not found. Run this script from your project folder.")
    raise SystemExit(1)

if os.path.exists(DB_FILE):
    answer = input(f"{DB_FILE} already exists. Overwrite? (y/n): ").strip().lower()
    if answer != "y":
        print("Aborted.")
        raise SystemExit(0)
    os.remove(DB_FILE)

print(f"Reading {EXCEL_FILE}...")

df_salary  = pd.read_excel(EXCEL_FILE, sheet_name="MAIN SALARY")
df_income  = pd.read_excel(EXCEL_FILE, sheet_name="OTHER INCOME")
df_expense = pd.read_excel(EXCEL_FILE, sheet_name="EXPENSES")
df_invest  = pd.read_excel(EXCEL_FILE, sheet_name="SHARES and FUNDS")

# Add Ticker column if it doesn't exist in the old file
if "Ticker" not in df_invest.columns:
    df_invest["Ticker"] = ""

# Normalise date columns to YYYY-MM-DD strings
for df in [df_salary, df_income, df_expense, df_invest]:
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")

# ── Write to SQLite with the column names the app expects ──────────────────────
conn = sqlite3.connect(DB_FILE)

# MAIN SALARY → main_salary
df_salary.rename(columns={
    "Sr No": "sr_no", "Date": "date", "Salary Credited": "salary"
}).to_sql("main_salary", conn, if_exists="replace", index=False)

# OTHER INCOME → other_income
df_income.rename(columns={
    "Sr No": "sr_no", "Date": "date", "Income Type": "income_type",
    "Amount": "amount", "Notes": "notes"
}).to_sql("other_income", conn, if_exists="replace", index=False)

# EXPENSES → expensest
df_expense.rename(columns={
    "Sr No": "sr_no", "Date": "date", "Expense Type": "expense_type",
    "Amount": "amount", "Notes": "notes"
}).to_sql("expenses", conn, if_exists="replace", index=False)

# SHARES and FUNDS → investments
df_invest.rename(columns={
    "Sr No": "sr_no", "Date": "date", "Share/Fund Name": "name",
    "Ticker": "ticker", "Quantity": "quantity", "Total Amount Invested": "cost"
}).to_sql("investments", conn, if_exists="replace", index=False)

conn.commit()
conn.close()

# ── Verify ─────────────────────────────────────────────────────────────────────
print(f"\n✅ Migration complete → {DB_FILE}\n")
conn = sqlite3.connect(DB_FILE)
for table, sheet in [("main_salary",  "MAIN SALARY"),
                      ("other_income", "OTHER INCOME"),
                      ("expenses",     "EXPENSES"),
                      ("investments",  "SHARES and FUNDS")]:
    excel_rows = len(pd.read_excel(EXCEL_FILE, sheet_name=sheet))
    db_rows    = pd.read_sql(f"SELECT COUNT(*) AS n FROM {table}", conn).iloc[0]["n"]
    match      = "✓" if excel_rows == db_rows else "✗ MISMATCH"
    print(f"  {sheet:<20} Excel: {excel_rows:>4} rows  →  DB: {db_rows:>4} rows  {match}")
conn.close()

print(f"\nYour Excel file is untouched and kept as a backup.")
print(f"Rename your app file to finance_tracker.py and run:  streamlit run finance_tracker.py")