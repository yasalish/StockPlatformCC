import psycopg2
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
import jdatetime
import sys
import os

# Add the directory containing the stock updater module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Try to import the stock updater module
try:
    from stock_updater import update_stock_prices, get_yesterday_jalali_date
    STOCK_UPDATER_AVAILABLE = True
except ImportError:
    st.warning("Stock updater module not found. Update functionality will be limited.")
    STOCK_UPDATER_AVAILABLE = False

# Set Streamlit page configuration
st.set_page_config(layout="wide")

# Database connection settings
DB_SETTINGS = {
    "dbname": "Stock",
    "user": "postgres",
    "password": "stock93",
    "host": "localhost",
    "port": "5432",
}

def get_latest_date_in_db():
    """Get the latest date available in the database for stocks."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT MAX(j_date) FROM StockPriceHistory"
                cursor.execute(query)
                result = cursor.fetchone()
                return result[0] if result and result[0] else None
    except Exception as e:
        st.error(f"Error getting latest date from database: {e}")
        return None

def get_yesterday_date():
    """Get yesterday's date in Jalali format."""
    try:
        # Try to use the function from stock_updater if available
        if STOCK_UPDATER_AVAILABLE:
            return get_yesterday_jalali_date()
        else:
            # Fallback implementation
            today_gregorian = datetime.now()
            yesterday_gregorian = today_gregorian - timedelta(days=1)
            yesterday_jalali = jdatetime.date.fromgregorian(
                year=yesterday_gregorian.year,
                month=yesterday_gregorian.month,
                day=yesterday_gregorian.day
            )
            return str(yesterday_jalali)
    except Exception as e:
        st.error(f"Error getting yesterday's date: {e}")
        return None

def get_next_day(jalali_date_str):
    """Get the next day after the given Jalali date."""
    try:
        # Parse the Jalali date
        year, month, day = map(int, jalali_date_str.split('-'))
        date_obj = jdatetime.date(year, month, day)
        next_day = date_obj + jdatetime.timedelta(days=1)
        return str(next_day)
    except Exception as e:
        st.error(f"Error calculating next day: {e}")
        return None

def fetch_all_stocks():
    """Fetch all stock tickers from the database."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT ticker FROM stocks ORDER BY ticker"
                cursor.execute(query)
                return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error fetching stock names: {e}")
        return []

def fetch_price_history(ticker, start_date, end_date):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date, adj_final
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date BETWEEN %s AND %s
                    ORDER BY j_date
                """
                cursor.execute(query, (ticker, start_date, end_date))
                rows = cursor.fetchall()
        return pd.DataFrame(rows, columns=["J-Date", "Adj Final"]).astype({"Adj Final": float})
    except Exception as e:
        st.error(f"Error fetching price history for {ticker}: {e}")
        return pd.DataFrame()

def date_exists(ticker, target_date):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date = %s
                """
                cursor.execute(query, (ticker, target_date))
                row = cursor.fetchone()
                return bool(row)
    except Exception as e:
        st.error(f"Error checking date for {ticker}: {e}")
        return False

def get_n_trading_days_before(ticker, target_date, n_days):
    """Find the date that is N trading days before the target date (inclusive)"""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT DISTINCT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date <= %s
                    ORDER BY j_date DESC
                    LIMIT %s
                """
                cursor.execute(query, (ticker, target_date, n_days))
                rows = cursor.fetchall()

                if rows and len(rows) == n_days:
                    return rows[-1][0]
                else:
                    return None
    except Exception as e:
        st.error(f"Error finding {n_days} trading days before {target_date} for {ticker}: {e}")
        return None

