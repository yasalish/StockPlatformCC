import streamlit as st
import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import jdatetime
import finpy_tse as fpy

# Register adapter for numpy.int64
psycopg2.extensions.register_adapter(np.int64, psycopg2._psycopg.AsIs)

# Database connection settings
DB_SETTINGS = {
    "dbname": "Stock",
    "user": "postgres",
    "password": "stock93",
    "host": "localhost",
    "port": "5432",
}

# Page configuration
st.set_page_config(
    page_title="نمایشگر تاریخچه سهام",
    page_icon="📈",
    layout="wide"
)

# Add custom CSS for RTL and Persian font
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Vazirmatn', sans-serif;
    }

    .rtl {
        direction: rtl;
        text-align: right;
    }

    .stTable, .stDataFrame {
        direction: rtl;
        text-align: right;
    }

    .stDataFrame table {
        direction: rtl;
        text-align: right;
    }

    .stDataFrame th {
        text-align: center !important;
        font-weight: 700;
        background-color: #f0f2f6;
    }

    .stDataFrame td {
        text-align: center !important;
    }

    .persian-text {
        font-family: 'Vazirmatn', sans-serif;
    }

    .metric-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        direction: rtl;
    }

    h1, h2, h3, h4, h5, h6 {
        font-family: 'Vazirmatn', sans-serif;
        font-weight: 700;
    }

    .stButton button {
        font-family: 'Vazirmatn', sans-serif;
        font-weight: 500;
    }

    /* Style for percentage change */
    .positive-change {
        color: #4caf50;
        font-weight: 600;
        direction: ltr;
        display: inline-block;
    }

    .negative-change {
        color: #f44336;
        font-weight: 600;
        direction: ltr;
        display: inline-block;
    }

    .zero-change {
        color: #9e9e9e;
        font-weight: 600;
        direction: ltr;
        display: inline-block;
    }

    /* Style for gain table */
    .gain-table {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 20px;
        border: 1px solid #e0e0e0;
        overflow-x: auto;
        overflow-y: auto;
        direction: ltr;
    }

    .gain-table table {
        direction: rtl;
        min-width: 1300px;
        width: 100%;
        border-collapse: collapse;
    }

    .gain-table th {
        padding: 10px;
        text-align: center;
        border: 1px solid #ddd;
        background-color: #e3f2fd;
        font-weight: 700;
        white-space: nowrap;
        min-width: 80px;
    }

    .gain-table td {
        padding: 10px;
        text-align: center;
        border: 1px solid #ddd;
        white-space: nowrap;
        min-width: 80px;
    }

    .gain-table th:first-child,
    .gain-table td:first-child {
        position: sticky;
        left: 0;
        background-color: #e3f2fd;
        z-index: 2;
        min-width: 100px;
    }

    .gain-table td:first-child {
        background-color: #f5f5f5;
        font-weight: 600;
    }

    .gain-table tr:nth-child(even) td:first-child {
        background-color: #fafafa;
    }

    .gain-positive {
        color: #4caf50;
        font-weight: 700;
    }

    .gain-negative {
        color: #f44336;
        font-weight: 700;
    }

    .ceil-positive {
        color: #2196F3;
        font-weight: 700;
    }

    .ceil-negative {
        color: #FF9800;
        font-weight: 700;
    }

    .floor-positive {
        color: #9C27B0;
        font-weight: 700;
    }

    .floor-negative {
        color: #F44336;
        font-weight: 700;
    }

    .date-range-box {
        background-color: #f5f7fa;
        padding: 15px 20px;
        border-radius: 8px;
        margin-bottom: 15px;
        direction: ltr;
        font-family: 'Vazirmatn', sans-serif;
        border: 1px solid #e0e0e0;
        line-height: 2;
        overflow-x: auto;
    }

    .date-range-box .period {
        display: inline-block;
        margin-left: 20px;
        font-size: 0.95rem;
        white-space: nowrap;
    }

    .date-range-box .period strong {
        color: #1a237e;
        font-weight: 700;
    }

    /* Style for specific date analysis box */
    .specific-date-box {
        background-color: #e8f5e9;
        padding: 20px;
        border-radius: 10px;
        margin: 15px 0;
        border-right: 4px solid #4caf50;
    }

    .specific-date-box .highlight {
        font-size: 1.2rem;
        font-weight: 700;
        color: #1a237e;
    }
