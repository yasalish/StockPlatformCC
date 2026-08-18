import psycopg2
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

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

# Fetch ETF names based on selected type
def fetch_etf_names(selected_type):
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                query = "SELECT id, ticker FROM ETF WHERE type = %s"
                cursor.execute(query, (selected_type,))
                return cursor.fetchall()
    except Exception as e:
        st.error(f"Error fetching ETF names: {e}")
        return []

# Fetch price history
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
        return pd.DataFrame(rows, columns=["J-Date", "Adj Final"]).astype({"Adj Final": float})
    except Exception as e:
        st.error(f"Error fetching price history for {ticker}: {e}")
        return pd.DataFrame()

# Check if date exists in database for a ticker
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

# Get N trading days before a date (including the start date itself as day 1)
def get_n_trading_days_before(ticker, target_date, n_days):
    """Find the date that is N trading days before the target date (inclusive)"""
    try:
        with psycopg2.connect(**DB_SETTINGS) as conn:
            with conn.cursor() as cursor:
                # Get N trading dates up to and including the target date
                # We need n_days total including the end date
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
                    # Return the oldest date (first of the N days)
                    return rows[-1][0]
                else:
                    return None
    except Exception as e:
        st.error(f"Error finding {n_days} trading days before {target_date} for {ticker}: {e}")
        return None

# Find nearest date for day periods (only look backward)
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

# Calculate returns
def calculate_return(df):
    if df.empty or len(df) < 2:
        return None
    start = df["Adj Final"].iloc[0]
    end = df["Adj Final"].iloc[-1]
    gain = ((end - start) / start) * 100
    return gain

# Main app
st.title("ETF Performance Analyzer")

# Input for stock ticker to compare
col1, col2 = st.columns(2)
with col1:
    input_date = st.text_input("Enter a date (YYYY-MM-DD):")
with col2:
    compare_ticker = st.text_input("Enter a stock ticker to compare (optional):").strip()

# Dropdown for selecting a single ETF type
etf_types = ["ثابت", "در سهام", "کالا", "مختلط"]
selected_type = st.selectbox("Select ETF Type", etf_types)

# Display the selected ETF type
st.write(f"You selected: {selected_type}")

