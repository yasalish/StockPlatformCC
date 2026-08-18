import psycopg2
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
import jdatetime
import sys
import os
import numpy as np
from decimal import Decimal
import io

# Add the directory containing the ETF updater module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Try to import the ETF updater module
try:
    from etf_updater import update_etf_prices, get_yesterday_jalali_date
    ETF_UPDATER_AVAILABLE = True
except ImportError:
    st.warning("ETF updater module not found. Update functionality will be limited.")
    ETF_UPDATER_AVAILABLE = False

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
    """Get the latest date available in the database."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT MAX(j_date) FROM EtfPriceHistory"
                cursor.execute(query)
                result = cursor.fetchone()
                return result[0] if result and result[0] else None
    except Exception as e:
        st.error(f"Error getting latest date from database: {e}")
        return None

def get_earliest_date_in_db():
    """Get the earliest date available in the database."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT MIN(j_date) FROM EtfPriceHistory"
                cursor.execute(query)
                result = cursor.fetchone()
                return result[0] if result and result[0] else None
    except Exception as e:
        st.error(f"Error getting earliest date from database: {e}")
        return None

def get_yesterday_date():
    """Get yesterday's date in Jalali format."""
    try:
        # Try to use the function from etf_updater if available
        if ETF_UPDATER_AVAILABLE:
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

def calculate_same_date_previous_year(current_date_str, years_back):
    """
    Calculate the same date in previous year(s).

    Parameters:
    -----------
    current_date_str : str
        Current date in Jalali format (YYYY-MM-DD)
    years_back : int
        Number of years to go back

    Returns:
    --------
    str: Date in previous year in Jalali format
    """
    try:
        year, month, day = map(int, current_date_str.split('-'))
        previous_year = year - years_back

        # Try to create the exact date
        try:
            date_obj = jdatetime.date(previous_year, month, day)
            return str(date_obj)
        except ValueError:
            # If date doesn't exist (e.g., day 30 in a month with 29 days), adjust
            # Try to use the last day of the month
            try:
                # Start from first day of month
                date_obj = jdatetime.date(previous_year, month, 1)
                # Find last day of month
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
                # If still fails, use the 1st of the month
                return f"{previous_year:04d}-{month:02d}-01"

    except Exception as e:
        st.error(f"Error calculating same date {years_back} year(s) back: {e}")
        return None

def fetch_etf_names(selected_type):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT id, ticker FROM ETF WHERE type = %s ORDER BY ticker"
                cursor.execute(query, (selected_type,))
                return cursor.fetchall()
    except Exception as e:
        st.error(f"Error fetching ETF names: {e}")
        return []

def fetch_etf_full_history(ticker, start_date, end_date):
    """Fetch all columns from EtfPriceHistory for a given ticker and date range."""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT *
                    FROM EtfPriceHistory
                    WHERE ticker = %s AND j_date BETWEEN %s AND %s
                    ORDER BY j_date DESC
                """
                cursor.execute(query, (ticker, start_date, end_date))
                rows = cursor.fetchall()
                # Get column names
                colnames = [desc[0] for desc in cursor.description]
                df = pd.DataFrame(rows, columns=colnames)
                return df
    except Exception as e:
        st.error(f"Error fetching full history for {ticker}: {e}")
        return pd.DataFrame()

def fetch_price_history(ticker, start_date, end_date):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date, adj_final
                    FROM EtfPriceHistory
                    WHERE ticker = %s AND j_date BETWEEN %s AND %s
                    ORDER BY j_date
                """
                cursor.execute(query, (ticker, start_date, end_date))
                rows = cursor.fetchall()
        # Convert Decimal to float for all price values
        df = pd.DataFrame(rows, columns=["J-Date", "Adj Final"])
        df["Adj Final"] = df["Adj Final"].apply(lambda x: float(x) if x is not None else None)
        return df
    except Exception as e:
        st.error(f"Error fetching price history for {ticker}: {e}")
        return pd.DataFrame()

