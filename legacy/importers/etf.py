import pandas as pd
import psycopg2

# Database connection configuration
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "Stock"
DB_USER = "postgres"
DB_PASSWORD = "stock93"

# Path to your Excel file
EXCEL_FILE_PATH = "Gold.xlsx"  # Replace with your Excel file path

# Table name in the database
TABLE_NAME = "ETF"

# Establish database connection
try:
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    cursor = conn.cursor()
    print("Connected to the database.")
except Exception as e:
    print("Error connecting to the database:", e)
    exit()

# Read the Excel file
try:
    df = pd.read_excel(EXCEL_FILE_PATH)
    print("Excel file read successfully.")
except Exception as e:
    print("Error reading Excel file:", e)
    exit()

# Prepare the SQL insert statement (without the 'id' column)
insert_query = f"""
INSERT INTO {TABLE_NAME} (ticker, name, type, comment)
VALUES (%s, %s, %s, %s)
"""

# Insert data into the database
try:
    for idx, row in df.iterrows():
        cursor.execute(insert_query, (
            row['Ticker'], 
            row['Name'], 
            row['Type'], 
            row.get('Comment', None)  # Use None for missing comments
        ))
    conn.commit()
    print("Data inserted successfully.")
except Exception as e:
    print("Error inserting data into the database:", e)
    conn.rollback()

# Close the connection
cursor.close()
conn.close()
print("Database connection closed.")
