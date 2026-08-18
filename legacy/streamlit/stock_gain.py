import psycopg2
import pandas as pd
import streamlit as st

# Database settings
DB_SETTINGS = {
    "dbname": "Stock",
    "user": "postgres",
    "password": "stock93",
    "host": "localhost",
    "port": "5432",
}

# Function to connect to the database
def connect_to_db():
    try:
        conn = psycopg2.connect(**DB_SETTINGS)
        return conn
    except Exception as e:
        st.error(f"Error connecting to the database: {e}")
        return None

# Function to fetch ticker names from the stocks table
def fetch_ticker_names():
    conn = connect_to_db()
    if conn is None:
        return []

    query = "SELECT DISTINCT ticker FROM stocks;"
    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            result = cursor.fetchall()
            return [row[0] for row in result]
    except Exception as e:
        st.error(f"Error fetching ticker names: {e}")
        return []
    finally:
        conn.close()

# Function to fetch stock price history from the stockpricehistory table
def fetch_stock_price_history(ticker, start_date, end_date):
    conn = connect_to_db()
    if conn is None:
        return pd.DataFrame()

    query = """
        SELECT j_date, adj_final
        FROM stockpricehistory
        WHERE ticker = %s AND j_date BETWEEN %s AND %s
        ORDER BY j_date;
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, (ticker, start_date, end_date))
            data = cursor.fetchall()
            return pd.DataFrame(data, columns=["j_date", "adj_final"]).set_index("j_date")
    except Exception as e:
        st.error(f"Error fetching price history for {ticker}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

# Function to calculate date ranges
def calculate_date_ranges(day, month, year):
    # End date
    end_date = f"{year}-{month:02d}-{day:02d}"

    # Start dates for different periods
    def calculate_start_date(offset_months):
        new_month = month - offset_months
        new_year = year
        if new_month <= 0:
            new_month += 12
            new_year -= 1
        return f"{new_year}-{new_month:02d}-{day:02d}"

    start_date_1month = calculate_start_date(1)
    start_date_3month = calculate_start_date(3)
    start_date_6month = calculate_start_date(6)
    start_date_12month = calculate_start_date(12)

    return end_date, start_date_1month, start_date_3month, start_date_6month, start_date_12month

# Streamlit app configuration
st.set_page_config(layout="wide")
st.title("Stock Performance Metrics")

# Input date from user
input_date = st.text_input("Enter a date (YYYY-MM-DD):")

if input_date:
    try:
        year, month, day = map(int, input_date.split('-'))
        end_date, start_date_1month, start_date_3month, start_date_6month, start_date_12month = calculate_date_ranges(day, month, year)
        
        # Fetch ticker names from the stocks table
        tickers = fetch_ticker_names()
        if not tickers:
            st.warning("No ticker names found in the database.")
        else:
            output = pd.DataFrame()
            i=1
            for ticker in tickers:
                print(i,"\t",ticker)
                i+=1
                try:
                    # Fetch stock price history
                    data = fetch_stock_price_history(ticker, start_date_12month, end_date)
                    if data.empty:
                        st.warning(f"No data found for {ticker}. Skipping...")
                        continue

                    # Calculate metrics for each time range
                    metrics = {}
                    for period, start_date in zip(
                        ["1 Month", "3 Months", "6 Months", "12 Months"],
                        [start_date_1month, start_date_3month, start_date_6month, start_date_12month]
                    ):
                        try:
                            df = data.loc[start_date:end_date]
                            start_value = df["adj_final"].iloc[0]
                            end_value = df["adj_final"].iloc[-1]
                            max_value = df["adj_final"].max()
                            min_value = df["adj_final"].min()

                            metrics[f"Gain {period}"] = ((end_value - start_value) / start_value) * 100
                            metrics[f"Ceil {period}"] = ((end_value - max_value) / max_value) * 100
                            metrics[f"Floor {period}"] = ((end_value - min_value) / min_value) * 100
                        except Exception as e:
                            st.warning(f"Error processing {ticker} for {period}: {e}")
                            continue
                    
                    # Add the calculated metrics to the output DataFrame
                    metrics["Ticker"] = ticker
                    output = pd.concat([output, pd.DataFrame([metrics])], ignore_index=True)

                except Exception as e:
                    st.error(f"Error processing {ticker}: {e}")

            # Display the results
            st.dataframe(output, width=2000)

    except ValueError:
        st.error("Please enter a valid date in the format YYYY-MM-DD.")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