def get_price_on_date(ticker, target_date, use_nearest=True):
    """
    Get the price on a specific date.
    If use_nearest=True and exact date not found, find nearest available date.

    Returns: (date_used, price) tuple
    """
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                # First try exact date
                query = """
                    SELECT j_date, adj_final
                    FROM EtfPriceHistory
                    WHERE ticker = %s AND j_date = %s
                """
                cursor.execute(query, (ticker, target_date))
                row = cursor.fetchone()

                if row:
                    # Convert Decimal to float
                    return row[0], float(row[1]) if row[1] is not None else None

                # If exact date not found and we should use nearest date
                if use_nearest:
                    # Find nearest date BEFORE the target date
                    query_before = """
                        SELECT j_date, adj_final
                        FROM EtfPriceHistory
                        WHERE ticker = %s AND j_date < %s
                        ORDER BY j_date DESC
                        LIMIT 1
                    """
                    cursor.execute(query_before, (ticker, target_date))
                    row_before = cursor.fetchone()

                    # Find nearest date AFTER the target date
                    query_after = """
                        SELECT j_date, adj_final
                        FROM EtfPriceHistory
                        WHERE ticker = %s AND j_date > %s
                        ORDER BY j_date ASC
                        LIMIT 1
                    """
                    cursor.execute(query_after, (ticker, target_date))
                    row_after = cursor.fetchone()

                    # Choose the nearest date (smallest difference)
                    if row_before and row_after:
                        # Calculate which one is closer
                        before_date = datetime.strptime(row_before[0], "%Y-%m-%d")
                        target_date_dt = datetime.strptime(target_date, "%Y-%m-%d")
                        after_date = datetime.strptime(row_after[0], "%Y-%m-%d")

                        diff_before = (target_date_dt - before_date).days
                        diff_after = (after_date - target_date_dt).days

                        if diff_before <= diff_after:
                            return row_before[0], float(row_before[1]) if row_before[1] is not None else None
                        else:
                            return row_after[0], float(row_after[1]) if row_after[1] is not None else None
                    elif row_before:
                        return row_before[0], float(row_before[1]) if row_before[1] is not None else None
                    elif row_after:
                        return row_after[0], float(row_after[1]) if row_after[1] is not None else None

                # No data found
                return None, None

    except Exception as e:
        st.error(f"Error getting price for {ticker} on or near {target_date}: {e}")
        return None, None

def date_exists(ticker, target_date):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT j_date
                    FROM EtfPriceHistory
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
                    FROM EtfPriceHistory
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
                    FROM EtfPriceHistory
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

    # Ensure both values are float
    start = float(start) if start is not None else 0
    end = float(end) if end is not None else 0

    if start == 0:
        return None

    gain = ((end - start) / start) * 100
    return float(gain)

def calculate_year_to_year_gain(ticker, current_date, years_back):
    """
    Calculate gain from same date in year-(years_back) to same date in year-(years_back-1)

    Example for years_back=2:
    Gain from date in year-2 to date in year-1

    Example for years_back=3:
    Gain from date in year-3 to date in year-2
    """
    try:
        # Calculate theoretical dates
        date_year_n_theoretical = calculate_same_date_previous_year(current_date, years_back)
        date_year_n_minus_1_theoretical = calculate_same_date_previous_year(current_date, years_back - 1)

        if not date_year_n_theoretical or not date_year_n_minus_1_theoretical:
            return None, None, None

        # Get actual dates and prices (using nearest available dates)
        date_year_n_actual, price_year_n = get_price_on_date(ticker, date_year_n_theoretical, use_nearest=True)
        date_year_n_minus_1_actual, price_year_n_minus_1 = get_price_on_date(ticker, date_year_n_minus_1_theoretical, use_nearest=True)

        if price_year_n is None or price_year_n_minus_1 is None or date_year_n_actual is None or date_year_n_minus_1_actual is None:
            return None, None, None

        # Ensure prices are float
        price_year_n = float(price_year_n) if price_year_n is not None else None
        price_year_n_minus_1 = float(price_year_n_minus_1) if price_year_n_minus_1 is not None else None

        if price_year_n is None or price_year_n_minus_1 is None or price_year_n == 0:
            return None, None, None

        # Calculate gain
        gain = ((price_year_n_minus_1 - price_year_n) / price_year_n) * 100

        # Return gain along with actual dates used
        return float(gain), date_year_n_actual, date_year_n_minus_1_actual

    except Exception as e:
        st.error(f"Error calculating year-to-year gain for {ticker}: {e}")
        return None, None, None

def get_all_periods():
    """Return all periods for analysis"""
    return {
        "5 Days": 5,
        "10 Days": 10,
        "15 Days": 15,
        "20 Days": 20,
        "30 Days": 30,
        "60 Days": 60,
        "90 Days": 90,
        "120 Days": 120,
        "180 Days": 180,
        "360 Days": 360,
        "2 Years Ago to 1 Year Ago": "2y_to_1y",
        "3 Years Ago to 2 Years Ago": "3y_to_2y"
    }

