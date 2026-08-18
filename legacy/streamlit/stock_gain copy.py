import psycopg2
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
import jdatetime
import sys
import os
import numpy as np

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
        if STOCK_UPDATER_AVAILABLE:
            return get_yesterday_jalali_date()
        else:
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
        year, month, day = map(int, jalali_date_str.split('-'))
        date_obj = jdatetime.date(year, month, day)
        next_day = date_obj + jdatetime.timedelta(days=1)
        return str(next_day)
    except Exception as e:
        st.error(f"Error calculating next day: {e}")
        return None

def fetch_stock_sectors():
    """Fetch all distinct sectors from the stocks table."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL ORDER BY sector"
                cursor.execute(query)
                return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error fetching stock sectors: {e}")
        return []

def fetch_sub_sectors(sector=None):
    """Fetch all distinct sub_sectors from the stocks table."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                if sector:
                    query = "SELECT DISTINCT sub_sector FROM stocks WHERE sector = %s AND sub_sector IS NOT NULL ORDER BY sub_sector"
                    cursor.execute(query, (sector,))
                else:
                    query = "SELECT DISTINCT sub_sector FROM stocks WHERE sub_sector IS NOT NULL ORDER BY sub_sector"
                    cursor.execute(query)
                return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error fetching sub_sectors: {e}")
        return []

def fetch_stocks_by_filters(sector=None, sub_sector=None):
    """Fetch stocks based on sector and sub_sector filters."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT ticker FROM stocks WHERE 1=1"
                params = []

                if sector and sector != "All Sectors":
                    query += " AND sector = %s"
                    params.append(sector)

                if sub_sector and sub_sector != "All Sub-Sectors":
                    query += " AND sub_sector = %s"
                    params.append(sub_sector)

                query += " ORDER BY ticker"
                cursor.execute(query, params)
                return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error fetching stocks: {e}")
        return []

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
                    WHERE ticker = %s AND j_date BETWEEN %s AND %s AND adj_final IS NOT NULL
                    ORDER BY j_date
                """
                cursor.execute(query, (ticker, start_date, end_date))
                rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["J-Date", "Adj Final"])
        df["Adj Final"] = df["Adj Final"].astype(float)
        return df
    except Exception as e:
        st.error(f"Error fetching price history for {ticker}: {e}")
        return pd.DataFrame()

def get_nearest_date(ticker, target_date):
    """Find the nearest available date for a given ticker."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                # Try exact date first
                query = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date = %s AND adj_final IS NOT NULL
                """
                cursor.execute(query, (ticker, target_date))
                row = cursor.fetchone()
                if row:
                    return row[0]

                # Find nearest date before target
                query_before = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date < %s AND adj_final IS NOT NULL
                    ORDER BY j_date DESC
                    LIMIT 1
                """
                cursor.execute(query_before, (ticker, target_date))
                row_before = cursor.fetchone()

                # Find nearest date after target
                query_after = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date > %s AND adj_final IS NOT NULL
                    ORDER BY j_date ASC
                    LIMIT 1
                """
                cursor.execute(query_after, (ticker, target_date))
                row_after = cursor.fetchone()

                # Choose the closest date
                if row_before and row_after:
                    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
                    before_dt = datetime.strptime(row_before[0], "%Y-%m-%d")
                    after_dt = datetime.strptime(row_after[0], "%Y-%m-%d")

                    diff_before = (target_dt - before_dt).days
                    diff_after = (after_dt - target_dt).days

                    return row_before[0] if diff_before <= diff_after else row_after[0]
                elif row_before:
                    return row_before[0]
                elif row_after:
                    return row_after[0]
                else:
                    return None
    except Exception as e:
        st.error(f"Error finding nearest date for {ticker}: {e}")
        return None

