# Expense Tracker

A comprehensive personal finance tracking application built with Streamlit and SQLite. Track income, expenses, investments, and financial goals in one place.

## Features

- **Transaction Tracking**: Record and categorize income and expenses
- **Budget Management**: Set and monitor budgets across custom categories
- **Investment Tracking**: Monitor your shares and funds portfolio
- **Financial Goals**: Set savings goals and track progress
- **Recurring Transactions**: Automate recurring income and expenses
- **Stock Watchlist**: Track favorite stocks with live price data from Yahoo Finance
- **AI-Powered Insights**: Uses Ollama for intelligent financial analysis
- **Visualization**: Interactive charts using Plotly
- **Data Management**: Migrate data between Excel and SQLite formats

## Setup

### Prerequisites
- Python 3.8+
- Ollama (for AI features)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/Arr0ww28/Expense_track.git
cd Expense_track
```

2. Create a virtual environment:
```bash
python -m venv exp_venv
source exp_venv/Scripts/activate  # On Windows: exp_venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

### Option 1: Using Streamlit directly
```bash
streamlit run tracker_sql.py
```
### Or if you want Excel storage
```bash
streamlit run main.py
```

### Option 2: Using the batch file (Windows)
```bash
run_app.bat
```

## Project Structure

- `tracker_sql.py` - Main Streamlit application using SQLite backend
- `main.py` - Excel-based version of the application
- `migrate_data.py` - Script to migrate data between Excel and SQLite formats
- `custom_categories.json` - User-defined transaction categories
- `goals.json` - Saved financial goals
- `budgets.json` - Budget configurations
- `recurring.json` - Recurring transaction templates
- `watchlist.json` - Stock watchlist

## Configuration

Key configuration variables are defined at the top of each Python file:
- `DB_FILE` - SQLite database filename
- `LLM_MODEL` - Ollama model for AI features
- `CUSTOM_CATS_INC_FILE` - Income categories JSON file
- `CUSTOM_CATS_EXP_FILE` - Expense categories JSON file

## Database

The application uses SQLite for data persistence with tables for:
- Main Salary transactions
- Other Income transactions
- Expenses
- Investments (Shares and Funds)

## Notes

- Database files (`.db`) and Excel files (`.xlsx`) are not tracked in version control as they contain personal financial data
- When cloning, the application will create a new empty database on first run
- Use `migrate_data.py` to transfer existing data between formats