def get_etf_gains(ticker, input_date, all_periods):
    """Get gains for a single ETF for all periods"""
    gains = {}
    dates_used = {}

    # Check if ETF has data for the input date
    if not date_exists(ticker, input_date):
        return None, None

    for period_name, period_value in all_periods.items():
        if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
            if period_name == "2 Years Ago to 1 Year Ago":
                gain, date_start, date_end = calculate_year_to_year_gain(ticker, input_date, 2)
            else:
                gain, date_start, date_end = calculate_year_to_year_gain(ticker, input_date, 3)

            if gain is not None:
                gains[period_name] = gain
                dates_used[period_name] = (date_start, date_end)
            else:
                gains[period_name] = None
                dates_used[period_name] = (None, None)
        else:
            if period_value <= 20:
                start_date = get_n_trading_days_before(ticker, input_date, period_value)
            else:
                end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                start_date = find_nearest_date_backward(ticker, calendar_start)

            if start_date:
                df = fetch_price_history(ticker, start_date, input_date)
                gain = calculate_return(df)
                if gain is not None:
                    gains[period_name] = gain
                    dates_used[period_name] = (start_date, input_date)
                else:
                    gains[period_name] = None
                    dates_used[period_name] = (None, input_date)
            else:
                gains[period_name] = None
                dates_used[period_name] = (None, input_date)

    return gains, dates_used