def find_nearest_date_backward(ticker, target_date):
    """Find the nearest available date before the target date"""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date
                    FROM StockPriceHistory
                    WHERE ticker = %s AND j_date <= %s AND adj_final IS NOT NULL
                    ORDER BY j_date DESC
                    LIMIT 1
                """
                cursor.execute(query, (ticker, target_date))
                row = cursor.fetchone()
                return row[0] if row else None
    except Exception as e:
        st.error(f"Error finding nearest date for {ticker}: {e}")
        return None

def calculate_same_date_previous_year(current_date_str, years_back):
    """
    Calculate the same date in previous year(s).
    """
    try:
        year, month, day = map(int, current_date_str.split('-'))
        previous_year = year - years_back

        try:
            date_obj = jdatetime.date(previous_year, month, day)
            return str(date_obj)
        except ValueError:
            try:
                date_obj = jdatetime.date(previous_year, month, 1)
                while True:
                    try:
                        next_day = date_obj + jdatetime.timedelta(days=1)
                        if next_day.month != month:
                            break
                        date_obj = next_day
                    except:
                        break
                return str(date_obj)
            except:
                return f"{previous_year:04d}-{month:02d}-01"
    except Exception as e:
        st.error(f"Error calculating same date {years_back} year(s) back: {e}")
        return None

def get_price_on_date(ticker, target_date, df_asc):
    """Get price on a specific date from the dataframe"""
    try:
        matching_rows = df_asc[df_asc['J-Date'] == target_date]
        if not matching_rows.empty:
            return matching_rows['Adj Final'].iloc[0]

        df_before = df_asc[df_asc['J-Date'] < target_date]
        if not df_before.empty:
            return df_before['Adj Final'].iloc[-1]

        return None
    except Exception as e:
        return None

def calculate_year_to_year_gain(df_asc, current_date, years_back):
    """
    Calculate gain from same date in year-(years_back) to current date
    """
    try:
        date_year_n_theoretical = calculate_same_date_previous_year(current_date, years_back)

        if not date_year_n_theoretical:
            return None, None, None

        price_year_n = get_price_on_date(None, date_year_n_theoretical, df_asc)

        if price_year_n is None:
            return None, None, None

        latest_price = df_asc['Adj Final'].iloc[-1]
        latest_date = df_asc['J-Date'].iloc[-1]

        if price_year_n != 0:
            gain = ((latest_price - price_year_n) / price_year_n) * 100
        else:
            gain = None

        return gain, date_year_n_theoretical, latest_date

    except Exception as e:
        st.error(f"Error calculating year-to-year gain: {e}")
        return None, None, None

def calculate_from_first_date(df_asc):
    """
    Calculate gain, Ceil and Floor from the first date in the database to the latest date
    """
    if df_asc.empty or len(df_asc) < 2:
        return None, None, None, None, None

    latest_price = df_asc['Adj Final'].iloc[-1]
    first_price = df_asc['Adj Final'].iloc[0]
    max_price = df_asc['Adj Final'].max()
    min_price = df_asc['Adj Final'].min()
    first_date = df_asc['J-Date'].iloc[0]
    latest_date = df_asc['J-Date'].iloc[-1]

    if first_price != 0:
        gain = ((latest_price - first_price) / first_price) * 100
    else:
        gain = None

    if max_price != 0:
        ceil = ((latest_price - max_price) / max_price) * 100
    else:
        ceil = None

    if min_price != 0:
        floor = ((latest_price - min_price) / min_price) * 100
    else:
        floor = None

    return gain, ceil, floor, first_date, latest_date

def calculate_metrics(df):
    """Calculate Gain, Ceil, and Floor metrics for a given dataframe."""
    if df.empty or len(df) < 2:
        return None, None, None

    start_value = df["Adj Final"].iloc[0]
    end_value = df["Adj Final"].iloc[-1]
    max_value = df["Adj Final"].max()
    min_value = df["Adj Final"].min()

    gain = ((end_value - start_value) / start_value) * 100 if start_value != 0 else None
    ceil = ((end_value - max_value) / max_value) * 100 if max_value != 0 else None
    floor = ((end_value - min_value) / min_value) * 100 if min_value != 0 else None

    return gain, ceil, floor

# Main app
st.title("📈 Stock Performance Analyzer & Updater")

# Sidebar for update functionality
with st.sidebar:
    st.header("📊 Database Information")

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

# Main content area
st.header("📈 Stock Performance Analysis")

# Input section - Date
col1, col2 = st.columns(2)

with col1:
    input_date = st.text_input(
        "Enter analysis date (YYYY-MM-DD):",
        value=latest_date if latest_date else "",
        help="Enter the date you want to analyze stock performance for"
    )

with col2:
    compare_ticker = st.text_input(
        "Enter a stock ticker to compare (optional):",
        help="Compare this stock with other stocks' performance"
    ).strip()

# Stock selection section
st.subheader("📂 Select Stock Group")

# Fetch sectors and sub-sectors
sectors = fetch_stock_sectors()
all_sub_sectors = fetch_sub_sectors()

# Create columns for sector and sub-sector dropdowns
col3, col4 = st.columns(2)

with col3:
    sector_options = ["All Sectors"] + sectors if sectors else ["All Sectors"]
    selected_sector = st.selectbox(
        "Select Sector:",
        options=sector_options,
        help="Select a sector to filter stocks"
    )

with col4:
    if selected_sector != "All Sectors":
        sub_sectors = fetch_sub_sectors(selected_sector)
    else:
        sub_sectors = all_sub_sectors

    sub_sector_options = ["All Sub-Sectors"] + sub_sectors if sub_sectors else ["All Sub-Sectors"]
    selected_sub_sector = st.selectbox(
        "Select Sub-Sector:",
        options=sub_sector_options,
        help="Select a sub-sector to filter stocks"
    )

# Show available stocks based on selections
stocks_in_group = fetch_stocks_by_filters(
    sector=None if selected_sector == "All Sectors" else selected_sector,
    sub_sector=None if selected_sub_sector == "All Sub-Sectors" else selected_sub_sector
)

if stocks_in_group:
    filter_text = ""
    if selected_sector != "All Sectors":
        filter_text += f"Sector: {selected_sector}"
    if selected_sub_sector != "All Sub-Sectors":
        filter_text += f" / Sub-Sector: {selected_sub_sector}" if filter_text else f"Sub-Sector: {selected_sub_sector}"

    if filter_text:
        st.info(f"📊 **{len(stocks_in_group)}** stocks found in {filter_text}")
    else:
        st.info(f"📊 **{len(stocks_in_group)}** stocks found in all sectors")
else:
    st.warning("⚠ No stocks found for the selected filters")

# Button to show analysis
show_analysis = st.button("📊 Calculate Stock Metrics", type="primary")

if show_analysis and input_date:
    try:
        st.write(f"**Analysis Date:** {input_date}")

        try:
            datetime.strptime(input_date, "%Y-%m-%d")
        except ValueError:
            st.error("❌ Invalid date format. Please use YYYY-MM-DD format.")
            st.stop()

        stocks = fetch_stocks_by_filters(
            sector=None if selected_sector == "All Sectors" else selected_sector,
            sub_sector=None if selected_sub_sector == "All Sub-Sectors" else selected_sub_sector
        )

        if not stocks:
            st.error("❌ No stocks found for the selected filters")
            st.stop()

        st.success(f"✅ Found {len(stocks)} stocks to analyze")

        # Define periods including 2-year, 3-year, and From First
        periods = {
            "1 Month": 30,
            "3 Months": 90,
            "6 Months": 180,
            "12 Months": 360,
            "2 Years": "2y",
            "3 Years": "3y",
            "From First": "first"
        }

        # Display date ranges using a sample stock
        with st.expander("📅 Date Ranges for Each Period", expanded=True):
            sample_ticker = stocks[0]
            sample_actual_date = get_nearest_date(sample_ticker, input_date)

            if sample_actual_date:
                st.info(f"**Using actual date:** {sample_actual_date} (nearest to {input_date})")

                # Get data in chronological order for year calculations
                df_full = fetch_price_history(sample_ticker, "1380-01-01", sample_actual_date)
                if not df_full.empty:
                    df_asc = df_full.iloc[::-1].reset_index(drop=True)

                    # Regular periods
                    for period_name, days_back in periods.items():
                        if period_name in ["2 Years", "3 Years", "From First"]:
                            continue
                        end_dt = datetime.strptime(sample_actual_date, "%Y-%m-%d")
                        calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
                        actual_start = find_nearest_date_backward(sample_ticker, calendar_start)
                        if actual_start:
                            st.write(f"**{period_name}:** {actual_start} to {sample_actual_date}")
                        else:
                            st.write(f"**{period_name}:** Not enough data before {calendar_start}")

                    # 2 Years
                    gain_2yr, date_2yr, _ = calculate_year_to_year_gain(df_asc, sample_actual_date, 2)
                    if gain_2yr is not None:
                        st.write(f"**2 Years:** {date_2yr} to {sample_actual_date}")
                    else:
                        st.write(f"**2 Years:** Not enough data")

                    # 3 Years
                    gain_3yr, date_3yr, _ = calculate_year_to_year_gain(df_asc, sample_actual_date, 3)
                    if gain_3yr is not None:
                        st.write(f"**3 Years:** {date_3yr} to {sample_actual_date}")
                    else:
                        st.write(f"**3 Years:** Not enough data")

                    # From First
                    gain_first, _, _, first_date, latest_date = calculate_from_first_date(df_asc)
                    if gain_first is not None:
                        st.write(f"**From First:** {first_date} to {latest_date}")
                    else:
                        st.write(f"**From First:** Not enough data")
            else:
                st.warning(f"No data found for {sample_ticker} near {input_date}")

        # Process stocks
        output = pd.DataFrame()
        compare_results = {}

        # First, process comparison ticker if provided
        if compare_ticker:
            with st.spinner(f"Processing comparison ticker: {compare_ticker}..."):
                compare_metrics = {"Name": compare_ticker}
                actual_date = get_nearest_date(compare_ticker, input_date)

                if actual_date:
                    st.info(f"Using date {actual_date} for {compare_ticker} (nearest to {input_date})")

                    # Get full data for year calculations
                    df_full = fetch_price_history(compare_ticker, "1380-01-01", actual_date)
                    df_asc = df_full.iloc[::-1].reset_index(drop=True) if not df_full.empty else pd.DataFrame()

                    for period_name, period_value in periods.items():
                        if period_name in ["2 Years", "3 Years", "From First"]:
                            if period_name == "2 Years" and not df_asc.empty:
                                gain, date_start, date_end = calculate_year_to_year_gain(df_asc, actual_date, 2)
                                if gain is not None:
                                    compare_results[period_name] = {"gain": gain}
                                    compare_metrics[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    df_period = df_asc[df_asc['J-Date'] >= date_start]
                                    if not df_period.empty:
                                        _, ceil, floor = calculate_metrics(df_period)
                                        compare_results[period_name]["ceil"] = ceil
                                        compare_results[period_name]["floor"] = floor
                                        compare_metrics[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                        compare_metrics[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                    else:
                                        compare_metrics[f"Ceil {period_name}"] = "N/A"
                                        compare_metrics[f"Floor {period_name}"] = "N/A"
                                else:
                                    compare_metrics[f"Gain {period_name}"] = "N/A"
                                    compare_metrics[f"Ceil {period_name}"] = "N/A"
                                    compare_metrics[f"Floor {period_name}"] = "N/A"
                            elif period_name == "3 Years" and not df_asc.empty:
                                gain, date_start, date_end = calculate_year_to_year_gain(df_asc, actual_date, 3)
                                if gain is not None:
                                    compare_results[period_name] = {"gain": gain}
                                    compare_metrics[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    df_period = df_asc[df_asc['J-Date'] >= date_start]
                                    if not df_period.empty:
                                        _, ceil, floor = calculate_metrics(df_period)
                                        compare_results[period_name]["ceil"] = ceil
                                        compare_results[period_name]["floor"] = floor
                                        compare_metrics[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                        compare_metrics[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                    else:
                                        compare_metrics[f"Ceil {period_name}"] = "N/A"
                                        compare_metrics[f"Floor {period_name}"] = "N/A"
                                else:
                                    compare_metrics[f"Gain {period_name}"] = "N/A"
                                    compare_metrics[f"Ceil {period_name}"] = "N/A"
                                    compare_metrics[f"Floor {period_name}"] = "N/A"
                            elif period_name == "From First" and not df_asc.empty:
                                gain, ceil, floor, first_date, latest_date = calculate_from_first_date(df_asc)
                                if gain is not None:
                                    compare_results[period_name] = {"gain": gain, "ceil": ceil, "floor": floor}
                                    compare_metrics[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    compare_metrics[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                    compare_metrics[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                else:
                                    compare_metrics[f"Gain {period_name}"] = "N/A"
                                    compare_metrics[f"Ceil {period_name}"] = "N/A"
                                    compare_metrics[f"Floor {period_name}"] = "N/A"
                        else:
                            end_dt = datetime.strptime(actual_date, "%Y-%m-%d")
                            calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                            start_date = find_nearest_date_backward(compare_ticker, calendar_start)
                            if start_date:
                                df = fetch_price_history(compare_ticker, start_date, actual_date)
                                gain, ceil, floor = calculate_metrics(df)
                                if gain is not None:
                                    compare_results[period_name] = {"gain": gain, "ceil": ceil, "floor": floor}
                                    compare_metrics[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    compare_metrics[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                    compare_metrics[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                else:
                                    compare_metrics[f"Gain {period_name}"] = "N/A"
                                    compare_metrics[f"Ceil {period_name}"] = "N/A"
                                    compare_metrics[f"Floor {period_name}"] = "N/A"
                            else:
                                compare_metrics[f"Gain {period_name}"] = "N/A"
                                compare_metrics[f"Ceil {period_name}"] = "N/A"
                                compare_metrics[f"Floor {period_name}"] = "N/A"
                else:
                    st.warning(f"⚠ Comparison ticker {compare_ticker} has no data near {input_date}")
                    for period_name in periods.keys():
                        compare_metrics[f"Gain {period_name}"] = "N/A"
                        compare_metrics[f"Ceil {period_name}"] = "N/A"
                        compare_metrics[f"Floor {period_name}"] = "N/A"

                output = pd.concat([output, pd.DataFrame([compare_metrics])], ignore_index=True)

        # Process all stocks
        with st.spinner(f"Processing {len(stocks)} stocks..."):
            stocks_without_data = []
            processed_count = 0

            progress_bar = st.progress(0)
            status_text = st.empty()

            for idx, ticker in enumerate(stocks):
                try:
                    status_text.text(f"Processing {ticker} ({idx+1}/{len(stocks)})...")
                    progress_bar.progress((idx + 1) / len(stocks))

                    results = {"Name": ticker}
                    actual_date = get_nearest_date(ticker, input_date)

                    if not actual_date:
                        stocks_without_data.append(ticker)
                        results["Name"] = f"{ticker} (No data near {input_date})"
                        for period_name in periods.keys():
                            results[f"Gain {period_name}"] = "N/A"
                            results[f"Ceil {period_name}"] = "N/A"
                            results[f"Floor {period_name}"] = "N/A"
                        output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)
                        continue

                    # Get full data for year calculations
                    df_full = fetch_price_history(ticker, "1380-01-01", actual_date)
                    df_asc = df_full.iloc[::-1].reset_index(drop=True) if not df_full.empty else pd.DataFrame()

                    # Calculate metrics for all periods
                    for period_name, period_value in periods.items():
                        if period_name in ["2 Years", "3 Years", "From First"]:
                            if period_name == "2 Years" and not df_asc.empty:
                                gain, date_start, date_end = calculate_year_to_year_gain(df_asc, actual_date, 2)
                                if gain is not None:
                                    results[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    df_period = df_asc[df_asc['J-Date'] >= date_start]
                                    if not df_period.empty:
                                        _, ceil, floor = calculate_metrics(df_period)
                                        results[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                        results[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                    else:
                                        results[f"Ceil {period_name}"] = "N/A"
                                        results[f"Floor {period_name}"] = "N/A"
                                else:
                                    results[f"Gain {period_name}"] = "N/A"
                                    results[f"Ceil {period_name}"] = "N/A"
                                    results[f"Floor {period_name}"] = "N/A"
                            elif period_name == "3 Years" and not df_asc.empty:
                                gain, date_start, date_end = calculate_year_to_year_gain(df_asc, actual_date, 3)
                                if gain is not None:
                                    results[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    df_period = df_asc[df_asc['J-Date'] >= date_start]
                                    if not df_period.empty:
                                        _, ceil, floor = calculate_metrics(df_period)
                                        results[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                        results[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                    else:
                                        results[f"Ceil {period_name}"] = "N/A"
                                        results[f"Floor {period_name}"] = "N/A"
                                else:
                                    results[f"Gain {period_name}"] = "N/A"
                                    results[f"Ceil {period_name}"] = "N/A"
                                    results[f"Floor {period_name}"] = "N/A"
                            elif period_name == "From First" and not df_asc.empty:
                                gain, ceil, floor, first_date, latest_date = calculate_from_first_date(df_asc)
                                if gain is not None:
                                    results[f"Gain {period_name}"] = f"{gain:.2f}%"
                                    results[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                    results[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                                else:
                                    results[f"Gain {period_name}"] = "N/A"
                                    results[f"Ceil {period_name}"] = "N/A"
                                    results[f"Floor {period_name}"] = "N/A"
                        else:
                            end_dt = datetime.strptime(actual_date, "%Y-%m-%d")
                            calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                            start_date = find_nearest_date_backward(ticker, calendar_start)
                            if start_date:
                                df = fetch_price_history(ticker, start_date, actual_date)
                                gain, ceil, floor = calculate_metrics(df)
                                results[f"Gain {period_name}"] = f"{gain:.2f}%" if gain is not None else "N/A"
                                results[f"Ceil {period_name}"] = f"{ceil:.2f}%" if ceil is not None else "N/A"
                                results[f"Floor {period_name}"] = f"{floor:.2f}%" if floor is not None else "N/A"
                            else:
                                results[f"Gain {period_name}"] = "N/A"
                                results[f"Ceil {period_name}"] = "N/A"
                                results[f"Floor {period_name}"] = "N/A"

                    output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)
                    processed_count += 1

                except Exception as e:
                    st.error(f"Error processing {ticker}: {e}")
                    results = {"Name": f"{ticker} (Error)"}
                    for period_name in periods.keys():
                        results[f"Gain {period_name}"] = "Error"
                        results[f"Ceil {period_name}"] = "Error"
                        results[f"Floor {period_name}"] = "Error"
                    output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)

            status_text.text(f"✅ Completed! Processed {processed_count} stocks")
            progress_bar.empty()

            if stocks_without_data:
                st.warning(f"⚠ {len(stocks_without_data)} stocks had no data near {input_date}")

        # Find top performers for each metric and period
        st.subheader("🏆 Top Performers by Period")
        valid_stocks = output[~output["Name"].str.contains("(No data|Error)")]

        if not valid_stocks.empty:
            cols = st.columns(3)
            col_idx = 0

            for period_name in periods.keys():
                gain_col = f"Gain {period_name}"
                if gain_col in valid_stocks.columns:
                    valid_stocks[f"{gain_col}_num"] = valid_stocks[gain_col].str.replace('%', '').str.replace('N/A', 'nan').astype(float)
                    max_gain_idx = valid_stocks[f"{gain_col}_num"].idxmax()

                    if pd.notna(max_gain_idx) and not pd.isna(valid_stocks.loc[max_gain_idx, f"{gain_col}_num"]):
                        max_ticker = valid_stocks.loc[max_gain_idx, "Name"]
                        max_gain = float(valid_stocks.loc[max_gain_idx, f"{gain_col}_num"])

                        with cols[col_idx % 3]:
                            st.metric(
                                label=f"**{period_name}**",
                                value=f"{max_gain:.2f}%",
                                delta=max_ticker
                            )
                        col_idx += 1
                    else:
                        with cols[col_idx % 3]:
                            st.write(f"**{period_name}:** No valid data")
                        col_idx += 1

        # Comparison with selected ticker
        if compare_ticker and compare_results and valid_stocks.shape[0] > 0:
            st.subheader("📊 Comparison Results")
            comparison_data = []

            for period_name in periods.keys():
                compare_metric = compare_results.get(period_name)
                if compare_metric:
                    gain_col = f"Gain {period_name}"
                    if gain_col in valid_stocks.columns:
                        valid_stocks[f"{gain_col}_num"] = valid_stocks[gain_col].str.replace('%', '').str.replace('N/A', 'nan').astype(float)
                        max_gain_idx = valid_stocks[f"{gain_col}_num"].idxmax()

                        if pd.notna(max_gain_idx) and not pd.isna(valid_stocks.loc[max_gain_idx, f"{gain_col}_num"]):
                            best_gain_ticker = valid_stocks.loc[max_gain_idx, "Name"]
                            best_gain_value = float(valid_stocks.loc[max_gain_idx, f"{gain_col}_num"])

                            comparison_data.append({
                                "Period": period_name,
                                "Your Stock Gain": f"{compare_metric.get('gain', 0):.2f}%",
                                "Your Stock Ceil": f"{compare_metric.get('ceil', 0):.2f}%" if compare_metric.get('ceil') is not None else "N/A",
                                "Your Stock Floor": f"{compare_metric.get('floor', 0):.2f}%" if compare_metric.get('floor') is not None else "N/A",
                                "Best Gain": f"{best_gain_value:.2f}% ({best_gain_ticker})",
                                "Difference": f"{compare_metric.get('gain', 0) - best_gain_value:.2f}%",
                                "Status": "✅ Ahead" if compare_metric.get('gain', 0) > best_gain_value else "❌ Behind"
                            })

            if comparison_data:
                comparison_df = pd.DataFrame(comparison_data)
                st.dataframe(comparison_df, use_container_width=True)

        # Display all results
        group_name = "All_Sectors"
        if selected_sector != "All Sectors":
            group_name = selected_sector
        if selected_sub_sector != "All Sub-Sectors":
            group_name = f"{group_name}_{selected_sub_sector}"

        st.subheader(f"📋 Stock Results - {group_name}")

        # Clean up temporary columns
        for col in output.columns:
            if col.endswith("_num"):
                output = output.drop(columns=[col])

        st.dataframe(output, use_container_width=True)

        # Add download button
        csv = output.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name=f"stock_metrics_{group_name}_{input_date}.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"❌ Error processing input date: {e}")

elif not input_date and show_analysis:
    st.warning("⚠ Please enter a date to analyze.")

# Display instructions
with st.expander("📖 How to use this app"):
    st.markdown("""
    ### 🔄 Update Stock Data
    1. Check the sidebar for latest database info
    2. Click "Update Stock Data" to fetch new data
    3. The app will automatically update from the day after the latest date to yesterday

    ### 📈 Analyze Stock Performance
    1. Enter an analysis date (defaults to latest date in DB)
    2. **Select a Sector** from the first dropdown
    3. **Select a Sub-Sector** from the second dropdown (filters based on selected sector)
    4. Optionally enter a stock ticker to compare
    5. Click "Calculate Stock Metrics"

    ### 📊 Understanding the Metrics
    - **Gain**: Percentage change from start to end of period (based on Adjusted Close - قیمت پایانی تعدیل شده)
    - **Ceil**: Percentage change from the highest price to end price (positive means ending above high)
    - **Floor**: Percentage change from the lowest price to end price (positive means ending above low)

    ### ⏱️ Periods Analyzed
    The app analyzes metrics for:
    - **1 Month**: ~30 days
    - **3 Months**: ~90 days
    - **6 Months**: ~180 days
    - **12 Months**: ~360 days
    - **2 Years**: Same date 2 years ago to today
    - **3 Years**: Same date 3 years ago to today
    - **From First**: First recorded date to today

    ### 📂 Filtering Options
    - **Sector**: Main industry category (e.g., "خودرو", "بانک", "پتروشیمی")
    - **Sub-Sector**: More specific category within a sector
    - The sub-sector dropdown automatically updates based on the selected sector
    - Choose "All Sectors" or "All Sub-Sectors" to include everything

    ### 🔍 Handling Missing Data
    - For each stock, the app finds the nearest available date to your input date
    - If a stock has no data near the input date, it will be marked as "No data"
    - The app continues processing all stocks without stopping on errors

    ### 🏆 Top Performers
    - **Best Gain**: Stock with highest gain in the period
    - **Best Ceil**: Stock closest to its high (ceil value closest to 0 or positive)
    - **Best Floor**: Stock with best floor performance (value closest to 0 or positive)
    """)

# Footer
st.markdown("---")
st.caption("Stock Performance Analyzer v3.4 | Using adj_final (قیمت پایانی تعدیل شده) for all calculations!")