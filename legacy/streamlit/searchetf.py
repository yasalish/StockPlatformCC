import streamlit as st
import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import jdatetime

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
    page_title="نمایشگر تاریخچه صندوق‌ها",
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

    /* Style for ETF type badges */
    .etf-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
        margin-left: 5px;
    }

    .etf-type-ثابت {
        background-color: #e3f2fd;
        color: #0d47a1;
    }

    .etf-type-سهام {
        background-color: #e8f5e8;
        color: #1b5e20;
    }

    .etf-type-کالا {
        background-color: #fff3e0;
        color: #bf360c;
    }

    .etf-type-مختلط {
        background-color: #f3e5f5;
        color: #4a148c;
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
    }

    .gain-title-row {
        background-color: #e3f2fd;
        font-weight: 700;
        text-align: center;
        padding: 10px;
        border-radius: 5px 5px 0 0;
    }

    .gain-value-row {
        text-align: center;
        padding: 10px;
        font-size: 1.2rem;
    }

    .gain-positive {
        color: #4caf50;
        font-weight: 700;
    }

    .gain-negative {
        color: #f44336;
        font-weight: 700;
    }

    .gain-cell {
        background-color: white;
        border: 1px solid #e0e0e0;
        padding: 10px;
        text-align: center;
        border-radius: 5px;
    }

    /* Style for date range gain box */
    .date-range-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
        text-align: center;
    }

    .date-range-title {
        font-size: 1.2rem;
        margin-bottom: 10px;
    }

    .date-range-gain {
        font-size: 2rem;
        font-weight: 700;
    }

    .date-range-dates {
        font-size: 1rem;
        opacity: 0.9;
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

# ETF type mapping for display
ETF_TYPE_DISPLAY = {
    'ثابت': 'صندوق با درآمد ثابت',
    'در سهام': 'صندوق سرمایه‌گذاری در سهام',
    'سهام': 'صندوق سرمایه‌گذاری در سهام',
    'کالا': 'صندوق کالایی',
    'مختلط': 'صندوق مختلط'
}

def get_db_connection():
    """Create database connection"""
    try:
        conn = psycopg2.connect(**DB_SETTINGS)
        return conn
    except Exception as e:
        st.error(f"خطا در اتصال به پایگاه داده: {e}")
        return None

def search_etfs(search_term):
    """Search for ETFs by ticker with Persian text optimization"""
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
            WITH etf_search AS (
                SELECT
                    id,
                    ticker,
                    name,
                    type,
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
                FROM ETF
                WHERE
                    ticker = %s OR
                    name = %s OR
                    ticker ILIKE %s OR
                    name ILIKE %s OR
                    ticker ILIKE %s OR
                    name ILIKE %s
            )
            SELECT id, ticker, name, type
            FROM etf_search
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

def get_etf_history(etf_id, limit=None):
    """Get price history for selected ETF - showing both close and final prices with percentage change"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        if isinstance(etf_id, np.integer):
            etf_id = int(etf_id)

        # Query with close and final prices
        query = """
            SELECT
                ticker,
                j_date,
                weekday,
                volume,
                close as close_price,
                final as final_price,
                date
            FROM EtfPriceHistory
            WHERE etf_id = %s
            ORDER BY date DESC
        """

        params = [etf_id]
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        df = pd.read_sql_query(query, conn, params=params)

        # Convert weekday to Persian
        if not df.empty:
            df['weekday_persian'] = df['weekday'].map(PERSIAN_WEEKDAYS).fillna(df['weekday'])

            # Calculate percentage change for final price
            df['final_change'] = df['final_price'].pct_change() * 100
            # For the first row (most recent), there's no previous day to compare with previous day
            # We want to compare each day with the previous day in chronological order
            # Since data is in DESC order, we need to reverse for calculation then reverse back
            df_reversed = df.iloc[::-1].copy()  # Reverse to ASC order
            df_reversed['final_change'] = df_reversed['final_price'].pct_change() * 100
            df['final_change'] = df_reversed['final_change'].iloc[::-1].values  # Reverse back

        return df
    except Exception as e:
        st.error(f"خطا در دریافت تاریخچه: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def calculate_period_gains(df, periods):
    """Calculate gains for different periods based on final price"""
    gains = {}

    if df.empty or len(df) < max(periods):
        return gains

    # Get data in chronological order (oldest to newest)
    df_asc = df.iloc[::-1].reset_index(drop=True)

    latest_price = df_asc['final_price'].iloc[-1]
    latest_date = df_asc['j_date'].iloc[-1]

    for period in periods:
        if len(df_asc) > period:
            past_price = df_asc['final_price'].iloc[-(period+1)]
            gain = ((latest_price - past_price) / past_price) * 100
            past_date = df_asc['j_date'].iloc[-(period+1)]
            gains[period] = {
                'gain': gain,
                'start_date': past_date,
                'end_date': latest_date
            }

    return gains

def calculate_date_range_gain(df, start_date, end_date):
    """Calculate gain between two specific dates - Fixed for Jalali dates"""
    try:
        if df.empty:
            return None, None, None

        # Convert string dates to integers for comparison (remove hyphens)
        # Jalali dates are in YYYY-MM-DD format, we can compare them as strings
        # since they're properly formatted for lexical ordering

        # Get list of all dates in the dataframe
        df_dates = df['j_date'].tolist()

        # Convert dates to integers for numeric comparison (YYYYMMDD)
        df_dates_int = [int(d.replace('-', '')) for d in df_dates]
        start_int = int(start_date.replace('-', ''))
        end_int = int(end_date.replace('-', ''))

        # Find closest start date
        closest_start_idx = min(range(len(df_dates_int)), key=lambda i: abs(df_dates_int[i] - start_int))
        closest_start_date = df_dates[closest_start_idx]

        # Find closest end date
        closest_end_idx = min(range(len(df_dates_int)), key=lambda i: abs(df_dates_int[i] - end_int))
        closest_end_date = df_dates[closest_end_idx]

        # Get prices for those dates
        start_row = df[df['j_date'] == closest_start_date].iloc[0]
        end_row = df[df['j_date'] == closest_end_date].iloc[0]

        start_price = start_row['final_price']
        end_price = end_row['final_price']

        if start_price is None or end_price is None or start_price == 0:
            return None, None, None

        gain = ((end_price - start_price) / start_price) * 100

        return gain, closest_start_date, closest_end_date
    except Exception as e:
        st.error(f"خطا در محاسبه بازدهی بازه تاریخی: {e}")
        return None, None, None

def get_etfs_by_type():
    """Get list of ETFs grouped by type for quick access"""
    conn = get_db_connection()
    if not conn:
        return {}

    try:
        query = """
            SELECT type, ticker, name
            FROM ETF
            ORDER BY type, ticker
        """
        df = pd.read_sql_query(query, conn)

        # Group by type
        etfs_by_type = {}
        for _, row in df.iterrows():
            etf_type = row['type']
            if etf_type not in etfs_by_type:
                etfs_by_type[etf_type] = []
            etfs_by_type[etf_type].append({
                'ticker': row['ticker'],
                'name': row['name']
            })

        return etfs_by_type
    except Exception as e:
        st.error(f"خطا در دریافت لیست صندوق‌ها: {e}")
        return {}
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
        num_str = f"{float(num):,.0f}"
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

        # Format with 2 decimal places
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

        # Convert to Persian digits
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

        # Format with 2 decimal places
        if gain > 0:
            gain_str = f"+{gain:.2f}%"
            css_class = "gain-positive"
        elif gain < 0:
            gain_str = f"{gain:.2f}%"
            css_class = "gain-negative"
        else:
            gain_str = f"{gain:.2f}%"
            css_class = ""

        # Convert to Persian digits
        for eng, per in persian_digits.items():
            gain_str = gain_str.replace(eng, per)

        return gain_str, css_class
    except:
        return "۰", ""

def get_etf_type_color(type_name):
    """Get color class for ETF type badge"""
    type_map = {
        'ثابت': 'etf-type-ثابت',
        'در سهام': 'etf-type-سهام',
        'سهام': 'etf-type-سهام',
        'کالا': 'etf-type-کالا',
        'مختلط': 'etf-type-مختلط'
    }
    return type_map.get(type_name, '')

def main():
    # Initialize session state
    if 'search_term' not in st.session_state:
        st.session_state.search_term = ""
    if 'date_filter_enabled' not in st.session_state:
        st.session_state.date_filter_enabled = False
    if 'selected_etf_type' not in st.session_state:
        st.session_state.selected_etf_type = None
    if 'range_start_date' not in st.session_state:
        st.session_state.range_start_date = None
    if 'range_end_date' not in st.session_state:
        st.session_state.range_end_date = None
    if 'show_range_gain' not in st.session_state:
        st.session_state.show_range_gain = False

    # Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/fund.png", width=80)
        st.title("📊 نمایشگر تاریخچه صندوق‌ها")
        st.markdown("---")

        # Search input with session state
        search_term = st.text_input(
            "🔍 جستجوی صندوق (نام یا نماد)",
            value=st.session_state.search_term,
            placeholder="مثال: آگاس, آکارد, آسام..."
        )

        # Update session state
        if search_term != st.session_state.search_term:
            st.session_state.search_term = search_term
            st.rerun()

        st.markdown("---")

        # Filter by ETF type
        st.subheader("🏷️ فیلتر بر اساس نوع")
        etf_types = ["همه", "ثابت", "در سهام", "کالا", "مختلط"]
        selected_type_filter = st.selectbox(
            "نوع صندوق",
            etf_types,
            index=0,
            key="etf_type_filter"
        )

        st.markdown("---")

        # Settings
        st.subheader("⚙️ تنظیمات")
        record_option = st.radio(
            "تعداد رکوردها",
            options=["۱۰۰ تایی", "۵۰۰ تایی", "۱۰۰۰ تایی", "۵۰۰۰ تایی", "همه رکوردها"],
            index=4,  # Default to "All Records"
            key="record_limit_option"
        )

        # Convert selection to limit value
        records_limit = {
            "۱۰۰ تایی": 100,
            "۵۰۰ تایی": 500,
            "۱۰۰۰ تایی": 1000,
            "۵۰۰۰ تایی": 5000,
            "همه رکوردها": None
        }[record_option]

        st.markdown("---")

        # Date filter
        st.subheader("📅 فیلتر تاریخ")

        enable_filter = st.checkbox(
            "فعال کردن فیلتر تاریخ",
            value=st.session_state.date_filter_enabled,
            key="date_filter_checkbox"
        )

        # Update session state from checkbox
        if enable_filter != st.session_state.date_filter_enabled:
            st.session_state.date_filter_enabled = enable_filter
            st.rerun()

        if st.session_state.date_filter_enabled:
            # Get actual date range from current ETF data if available
            if 'history_df' in st.session_state and not st.session_state.history_df.empty:
                history_df = st.session_state.history_df
                min_date = history_df['j_date'].min()
                max_date = history_df['j_date'].max()

                # Show available date range
                st.info(f"📅 بازه موجود: **{min_date}** تا **{max_date}**")

                # Parse dates for defaults
                min_year, min_month, min_day = map(int, min_date.split('-'))
                max_year, max_month, max_day = map(int, max_date.split('-'))
            else:
                # Default range if no data loaded yet
                min_year, min_month, min_day = 1400, 1, 1
                max_year, max_month, max_day = 1404, 12, 29

            st.markdown("**تاریخ شروع (شمسی)**")
            col1, col2, col3 = st.columns(3)
            with col1:
                start_year = st.number_input(
                    "سال",
                    min_value=1380,
                    max_value=1410,
                    value=min_year,
                    step=1,
                    key="filter_start_year"
                )
            with col2:
                start_month = st.number_input(
                    "ماه",
                    min_value=1,
                    max_value=12,
                    value=min_month,
                    step=1,
                    key="filter_start_month"
                )
            with col3:
                # Adjust max days based on month
                if start_month in [1, 2, 3, 4, 5, 6]:
                    max_start_day = 31
                elif start_month in [7, 8, 9, 10, 11]:
                    max_start_day = 30
                else:  # Month 12 (Esfand)
                    max_start_day = 29

                start_day = st.number_input(
                    "روز",
                    min_value=1,
                    max_value=max_start_day,
                    value=min(min_day, max_start_day),
                    step=1,
                    key="filter_start_day"
                )

            st.markdown("**تاریخ پایان (شمسی)**")
            col1, col2, col3 = st.columns(3)
            with col1:
                end_year = st.number_input(
                    "سال",
                    min_value=1380,
                    max_value=1410,
                    value=max_year,
                    step=1,
                    key="filter_end_year"
                )
            with col2:
                end_month = st.number_input(
                    "ماه",
                    min_value=1,
                    max_value=12,
                    value=max_month,
                    step=1,
                    key="filter_end_month"
                )
            with col3:
                # Adjust max days based on month
                if end_month in [1, 2, 3, 4, 5, 6]:
                    max_end_day = 31
                elif end_month in [7, 8, 9, 10, 11]:
                    max_end_day = 30
                else:  # Month 12 (Esfand)
                    max_end_day = 29

                end_day = st.number_input(
                    "روز",
                    min_value=1,
                    max_value=max_end_day,
                    value=min(max_day, max_end_day),
                    step=1,
                    key="filter_end_day"
                )

            # Format date strings for display
            start_date_str = f"{start_year:04d}-{start_month:02d}-{start_day:02d}"
            end_date_str = f"{end_year:04d}-{end_month:02d}-{end_day:02d}"

            st.info(f"📅 فیلتر: {start_date_str} تا {end_date_str}")

            # Store in session state
            st.session_state.filter_start = start_date_str
            st.session_state.filter_end = end_date_str

        st.markdown("---")

        # Date Range Gain Calculator
        st.subheader("📊 محاسبه بازدهی در بازه دلخواه")

        # Get min and max dates from current ETF data if available
        if 'history_df' in st.session_state and not st.session_state.history_df.empty:
            history_df = st.session_state.history_df
            min_date_range = history_df['j_date'].min()
            max_date_range = history_df['j_date'].max()

            st.caption(f"بازه موجود: {min_date_range} تا {max_date_range}")

            # Date inputs
            col1, col2 = st.columns(2)
            with col1:
                range_start = st.text_input(
                    "تاریخ شروع",
                    value=st.session_state.range_start_date if st.session_state.range_start_date else min_date_range,
                    key="range_start_input",
                    placeholder="YYYY-MM-DD"
                )
            with col2:
                range_end = st.text_input(
                    "تاریخ پایان",
                    value=st.session_state.range_end_date if st.session_state.range_end_date else max_date_range,
                    key="range_end_input",
                    placeholder="YYYY-MM-DD"
                )

            # Validate date format
            valid_dates = True
            try:
                if range_start:
                    # Simple validation: check if it has the right format (YYYY-MM-DD)
                    parts = range_start.split('-')
                    if len(parts) == 3 and len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
                        pass
                    else:
                        valid_dates = False
                        st.error("فرمت تاریخ شروع نادرست است. از قالب YYYY-MM-DD استفاده کنید.")

                if range_end:
                    parts = range_end.split('-')
                    if len(parts) == 3 and len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
                        pass
                    else:
                        valid_dates = False
                        st.error("فرمت تاریخ پایان نادرست است. از قالب YYYY-MM-DD استفاده کنید.")
            except:
                valid_dates = False
                st.error("فرمت تاریخ نادرست است. از قالب YYYY-MM-DD استفاده کنید.")

            if valid_dates and st.button("📈 محاسبه بازدهی", key="calc_range_gain", use_container_width=True):
                st.session_state.range_start_date = range_start
                st.session_state.range_end_date = range_end
                st.session_state.show_range_gain = True
                st.rerun()
        else:
            st.info("ابتدا یک صندوق را انتخاب کنید")

        st.markdown("---")

        # Export options
        st.subheader("💾 خروجی")
        if st.button("📥 خروجی CSV", use_container_width=True):
            if 'history_df' in st.session_state:
                csv = st.session_state.history_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📄 دانلود CSV",
                    data=csv,
                    file_name=f"etf_history_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )

        # Reset button
        if st.button("🔄 بازنشانی همه فیلترها", use_container_width=True):
            keys_to_clear = ['search_term', 'date_filter_enabled', 'filter_start', 'filter_end',
                            'selected_etf_type', 'range_start_date', 'range_end_date', 'show_range_gain']
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

        st.markdown("---")
        st.caption("منبع داده: پایگاه داده صندوق‌ها")
        st.caption(f"آخرین بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Main content area
    st.title("📊 نمایشگر تاریخچه قیمت صندوق‌ها")

    # Get ETFs grouped by type for quick access
    etfs_by_type = get_etfs_by_type()

    # Popular ETFs section (when no search)
    if not st.session_state.search_term and selected_type_filter == "همه":
        # Display ETF types as tabs
        st.markdown("### 🔥 صندوق‌های محبوب بر اساس نوع")

        etf_tabs = st.tabs(["📈 همه", "💰 ثابت", "📊 در سهام", "🛢️ کالا", "🔄 مختلط"])

        with etf_tabs[0]:  # All
            if etfs_by_type:
                all_etfs = []
                for etf_type, etfs in etfs_by_type.items():
                    for etf in etfs[:4]:  # Show first 4 of each type
                        all_etfs.append(etf)

                cols = st.columns(4)
                for i, etf in enumerate(all_etfs[:12]):
                    with cols[i % 4]:
                        display_type = etf['ticker']
                        if st.button(
                            f"{display_type}",
                            key=f"pop_all_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[1]:  # Fixed Income
            if 'ثابت' in etfs_by_type:
                cols = st.columns(4)
                for i, etf in enumerate(etfs_by_type['ثابت'][:8]):
                    with cols[i % 4]:
                        if st.button(
                            f"{etf['ticker']}",
                            key=f"pop_fixed_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[2]:  # Equity
            if 'در سهام' in etfs_by_type:
                cols = st.columns(4)
                for i, etf in enumerate(etfs_by_type['در سهام'][:8]):
                    with cols[i % 4]:
                        if st.button(
                            f"{etf['ticker']}",
                            key=f"pop_equity_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[3]:  # Commodity
            if 'کالا' in etfs_by_type:
                cols = st.columns(4)
                for i, etf in enumerate(etfs_by_type['کالا'][:8]):
                    with cols[i % 4]:
                        if st.button(
                            f"{etf['ticker']}",
                            key=f"pop_commodity_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[4]:  # Mixed
            if 'مختلط' in etfs_by_type:
                cols = st.columns(4)
                for i, etf in enumerate(etfs_by_type['مختلط'][:8]):
                    with cols[i % 4]:
                        if st.button(
                            f"{etf['ticker']}",
                            key=f"pop_mixed_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.search_term = etf['ticker']
                            st.rerun()

        st.markdown("---")

        # Welcome message
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image("https://img.icons8.com/color/200/fund.png")
            st.markdown("""
            <div style='text-align: center; direction: rtl; font-family: Vazirmatn;'>
                <h3>👋 به نمایشگر تاریخچه صندوق‌ها خوش آمدید</h3>
                <p><strong>شروع کار:</strong></p>
                <ol style='text-align: right;'>
                    <li>نام یا نماد صندوق را در کادر جستجو (سمت راست) وارد کنید</li>
                    <li>صندوق مورد نظر را از نتایج انتخاب کنید</li>
                    <li>تاریخچه کامل قیمت را مشاهده کنید</li>
                </ol>
                <p><strong>داده‌های موجود:</strong></p>
                <ul style='text-align: right;'>
                    <li>📅 تاریخ شمسی (YYYY-MM-DD)</li>
                    <li>📆 روز هفته (به فارسی)</li>
                    <li>📊 حجم معاملات</li>
                    <li>💰 آخرین قیمت</li>
                    <li>🏁 قیمت پایانی</li>
                    <li>📈 درصد تغییر روزانه</li>
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
                    <li>📅 بازه دلخواه (با وارد کردن تاریخ)</li>
                </ul>
                <p><strong>امکانات:</strong></p>
                <ul style='text-align: right;'>
                    <li>🔍 جستجوی هوشمند فارسی</li>
                    <li>📅 فیلتر تاریخ پویا بر اساس داده‌های واقعی</li>
                    <li>💾 خروجی CSV</li>
                    <li>📈 آمار خلاصه و بازدهی دوره‌ای</li>
                    <li>📊 محاسبه بازدهی در بازه دلخواه</li>
                    <li>📊 نمایش تمام داده‌های تاریخی</li>
                    <li>🇮🇷 پشتیبانی کامل از راست‌چین و اعداد فارسی</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

    # Search and display
    elif st.session_state.search_term or selected_type_filter != "همه":
        search_query = st.session_state.search_term

        with st.spinner(f"در حال جستجوی '{search_query}'..." if search_query else "در حال بارگذاری صندوق‌ها..."):
            if search_query:
                etfs_df = search_etfs(search_query)
            else:
                # If no search term but type filter is active, get all ETFs of that type
                conn = get_db_connection()
                if conn:
                    type_filter = selected_type_filter if selected_type_filter != "همه" else None
                    if type_filter:
                        query = "SELECT id, ticker, name, type FROM ETF WHERE type = %s ORDER BY ticker LIMIT 50"
                        etfs_df = pd.read_sql_query(query, conn, params=(type_filter,))
                    else:
                        query = "SELECT id, ticker, name, type FROM ETF ORDER BY ticker LIMIT 50"
                        etfs_df = pd.read_sql_query(query, conn)
                    conn.close()
                else:
                    etfs_df = pd.DataFrame()

        if not etfs_df.empty:
            st.success(f"✅ {len(etfs_df)} صندوق یافت شد")

            if search_query:
                st.info(f"🔍 نتایج جستجو برای: **{search_query}**")

            # Create display options with type badges
            stock_options = []
            for _, row in etfs_df.iterrows():
                etf_type = row['type']
                type_class = get_etf_type_color(etf_type)
                display_text = f"{row['ticker']} - {row['name']}"
                stock_options.append(display_text)

            selected_etf = st.selectbox(
                "انتخاب صندوق برای مشاهده تاریخچه:",
                stock_options,
                key="etf_selector"
            )

            if selected_etf:
                selected_index = stock_options.index(selected_etf)
                selected_etf_id = etfs_df.iloc[selected_index]['id']
                selected_ticker = etfs_df.iloc[selected_index]['ticker']
                selected_name = etfs_df.iloc[selected_index]['name']
                selected_type = etfs_df.iloc[selected_index]['type']

                # Display ETF info with type badge
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"### 📊 {selected_ticker} - {selected_name}")
                with col2:
                    type_display = ETF_TYPE_DISPLAY.get(selected_type, selected_type)
                    st.markdown(f"<div class='etf-badge {get_etf_type_color(selected_type)}' style='text-align: center;'>{type_display}</div>", unsafe_allow_html=True)

                with st.spinner(f"در حال بارگذاری تاریخچه قیمت {selected_ticker}..."):
                    history_df = get_etf_history(selected_etf_id, limit=records_limit)

                if not history_df.empty:
                    # Store in session state for export and filter
                    st.session_state.history_df = history_df.copy()

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
                        latest_close = history_df['close_price'].iloc[0] if not history_df.empty else 0
                        st.metric("آخرین قیمت", format_price(latest_close))

                    st.markdown("---")

                    # Display Date Range Gain if requested
                    if st.session_state.show_range_gain and st.session_state.range_start_date and st.session_state.range_end_date:
                        gain, actual_start, actual_end = calculate_date_range_gain(
                            history_df,
                            st.session_state.range_start_date,
                            st.session_state.range_end_date
                        )

                        if gain is not None:
                            gain_str, css_class = format_gain(gain)

                            # Create styled box for date range gain
                            st.markdown(f"""
                            <div class='date-range-box'>
                                <div class='date-range-title'>📊 بازدهی در بازه دلخواه</div>
                                <div class='date-range-gain {css_class}'>{gain_str}</div>
                                <div class='date-range-dates'>{actual_start} تا {actual_end}</div>
                                <div style='font-size: 0.9rem; margin-top: 10px;'>(نزدیک‌ترین تاریخ‌های موجود)</div>
                            </div>
                            """, unsafe_allow_html=True)
                        else:
                            st.error("❌ داده‌ای برای این بازه یافت نشد")

                    # Calculate period gains
                    periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
                    period_names = ["۵ روزه", "۱۰ روزه", "۱۵ روزه", "۲۰ روزه", "۳۰ روزه", "۶۰ روزه", "۱۲۰ روزه", "۱۸۰ روزه", "۳۶۰ روزه"]
                    gains = calculate_period_gains(history_df, periods)

                    # Display period gains as a 2-row table
                    if gains:
                        st.subheader("📈 بازدهی در بازه‌های زمانی مختلف")

                        # Create HTML for 2-row table
                        html_table = """
                        <div class='gain-table' style='direction: rtl;'>
                            <table style='width: 100%; border-collapse: collapse;'>
                                <tr style='background-color: #e3f2fd; font-weight: 700;'>
                        """

                        # First row - Period names
                        for period_name in period_names:
                            html_table += f"<th style='padding: 10px; text-align: center; border: 1px solid #ddd;'>{period_name}</th>"

                        html_table += "</tr><tr>"

                        # Second row - Gain values
                        for period in periods:
                            if period in gains:
                                gain_value, css_class = format_gain(gains[period]['gain'])
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; font-size: 1.1rem;' class='{css_class}'>{gain_value}</td>"
                            else:
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; color: #999;'>ندارد</td>"

                        html_table += "</tr></table></div>"

                        st.markdown(html_table, unsafe_allow_html=True)

                        # Show date ranges in an expander
                        with st.expander("📅 مشاهده بازه‌های تاریخی"):
                            date_data = []
                            for period in periods:
                                if period in gains:
                                    gain_info = gains[period]
                                    date_data.append({
                                        "بازه": f"{period} روزه",
                                        "تاریخ شروع": gain_info['start_date'],
                                        "تاریخ پایان": gain_info['end_date']
                                    })

                            if date_data:
                                date_df = pd.DataFrame(date_data)
                                st.dataframe(date_df, use_container_width=True)

                    st.markdown("---")

                    # Apply date filter if enabled
                    filtered_df = history_df.copy()
                    if st.session_state.date_filter_enabled and 'filter_start' in st.session_state and 'filter_end' in st.session_state:
                        filtered_df = history_df[
                            (history_df['j_date'] >= st.session_state.filter_start) &
                            (history_df['j_date'] <= st.session_state.filter_end)
                        ]

                        if filtered_df.empty:
                            st.warning(f"داده‌ای بین {st.session_state.filter_start} و {st.session_state.filter_end} یافت نشد")
                            st.info(f"بازه موجود: {history_df['j_date'].min()} تا {history_df['j_date'].max()}")

                    if not filtered_df.empty:
                        # Prepare display dataframe - showing close, final prices and percentage change
                        display_df = filtered_df[[
                            'j_date', 'weekday_persian', 'volume',
                            'close_price', 'final_price', 'final_change'
                        ]].copy()

                        # Rename columns for display
                        display_df.columns = [
                            '📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم',
                            '💰 آخرین قیمت', '🏁 قیمت پایانی', '📈 درصد تغییر'
                        ]

                        # Format volume with Persian digits
                        display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)

                        # Format price columns with Persian digits
                        display_df['💰 آخرین قیمت'] = display_df['💰 آخرین قیمت'].apply(
                            lambda x: format_price(x) if pd.notna(x) and x != "" else ""
                        )
                        display_df['🏁 قیمت پایانی'] = display_df['🏁 قیمت پایانی'].apply(
                            lambda x: format_price(x) if pd.notna(x) and x != "" else ""
                        )

                        # Format percentage change with HTML styling
                        display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(
                            lambda x: format_percent_change(x) if pd.notna(x) else ""
                        )

                        # Display table
                        st.subheader(f"📋 تاریخچه قیمت ({format_number(len(filtered_df))} رکورد)")

                        # Apply RTL styling
                        st.markdown('<div class="rtl">', unsafe_allow_html=True)

                        # Use HTML for rendering to show colored percentages
                        html_table = display_df.to_html(escape=False, index=False)
                        st.markdown(html_table, unsafe_allow_html=True)

                        st.markdown('</div>', unsafe_allow_html=True)

                        # Summary statistics
                        st.markdown("---")
                        st.subheader("📊 آمار خلاصه")

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.markdown("**آخرین قیمت**")
                            stats_df = pd.DataFrame({
                                'شاخص': ['بیشترین', 'کمترین', 'میانگین'],
                                'مقدار': [
                                    format_price(filtered_df['close_price'].max()),
                                    format_price(filtered_df['close_price'].min()),
                                    format_price(filtered_df['close_price'].mean())
                                ]
                            })
                            st.table(stats_df)

                        with col2:
                            st.markdown("**قیمت پایانی**")
                            final_stats = pd.DataFrame({
                                'شاخص': ['بیشترین', 'کمترین', 'میانگین'],
                                'مقدار': [
                                    format_price(filtered_df['final_price'].max()),
                                    format_price(filtered_df['final_price'].min()),
                                    format_price(filtered_df['final_price'].mean())
                                ]
                            })
                            st.table(final_stats)

                        with col3:
                            st.markdown("**درصد تغییر روزانه**")
                            valid_changes = filtered_df['final_change'].dropna()
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
                    else:
                        st.warning("داده‌ای در بازه انتخاب شده وجود ندارد")

                else:
                    st.warning(f"تاریخچه قیمتی برای {selected_ticker} یافت نشد")
                    st.info("""
                    **این صندوق در جدول ETF وجود دارد اما تاریخچه قیمتی ندارد.**

                    برای دریافت داده‌های تاریخی، etf_updater.py را اجرا کنید.
                    """)
        else:
            st.warning(f"❌ صندوقی با عبارت '{search_query}' یافت نشد")
            st.info("💡 صندوق‌های محبوب را امتحان کنید:")

            popular_etfs = ["آگاس", "آکارد", "آسام", "آگین", "آکیم", "آساه"]
            cols = st.columns(6)
            for i, ticker in enumerate(popular_etfs):
                with cols[i]:
                    if st.button(ticker, key=f"quick_{ticker}"):
                        st.session_state.search_term = ticker
                        st.rerun()

if __name__ == "__main__":
    main()