def display_etf_gains_table(input_date, selected_type, compare_ticker):
    """Display the ETF gains table for a specific type and store in session state"""
    try:
        st.write(f"**Analysis Date: {input_date}**")

        # Validate date format
        try:
            datetime.strptime(input_date, "%Y-%m-%d")
        except ValueError:
            st.error("❌ Invalid date format. Please use YYYY-MM-DD format.")
            return

        # Fetch all ETFs for the selected type
        etfs = fetch_etf_names(selected_type)

        if not etfs:
            st.error("❌ No ETFs found for the selected type")
            return

        # Check if the first ETF has data for the input date
        sample_ticker = etfs[0][1]
        if not date_exists(sample_ticker, input_date):
            st.error(f"❌ Date {input_date} not found in database for {sample_ticker}. Please enter a valid trading date.")
            return

        st.success(f"✅ Date {input_date} found in database")

        # Define ALL periods
        all_periods = get_all_periods()

        # Display date ranges for each period
        with st.expander("📅 Date Ranges for Each Period", expanded=True):
            for period_name, days_back in all_periods.items():
                if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                    if period_name == "2 Years Ago to 1 Year Ago":
                        year_2_theoretical = calculate_same_date_previous_year(input_date, 2)
                        year_1_theoretical = calculate_same_date_previous_year(input_date, 1)
                        year_2_actual, _ = get_price_on_date(sample_ticker, year_2_theoretical, use_nearest=True)
                        year_1_actual, _ = get_price_on_date(sample_ticker, year_1_theoretical, use_nearest=True)
                        if year_2_actual and year_1_actual:
                            st.write(f"**{period_name}:** {year_2_actual} to {year_1_actual}")
                        else:
                            st.write(f"**{period_name}:** No data available")
                    else:
                        year_3_theoretical = calculate_same_date_previous_year(input_date, 3)
                        year_2_theoretical = calculate_same_date_previous_year(input_date, 2)
                        year_3_actual, _ = get_price_on_date(sample_ticker, year_3_theoretical, use_nearest=True)
                        year_2_actual, _ = get_price_on_date(sample_ticker, year_2_theoretical, use_nearest=True)
                        if year_3_actual and year_2_actual:
                            st.write(f"**{period_name}:** {year_3_actual} to {year_2_actual}")
                        else:
                            st.write(f"**{period_name}:** No data available")
                else:
                    end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                    calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")

                    if days_back <= 20:
                        start_date = get_n_trading_days_before(sample_ticker, input_date, days_back)
                        if start_date:
                            st.write(f"**{period_name}:** {start_date} to {input_date}")
                        else:
                            st.write(f"**{period_name}:** Not enough trading data before {input_date}")
                    else:
                        actual_start = find_nearest_date_backward(sample_ticker, calendar_start)
                        if actual_start:
                            st.write(f"**{period_name}:** {actual_start} to {input_date}")
                        else:
                            st.write(f"**{period_name}:** Not enough data before {calendar_start}")

        # Process each ETF
        output = pd.DataFrame()
        compare_gains = {}  # Store gains for the comparison ticker if provided

        # First, process the comparison ticker if provided
        if compare_ticker:
            with st.spinner(f"Processing comparison ticker: {compare_ticker}..."):
                compare_results = {"Name": compare_ticker}

                if not date_exists(compare_ticker, input_date):
                    st.warning(f"⚠ Comparison ticker {compare_ticker} has no data for {input_date}")
                else:
                    for period_name, period_value in all_periods.items():
                        if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                            if period_name == "2 Years Ago to 1 Year Ago":
                                gain, date_start, date_end = calculate_year_to_year_gain(compare_ticker, input_date, 2)
                            else:
                                gain, date_start, date_end = calculate_year_to_year_gain(compare_ticker, input_date, 3)

                            if gain is not None:
                                compare_gains[period_name] = float(gain)
                                compare_results[f"Gain ({period_name})"] = f"{gain:.2f}%"
                            else:
                                compare_gains[period_name] = None
                                compare_results[f"Gain ({period_name})"] = "N/A"
                        else:
                            if period_value <= 20:
                                start_date = get_n_trading_days_before(compare_ticker, input_date, period_value)
                            else:
                                end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                                calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                                start_date = find_nearest_date_backward(compare_ticker, calendar_start)

                            if start_date:
                                df = fetch_price_history(compare_ticker, start_date, input_date)
                                gain = calculate_return(df)
                                if gain is not None:
                                    compare_gains[period_name] = float(gain)
                                    compare_results[f"Gain ({period_name})"] = f"{gain:.2f}%"
                                else:
                                    compare_gains[period_name] = None
                                    compare_results[f"Gain ({period_name})"] = "N/A"
                            else:
                                compare_gains[period_name] = None
                                compare_results[f"Gain ({period_name})"] = "N/A"

                    output = pd.concat([output, pd.DataFrame([compare_results])], ignore_index=True)

        # Process all ETFs
        with st.spinner(f"Processing {len(etfs)} ETFs..."):
            for _, ticker in etfs:
                try:
                    results = {"Name": ticker}

                    if not date_exists(ticker, input_date):
                        results["Name"] = f"{ticker} (No data for {input_date})"
                        for period_name in all_periods.keys():
                            results[f"Gain ({period_name})"] = "N/A"
                        output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)
                        continue

                    for period_name, period_value in all_periods.items():
                        if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                            if period_name == "2 Years Ago to 1 Year Ago":
                                gain, date_start, date_end = calculate_year_to_year_gain(ticker, input_date, 2)
                            else:
                                gain, date_start, date_end = calculate_year_to_year_gain(ticker, input_date, 3)

                            results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                        else:
                            if period_value <= 20:
                                start_date = get_n_trading_days_before(ticker, input_date, period_value)
                            else:
                                end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                                calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                                start_date = find_nearest_date_backward(ticker, calendar_start)

                            if start_date:
                                df = fetch_price_history(ticker, start_date, input_date)
                                gain = calculate_return(df)
                                results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                            else:
                                results[f"Gain ({period_name})"] = "N/A"

                    output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)

                except Exception as e:
                    st.error(f"Error processing {ticker}: {e}")
                    results = {"Name": f"{ticker} (Error)"}
                    for period_name in all_periods.keys():
                        results[f"Gain ({period_name})"] = "Error"
                    output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)

        # Find top performers for each period
        st.subheader("🏆 Top Performers by Period")
        top_performers = {}

        valid_etfs = output[~output["Name"].str.contains("(No data|Error)")]

        if not valid_etfs.empty:
            cols = st.columns(3)
            col_idx = 0

            for period_name in all_periods.keys():
                col_name = f"Gain ({period_name})"
                valid_etfs[f"{col_name}_num"] = valid_etfs[col_name].str.replace('%', '').str.replace('N/A', 'nan').astype(float)

                max_idx = valid_etfs[f"{col_name}_num"].idxmax()
                if pd.notna(max_idx) and not pd.isna(valid_etfs.loc[max_idx, f"{col_name}_num"]):
                    max_ticker = valid_etfs.loc[max_idx, "Name"]
                    max_gain = float(valid_etfs.loc[max_idx, f"{col_name}_num"])
                    top_performers[period_name] = (max_ticker, max_gain)

                    with cols[col_idx % 3]:
                        st.metric(
                            label=f"**{period_name}**",
                            value=f"{max_gain:.2f}%",
                            delta=max_ticker
                        )
                    col_idx += 1
                else:
                    top_performers[period_name] = (None, None)
                    with cols[col_idx % 3]:
                        st.write(f"**{period_name}:** No valid data")
                    col_idx += 1

        # Compare with selected ticker if provided
        if compare_ticker and compare_gains and valid_etfs.shape[0] > 0:
            st.subheader("📊 Comparison Results")
            comparison_data = []

            for period_name in all_periods.keys():
                compare_gain = compare_gains.get(period_name)
                top_ticker, top_gain = top_performers.get(period_name, (None, None))

                if compare_gain is not None and top_gain is not None:
                    compare_gain_float = float(compare_gain)
                    top_gain_float = float(top_gain)
                    diff = compare_gain_float - top_gain_float
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": f"{compare_gain_float:.2f}%",
                        "Top Performer": top_ticker,
                        "Top Gain": f"{top_gain_float:.2f}%",
                        "Difference": f"{diff:.2f}%",
                        "Status": "✅ Ahead" if diff > 0 else "❌ Behind"
                    })
                elif compare_gain is not None:
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": f"{compare_gain:.2f}%",
                        "Top Performer": "N/A",
                        "Top Gain": "N/A",
                        "Difference": "N/A",
                        "Status": "⚠ No comparison"
                    })
                else:
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": "N/A",
                        "Top Performer": top_ticker if top_ticker else "N/A",
                        "Top Gain": f"{top_gain:.2f}%" if top_gain else "N/A",
                        "Difference": "N/A",
                        "Status": "📭 No data"
                    })

            if comparison_data:
                comparison_df = pd.DataFrame(comparison_data)
                st.dataframe(comparison_df, use_container_width=True)

        # Display all results
        st.subheader("📋 All ETF Results")

        for period_name in all_periods.keys():
            col_name = f"Gain ({period_name})_num"
            if col_name in output.columns:
                output = output.drop(columns=[col_name])

        st.dataframe(output, use_container_width=True)

        # Store results in session state so they persist
        st.session_state['main_table_output'] = output
        st.session_state['main_table_top_performers'] = top_performers
        st.session_state['main_table_compare_gains'] = compare_gains
        st.session_state['main_table_valid_etfs'] = valid_etfs
        st.session_state['main_table_has_data'] = True

        # Add download button
        csv = output.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name=f"etf_gains_{input_date}_{selected_type}.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"❌ Error processing input date: {e}")