def find_nearest_date_backward(ticker, target_date):
    """Find the nearest available date before the target date"""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date <= %s
                    ORDER BY j_date DESC
                    LIMIT 1
                """
                cursor.execute(query, (ticker, target_date))
                row = cursor.fetchone()
                return row[0] if row else None
    except Exception as e:
        st.error(f"Error finding nearest date for {ticker}: {e}")
        return None

def calculate_return(df):
    if df.empty or len(df) < 2:
        return None
    start = df["Adj Final"].iloc[0]
    end = df["Adj Final"].iloc[-1]
    gain = ((end - start) / start) * 100
    return gain

# Main app
st.title("📈 Stock Performance Analyzer & Updater")

# Sidebar for update functionality
with st.sidebar:
    st.header("📊 Database Information")

    # Get latest date from database
    latest_date = get_latest_date_in_db()
    yesterday_date = get_yesterday_date()

    if latest_date:
        st.info(f"**Latest date in database:** {latest_date}")
    else:
        st.warning("No data found in database.")

    if yesterday_date:
        st.info(f"**Yesterday's date:** {yesterday_date}")

    st.header("🔄 Update Stock Data")

    if STOCK_UPDATER_AVAILABLE:
        if latest_date and yesterday_date:
            # Calculate update range
            update_start_date = get_next_day(latest_date)

            if update_start_date:
                st.write(f"**Update range:** {update_start_date} to {yesterday_date}")

                if st.button("🔄 Update Stock Data", type="primary", use_container_width=True):
                    with st.spinner(f"Updating stock data from {update_start_date} to {yesterday_date}..."):
                        try:
                            success, failure, total = update_stock_prices(
                                start_date=update_start_date,
                                end_date=yesterday_date
                            )

                            if total > 0:
                                st.success(f"✅ Update completed!")
                                st.write(f"- Successfully updated: {success} stocks")
                                st.write(f"- Failed: {failure} stocks")
                                st.write(f"- Total: {total} stocks")

                                # Refresh the page to show updated data
                                st.rerun()
                            else:
                                st.warning("No stocks found to update.")

                        except Exception as e:
                            st.error(f"Error updating stock data: {e}")
            else:
                st.warning("Cannot calculate update range.")
        else:
            st.warning("Need both latest date and yesterday's date to update.")
    else:
        st.error("Stock updater module not available.")

    # Show available stocks count
    all_stocks = fetch_all_stocks()
    if all_stocks:
        st.header("📋 Available Stocks")
        st.write(f"Total stocks in database: {len(all_stocks)}")

# Main content area
st.header("📈 Stock Performance Analysis")

# Create two columns for input
col1, col2 = st.columns(2)

with col1:
    input_date = st.text_input(
        "Enter analysis date (YYYY-MM-DD):",
        value=latest_date if latest_date else "",
        help="Enter the date you want to analyze stock performance for"
    )

with col2:
    # Get available stocks for dropdown
    all_stocks = fetch_all_stocks()
    if all_stocks:
        compare_ticker = st.selectbox(
            "Select a stock to analyze:",
            options=[""] + all_stocks,
            help="Select a stock to analyze its performance"
        )
    else:
        compare_ticker = st.text_input(
            "Enter a stock ticker to analyze:",
            help="Enter a stock ticker to analyze its performance"
        )

# Button to show gains
show_gains = st.button("📊 Calculate Stock Gains", type="primary")

if show_gains and input_date and compare_ticker:
    try:
        st.write(f"**Analysis Date:** {input_date}")
        st.write(f"**Selected Stock:** {compare_ticker}")

        # Validate date format
        try:
            datetime.strptime(input_date, "%Y-%m-%d")
        except ValueError:
            st.error("❌ Invalid date format. Please use YYYY-MM-DD format.")
            st.stop()

        # Check if the stock has data for the input date
        if not date_exists(compare_ticker, input_date):
            st.error(f"❌ Date {input_date} not found in database for {compare_ticker}. Please enter a valid trading date.")
            st.stop()

        st.success(f"✅ Date {input_date} found in database for {compare_ticker}")

        # Define ALL periods
        all_periods = {
            "5 Days": 5,
            "10 Days": 10,
            "15 Days": 15,
            "20 Days": 20,
            "30 Days": 30,
            "60 Days": 60,
            "90 Days": 90,
            "120 Days": 120,
            "180 Days": 180,
            "360 Days": 360
        }

        # Display date ranges for each period
        with st.expander("📅 Date Ranges for Each Period", expanded=True):
            date_ranges = {}

            for period_name, days_back in all_periods.items():
                end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")

                # For trading day periods (5, 10, 15, 20 days), get exact trading days
                if days_back <= 20:
                    start_date = get_n_trading_days_before(compare_ticker, input_date, days_back)
                    if start_date:
                        date_ranges[period_name] = (start_date, input_date)
                        st.write(f"**{period_name}:** {start_date} to {input_date}")
                    else:
                        date_ranges[period_name] = (None, input_date)
                        st.write(f"**{period_name}:** Not enough trading data before {input_date}")
                else:
                    # For longer periods, find nearest available date
                    actual_start = find_nearest_date_backward(compare_ticker, calendar_start)
                    if actual_start:
                        date_ranges[period_name] = (actual_start, input_date)
                        st.write(f"**{period_name}:** {actual_start} to {input_date}")
                    else:
                        date_ranges[period_name] = (None, input_date)
                        st.write(f"**{period_name}:** Not enough data before {input_date}")

        # Calculate gains for the selected stock
        with st.spinner(f"Calculating gains for {compare_ticker}..."):
            stock_results = {"Name": compare_ticker}
            stock_gains = {}

            for period_name, days_back in all_periods.items():
                if days_back <= 20:
                    # For trading day periods
                    start_date = get_n_trading_days_before(compare_ticker, input_date, days_back)
                else:
                    # For longer periods
                    end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                    calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
                    start_date = find_nearest_date_backward(compare_ticker, calendar_start)

                if start_date:
                    df = fetch_price_history(compare_ticker, start_date, input_date)
                    gain = calculate_return(df)
                    if gain is not None:
                        stock_gains[period_name] = gain
                        stock_results[f"Gain ({period_name})"] = f"{gain:.2f}%"
                    else:
                        stock_gains[period_name] = None
                        stock_results[f"Gain ({period_name})"] = "N/A"
                else:
                    stock_gains[period_name] = None
                    stock_results[f"Gain ({period_name})"] = "N/A"

        # Display results for the selected stock
        st.subheader(f"📊 Performance Results for {compare_ticker}")

        # Create metrics display
        cols = st.columns(2)
        col_idx = 0

        for period_name, gain in stock_gains.items():
            if gain is not None:
                with cols[col_idx % 2]:
                    st.metric(
                        label=f"**{period_name}**",
                        value=f"{gain:.2f}%",
                        delta="Gain"
                    )
                col_idx += 1

        # Display detailed table
        output = pd.DataFrame([stock_results])
        st.dataframe(output, use_container_width=True)

        # Add download button
        csv = output.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name=f"stock_gains_{compare_ticker}_{input_date}.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"❌ Error processing input: {e}")

elif not input_date and show_gains:
    st.warning("⚠ Please enter a date to analyze.")
elif not compare_ticker and show_gains:
    st.warning("⚠ Please select a stock to analyze.")

# Display instructions
with st.expander("📖 How to use this app"):
    st.markdown("""
    ### 🔄 Update Stock Data
    1. Check the sidebar for latest database info
    2. Click "Update Stock Data" to fetch new data
    3. The app will automatically update from the day after the latest date to yesterday

    ### 📈 Analyze Stock Performance
    1. Enter an analysis date (defaults to latest date in DB)
    2. Select a stock from the dropdown
    3. Click "Calculate Stock Gains"

    ### 📊 Understanding the Results
    - Shows performance gains for selected stock across different time periods
    - Periods analyzed: 5, 10, 15, 20, 30, 60, 90, 120, 180, and 360 days

    ### 📋 Available Stocks
    - Check the sidebar to see total number of available stocks
    - Use the dropdown to select any stock in the database
    """)

# Footer
st.markdown("---")
st.caption("Stock Performance Analyzer v2.0 | Updated data provides better insights!")