</style>
""", unsafe_allow_html=True)

# Persian weekday mapping
PERSIAN_WEEKDAYS = {
    'Saturday': 'شنبه',
    'Sunday': 'یکشنبه',
    'Monday': 'دوشنبه',
    'Tuesday': 'سه‌شنبه',
    'Wednesday': 'چهارشنبه',
    'Thursday': 'پنج‌شنبه',
    'Friday': 'جمعه'
}

def get_db_connection():
    """Create database connection"""
    try:
        conn = psycopg2.connect(**DB_SETTINGS)
        return conn
    except Exception as e:
        st.error(f"خطا در اتصال به پایگاه داده: {e}")
        return None

def search_stocks(search_term):
    """Search for stocks by name or ticker with Persian text optimization"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        search_term = search_term.strip()

        # Try to enable pg_trgm extension if not exists
        try:
            cursor = conn.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            conn.commit()
            cursor.close()
        except:
            pass

        query = """
            WITH stock_search AS (
                SELECT
                    stockid,
                    ticker,
                    name,
                    CASE
                        WHEN ticker = %s THEN 1
                        WHEN name = %s THEN 2
                        WHEN ticker ILIKE %s THEN 3
                        WHEN name ILIKE %s THEN 4
                        WHEN ticker ILIKE %s THEN 5
                        WHEN name ILIKE %s THEN 6
                        ELSE 7
                    END as relevance,
                    CASE
                        WHEN ticker = %s THEN 100
                        WHEN name = %s THEN 90
                        WHEN ticker ILIKE %s THEN 80
                        WHEN name ILIKE %s THEN 70
                        WHEN ticker ILIKE %s THEN 60
                        WHEN name ILIKE %s THEN 50
                        ELSE 0
                    END +
                    COALESCE(similarity(ticker, %s) * 20, 0) +
                    COALESCE(similarity(name, %s) * 10, 0) as score
                FROM stocks
                WHERE
                    ticker = %s OR
                    name = %s OR
                    ticker ILIKE %s OR
                    name ILIKE %s OR
                    ticker ILIKE %s OR
                    name ILIKE %s
            )
            SELECT stockid, ticker, name
            FROM stock_search
            ORDER BY relevance, score DESC
            LIMIT 30
        """

        starts_with = f"{search_term}%"
        contains = f"%{search_term}%"

        df = pd.read_sql_query(
            query, conn,
            params=(
                search_term, search_term,
                starts_with, starts_with,
                contains, contains,
                search_term, search_term,
                starts_with, starts_with,
                contains, contains,
                search_term, search_term,
                search_term, search_term,
                starts_with, starts_with,
                contains, contains
            )
        )

        return df

    except Exception as e:
        st.error(f"خطا در جستجو: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def fetch_stock_history_from_api(ticker, start_date=None, end_date=None):
    """
    Fetch stock price history using finpy_tse API

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    start_date : str, optional
        Start date in Jalali format (YYYY-MM-DD)
    end_date : str, optional
        End date in Jalali format (YYYY-MM-DD)

    Returns:
    --------
    pd.DataFrame: Stock price history data
    """
    try:
        # Fetch historical price data from API
        price_history = fpy.Get_Price_History(
            stock=ticker,
            start_date=start_date,
            end_date=end_date,
            adjust_price=True,
            show_weekday=True,
            double_date=True,
        )

        # Convert to pandas DataFrame
        df = pd.DataFrame(price_history)

        if df.empty:
            return pd.DataFrame()

        # Reset index and process columns
        df.reset_index(inplace=True)
        df["J-Date"] = df["J-Date"].astype(str)

        # Map weekdays to Persian
        df['weekday_persian'] = df['Weekday'].map(PERSIAN_WEEKDAYS).fillna(df['Weekday'])

        # Calculate daily changes based on adj_final
        df_sorted = df.sort_values('J-Date').reset_index(drop=True)
        df_sorted['daily_gain'] = df_sorted['Adj Final'].pct_change() * 100

        # Rename columns to match database format for consistency
        df_sorted.columns = [
            'index', 'j_date', 'date', 'weekday', 'open', 'high', 'low',
            'close', 'final', 'volume', 'value', 'no', 'ticker', 'name',
            'adj_open', 'adj_high', 'adj_low', 'adj_close', 'adj_final',
            'weekday_persian', 'daily_gain'
        ]

        # Drop unnecessary columns
        if 'index' in df_sorted.columns:
            df_sorted = df_sorted.drop('index', axis=1)

        return df_sorted

    except Exception as e:
        st.error(f"خطا در دریافت داده‌ها از API: {e}")
        return pd.DataFrame()

def get_stock_history(stock_id, limit=None, ticker=None, start_date=None, end_date=None):
    """
    Get price history - either from database or API

    If ticker is provided, fetch from API. Otherwise, fetch from database.
    """
    # If ticker is provided, fetch from API
    if ticker:
        return fetch_stock_history_from_api(ticker, start_date, end_date)

    # Otherwise fetch from database (fallback)
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        if isinstance(stock_id, np.integer):
            stock_id = int(stock_id)

        query = """
            SELECT
                ticker,
                j_date,
                weekday,
                volume,
                close,
                final,
                adj_close,
                adj_final,
                date
            FROM StockPriceHistory
            WHERE stock_id = %s
            ORDER BY date DESC
        """

        params = [stock_id]
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df['weekday_persian'] = df['weekday'].map(PERSIAN_WEEKDAYS).fillna(df['weekday'])
            df_reversed = df.iloc[::-1].copy()
            df_reversed['daily_gain'] = df_reversed['adj_final'].pct_change() * 100
            df['daily_gain'] = df_reversed['daily_gain'].iloc[::-1].values

        return df
    except Exception as e:
        st.error(f"خطا در دریافت تاریخچه: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def calculate_same_date_previous_year(current_date_str, years_back):
    """Calculate the same date in previous year(s)."""
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
        matching_rows = df_asc[df_asc['j_date'] == target_date]
        if not matching_rows.empty:
            return matching_rows['adj_final'].iloc[0]

        df_before = df_asc[df_asc['j_date'] < target_date]
        if not df_before.empty:
            return df_before['adj_final'].iloc[-1]

        return None
    except Exception as e:
        return None

def calculate_year_to_year_gain(df_asc, current_date, years_back):
    """Calculate gain from same date in year-(years_back) to current date"""
    try:
        date_year_n_theoretical = calculate_same_date_previous_year(current_date, years_back)

        if not date_year_n_theoretical:
            return None, None, None

        price_year_n = get_price_on_date(None, date_year_n_theoretical, df_asc)

        if price_year_n is None:
            return None, None, None

        latest_price = df_asc['adj_final'].iloc[-1]
        latest_date = df_asc['j_date'].iloc[-1]

        if price_year_n != 0:
            gain = ((latest_price - price_year_n) / price_year_n) * 100
        else:
            gain = None

        return gain, date_year_n_theoretical, latest_date

    except Exception as e:
        st.error(f"Error calculating year-to-year gain: {e}")
        return None, None, None

def calculate_period_gains(df, periods):
    """Calculate gains for different periods based on adj_final price"""
    gains = {}

    if df.empty or len(df) < max(periods):
        return gains

    df_asc = df.iloc[::-1].reset_index(drop=True)

    latest_price = df_asc['adj_final'].iloc[-1]
    latest_date = df_asc['j_date'].iloc[-1]

    for period in periods:
        if len(df_asc) > period:
            past_price = df_asc['adj_final'].iloc[-(period+1)]
            gain = ((latest_price - past_price) / past_price) * 100
            past_date = df_asc['j_date'].iloc[-(period+1)]
            gains[period] = {
                'gain': gain,
                'start_date': past_date,
                'end_date': latest_date
            }

    return gains

def calculate_ceil_floor_metrics(df, periods):
    """Calculate Ceil and Floor metrics for different periods based on adj_final"""
    metrics = {}

    if df.empty or len(df) < max(periods):
        return metrics

    df_asc = df.iloc[::-1].reset_index(drop=True)

    for period in periods:
        if len(df_asc) > period:
            period_data = df_asc.tail(period + 1)

            if len(period_data) > 1:
                latest_price = period_data['adj_final'].iloc[-1]
                max_price = period_data['adj_final'].max()
                min_price = period_data['adj_final'].min()

                if max_price != 0:
                    ceil = ((latest_price - max_price) / max_price) * 100
                else:
                    ceil = None

                if min_price != 0:
                    floor = ((latest_price - min_price) / min_price) * 100
                else:
                    floor = None

                metrics[period] = {
                    'ceil': ceil,
                    'floor': floor,
                    'max_price': max_price,
                    'min_price': min_price,
                    'latest_price': latest_price,
                    'start_date': period_data['j_date'].iloc[0],
                    'end_date': period_data['j_date'].iloc[-1]
                }

    return metrics

def calculate_from_first_date(df_asc):
    """Calculate gain, Ceil and Floor from the first date to the latest date"""
    if df_asc.empty or len(df_asc) < 2:
        return None, None, None, None, None

    latest_price = df_asc['adj_final'].iloc[-1]
    first_price = df_asc['adj_final'].iloc[0]
    max_price = df_asc['adj_final'].max()
    min_price = df_asc['adj_final'].min()
    first_date = df_asc['j_date'].iloc[0]
    latest_date = df_asc['j_date'].iloc[-1]

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

def get_popular_stocks():
    """Get list of popular stocks for quick access"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        query = """
            SELECT DISTINCT ticker, name
            FROM stocks
            WHERE ticker IN ('فولاد', 'خودرو', 'وبملت', 'شپنا', 'کگل', 'فملی', 'فخوز', 'پارس', 'حکشتی', 'بوعلی', 'بنیرو')
            ORDER BY ticker
            LIMIT 12
        """
        df = pd.read_sql_query(query, conn)
        return df.to_dict('records')
    except Exception as e:
        st.error(f"خطا در دریافت سهام‌های محبوب: {e}")
        return []
    finally:
        conn.close()

def format_number(num):
    """Format large numbers with commas and Persian digits"""
    try:
        if pd.isna(num) or num is None:
            return ""
        persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                         '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹'}
        num_str = f"{int(num):,}"
        for eng, per in persian_digits.items():
            num_str = num_str.replace(eng, per)
        return num_str
    except:
        return str(num) if num is not None else ""

def format_price(num):
    """Format price with commas and Persian digits"""
    try:
        if pd.isna(num) or num is None:
            return ""
        persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                         '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹'}
        num_str = f"{int(num):,}"
        for eng, per in persian_digits.items():
            num_str = num_str.replace(eng, per)
        return num_str
    except:
        return str(num) if num is not None else ""

def format_percent_change(change):
    """Format percentage change with Persian digits and styling"""
    try:
        if pd.isna(change) or change is None:
            return ""

        persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                         '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹',
                         '-': '−', '.': '٫'}

        if change > 0:
            prefix = "+"
            css_class = "positive-change"
        elif change < 0:
            prefix = ""
            css_class = "negative-change"
        else:
            prefix = ""
            css_class = "zero-change"

        change_str = f"{prefix}{change:.2f}%"

        for eng, per in persian_digits.items():
            change_str = change_str.replace(eng, per)

        return f"<span class='{css_class}'>{change_str}</span>"
    except:
        return ""

def format_gain(gain):
    """Format gain percentage for the summary table"""
    try:
        if pd.isna(gain) or gain is None:
            return "۰", ""

        persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                         '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹',
                         '-': '−', '.': '٫'}

        if gain > 0:
            gain_str = f"+{gain:.2f}%"
            css_class = "gain-positive"
        elif gain < 0:
            gain_str = f"{gain:.2f}%"
            css_class = "gain-negative"
        else:
            gain_str = f"{gain:.2f}%"
            css_class = ""

        for eng, per in persian_digits.items():
            gain_str = gain_str.replace(eng, per)

        return gain_str, css_class
    except:
        return "۰", ""

def format_ceil_floor(value, metric_type):
    """Format Ceil/Floor percentage for display"""
    try:
        if pd.isna(value) or value is None:
            return "۰", ""

        persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                         '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹',
                         '-': '−', '.': '٫'}

        if value > 0:
            gain_str = f"+{value:.2f}%"
            if metric_type == 'ceil':
                css_class = "ceil-positive"
            else:
                css_class = "floor-positive"
        elif value < 0:
            gain_str = f"{value:.2f}%"
            if metric_type == 'ceil':
                css_class = "ceil-negative"
            else:
                css_class = "floor-negative"
        else:
            gain_str = f"{value:.2f}%"
            css_class = ""

        for eng, per in persian_digits.items():
            gain_str = gain_str.replace(eng, per)

        return gain_str, css_class
    except:
        return "۰", ""

def get_price_and_data_on_date(df_asc, target_date):
    """Get price and all relevant data for a specific date"""
    try:
        matching_rows = df_asc[df_asc['j_date'] == target_date]
        if not matching_rows.empty:
            row = matching_rows.iloc[0]
            return {
                'date': target_date,
                'adj_final': row['adj_final'],
                'close': row['close'],
                'final': row['final'],
                'adj_close': row['adj_close'],
                'volume': row['volume'],
                'weekday': row['weekday'],
                'j_date': row['j_date'],
                'found_exact': True
            }

        df_before = df_asc[df_asc['j_date'] < target_date]
        if not df_before.empty:
            row = df_before.iloc[-1]
            return {
                'date': row['j_date'],
                'adj_final': row['adj_final'],
                'close': row['close'],
                'final': row['final'],
                'adj_close': row['adj_close'],
                'volume': row['volume'],
                'weekday': row['weekday'],
                'j_date': row['j_date'],
                'found_exact': False
            }

        return None
    except Exception as e:
        return None

def calculate_performance_from_date(df_asc, target_date):
    """Calculate performance metrics from a specific date to now"""
    try:
        date_data = get_price_and_data_on_date(df_asc, target_date)
        if date_data is None:
            return None

        latest_row = df_asc.iloc[-1]
        latest_date = latest_row['j_date']

        start_price = date_data['adj_final']
        latest_price = latest_row['adj_final']

        if start_price != 0:
            gain = ((latest_price - start_price) / start_price) * 100
        else:
            gain = None

        period_data = df_asc[df_asc['j_date'] >= date_data['date']]
        max_price = period_data['adj_final'].max()
        min_price = period_data['adj_final'].min()

        if max_price != 0:
            ceil = ((latest_price - max_price) / max_price) * 100
        else:
            ceil = None

        if min_price != 0:
            floor = ((latest_price - min_price) / min_price) * 100
        else:
            floor = None

        return {
            'start_date': date_data['date'],
            'end_date': latest_date,
            'start_price': start_price,
            'latest_price': latest_price,
            'gain': gain,
            'ceil': ceil,
            'floor': floor,
            'max_price': max_price,
            'min_price': min_price,
            'start_data': date_data,
            'latest_data': latest_row,
            'period_days': len(period_data) - 1
        }

    except Exception as e:
        st.error(f"Error calculating performance from date: {e}")
        return None

def main():
    # Initialize session state
    if 'search_term' not in st.session_state:
        st.session_state.search_term = ""
    if 'date_filter_enabled' not in st.session_state:
        st.session_state.date_filter_enabled = False
    if 'specific_date_analysis' not in st.session_state:
        st.session_state.specific_date_analysis = False
    if 'use_api' not in st.session_state:
        st.session_state.use_api = True  # Default to API

    # Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/stock.png", width=80)
        st.title("📊 نمایشگر تاریخچه سهام")
        st.markdown("---")

        # Data source selection
        st.subheader("📡 منبع داده")
        use_api = st.radio(
            "انتخاب منبع داده",
            options=["API (finpy_tse)", "پایگاه داده"],
            index=0 if st.session_state.use_api else 1,
            key="data_source"
        )
        st.session_state.use_api = (use_api == "API (finpy_tse)")

        if st.session_state.use_api:
            st.info("✅ داده‌ها مستقیماً از API دریافت می‌شوند")
        else:
            st.warning("⚠️ داده‌ها از پایگاه داده دریافت می‌شوند")

        st.markdown("---")

        # Search input with session state
        search_term = st.text_input(
            "🔍 جستجوی سهام (نام یا نماد)",
            value=st.session_state.search_term,
            placeholder="مثال: فولاد, خودرو, وبملت..."
        )

        # Update session state
        if search_term != st.session_state.search_term:
            st.session_state.search_term = search_term
            st.rerun()

        st.markdown("---")

        # Settings
        st.subheader("⚙️ تنظیمات")

        # Date range selection
        st.markdown("**📅 بازه زمانی**")

        # Get current Jalali date
        today = jdatetime.date.today()
        current_year = today.year
        current_month = today.month
        current_day = today.day

        col1, col2, col3 = st.columns(3)
        with col1:
            start_year = st.number_input(
                "سال شروع",
                min_value=1380,
                max_value=current_year,
                value=current_year - 1,
                step=1,
                key="start_year"
            )
        with col2:
            start_month = st.number_input(
                "ماه شروع",
                min_value=1,
                max_value=12,
                value=1,
                step=1,
                key="start_month"
            )
        with col3:
            max_start_day = 31 if start_month in [1,2,3,4,5,6] else 30 if start_month in [7,8,9,10,11] else 29
            start_day = st.number_input(
                "روز شروع",
                min_value=1,
                max_value=max_start_day,
                value=1,
                step=1,
                key="start_day"
            )

        st.markdown("**تا**")

        col1, col2, col3 = st.columns(3)
        with col1:
            end_year = st.number_input(
                "سال پایان",
                min_value=1380,
                max_value=current_year,
                value=current_year,
                step=1,
                key="end_year"
            )
        with col2:
            end_month = st.number_input(
                "ماه پایان",
                min_value=1,
                max_value=12,
                value=current_month,
                step=1,
                key="end_month"
            )
        with col3:
            max_end_day = 31 if end_month in [1,2,3,4,5,6] else 30 if end_month in [7,8,9,10,11] else 29
            end_day = st.number_input(
                "روز پایان",
                min_value=1,
                max_value=max_end_day,
                value=min(current_day, max_end_day),
                step=1,
                key="end_day"
            )

        start_date = f"{start_year:04d}-{start_month:02d}-{start_day:02d}"
        end_date = f"{end_year:04d}-{end_month:02d}-{end_day:02d}"

        st.info(f"📅 بازه: {start_date} تا {end_date}")

        st.markdown("---")

        # Specific Date Analysis Section
        st.subheader("📌 تحلیل تاریخ خاص")
        st.markdown("تاریخ مورد نظر را وارد کنید تا اطلاعات آن روز و عملکرد تا امروز را مشاهده کنید")

        col1, col2, col3 = st.columns(3)
        with col1:
            analysis_year = st.number_input(
                "سال",
                min_value=1380,
                max_value=1410,
                value=1400,
                step=1,
                key="analysis_year"
            )
        with col2:
            analysis_month = st.number_input(
                "ماه",
                min_value=1,
                max_value=12,
                value=1,
                step=1,
                key="analysis_month"
            )
        with col3:
            max_day = 31 if analysis_month in [1,2,3,4,5,6] else 30 if analysis_month in [7,8,9,10,11] else 29
            analysis_day = st.number_input(
                "روز",
                min_value=1,
                max_value=max_day,
                value=1,
                step=1,
                key="analysis_day"
            )

        analysis_date = f"{analysis_year:04d}-{analysis_month:02d}-{analysis_day:02d}"

        if st.button("🔍 تحلیل این تاریخ", use_container_width=True):
            st.session_state.specific_date_analysis = True
            st.session_state.analysis_date = analysis_date
            st.rerun()

        st.markdown("---")

        # Export options
        st.subheader("💾 خروجی")
        if st.button("📥 خروجی CSV", use_container_width=True):
            if 'history_df' in st.session_state:
                csv = st.session_state.history_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📄 دانلود CSV",
                    data=csv,
                    file_name=f"stock_history_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )

        # Reset button
        if st.button("🔄 بازنشانی همه فیلترها", use_container_width=True):
            keys_to_clear = ['search_term', 'date_filter_enabled', 'filter_start', 'filter_end',
                           'specific_date_analysis', 'analysis_date', 'history_df']
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

        st.markdown("---")
        st.caption("منبع داده: finpy_tse API")
        st.caption(f"آخرین بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Main content area
    st.title("📈 نمایشگر تاریخچه قیمت سهام")

    # Popular stocks section (when no search)
    if not st.session_state.search_term:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown("### 🔥 سهام‌های محبوب")
        with col2:
            st.markdown("### 🔍 جستجوی سریع")

        popular_stocks = get_popular_stocks()

        if popular_stocks:
            cols = st.columns(4)
            for i, stock in enumerate(popular_stocks[:8]):
                with cols[i % 4]:
                    if st.button(
                        f"{stock['ticker']}",
                        key=f"pop_{stock['ticker']}",
                        use_container_width=True,
                        help=stock['name']
                    ):
                        st.session_state.search_term = stock['ticker']
                        st.rerun()

        st.markdown("---")

        # Welcome message
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image("https://img.icons8.com/color/200/stock.png")
            st.markdown("""
            <div style='text-align: center; direction: rtl; font-family: Vazirmatn;'>
                <h3>👋 به نمایشگر تاریخچه سهام خوش آمدید</h3>
                <p><strong>شروع کار:</strong></p>
                <ol style='text-align: right;'>
                    <li>نام یا نماد سهام را در کادر جستجو (سمت راست) وارد کنید</li>
                    <li>سهام مورد نظر را از نتایج انتخاب کنید</li>
                    <li>تاریخچه کامل قیمت را مشاهده کنید</li>
                </ol>
                <p><strong>داده‌های موجود:</strong></p>
                <ul style='text-align: right;'>
                    <li>📅 تاریخ شمسی (YYYY-MM-DD) از <strong>۱۳۸۲</strong> به بعد</li>
                    <li>📆 روز هفته (به فارسی)</li>
                    <li>📊 حجم معاملات</li>
                    <li>💰 قیمت بازگشایی، بالاترین، پایین‌ترین</li>
                    <li>🏁 قیمت پایانی</li>
                    <li>📊 قیمت‌های تعدیل شده</li>
                    <li>🎯 قیمت پایانی تعدیل شده</li>
                    <li>📈 درصد تغییر روزانه (بر اساس قیمت پایانی تعدیل شده)</li>
                </ul>
                <p><strong>بازه‌های زمانی:</strong></p>
                <ul style='text-align: right;'>
                    <li>۵ روزه</li>
                    <li>۱۰ روزه</li>
                    <li>۱۵ روزه</li>
                    <li>۲۰ روزه</li>
                    <li>۳۰ روزه</li>
                    <li>۶۰ روزه</li>
                    <li>۱۲۰ روزه</li>
                    <li>۱۸۰ روزه</li>
                    <li>۳۶۰ روزه</li>
                    <li>۲ ساله</li>
                    <li>۳ ساله</li>
                    <li>📅 از ابتدا تا کنون</li>
                </ul>
                <p><strong>شاخص‌های جدید:</strong></p>
                <ul style='text-align: right;'>
                    <li>📈 <strong>سقف (Ceil):</strong> درصد تغییر از بالاترین قیمت بازه تا انتهای بازه</li>
                    <li>📉 <strong>کف (Floor):</strong> درصد تغییر از پایین‌ترین قیمت بازه تا انتهای بازه</li>
                </ul>
                <p><strong>امکانات جدید:</strong></p>
                <ul style='text-align: right;'>
                    <li>📌 <strong>تحلیل تاریخ خاص:</strong> وارد کردن یک تاریخ و مشاهده اطلاعات آن روز و عملکرد از آن تاریخ تا امروز</li>
                    <li>📡 <strong>دریافت مستقیم از API:</strong> داده‌ها مستقیماً از finpy_tse دریافت می‌شوند</li>
                </ul>
                <p><strong>امکانات:</strong></p>
                <ul style='text-align: right;'>
                    <li>🔍 جستجوی هوشمند فارسی</li>
                    <li>📅 انتخاب بازه زمانی دلخواه</li>
                    <li>💾 خروجی CSV</li>
                    <li>📈 آمار خلاصه و بازدهی دوره‌ای</li>
                    <li>🇮🇷 پشتیبانی کامل از راست‌چین و اعداد فارسی</li>
                    <li>↔️ اسکرول افقی برای جدول‌های بزرگ</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

    # Search and display
    else:
        with st.spinner(f"در حال جستجوی '{st.session_state.search_term}'..."):
            stocks_df = search_stocks(st.session_state.search_term)

        if not stocks_df.empty:
            st.success(f"✅ {len(stocks_df)} سهام یافت شد")

            st.info(f"🔍 نتایج جستجو برای: **{st.session_state.search_term}**")

            stock_options = stocks_df.apply(
                lambda x: f"📊 {x['ticker']} - {x['name']}", axis=1
            ).tolist()

            selected_stock = st.selectbox(
                "انتخاب سهام برای مشاهده تاریخچه:",
                stock_options,
                key="stock_selector"
            )

            if selected_stock:
                selected_index = stock_options.index(selected_stock)
                selected_ticker = stocks_df.iloc[selected_index]['ticker']
                selected_name = stocks_df.iloc[selected_index]['name']
                selected_stock_id = stocks_df.iloc[selected_index]['stockid']

                st.markdown(f"### 📈 {selected_ticker} - {selected_name}")

                with st.spinner(f"در حال دریافت تاریخچه قیمت {selected_ticker}..."):
                    # Fetch from API or database based on selection
                    if st.session_state.use_api:
                        history_df = fetch_stock_history_from_api(selected_ticker, start_date, end_date)
                        st.info("📡 داده‌ها از API دریافت شدند")
                    else:
                        history_df = get_stock_history(selected_stock_id, limit=None)
                        st.info("💾 داده‌ها از پایگاه داده دریافت شدند")

                if not history_df.empty:
                    # Store in session state
                    st.session_state.history_df = history_df.copy()

                    # Sort by date descending for display
                    history_df = history_df.sort_values('j_date', ascending=False)

                    # Display metrics
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("نماد", selected_ticker)
                    with col2:
                        total_records = len(history_df)
                        st.metric("تعداد رکوردها", format_number(total_records))
                    with col3:
                        oldest_date = history_df['j_date'].iloc[-1] if len(history_df) > 0 else "N/A"
                        latest_date = history_df['j_date'].iloc[0] if not history_df.empty else "N/A"
                        st.metric("بازه تاریخی", f"{oldest_date} تا {latest_date}")
                    with col4:
                        latest_adj_final = history_df['adj_final'].iloc[0] if not history_df.empty else 0
                        st.metric("آخرین قیمت تعدیل شده", format_price(latest_adj_final))

                    # === SPECIFIC DATE ANALYSIS SECTION ===
                    if st.session_state.get('specific_date_analysis', False) and 'analysis_date' in st.session_state:
                        analysis_date = st.session_state.analysis_date

                        # Get data in chronological order
                        df_asc = history_df.iloc[::-1].reset_index(drop=True)

                        # Perform analysis
                        performance = calculate_performance_from_date(df_asc, analysis_date)

                        if performance:
                            st.markdown("---")
                            st.subheader(f"📌 تحلیل تاریخ {analysis_date}")

                            st.markdown(f"""
                            <div class='specific-date-box'>
                                <h4>📊 اطلاعات تاریخ {analysis_date}</h4>
                            """, unsafe_allow_html=True)

                            if performance['start_data']['found_exact']:
                                st.markdown(f"✅ تاریخ **{analysis_date}** در داده‌ها موجود است")
                            else:
                                st.markdown(f"⚠️ تاریخ **{analysis_date}** در داده‌ها موجود نیست. نزدیک‌ترین تاریخ موجود: **{performance['start_date']}**")

                            col1, col2, col3, col4 = st.columns(4)
                            with col1:
                                st.metric("📅 تاریخ", performance['start_date'])
                            with col2:
                                st.metric("📆 روز هفته", PERSIAN_WEEKDAYS.get(performance['start_data']['weekday'], performance['start_data']['weekday']))
                            with col3:
                                st.metric("🎯 قیمت پایانی تعدیل شده", format_price(performance['start_price']))
                            with col4:
                                st.metric("📊 حجم معاملات", format_number(performance['start_data']['volume']))

                            st.markdown("---")
                            st.markdown(f"### 📈 عملکرد از {performance['start_date']} تا {performance['end_date']}")

                            col1, col2, col3, col4 = st.columns(4)
                            with col1:
                                st.markdown("**بازدهی کل**")
                                gain_str, css_class = format_gain(performance['gain'])
                                st.markdown(f"<div style='font-size: 1.5rem; font-weight: 700;' class='{css_class}'>{gain_str}</div>", unsafe_allow_html=True)

                            with col2:
                                st.markdown("**تعداد روزهای معاملاتی**")
                                st.markdown(f"<div style='font-size: 1.5rem; font-weight: 700;'>{format_number(performance['period_days'])}</div>", unsafe_allow_html=True)

                            with col3:
                                st.markdown("**سقف (Ceil)**")
                                ceil_str, css_class = format_ceil_floor(performance['ceil'], 'ceil')
                                st.markdown(f"<div style='font-size: 1.5rem; font-weight: 700;' class='{css_class}'>{ceil_str}</div>", unsafe_allow_html=True)

                            with col4:
                                st.markdown("**کف (Floor)**")
                                floor_str, css_class = format_ceil_floor(performance['floor'], 'floor')
                                st.markdown(f"<div style='font-size: 1.5rem; font-weight: 700;' class='{css_class}'>{floor_str}</div>", unsafe_allow_html=True)

                            with st.expander("📊 مشاهده جزئیات بیشتر"):
                                detail_data = {
                                    "شاخص": ["قیمت شروع", "قیمت فعلی", "بیشترین قیمت", "کمترین قیمت", "تغییر قیمت", "تعداد روزهای معاملاتی", "نوع تطابق"],
                                    "مقدار": [
                                        format_price(performance['start_price']),
                                        format_price(performance['latest_price']),
                                        format_price(performance['max_price']),
                                        format_price(performance['min_price']),
                                        f"{performance['gain']:.2f}%" if performance['gain'] is not None else "ندارد",
                                        format_number(performance['period_days']),
                                        "دقیق" if performance['start_data']['found_exact'] else "نزدیک‌ترین تاریخ"
                                    ]
                                }
                                detail_df = pd.DataFrame(detail_data)
                                st.table(detail_df)

                            st.markdown("</div>", unsafe_allow_html=True)

                            # Clear the flag after displaying
                            st.session_state.specific_date_analysis = False

                        else:
                            st.warning(f"❌ داده‌ای برای تاریخ {analysis_date} یافت نشد")
                            st.session_state.specific_date_analysis = False

                    # Continue with regular display
                    st.markdown("---")

                    # Define periods
                    periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
                    period_names = ["۵ روزه", "۱۰ روزه", "۱۵ روزه", "۲۰ روزه", "۳۰ روزه", "۶۰ روزه", "۱۲۰ روزه", "۱۸۰ روزه", "۳۶۰ روزه"]

                    # Get data in chronological order
                    df_asc = history_df.iloc[::-1].reset_index(drop=True)
                    latest_date = df_asc['j_date'].iloc[-1]

                    # Calculate 2-year and 3-year gains
                    gain_2yr, date_2yr, _ = calculate_year_to_year_gain(df_asc, latest_date, 2)
                    gain_3yr, date_3yr, _ = calculate_year_to_year_gain(df_asc, latest_date, 3)

                    # Calculate from first date to now
                    gain_first, ceil_first, floor_first, first_date, latest_date_first = calculate_from_first_date(df_asc)

                    # Calculate period gains
                    gains = calculate_period_gains(history_df, periods)

                    # Calculate Ceil and Floor metrics
                    ceil_floor_metrics = calculate_ceil_floor_metrics(history_df, periods)

                    # Display period gains
                    if gains or ceil_floor_metrics or gain_2yr is not None or gain_3yr is not None or gain_first is not None:
                        st.subheader("📈 بازدهی در بازه‌های زمانی مختلف")

                        date_ranges_html = '<div class="date-range-box">'
                        for period in periods:
                            if period in gains:
                                start_date = gains[period]['start_date']
                                end_date = gains[period]['end_date']
                                date_ranges_html += f'<div class="period"><strong>{period} روز:</strong> {start_date} تا {end_date}</div>'
                            else:
                                date_ranges_html += f'<div class="period"><strong>{period} روز:</strong> داده‌ای موجود نیست</div>'

                        if gain_2yr is not None and date_2yr:
                            date_ranges_html += f'<div class="period"><strong>۲ سال:</strong> {date_2yr} تا {latest_date}</div>'
                        else:
                            date_ranges_html += f'<div class="period"><strong>۲ سال:</strong> داده‌ای موجود نیست</div>'

                        if gain_3yr is not None and date_3yr:
                            date_ranges_html += f'<div class="period"><strong>۳ سال:</strong> {date_3yr} تا {latest_date}</div>'
                        else:
                            date_ranges_html += f'<div class="period"><strong>۳ سال:</strong> داده‌ای موجود نیست</div>'

                        if gain_first is not None and first_date:
                            date_ranges_html += f'<div class="period"><strong>از ابتدا:</strong> {first_date} تا {latest_date_first}</div>'
                        else:
                            date_ranges_html += f'<div class="period"><strong>از ابتدا:</strong> داده‌ای موجود نیست</div>'

                        date_ranges_html += '</div>'
                        st.markdown(date_ranges_html, unsafe_allow_html=True)

                        # Create HTML table
                        html_table = """
                        <div class='gain-table'>
                            <table>
                                <thead>
                                    <tr style='background-color: #e3f2fd; font-weight: 700;'>
                        """

                        all_period_names = ["بازه"] + period_names + ["۲ ساله", "۳ ساله", "از ابتدا"]
                        for period_name in all_period_names:
                            html_table += f"<th>{period_name}</th>"

                        html_table += """
                                    </tr>
                                </thead>
                                <tbody>
                        """

                        # Gain row
                        html_table += "<tr style='background-color: #f5f5f5;'>"
                        html_table += "<td><strong>بازدهی</strong></td>"

                        for period in periods:
                            if period in gains:
                                gain_value, css_class = format_gain(gains[period]['gain'])
                                html_table += f"<td class='{css_class}'><strong>{gain_value}</strong></td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_2yr is not None:
                            gain_value, css_class = format_gain(gain_2yr)
                            html_table += f"<td class='{css_class}'><strong>{gain_value}</strong></td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_3yr is not None:
                            gain_value, css_class = format_gain(gain_3yr)
                            html_table += f"<td class='{css_class}'><strong>{gain_value}</strong></td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_first is not None:
                            gain_value, css_class = format_gain(gain_first)
                            html_table += f"<td class='{css_class}'><strong>{gain_value}</strong></td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        html_table += "</tr>"

                        # Ceil row
                        html_table += "<tr style='background-color: #fff3e0;'>"
                        html_table += "<td><strong>سقف</strong></td>"

                        for period in periods:
                            if period in ceil_floor_metrics:
                                ceil_value = ceil_floor_metrics[period]['ceil']
                                if ceil_value is not None:
                                    ceil_str, css_class = format_ceil_floor(ceil_value, 'ceil')
                                    html_table += f"<td class='{css_class}'><strong>{ceil_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_2yr is not None and date_2yr:
                            df_2yr = df_asc[df_asc['j_date'] >= date_2yr]
                            if not df_2yr.empty:
                                max_price_2yr = df_2yr['adj_final'].max()
                                latest_price_2yr = df_2yr['adj_final'].iloc[-1]
                                if max_price_2yr != 0:
                                    ceil_2yr = ((latest_price_2yr - max_price_2yr) / max_price_2yr) * 100
                                    ceil_str, css_class = format_ceil_floor(ceil_2yr, 'ceil')
                                    html_table += f"<td class='{css_class}'><strong>{ceil_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_3yr is not None and date_3yr:
                            df_3yr = df_asc[df_asc['j_date'] >= date_3yr]
                            if not df_3yr.empty:
                                max_price_3yr = df_3yr['adj_final'].max()
                                latest_price_3yr = df_3yr['adj_final'].iloc[-1]
                                if max_price_3yr != 0:
                                    ceil_3yr = ((latest_price_3yr - max_price_3yr) / max_price_3yr) * 100
                                    ceil_str, css_class = format_ceil_floor(ceil_3yr, 'ceil')
                                    html_table += f"<td class='{css_class}'><strong>{ceil_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if ceil_first is not None:
                            ceil_str, css_class = format_ceil_floor(ceil_first, 'ceil')
                            html_table += f"<td class='{css_class}'><strong>{ceil_str}</strong></td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        html_table += "</tr>"

                        # Floor row
                        html_table += "<tr style='background-color: #f3e5f5;'>"
                        html_table += "<td><strong>کف</strong></td>"

                        for period in periods:
                            if period in ceil_floor_metrics:
                                floor_value = ceil_floor_metrics[period]['floor']
                                if floor_value is not None:
                                    floor_str, css_class = format_ceil_floor(floor_value, 'floor')
                                    html_table += f"<td class='{css_class}'><strong>{floor_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_2yr is not None and date_2yr:
                            df_2yr = df_asc[df_asc['j_date'] >= date_2yr]
                            if not df_2yr.empty:
                                min_price_2yr = df_2yr['adj_final'].min()
                                latest_price_2yr = df_2yr['adj_final'].iloc[-1]
                                if min_price_2yr != 0:
                                    floor_2yr = ((latest_price_2yr - min_price_2yr) / min_price_2yr) * 100
                                    floor_str, css_class = format_ceil_floor(floor_2yr, 'floor')
                                    html_table += f"<td class='{css_class}'><strong>{floor_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if gain_3yr is not None and date_3yr:
                            df_3yr = df_asc[df_asc['j_date'] >= date_3yr]
                            if not df_3yr.empty:
                                min_price_3yr = df_3yr['adj_final'].min()
                                latest_price_3yr = df_3yr['adj_final'].iloc[-1]
                                if min_price_3yr != 0:
                                    floor_3yr = ((latest_price_3yr - min_price_3yr) / min_price_3yr) * 100
                                    floor_str, css_class = format_ceil_floor(floor_3yr, 'floor')
                                    html_table += f"<td class='{css_class}'><strong>{floor_str}</strong></td>"
                                else:
                                    html_table += "<td style='color: #999;'>ندارد</td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        if floor_first is not None:
                            floor_str, css_class = format_ceil_floor(floor_first, 'floor')
                            html_table += f"<td class='{css_class}'><strong>{floor_str}</strong></td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"

                        html_table += "</tr>"

                        html_table += """
                                </tbody>
                            </table>
                        </div>
                        """

                        st.markdown(html_table, unsafe_allow_html=True)

                        st.info("""
                        **🔍 توضیح شاخص‌ها:**
                        - **بازدهی:** درصد تغییر قیمت از ابتدا تا انتهای بازه (بر اساس قیمت پایانی تعدیل شده)
                        - **سقف (Ceil):** درصد تغییر قیمت از بالاترین قیمت بازه تا انتهای بازه
                        - **کف (Floor):** درصد تغییر قیمت از پایین‌ترین قیمت بازه تا انتهای بازه
                        """)

                    st.markdown("---")

                    # Prepare display dataframe
                    display_df = history_df[[
                        'j_date', 'weekday_persian', 'volume',
                        'open', 'high', 'low', 'close', 'final',
                        'adj_open', 'adj_high', 'adj_low', 'adj_close', 'adj_final',
                        'daily_gain'
                    ]].copy()

                    display_df.columns = [
                        '📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم',
                        '💰 قیمت بازگشایی', '📈 بالاترین', '📉 پایین‌ترین',
                        '🔚 آخرین قیمت', '🏁 قیمت پایانی',
                        '📊 بازگشایی تعدیل شده', '📈 بالاترین تعدیل شده', '📉 پایین‌ترین تعدیل شده',
                        '🔚 آخرین تعدیل شده', '🎯 پایانی تعدیل شده',
                        '📈 درصد تغییر'
                    ]

                    # Format columns
                    display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)
                    price_columns = ['💰 قیمت بازگشایی', '📈 بالاترین', '📉 پایین‌ترین',
                                   '🔚 آخرین قیمت', '🏁 قیمت پایانی',
                                   '📊 بازگشایی تعدیل شده', '📈 بالاترین تعدیل شده', '📉 پایین‌ترین تعدیل شده',
                                   '🔚 آخرین تعدیل شده', '🎯 پایانی تعدیل شده']
                    for col in price_columns:
                        display_df[col] = display_df[col].apply(
                            lambda x: format_price(x) if pd.notna(x) and x != "" else ""
                        )

                    display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(
                        lambda x: format_percent_change(x) if pd.notna(x) else ""
                    )

                    # Display table
                    st.subheader(f"📋 تاریخچه قیمت ({format_number(len(history_df))} رکورد)")

                    st.markdown('<div class="rtl" style="overflow-x: auto;">', unsafe_allow_html=True)
                    html_table = display_df.to_html(escape=False, index=False)
                    st.markdown(html_table, unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)

                    # Summary statistics
                    st.markdown("---")
                    st.subheader("📊 آمار خلاصه")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown("**قیمت پایانی تعدیل شده**")
                        stats_df = pd.DataFrame({
                            'شاخص': ['بیشترین', 'کمترین', 'میانگین'],
                            'مقدار': [
                                format_price(history_df['adj_final'].max()),
                                format_price(history_df['adj_final'].min()),
                                format_price(history_df['adj_final'].mean())
                            ]
                        })
                        st.table(stats_df)

                    with col2:
                        st.markdown("**آمار حجم**")
                        volume_stats = pd.DataFrame({
                            'شاخص': ['بیشترین حجم', 'کمترین حجم', 'میانگین حجم'],
                            'مقدار': [
                                format_number(history_df['volume'].max()),
                                format_number(history_df['volume'].min()),
                                format_number(history_df['volume'].mean())
                            ]
                        })
                        st.table(volume_stats)

                    with col3:
                        st.markdown("**درصد تغییر روزانه**")
                        valid_changes = history_df['daily_gain'].dropna()
                        if not valid_changes.empty:
                            change_stats = pd.DataFrame({
                                'شاخص': ['بیشترین افزایش', 'بیشترین کاهش', 'میانگین'],
                                'مقدار': [
                                    format_gain(valid_changes.max())[0],
                                    format_gain(valid_changes.min())[0],
                                    format_gain(valid_changes.mean())[0]
                                ]
                            })
                        else:
                            change_stats = pd.DataFrame({
                                'شاخص': ['بیشترین افزایش', 'بیشترین کاهش', 'میانگین'],
                                'مقدار': ['۰', '۰', '۰']
                            })
                        st.table(change_stats)

                    # Download button
                    st.markdown("---")
                    csv = history_df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label="📥 دانلود CSV",
                        data=csv,
                        file_name=f"{selected_ticker}_history_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv"
                    )

                else:
                    st.warning(f"تاریخچه قیمتی برای {selected_ticker} یافت نشد")
                    st.info("""
                    **داده‌ای برای این سهام در بازه زمانی انتخاب شده وجود ندارد.**

                    نکات:
                    - ممکن است نماد سهام اشتباه باشد
                    - ممکن است در بازه زمانی انتخابی معاملاتی انجام نشده باشد
                    - ممکن است سهام در بازار سهام وجود نداشته باشد
                    """)
        else:
            st.warning(f"❌ سهامی با عبارت '{st.session_state.search_term}' یافت نشد")
            st.info("💡 سهام‌های محبوب را امتحان کنید:")

            popular = ["فولاد", "خودرو", "وبملت", "شپنا", "کگل", "فملی", "بنیرو"]
            cols = st.columns(7)
            for i, ticker in enumerate(popular):
                with cols[i]:
                    if st.button(ticker, key=f"quick_{ticker}"):
                        st.session_state.search_term = ticker
                        st.rerun()

if __name__ == "__main__":
    main()