def export_etf_full_history_to_excel(selected_type, start_date, end_date):
    """Export full history of all ETFs in a category to Excel with each ETF in a separate sheet."""
    try:
        # Fetch all ETFs for the selected type
        etfs = fetch_etf_names(selected_type)

        if not etfs:
            st.error("❌ No ETFs found for the selected type")
            return None

        # Create a BytesIO object for the Excel file
        output = io.BytesIO()

        # Create Excel writer
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            for _, ticker in etfs:
                # Fetch full history for this ETF
                df = fetch_etf_full_history(ticker, start_date, end_date)

                if not df.empty:
                    # Write to sheet with ETF ticker as sheet name
                    # Excel sheet names have a 31 character limit
                    sheet_name = ticker[:31]
                    df.to_excel(writer, sheet_name=sheet_name, index=False)

                    # Auto-adjust column widths
                    worksheet = writer.sheets[sheet_name]
                    for i, col in enumerate(df.columns):
                        # Get the maximum length of the column
                        max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                        worksheet.set_column(i, i, min(max_len, 50))

        # Get the value from the BytesIO buffer
        output.seek(0)
        return output

    except Exception as e:
        st.error(f"❌ Error exporting to Excel: {e}")
        return None

# Initialize session state for main table
if 'main_table_has_data' not in st.session_state:
    st.session_state['main_table_has_data'] = False
if 'main_table_output' not in st.session_state:
    st.session_state['main_table_output'] = pd.DataFrame()
if 'main_table_top_performers' not in st.session_state:
    st.session_state['main_table_top_performers'] = {}
if 'main_table_compare_gains' not in st.session_state:
    st.session_state['main_table_compare_gains'] = {}
if 'main_table_valid_etfs' not in st.session_state:
    st.session_state['main_table_valid_etfs'] = pd.DataFrame()

# Main app
st.title("📈 ETF Performance Analyzer & Updater")

# Sidebar for update functionality
with st.sidebar:
    st.header("📊 Database Information")

    # Get latest date from database
    latest_date = get_latest_date_in_db()
    earliest_date = get_earliest_date_in_db()
    yesterday_date = get_yesterday_date()

    if latest_date:
        st.info(f"**Latest date in database:** {latest_date}")
    else:
        st.warning("No data found in database.")

    if earliest_date:
        st.info(f"**Earliest date in database:** {earliest_date}")

    if yesterday_date:
        st.info(f"**Yesterday's date:** {yesterday_date}")

    st.header("🔄 Update ETF Data")

    if ETF_UPDATER_AVAILABLE:
        if latest_date and yesterday_date:
            # Calculate update range
            update_start_date = get_next_day(latest_date)

            if update_start_date:
                st.write(f"**Update range:** {update_start_date} to {yesterday_date}")

                if st.button("🔄 Update ETF Data", type="primary", use_container_width=True):
                    with st.spinner(f"Updating ETF data from {update_start_date} to {yesterday_date}..."):
                        try:
                            success, failure, total = update_etf_prices(
                                start_date=update_start_date,
                                end_date=yesterday_date
                            )

                            if total > 0:
                                st.success(f"✅ Update completed!")
                                st.write(f"- Successfully updated: {success} ETFs")
                                st.write(f"- Failed: {failure} ETFs")
                                st.write(f"- Total: {total} ETFs")

                                # Refresh the page to show updated data
                                st.rerun()
                            else:
                                st.warning("No ETFs found to update.")

                        except Exception as e:
                            st.error(f"Error updating ETF data: {e}")
            else:
                st.warning("Cannot calculate update range.")
        else:
            st.warning("Need both latest date and yesterday's date to update.")
    else:
        st.error("ETF updater module not available.")