if input_date:
    try:
        st.write(f"**Input Date: {input_date}**")

        # Validate date format
        try:
            datetime.strptime(input_date, "%Y-%m-%d")
        except ValueError:
            st.error("Invalid date format. Please use YYYY-MM-DD format.")
            st.stop()

        # Fetch all ETFs for the selected type
        etfs = fetch_etf_names(selected_type)

        if not etfs:
            st.error("No ETFs found for the selected type")
            st.stop()

        # Check if the first ETF has data for the input date
        sample_ticker = etfs[0][1]
        if not date_exists(sample_ticker, input_date):
            st.error(f"Date {input_date} not found in database for {sample_ticker}. Please enter a valid trading date.")
            st.stop()

        st.write(f"✓ Date {input_date} found in database")

        # Define ALL periods as requested: 5, 10, 15, 20, 30, 60, 90, 120, 180, 360 days
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
        st.write("### Date Ranges for Each Period")
        date_ranges = {}

        for period_name, days_back in all_periods.items():
            end_dt = datetime.strptime(input_date, "%Y-%m-%d")
            calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")

            # For trading day periods (5, 10, 15, 20 days), get exact trading days
            if days_back <= 20:
                start_date = get_n_trading_days_before(sample_ticker, input_date, days_back)
                if start_date:
                    date_ranges[period_name] = (start_date, input_date)
                    st.write(f"{period_name}: {start_date} to {input_date}")
                else:
                    date_ranges[period_name] = (None, input_date)
                    st.write(f"{period_name}: Not enough trading data before {input_date}")
            else:
                # For longer periods, find nearest available date
                actual_start = find_nearest_date_backward(sample_ticker, calendar_start)
                if actual_start:
                    date_ranges[period_name] = (actual_start, input_date)
                    st.write(f"{period_name}: {actual_start} to {input_date}")
                else:
                    date_ranges[period_name] = (None, input_date)
                    st.write(f"{period_name}: Not enough data before {input_date}")

        # Process each ETF
        output = pd.DataFrame()
        compare_gains = {}  # Store gains for the comparison ticker if provided

        # First, process the comparison ticker if provided
        if compare_ticker:
            st.write(f"\n### Processing comparison ticker: {compare_ticker}")
            compare_results = {"Name": compare_ticker}

            # Check if comparison ticker has data for input date
            if not date_exists(compare_ticker, input_date):
                st.warning(f"Comparison ticker {compare_ticker} has no data for {input_date}")
            else:
                # Calculate gains for comparison ticker
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
                            compare_gains[period_name] = gain
                            compare_results[f"Gain ({period_name})"] = f"{gain:.2f}%"
                        else:
                            compare_gains[period_name] = None
                            compare_results[f"Gain ({period_name})"] = "N/A"
                    else:
                        compare_gains[period_name] = None
                        compare_results[f"Gain ({period_name})"] = "N/A"

                # Add comparison ticker to output
                output = pd.concat([output, pd.DataFrame([compare_results])], ignore_index=True)

        # Process all ETFs
        for _, ticker in etfs:
            try:
                results = {"Name": ticker}

                # Check if this ETF has data for the input date
                if not date_exists(ticker, input_date):
                    results["Name"] = f"{ticker} (No data for {input_date})"
                    for period_name in all_periods.keys():
                        results[f"Gain ({period_name})"] = "N/A"
                    output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)
                    continue

                # Calculate gains for all periods
                for period_name, days_back in all_periods.items():
                    if days_back <= 20:
                        # For trading day periods
                        start_date = get_n_trading_days_before(ticker, input_date, days_back)
                    else:
                        # For longer periods
                        end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                        calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
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
        st.write("\n### Top Performers by Period")
        top_performers = {}

        # Filter out comparison ticker and error entries
        valid_etfs = output[~output["Name"].str.contains("(No data|Error)")]

        for period_name in all_periods.keys():
            col_name = f"Gain ({period_name})"
            # Extract numeric gain values
            valid_etfs[f"{col_name}_num"] = valid_etfs[col_name].str.replace('%', '').str.replace('N/A', 'nan').astype(float)

            # Find max gain
            max_idx = valid_etfs[f"{col_name}_num"].idxmax()
            if pd.notna(max_idx):
                max_ticker = valid_etfs.loc[max_idx, "Name"]
                max_gain = valid_etfs.loc[max_idx, f"{col_name}_num"]
                top_performers[period_name] = (max_ticker, max_gain)

                # Display top performer
                st.write(f"**{period_name}**: {max_ticker} - {max_gain:.2f}%")
            else:
                top_performers[period_name] = (None, None)
                st.write(f"**{period_name}**: No valid data")

        # Compare with selected ticker if provided
        if compare_ticker and compare_gains:
            st.write("\n### Comparison Results")
            comparison_data = []

            for period_name in all_periods.keys():
                compare_gain = compare_gains.get(period_name)
                top_ticker, top_gain = top_performers.get(period_name, (None, None))

                if compare_gain is not None and top_gain is not None:
                    diff = compare_gain - top_gain
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": f"{compare_gain:.2f}%",
                        "Top Performer": top_ticker,
                        "Top Gain": f"{top_gain:.2f}%",
                        "Difference": f"{diff:.2f}%",
                        "Status": "Ahead" if diff > 0 else "Behind"
                    })
                elif compare_gain is not None:
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": f"{compare_gain:.2f}%",
                        "Top Performer": "N/A",
                        "Top Gain": "N/A",
                        "Difference": "N/A",
                        "Status": "No comparison"
                    })
                else:
                    comparison_data.append({
                        "Period": period_name,
                        "Your Stock": "N/A",
                        "Top Performer": top_ticker if top_ticker else "N/A",
                        "Top Gain": f"{top_gain:.2f}%" if top_gain else "N/A",
                        "Difference": "N/A",
                        "Status": "No data"
                    })

            # Display comparison table
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, width=2000)

        # Display all results
        st.write("\n### All ETF Results")

        # Clean up temporary columns
        for period_name in all_periods.keys():
            col_name = f"Gain ({period_name})_num"
            if col_name in output.columns:
                output = output.drop(columns=[col_name])

        st.dataframe(output, width=2000)

    except Exception as e:
        st.error(f"Error processing input date: {e}")