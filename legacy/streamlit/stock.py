import streamlit as st
import pandas as pd
import finpy_tse as fpy
import jdatetime
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
import numpy as np

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
    page_title="دریافت و نمایش تاریخچه سهام",
    page_icon="📊",
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

def get_stock_id_from_db(ticker):
    """Get stock ID from database for a given ticker"""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT stockid FROM stocks WHERE ticker = %s", (ticker,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        st.error(f"خطا در دریافت شناسه سهام: {e}")
        return None

def save_to_database(stock_id, ticker, df):
    """Save fetched price history to database"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()

        # Check if data already exists for this stock and date
        check_query = """
            SELECT COUNT(*) FROM StockPriceHistory
            WHERE stock_id = %s AND j_date = %s
        """

        insert_query = """
            INSERT INTO StockPriceHistory (
                stock_id, ticker, j_date, date, weekday, open, high, low, close, final,
                volume, value, no, name, adj_open, adj_high, adj_low, adj_close, adj_final
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        inserted_count = 0
        skipped_count = 0

        for _, row in df.iterrows():
            # Check if record exists
            cursor.execute(check_query, (stock_id, row['J-Date']))
            if cursor.fetchone()[0] > 0:
                skipped_count += 1
                continue

            # Insert new record
            cursor.execute(insert_query, (
                stock_id,
                row['Ticker'],
                row['J-Date'],
                row['Date'],
                row['Weekday'],
                row['Open'],
                row['High'],
                row['Low'],
                row['Close'],
                row['Final'],
                row['Volume'],
                row['Value'],
                row['No'],
                row['Name'],
                row['Adj Open'],
                row['Adj High'],
                row['Adj Low'],
                row['Adj Close'],
                row['Adj Final']
            ))
            inserted_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        st.success(f"✅ {inserted_count} رکورد جدید به پایگاه داده اضافه شد. {skipped_count} رکورد تکراری وجود داشت.")
        return True

    except Exception as e:
        st.error(f"خطا در ذخیره‌سازی در پایگاه داده: {e}")
        return False

def fetch_stock_history(ticker, start_date=None, end_date=None):
    """
    Fetch stock price history using finpy_tse

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
        with st.spinner(f"در حال دریافت داده‌های {ticker}..."):
            # Fetch historical price data
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
                st.warning(f"هیچ داده‌ای برای نماد {ticker} در بازه زمانی انتخاب شده یافت نشد.")
                return pd.DataFrame()

            # Reset index and process columns
            df.reset_index(inplace=True)
            df["J-Date"] = df["J-Date"].astype(str)

            # Map weekdays to Persian
            df['Weekday_Persian'] = df['Weekday'].map(PERSIAN_WEEKDAYS).fillna(df['Weekday'])

            # Calculate daily changes
            df_sorted = df.sort_values('J-Date').reset_index(drop=True)
            df_sorted['Daily_Change'] = df_sorted['Adj Final'].pct_change() * 100

            return df_sorted

    except Exception as e:
        st.error(f"خطا در دریافت داده‌ها: {e}")
        return pd.DataFrame()

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

def calculate_period_gains(df):
    """Calculate gains for different periods"""
    periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
    gains = {}

    if df.empty or len(df) < 5:
        return gains

    df_sorted = df.sort_values('J-Date').reset_index(drop=True)
    latest_price = df_sorted['Adj Final'].iloc[-1]
    latest_date = df_sorted['J-Date'].iloc[-1]

    for period in periods:
        if len(df_sorted) > period:
            past_price = df_sorted['Adj Final'].iloc[-(period+1)]
            gain = ((latest_price - past_price) / past_price) * 100
            past_date = df_sorted['J-Date'].iloc[-(period+1)]
            gains[period] = {
                'gain': gain,
                'start_date': past_date,
                'end_date': latest_date
            }

    # Calculate year-to-year gains
    current_date = latest_date
    year, month, day = map(int, current_date.split('-'))

    # 1-year gain
    try:
        date_1yr = f"{year-1:04d}-{month:02d}-{day:02d}"
        df_1yr = df_sorted[df_sorted['J-Date'] <= date_1yr]
        if not df_1yr.empty:
            price_1yr = df_1yr['Adj Final'].iloc[-1]
            gain_1yr = ((latest_price - price_1yr) / price_1yr) * 100
            gains['1yr'] = {
                'gain': gain_1yr,
                'start_date': df_1yr['J-Date'].iloc[-1],
                'end_date': latest_date
            }
    except:
        pass

    # 2-year gain
    try:
        date_2yr = f"{year-2:04d}-{month:02d}-{day:02d}"
        df_2yr = df_sorted[df_sorted['J-Date'] <= date_2yr]
        if not df_2yr.empty:
            price_2yr = df_2yr['Adj Final'].iloc[-1]
            gain_2yr = ((latest_price - price_2yr) / price_2yr) * 100
            gains['2yr'] = {
                'gain': gain_2yr,
                'start_date': df_2yr['J-Date'].iloc[-1],
                'end_date': latest_date
            }
    except:
        pass

    # 3-year gain
    try:
        date_3yr = f"{year-3:04d}-{month:02d}-{day:02d}"
        df_3yr = df_sorted[df_sorted['J-Date'] <= date_3yr]
        if not df_3yr.empty:
            price_3yr = df_3yr['Adj Final'].iloc[-1]
            gain_3yr = ((latest_price - price_3yr) / price_3yr) * 100
            gains['3yr'] = {
                'gain': gain_3yr,
                'start_date': df_3yr['J-Date'].iloc[-1],
                'end_date': latest_date
            }
    except:
        pass

    # From first date
    if len(df_sorted) > 1:
        first_price = df_sorted['Adj Final'].iloc[0]
        first_date = df_sorted['J-Date'].iloc[0]
        if first_price != 0:
            gain_first = ((latest_price - first_price) / first_price) * 100
            gains['first'] = {
                'gain': gain_first,
                'start_date': first_date,
                'end_date': latest_date
            }

    return gains

def calculate_ceil_floor_metrics(df, periods):
    """Calculate Ceil and Floor metrics for different periods"""
    metrics = {}

    if df.empty or len(df) < 5:
        return metrics

    df_sorted = df.sort_values('J-Date').reset_index(drop=True)

    for period in periods:
        if len(df_sorted) > period:
            period_data = df_sorted.tail(period + 1)

            if len(period_data) > 1:
                latest_price = period_data['Adj Final'].iloc[-1]
                max_price = period_data['Adj Final'].max()
                min_price = period_data['Adj Final'].min()

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
                    'start_date': period_data['J-Date'].iloc[0],
                    'end_date': period_data['J-Date'].iloc[-1]
                }

    return metrics

def get_price_and_data_on_date(df_asc, target_date):
    """
    Get price and all relevant data for a specific date
    """
    try:
        # Try exact match
        matching_rows = df_asc[df_asc['J-Date'] == target_date]
        if not matching_rows.empty:
            row = matching_rows.iloc[0]
            return {
                'date': target_date,
                'adj_final': row['Adj Final'],
                'adj_close': row['Adj Close'],
                'close': row['Close'],
                'final': row['Final'],
                'volume': row['Volume'],
                'weekday': row['Weekday'],
                'j_date': row['J-Date'],
                'found_exact': True
            }

        # If not found, find nearest date before
        df_before = df_asc[df_asc['J-Date'] < target_date]
        if not df_before.empty:
            row = df_before.iloc[-1]
            return {
                'date': row['J-Date'],
                'adj_final': row['Adj Final'],
                'adj_close': row['Adj Close'],
                'close': row['Close'],
                'final': row['Final'],
                'volume': row['Volume'],
                'weekday': row['Weekday'],
                'j_date': row['J-Date'],
                'found_exact': False
            }

        return None
    except Exception as e:
        return None

def calculate_performance_from_date(df_asc, target_date):
    """
    Calculate performance metrics from a specific date to now
    """
    try:
        # Get data for the target date
        date_data = get_price_and_data_on_date(df_asc, target_date)
        if date_data is None:
            return None

        # Get latest data
        latest_row = df_asc.iloc[-1]
        latest_date = latest_row['J-Date']

        # Calculate performance metrics
        start_price = date_data['adj_final']
        latest_price = latest_row['Adj Final']

        if start_price != 0:
            gain = ((latest_price - start_price) / start_price) * 100
        else:
            gain = None

        # Calculate Ceil and Floor for the period
        period_data = df_asc[df_asc['J-Date'] >= date_data['date']]
        max_price = period_data['Adj Final'].max()
        min_price = period_data['Adj Final'].min()

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
    if 'specific_date_analysis' not in st.session_state:
        st.session_state.specific_date_analysis = False
    if 'show_analysis' not in st.session_state:
        st.session_state.show_analysis = False

    st.title("📊 دریافت و نمایش تاریخچه سهام")

    # Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/stock.png", width=80)
        st.title("⚙️ تنظیمات")
        st.markdown("---")

        # Stock input
        ticker = st.text_input(
            "🔍 نماد سهام",
            placeholder="مثال: فولاد, خودرو, وبملت...",
            help="نماد سهام مورد نظر را وارد کنید"
        ).strip()

        st.markdown("---")

        # Date range selection
        st.subheader("📅 بازه زمانی")

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
                step=1
            )
        with col2:
            start_month = st.number_input(
                "ماه شروع",
                min_value=1,
                max_value=12,
                value=1,
                step=1
            )
        with col3:
            max_start_day = 31 if start_month in [1,2,3,4,5,6] else 30 if start_month in [7,8,9,10,11] else 29
            start_day = st.number_input(
                "روز شروع",
                min_value=1,
                max_value=max_start_day,
                value=1,
                step=1
            )

        st.markdown("تا")

        col1, col2, col3 = st.columns(3)
        with col1:
            end_year = st.number_input(
                "سال پایان",
                min_value=1380,
                max_value=current_year,
                value=current_year,
                step=1
            )
        with col2:
            end_month = st.number_input(
                "ماه پایان",
                min_value=1,
                max_value=12,
                value=current_month,
                step=1
            )
        with col3:
            max_end_day = 31 if end_month in [1,2,3,4,5,6] else 30 if end_month in [7,8,9,10,11] else 29
            end_day = st.number_input(
                "روز پایان",
                min_value=1,
                max_value=max_end_day,
                value=min(current_day, max_end_day),
                step=1
            )

        start_date = f"{start_year:04d}-{start_month:02d}-{start_day:02d}"
        end_date = f"{end_year:04d}-{end_month:02d}-{end_day:02d}"

        st.info(f"📅 بازه: {start_date} تا {end_date}")

        st.markdown("---")

        # Fetch button
        fetch_button = st.button("📥 دریافت داده‌ها", use_container_width=True, type="primary")

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
            st.session_state.show_analysis = True
            st.rerun()

        st.markdown("---")

        # Save to database option
        save_to_db = st.checkbox("💾 ذخیره در پایگاه داده", value=False)

        st.markdown("---")

        # Popular stocks
        st.subheader("🔥 سهام‌های محبوب")
        popular = ["فولاد", "خودرو", "وبملت", "شپنا", "کگل", "فملی", "فخوز", "پارس"]
        cols = st.columns(4)
        for i, tick in enumerate(popular[:4]):
            with cols[i]:
                if st.button(tick, key=f"pop_{tick}", use_container_width=True):
                    ticker = tick
                    st.rerun()

    # Main content
    if fetch_button and ticker:
        # Fetch data
        df = fetch_stock_history(ticker, start_date, end_date)

        if not df.empty:
            # Store in session state
            st.session_state.df = df
            st.session_state.ticker = ticker

            # Check if stock exists in database
            stock_id = get_stock_id_from_db(ticker)

            # Display stock info
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("📊 نماد", ticker)
            with col2:
                latest_price = df['Adj Final'].iloc[-1]
                st.metric("💰 آخرین قیمت", format_price(latest_price))
            with col3:
                total_records = len(df)
                st.metric("📋 تعداد رکوردها", format_number(total_records))
            with col4:
                if stock_id:
                    st.metric("🆔 شناسه در پایگاه داده", stock_id)
                else:
                    st.metric("🆔 وضعیت", "⚠️ در پایگاه داده موجود نیست")

            # Save to database if requested
            if save_to_db:
                if stock_id:
                    save_to_database(stock_id, ticker, df)
                else:
                    st.warning("⚠️ ابتدا سهام را به جدول Stocks اضافه کنید. برای این کار از stock_updater.py استفاده کنید.")

            st.markdown("---")

            # === SPECIFIC DATE ANALYSIS SECTION ===
            if st.session_state.get('show_analysis', False) and 'analysis_date' in st.session_state:
                analysis_date = st.session_state.analysis_date

                # Get data in chronological order
                df_asc = df.sort_values('J-Date').reset_index(drop=True)

                # Perform analysis
                performance = calculate_performance_from_date(df_asc, analysis_date)

                if performance:
                    st.markdown("---")
                    st.subheader(f"📌 تحلیل تاریخ {analysis_date}")

                    # Display information in a nice box
                    st.markdown(f"""
                    <div class='specific-date-box'>
                        <h4>📊 اطلاعات تاریخ {analysis_date}</h4>
                    """, unsafe_allow_html=True)

                    # Check if exact date was found
                    if performance['start_data']['found_exact']:
                        st.markdown(f"✅ تاریخ **{analysis_date}** در داده‌ها موجود است")
                    else:
                        st.markdown(f"⚠️ تاریخ **{analysis_date}** در داده‌ها موجود نیست. نزدیک‌ترین تاریخ موجود: **{performance['start_date']}**")

                    # Display date data
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("📅 تاریخ", performance['start_date'])
                    with col2:
                        weekday_persian = PERSIAN_WEEKDAYS.get(performance['start_data']['weekday'], performance['start_data']['weekday'])
                        st.metric("📆 روز هفته", weekday_persian)
                    with col3:
                        st.metric("🎯 قیمت پایانی تعدیل شده", format_price(performance['start_price']))
                    with col4:
                        st.metric("📊 حجم معاملات", format_number(performance['start_data']['volume']))

                    st.markdown("---")
                    st.markdown(f"### 📈 عملکرد از {performance['start_date']} تا {performance['end_date']}")

                    # Performance metrics
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

                    # Additional information in expander
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

                    # Show the specific date data in the main table with highlight
                    st.markdown("---")
                    st.subheader(f"📋 تاریخچه قیمت (با هایلایت تاریخ {analysis_date})")

                    # Prepare display dataframe with highlighting
                    display_df = df[[
                        'J-Date', 'Weekday_Persian', 'Volume',
                        'Close', 'Final', 'Adj Close', 'Adj Final', 'Daily_Change'
                    ]].copy()

                    display_df.columns = [
                        '📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم',
                        '💰 آخرین قیمت', '🏁 قیمت پایانی',
                        '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده',
                        '📈 درصد تغییر'
                    ]

                    # Format columns
                    display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)
                    price_columns = ['💰 آخرین قیمت', '🏁 قیمت پایانی', '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده']
                    for col in price_columns:
                        display_df[col] = display_df[col].apply(
                            lambda x: format_price(x) if pd.notna(x) and x != "" else ""
                        )

                    display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(
                        lambda x: format_percent_change(x) if pd.notna(x) else ""
                    )

                    # Create HTML with highlight for the specific date
                    html_table = '<div class="rtl" style="overflow-x: auto;"><table class="dataframe">'

                    # Header
                    html_table += '<thead><tr>'
                    for col in display_df.columns:
                        html_table += f'<th>{col}</th>'
                    html_table += '</tr></thead><tbody>'

                    # Rows
                    for idx, row in display_df.iterrows():
                        is_target_date = (row['📅 تاریخ شمسی'] == performance['start_date'])
                        if is_target_date:
                            html_table += '<tr style="background-color: #fff9c4; font-weight: 700; border: 2px solid #fdd835;">'
                        else:
                            html_table += '<tr>'

                        for col in display_df.columns:
                            html_table += f'<td>{row[col]}</td>'
                        html_table += '</tr>'

                    html_table += '</tbody></table></div>'
                    st.markdown(html_table, unsafe_allow_html=True)

                    # Clear the flag after displaying
                    st.session_state.show_analysis = False

                else:
                    st.warning(f"❌ داده‌ای برای تاریخ {analysis_date} یافت نشد")
                    st.session_state.show_analysis = False

            # Continue with regular display if not showing specific date analysis
            if not st.session_state.get('show_analysis', False):
                # Calculate gains and metrics
                periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
                gains = calculate_period_gains(df)
                ceil_floor_metrics = calculate_ceil_floor_metrics(df, periods)

                # Display gains table
                if gains or ceil_floor_metrics:
                    st.subheader("📈 بازدهی در بازه‌های زمانی مختلف")

                    # Display date ranges
                    date_ranges_html = '<div class="date-range-box">'
                    for period, data in gains.items():
                        if period == 'first':
                            label = "از ابتدا"
                        elif period == '1yr':
                            label = "۱ ساله"
                        elif period == '2yr':
                            label = "۲ ساله"
                        elif period == '3yr':
                            label = "۳ ساله"
                        else:
                            label = f"{period} روزه"
                        date_ranges_html += f'<div class="period"><strong>{label}:</strong> {data["start_date"]} تا {data["end_date"]}</div>'
                    date_ranges_html += '</div>'
                    st.markdown(date_ranges_html, unsafe_allow_html=True)

                    # Create HTML table
                    html_table = """
                    <div class='gain-table'>
                        <table>
                            <thead>
                                <tr style='background-color: #e3f2fd; font-weight: 700;'>
                                    <th>بازه</th>
                    """

                    # Header
                    period_labels = []
                    for period in gains.keys():
                        if period == 'first':
                            label = "از ابتدا"
                        elif period == '1yr':
                            label = "۱ ساله"
                        elif period == '2yr':
                            label = "۲ ساله"
                        elif period == '3yr':
                            label = "۳ ساله"
                        else:
                            label = f"{period} روزه"
                        period_labels.append(label)
                        html_table += f"<th>{label}</th>"

                    html_table += """
                                </tr>
                            </thead>
                            <tbody>
                                <tr style='background-color: #f5f5f5;'>
                                    <td><strong>بازدهی</strong></td>
                    """

                    # Gains row
                    for period in gains.keys():
                        gain_value, css_class = format_gain(gains[period]['gain'])
                        html_table += f"<td class='{css_class}'><strong>{gain_value}</strong></td>"

                    html_table += "</tr>"

                    # Ceil row
                    html_table += "<tr style='background-color: #fff3e0;'><td><strong>سقف</strong></td>"
                    for period in gains.keys():
                        if period in ceil_floor_metrics:
                            ceil_value = ceil_floor_metrics[period]['ceil']
                            if ceil_value is not None:
                                ceil_str, css_class = format_gain(ceil_value)
                                html_table += f"<td class='{css_class}'><strong>{ceil_str}</strong></td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
                        else:
                            html_table += "<td style='color: #999;'>ندارد</td>"
                    html_table += "</tr>"

                    # Floor row
                    html_table += "<tr style='background-color: #f3e5f5;'><td><strong>کف</strong></td>"
                    for period in gains.keys():
                        if period in ceil_floor_metrics:
                            floor_value = ceil_floor_metrics[period]['floor']
                            if floor_value is not None:
                                floor_str, css_class = format_gain(floor_value)
                                html_table += f"<td class='{css_class}'><strong>{floor_str}</strong></td>"
                            else:
                                html_table += "<td style='color: #999;'>ندارد</td>"
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

                # Display full history table with simplified columns
                st.subheader(f"📋 تاریخچه کامل قیمت {ticker} ({format_number(len(df))} رکورد)")

                # Prepare display dataframe - showing essential columns
                display_df = df[[
                    'J-Date', 'Weekday_Persian', 'Volume',
                    'Close', 'Final', 'Adj Close', 'Adj Final', 'Daily_Change'
                ]].copy()

                display_df.columns = [
                    '📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم',
                    '💰 آخرین قیمت', '🏁 قیمت پایانی',
                    '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده',
                    '📈 درصد تغییر'
                ]

                # Format columns
                display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)
                price_columns = ['💰 آخرین قیمت', '🏁 قیمت پایانی', '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده']
                for col in price_columns:
                    display_df[col] = display_df[col].apply(
                        lambda x: format_price(x) if pd.notna(x) and x != "" else ""
                    )

                display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(
                    lambda x: format_percent_change(x) if pd.notna(x) else ""
                )

                # Display with RTL styling
                st.markdown('<div class="rtl" style="overflow-x: auto;">', unsafe_allow_html=True)

                # Convert to HTML for colored percentages
                html_table = display_df.to_html(escape=False, index=False)
                st.markdown(html_table, unsafe_allow_html=True)

                st.markdown('</div>', unsafe_allow_html=True)

                # Download button
                st.markdown("---")
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 دانلود CSV",
                    data=csv,
                    file_name=f"{ticker}_history_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )

        else:
            st.warning(f"هیچ داده‌ای برای نماد {ticker} در بازه زمانی انتخاب شده یافت نشد.")

    elif fetch_button and not ticker:
        st.warning("⚠️ لطفاً نماد سهام را وارد کنید.")

    # Show help if no data loaded
    if not fetch_button and not ticker:
        st.markdown("""
        <div style='text-align: center; padding: 50px 20px;'>
            <h2>👋 به دریافت‌کننده تاریخچه سهام خوش آمدید</h2>
            <p style='font-size: 1.1rem;'>
                برای شروع، نماد سهام مورد نظر را در سمت راست وارد کنید و دکمه "دریافت داده‌ها" را بزنید.
            </p>
            <br>
            <h4>📌 ویژگی‌ها:</h4>
            <ul style='text-align: right; display: inline-block;'>
                <li>🔍 دریافت داده‌های تاریخی با استفاده از finpy_tse</li>
                <li>📅 انتخاب بازه زمانی دلخواه به صورت شمسی</li>
                <li>📊 نمایش کامل تاریخچه قیمت با فرمت فارسی</li>
                <li>📈 محاسبه بازدهی در بازه‌های زمانی مختلف (۵ تا ۳۶۰ روزه، ۱-۳ ساله و از ابتدا)</li>
                <li>📉 محاسبه شاخص‌های سقف (Ceil) و کف (Floor)</li>
                <li>📌 <strong>تحلیل تاریخ خاص:</strong> وارد کردن یک تاریخ و مشاهده اطلاعات آن روز و عملکرد از آن تاریخ تا امروز</li>
                <li>💾 ذخیره‌سازی در پایگاه داده PostgreSQL</li>
                <li>📥 خروجی CSV</li>
            </ul>
            <br>
            <p style='font-size: 0.9rem; color: #666;'>
                🔥 سهام‌های محبوب: فولاد, خودرو, وبملت, شپنا, کگل, فملی, فخوز, پارس
            </p>
        </div>
        """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()