# ============ SECTION 1: ETF TYPE SELECTION AND ALL ETFs ANALYSIS ============
st.header("📈 ETF Performance Analysis")

# Create two columns for input
col1, col2 = st.columns(2)

with col1:
    input_date = st.text_input(
        "Enter analysis date (YYYY-MM-DD):",
        value=latest_date if latest_date else "",
        help="Enter the date you want to analyze ETF performance for"
    )

with col2:
    compare_ticker = st.text_input(
        "Enter a stock ticker to compare (optional):",
        help="Compare this stock with ETF performance"
    ).strip()

# ETF type selection
st.subheader("📋 Select ETF Type")
etf_types = ["ثابت", "در سهام", "کالا", "مختلط"]
selected_type = st.selectbox("Choose ETF category:", etf_types, index=1)

# Button to show gains
show_gains = st.button("📊 Calculate ETF Gains", type="primary")

if show_gains and input_date:
    display_etf_gains_table(input_date, selected_type, compare_ticker)
elif not input_date and show_gains:
    st.warning("⚠ Please enter a date to analyze.")

# Display stored main table results if they exist (and no new calculation was done)
if st.session_state['main_table_has_data'] and not show_gains:
    # Show stored results
    st.subheader("📋 All ETF Results (Previously Calculated)")
    st.dataframe(st.session_state['main_table_output'], use_container_width=True)

    # Show top performers if they exist
    if st.session_state['main_table_top_performers']:
        st.subheader("🏆 Top Performers by Period")
        cols = st.columns(3)
        col_idx = 0
        for period_name, (ticker, gain) in st.session_state['main_table_top_performers'].items():
            if ticker and gain is not None:
                with cols[col_idx % 3]:
                    st.metric(
                        label=f"**{period_name}**",
                        value=f"{gain:.2f}%",
                        delta=ticker
                    )
                col_idx += 1

    # Show comparison results if they exist
    if st.session_state['main_table_compare_gains'] and not st.session_state['main_table_valid_etfs'].empty:
        st.subheader("📊 Comparison Results")
        comparison_data = []
        all_periods = get_all_periods()

        for period_name in all_periods.keys():
            compare_gain = st.session_state['main_table_compare_gains'].get(period_name)
            top_ticker, top_gain = st.session_state['main_table_top_performers'].get(period_name, (None, None))

            if compare_gain is not None and top_gain is not None:
                diff = compare_gain - top_gain
                comparison_data.append({
                    "Period": period_name,
                    "Your Stock": f"{compare_gain:.2f}%",
                    "Top Performer": top_ticker,
                    "Top Gain": f"{top_gain:.2f}%",
                    "Difference": f"{diff:.2f}%",
                    "Status": "✅ Ahead" if diff > 0 else "❌ Behind"
                })
            elif compare_gain is not None:
                comparison_data.append({
                    "Period": period_name,
                    "Your Stock": f"{compare_gain:.2f}%",
                    "Top Performer": "N/A",
                    "Top Gain": "N/A",
                    "Difference": "N/A",
                    "Status": "⚠ No comparison"
                })

        if comparison_data:
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, use_container_width=True)

# ============ SECTION 2: ETF COMPARISON ============
st.markdown("---")
st.header("📊 Compare Two ETFs")

# Separate ETF type selection for comparison
st.subheader("📋 Select ETF Category for Comparison")
comparison_etf_types = ["ثابت", "در سهام", "کالا", "مختلط"]
comparison_type = st.selectbox(
    "Choose ETF category for comparison:",
    options=comparison_etf_types,
    index=1,
    key="comparison_type_selector"
)

# Get ETF names for the comparison type
comparison_etfs = fetch_etf_names(comparison_type)
comparison_etf_options = [""] + [etf[1] for etf in comparison_etfs] if comparison_etfs else [""]

# Create two columns for ETF selection
col1, col2 = st.columns(2)

with col1:
    etf1 = st.selectbox(
        "Select First ETF:",
        options=comparison_etf_options,
        key="compare_etf1"
    )

