import psycopg2
import finpy_tse as fpy
import pandas as pd


# Database connection settings
DB_SETTINGS = {
    "dbname": "Stock",
    "user": "postgres",
    "password": "stock93",
    "host": "localhost",
    "port": "5432",
}

def fetch_tickers(start_id=352):
    try:
        connection = psycopg2.connect(**DB_SETTINGS)
        cursor = connection.cursor()
        # Fetch stocks starting from the specified ID
        cursor.execute("SELECT stockid, ticker FROM stocks WHERE stockid = %s", (start_id,))
        tickers = cursor.fetchall()
        cursor.close()
        connection.close()
        return tickers
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def insert_price_history(stockid, price_history):
    try:
        connection = psycopg2.connect(**DB_SETTINGS)
        cursor = connection.cursor()

        insert_query = """
            INSERT INTO StockPriceHistory (
                stock_id, ticker, j_date, date, weekday, open, high, low, close, final,
                volume, value, no, name, adj_open, adj_high, adj_low, adj_close, adj_final
            ) VALUES (
                %(stock_id)s, %(ticker)s, %(j_date)s, %(date)s, %(weekday)s, %(open)s, %(high)s, %(low)s, %(close)s, %(final)s,
                %(volume)s, %(value)s, %(no)s, %(name)s, %(adj_open)s, %(adj_high)s, %(adj_low)s, %(adj_close)s, %(adj_final)s
            )
        """
        
        # Convert DataFrame rows to dictionary and insert into the table
        for _, row in price_history.iterrows():
            cursor.execute(insert_query, {
                "stock_id": stock_id,
                "ticker": row["Ticker"],
                "j_date": row["J-Date"],
                "date": row["Date"],
                "weekday": row["Weekday"],
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "final": row["Final"],
                "volume": row["Volume"],
                "value": row["Value"],
                "no": row["No"],
                "name": row["Name"],
                "adj_open": row["Adj Open"],
                "adj_high": row["Adj High"],
                "adj_low": row["Adj Low"],
                "adj_close": row["Adj Close"],
                "adj_final": row["Adj Final"],
            })

        connection.commit()
        cursor.close()
        connection.close()
        print(f"-----> Inserted price history for stockid ID {stock_id}")
    except Exception as e:
        print(f"Error inserting price history for stockid ID {stock_id}: {e}")

if __name__ == "__main__":
    start_id = 352
    tickers = fetch_tickers(start_id)
    if not tickers:
        print("No tickers found in the Stocks table.")
    else:
        for stock_id, ticker in tickers:
            print(f"Fetching price history for ticker: {ticker}")
            try:
                # Fetch historical price data
                price_history = fpy.Get_Price_History(stock=ticker, ignore_date=True, adjust_price=True, show_weekday=True, double_date=True)
                #print(price_history)
                # Convert to pandas DataFrame
                df = pd.DataFrame(price_history)
                
                # Reset index and process columns
                df.reset_index(inplace=True)
                df["J-Date"] = df["J-Date"].astype(str)
                df = df[[
                    "J-Date", "Date", "Weekday", "Open", "High", "Low", "Close", "Final",
                    "Volume", "Value", "No", "Ticker", "Name", "Adj Open",
                    "Adj High", "Adj Low", "Adj Close", "Adj Final"
                ]]
                print(df)
                # Debugging
                print("Processed DataFrame:\n", df.head())

                # Insert data into database
                insert_price_history(stock_id, df)
            except Exception as e:
                print(f"Error processing ticker {ticker}: {e}")