with col2:
    etf2 = st.selectbox(
        "Select Second ETF:",
        options=comparison_etf_options,
        key="compare_etf2"
    )

# Comparison button
compare_etfs = st.button("📊 Compare ETFs", type="secondary")

if compare_etfs:
    # Get the analysis date from the main section
    analysis_date = input_date if 'input_date' in locals() else latest_date

    if not analysis_date:
        st.warning("⚠ Please enter an analysis date in the section above first.")
    elif not etf1 and not etf2:
        st.warning("⚠ Please select at least one ETF to compare.")
    else:
        try:
            all_periods = get_all_periods()

            # Get gains for both ETFs
            gains_data = {}
            selected_etfs = []

            if etf1:
                if date_exists(etf1, analysis_date):
                    gains1, dates1 = get_etf_gains(etf1, analysis_date, all_periods)
                    if gains1:
                        gains_data[etf1] = gains1
                        selected_etfs.append(etf1)
                    else:
                        st.warning(f"⚠ {etf1} has no data for {analysis_date}")
                else:
                    st.warning(f"⚠ {etf1} has no data for {analysis_date}")

            if etf2:
                if date_exists(etf2, analysis_date):
                    gains2, dates2 = get_etf_gains(etf2, analysis_date, all_periods)
                    if gains2:
                        gains_data[etf2] = gains2
                        selected_etfs.append(etf2)
                    else:
                        st.warning(f"⚠ {etf2} has no data for {analysis_date}")
                else:
                    st.warning(f"⚠ {etf2} has no data for {analysis_date}")

            if len(gains_data) >= 2:
                st.subheader(f"📊 ETF Comparison: {etf1} vs {etf2}")
                st.write(f"**Analysis Date:** {analysis_date}")
                st.write(f"**ETF Category:** {comparison_type}")

                comparison_data = []
                gains1 = gains_data[etf1]
                gains2 = gains_data[etf2]

                for period_name in all_periods.keys():
                    g1 = gains1.get(period_name)
                    g2 = gains2.get(period_name)

                    if g1 is not None and g2 is not None:
                        diff = g1 - g2
                        comparison_data.append({
                            "Period": period_name,
                            etf1: f"{g1:.2f}%",
                            etf2: f"{g2:.2f}%",
                            "Difference": f"{diff:.2f}%",
                            "Better Performer": etf1 if diff > 0 else (etf2 if diff < 0 else "Equal")
                        })
                    elif g1 is not None:
                        comparison_data.append({
                            "Period": period_name,
                            etf1: f"{g1:.2f}%",
                            etf2: "N/A",
                            "Difference": "N/A",
                            "Better Performer": etf1
                        })
                    elif g2 is not None:
                        comparison_data.append({
                            "Period": period_name,
                            etf1: "N/A",
                            etf2: f"{g2:.2f}%",
                            "Difference": "N/A",
                            "Better Performer": etf2
                        })
                    else:
                        comparison_data.append({
                            "Period": period_name,
                            etf1: "N/A",
                            etf2: "N/A",
                            "Difference": "N/A",
                            "Better Performer": "N/A"
                        })

                if comparison_data:
                    comparison_df = pd.DataFrame(comparison_data)
                    st.dataframe(comparison_df, use_container_width=True)

                    # Summary statistics
                    st.subheader("📊 Comparison Summary")

                    # Count wins for each ETF
                    wins1 = 0
                    wins2 = 0
                    ties = 0

                    for row in comparison_data:
                        if row["Better Performer"] == etf1:
                            wins1 += 1
                        elif row["Better Performer"] == etf2:
                            wins2 += 1
                        elif row["Better Performer"] == "Equal":
                            ties += 1

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric(f"🏆 {etf1} Wins", wins1)
                    with col2:
                        st.metric(f"🏆 {etf2} Wins", wins2)
                    with col3:
                        st.metric("🤝 Ties", ties)
                    with col4:
                        total_periods = len([r for r in comparison_data if r["Better Performer"] != "N/A"])
                        if total_periods > 0:
                            winner = etf1 if wins1 > wins2 else (etf2 if wins2 > wins1 else "Equal")
                            st.metric("👑 Overall Winner", winner)

                    # Download comparison results
                    csv = comparison_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Download Comparison Results as CSV",
                        data=csv,
                        file_name=f"etf_comparison_{etf1}_vs_{etf2}_{analysis_date}.csv",
                        mime="text/csv"
                    )
            elif len(gains_data) == 1:
                st.info("Select two ETFs to compare them side by side.")
            else:
                st.warning("No data available for the selected ETFs.")

        except Exception as e:
            st.error(f"❌ Error comparing ETFs: {e}")

# ============ SECTION 3: EXPORT FULL HISTORY TO EXCEL ============
st.markdown("---")
st.header("📥 Export Full ETF History to Excel")

st.subheader("📋 Select ETF Category for Export")
export_etf_types = ["ثابت", "در سهام", "کالا", "مختلط"]
export_type = st.selectbox(
    "Choose ETF category to export:",
    options=export_etf_types,
    index=1,
    key="export_type_selector"
)

# Date range selection for export
st.subheader("📅 Select Date Range for Export")

# Get available date range
available_earliest = get_earliest_date_in_db()
available_latest = get_latest_date_in_db()

col1, col2 = st.columns(2)

with col1:
    export_start_date = st.text_input(
        "Start Date (YYYY-MM-DD):",
        value=available_earliest if available_earliest else "",
        help="Enter the start date for export"
    )

with col2:
    export_end_date = st.text_input(
        "End Date (YYYY-MM-DD):",
        value=available_latest if available_latest else "",
        help="Enter the end date for export"
    )

# Export button
export_btn = st.button("📥 Export to Excel", type="primary", key="export_btn")

if export_btn:
    if not export_start_date or not export_end_date:
        st.warning("⚠ Please enter both start and end dates.")
    else:
        try:
            # Validate dates
            datetime.strptime(export_start_date, "%Y-%m-%d")
            datetime.strptime(export_end_date, "%Y-%m-%d")

            with st.spinner(f"Exporting {export_type} ETFs from {export_start_date} to {export_end_date}..."):
                excel_data = export_etf_full_history_to_excel(export_type, export_start_date, export_end_date)

                if excel_data is not None:
                    st.success("✅ Export completed successfully!")

                    # Download button for the Excel file
                    st.download_button(
                        label="📥 Download Excel File",
                        data=excel_data,
                        file_name=f"etf_full_history_{export_type}_{export_start_date}_to_{export_end_date}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                    # Show info about the export
                    etfs = fetch_etf_names(export_type)
                    st.info(f"📊 Exported {len(etfs)} ETFs from {export_type} category")

        except ValueError:
            st.error("❌ Invalid date format. Please use YYYY-MM-DD format.")
        except Exception as e:
            st.error(f"❌ Error exporting to Excel: {e}")

# Display instructions
with st.expander("📖 How to use this app"):
    st.markdown("""
    ### 🔄 Update ETF Data
    1. Check the sidebar for latest database info
    2. Click "Update ETF Data" to fetch new data
    3. The app will automatically update from the day after the latest date to yesterday

    ### 📈 Analyze ETF Performance (Section 1)
    1. Enter an analysis date (defaults to latest date in DB)
    2. Optionally enter a stock ticker to compare
    3. Select an ETF type from the dropdown
    4. Click "Calculate ETF Gains"
    5. **The results will remain visible even when you use the comparison section below**

    ### 📊 Compare Two ETFs (Section 2)
    1. Enter an analysis date in Section 1 first
    2. Select an ETF category for comparison (independent from Section 1)
    3. Select two ETFs from the dropdowns
    4. Click "Compare ETFs" to see side-by-side comparison

    ### 📥 Export Full History to Excel (Section 3)
    1. Select an ETF category to export
    2. Enter start and end dates
    3. Click "Export to Excel"
    4. **Each ETF gets its own sheet** with all columns from EtfPriceHistory table
    5. Column widths are auto-adjusted for better readability

    ### 📊 Understanding the Results
    - **Top Performers**: Shows the best performing ETF for each period
    - **Comparison Results**: Compares your selected stock with top performers
    - **All ETF Results**: Complete table of all ETF gains
    - **ETF Comparison**: Side-by-side comparison of two selected ETFs with win/loss summary

    ### ⏱️ Periods Analyzed
    The app analyzes gains for:
    - **Daily periods**: 5, 10, 15, 20, 30, 60, 90, 120, 180, and 360 days
    - **Year-to-year periods**:
        * **2 Years Ago to 1 Year Ago**: Gain from same date in Year-2 to same date in Year-1
        * **3 Years Ago to 2 Years Ago**: Gain from same date in Year-3 to same date in Year-2

    **Note**:
    - The comparison section uses the same date as Section 1 but can have a different ETF category
    - **Both tables remain visible** when you switch between sections (results are stored in session state)
    - The Excel export includes **all columns** from the EtfPriceHistory table
    - For year-to-year periods, if the exact date doesn't exist in the database, the app will use the nearest available date
    """)

# Footer
st.markdown("---")
st.caption("ETF Performance Analyzer v3.5 | Added full history export to Excel with multiple sheets!")