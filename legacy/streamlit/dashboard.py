import streamlit as st
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import jdatetime
import sys
import os
from decimal import Decimal
import importlib.util
import inspect

# MUST BE THE FIRST STREAMLIT COMMAND
st.set_page_config(
    page_title="سامانه جامع تحلیل بازار سرمایه",
    page_icon="📊",
    layout="wide"
)

# Add the directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# ==================== DYNAMIC FUNCTION IMPORTER ====================

def import_functions_from_file(file_path, function_names):
    """
    Dynamically import specific functions from a Python file
    without executing the file's top-level code
    """
    try:
        # Get the module name from file path
        module_name = os.path.basename(file_path).replace('.py', '')

        # Load the module spec
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return None, f"Could not load spec from {file_path}"

        # Create the module
        module = importlib.util.module_from_spec(spec)

        # Execute the module (this will run the code)
        spec.loader.exec_module(module)

        # Extract the requested functions
        imported_functions = {}
        missing_functions = []

        for func_name in function_names:
            if hasattr(module, func_name):
                imported_functions[func_name] = getattr(module, func_name)
            else:
                missing_functions.append(func_name)

        return imported_functions, missing_functions
    except Exception as e:
        return None, str(e)

# ==================== IMPORT STOCK GAINER FUNCTIONS ====================

stock_gainer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_gainer.py')
stock_gainer_functions = [
    'fetch_all_stocks',
    'fetch_price_history',
    'date_exists',
    'get_n_trading_days_before',
    'find_nearest_date_backward',
    'calculate_return',
    'get_latest_date_in_db',
    'get_yesterday_date',
    'get_next_day'
]

stock_funcs, stock_missing = import_functions_from_file(stock_gainer_path, stock_gainer_functions)

if stock_funcs:
    STOCK_GAINER_AVAILABLE = True
    # Assign imported functions to variables
    fetch_all_stocks = stock_funcs.get('fetch_all_stocks')
    stock_fetch_price_history = stock_funcs.get('fetch_price_history')
    stock_date_exists = stock_funcs.get('date_exists')
    stock_get_trading_days = stock_funcs.get('get_n_trading_days_before')
    stock_find_nearest = stock_funcs.get('find_nearest_date_backward')
    stock_calculate_return = stock_funcs.get('calculate_return')
    stock_get_latest_date = stock_funcs.get('get_latest_date_in_db')
    stock_get_yesterday = stock_funcs.get('get_yesterday_date')
    stock_get_next_day = stock_funcs.get('get_next_day')
else:
    STOCK_GAINER_AVAILABLE = False
    st.warning(f"⚠ خطا در بارگذاری stock_gainer.py: {stock_missing}")

# ==================== IMPORT ETF GAINER FUNCTIONS ====================

etf_gainer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'etf_gainer.py')
etf_gainer_functions = [
    'fetch_etf_names',
    'fetch_price_history',
    'date_exists',
    'get_n_trading_days_before',
    'find_nearest_date_backward',
    'calculate_return',
    'calculate_year_to_year_gain',
    'calculate_same_date_previous_year',
    'get_price_on_date',
    'get_latest_date_in_db',
    'get_yesterday_date',
    'get_next_day'
]

etf_funcs, etf_missing = import_functions_from_file(etf_gainer_path, etf_gainer_functions)

if etf_funcs:
    ETF_GAINER_AVAILABLE = True
    # Assign imported functions to variables
    fetch_etf_names = etf_funcs.get('fetch_etf_names')
    etf_fetch_price_history = etf_funcs.get('fetch_price_history')
    etf_date_exists = etf_funcs.get('date_exists')
    etf_get_trading_days = etf_funcs.get('get_n_trading_days_before')
    etf_find_nearest = etf_funcs.get('find_nearest_date_backward')
    etf_calculate_return = etf_funcs.get('calculate_return')
    calculate_year_to_year_gain = etf_funcs.get('calculate_year_to_year_gain')
    calculate_same_date_previous_year = etf_funcs.get('calculate_same_date_previous_year')
    get_price_on_date = etf_funcs.get('get_price_on_date')
    etf_get_latest_date = etf_funcs.get('get_latest_date_in_db')
    etf_get_yesterday = etf_funcs.get('get_yesterday_date')
    etf_get_next_day = etf_funcs.get('get_next_day')
else:
    ETF_GAINER_AVAILABLE = False
    st.warning(f"⚠ خطا در بارگذاری etf_gainer.py: {etf_missing}")

# ==================== IMPORT UPDATER MODULES ====================

# Try to import stock updater module normally
try:
    from stock_updater import update_stock_prices, get_yesterday_jalali_date as get_stock_yesterday
    STOCK_UPDATER_AVAILABLE = True
except ImportError as e:
    STOCK_UPDATER_AVAILABLE = False
    st.warning(f"⚠ ماژول stock_updater.py یافت نشد: {e}")

# Try to import ETF updater module normally
try:
    from etf_updater import update_etf_prices, get_yesterday_jalali_date as get_etf_yesterday
    ETF_UPDATER_AVAILABLE = True
except ImportError as e:
    ETF_UPDATER_AVAILABLE = False
    st.warning(f"⚠ ماژول etf_updater.py یافت نشد: {e}")

# Add custom CSS
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

    .stDataFrame th, .stDataFrame td {
        text-align: center !important;
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

    /* Gain styles */
    .gain-positive {
        color: #4caf50;
        font-weight: 700;
    }

    .gain-negative {
        color: #f44336;
        font-weight: 700;
    }

    /* ETF type badges */
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

    /* Update page styles */
    .info-box {
        background-color: #e3f2fd;
        border-radius: 10px;
        padding: 15px;
        margin: 10px 0;
        border-right: 5px solid #2196f3;
    }
</style>
""", unsafe_allow_html=True)

# Database connection settings
DB_SETTINGS = {
    "dbname": "Stock",
    "user": "postgres",
    "password": "stock93",
    "host": "localhost",
    "port": "5432",
}

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

# ETF type mapping
ETF_TYPE_DISPLAY = {
    'ثابت': 'صندوق با درآمد ثابت',
    'در سهام': 'صندوق سرمایه‌گذاری در سهام',
    'سهام': 'صندوق سرمایه‌گذاری در سهام',
    'کالا': 'صندوق کالایی',
    'مختلط': 'صندوق مختلط'
}

# ==================== COMMON FUNCTIONS ====================

def get_db_connection():
    """Create database connection"""
    try:
        conn = psycopg2.connect(**DB_SETTINGS)
        return conn
    except Exception as e:
        st.error(f"خطا در اتصال به پایگاه داده: {e}")
        return None

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
    """Format percentage change with Persian digits"""
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
    """Format gain percentage"""
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

def get_yesterday_jalali():
    """Get yesterday's date in Jalali format"""
    today_gregorian = datetime.now()
    yesterday_gregorian = today_gregorian - timedelta(days=1)
    yesterday_jalali = jdatetime.date.fromgregorian(
        year=yesterday_gregorian.year,
        month=yesterday_gregorian.month,
        day=yesterday_gregorian.day
    )
    return str(yesterday_jalali)

def get_next_day(jalali_date_str):
    """Get the next day after the given Jalali date"""
    try:
        year, month, day = map(int, jalali_date_str.split('-'))
        date_obj = jdatetime.date(year, month, day)
        next_day = date_obj + jdatetime.timedelta(days=1)
        return str(next_day)
    except Exception as e:
        return None

# ==================== STOCK SEARCH FUNCTIONS ====================

def search_stocks(search_term):
    """Search for stocks by name or ticker"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        search_term = search_term.strip()
        query = """
            SELECT stockid, ticker, name
            FROM stocks
            WHERE ticker ILIKE %s OR name ILIKE %s
            ORDER BY
                CASE
                    WHEN ticker = %s THEN 1
                    WHEN ticker ILIKE %s THEN 2
                    ELSE 3
                END,
                ticker
            LIMIT 30
        """
        starts_with = f"{search_term}%"
        contains = f"%{search_term}%"
        df = pd.read_sql_query(
            query, conn,
            params=(starts_with, contains, search_term, starts_with)
        )
        return df
    except Exception as e:
        st.error(f"خطا در جستجو: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_stock_history(stock_id, limit=None):
    """Get price history for selected stock"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
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
            df_reversed['daily_gain'] = df_reversed['final'].pct_change() * 100
            df['daily_gain'] = df_reversed['daily_gain'].iloc[::-1].values

        return df
    except Exception as e:
        st.error(f"خطا در دریافت تاریخچه: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_popular_stocks():
    """Get list of popular stocks"""
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
        return []
    finally:
        conn.close()

# ==================== ETF SEARCH FUNCTIONS ====================

def search_etfs(search_term):
    """Search for ETFs by ticker"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        search_term = search_term.strip()
        query = """
            SELECT id, ticker, name, type
            FROM ETF
            WHERE ticker ILIKE %s OR name ILIKE %s
            ORDER BY
                CASE
                    WHEN ticker = %s THEN 1
                    WHEN ticker ILIKE %s THEN 2
                    ELSE 3
                END,
                ticker
            LIMIT 30
        """
        starts_with = f"{search_term}%"
        contains = f"%{search_term}%"
        df = pd.read_sql_query(
            query, conn,
            params=(starts_with, contains, search_term, starts_with)
        )
        return df
    except Exception as e:
        st.error(f"خطا در جستجو: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_etf_history(etf_id, limit=None):
    """Get price history for selected ETF"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
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

        if not df.empty:
            df['weekday_persian'] = df['weekday'].map(PERSIAN_WEEKDAYS).fillna(df['weekday'])
            df_reversed = df.iloc[::-1].copy()
            df_reversed['final_change'] = df_reversed['final_price'].pct_change() * 100
            df['final_change'] = df_reversed['final_change'].iloc[::-1].values

        return df
    except Exception as e:
        st.error(f"خطا در دریافت تاریخچه: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_etfs_by_type():
    """Get list of ETFs grouped by type"""
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
        return {}
    finally:
        conn.close()

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

# ==================== STOCK GAINER PAGE (USING IMPORTED FUNCTIONS) ====================

def stock_gainer_page():
    st.header("📈 تحلیل عملکرد سهام")

    if not STOCK_GAINER_AVAILABLE:
        st.error("❌ ماژول stock_gainer.py به درستی بارگذاری نشده است.")
        st.info("لطفا مطمئن شوید فایل stock_gainer.py در مسیر صحیح قرار دارد.")
        return

    # Get latest date using the existing function
    latest_date = stock_get_latest_date()

    if latest_date:
        st.info(f"**آخرین تاریخ در پایگاه داده:** {latest_date}")
    else:
        st.warning("داده‌ای در پایگاه داده یافت نشد.")

    col1, col2 = st.columns(2)
    with col1:
        input_date = st.text_input(
            "تاریخ مورد نظر (YYYY-MM-DD):",
            value=latest_date if latest_date else "",
            key="stock_gainer_date"
        )
    with col2:
        all_stocks = fetch_all_stocks()
        if all_stocks:
            selected_ticker = st.selectbox(
                "انتخاب سهام برای تحلیل:",
                options=[""] + all_stocks,
                key="stock_gainer_ticker"
            )
        else:
            selected_ticker = st.text_input("نماد سهام:", key="stock_gainer_ticker_input")

    show_gains = st.button("📊 محاسبه بازدهی سهام", type="primary", key="stock_gainer_btn")

    if show_gains and input_date and selected_ticker:
        try:
            st.write(f"**تاریخ تحلیل:** {input_date}")
            st.write(f"**سهام انتخاب شده:** {selected_ticker}")

            # Validate date format
            try:
                datetime.strptime(input_date, "%Y-%m-%d")
            except ValueError:
                st.error("❌ فرمت تاریخ نامعتبر است. لطفا از فرمت YYYY-MM-DD استفاده کنید.")
                return

            # Check if date exists using existing function
            if not stock_date_exists(selected_ticker, input_date):
                st.error(f"❌ تاریخ {input_date} برای {selected_ticker} در پایگاه داده یافت نشد.")
                return

            st.success(f"✅ تاریخ {input_date} برای {selected_ticker} یافت شد.")

            # Define periods (same as original stock_gainer.py)
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

            # Display date ranges
            with st.expander("📅 بازه‌های زمانی", expanded=True):
                for period_name, days_back in all_periods.items():
                    end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                    calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")

                    if days_back <= 20:
                        start_date = stock_get_trading_days(selected_ticker, input_date, days_back)
                        if start_date:
                            st.write(f"**{period_name}:** {start_date} تا {input_date}")
                        else:
                            st.write(f"**{period_name}:** داده کافی قبل از {input_date} وجود ندارد")
                    else:
                        actual_start = stock_find_nearest(selected_ticker, calendar_start)
                        if actual_start:
                            st.write(f"**{period_name}:** {actual_start} تا {input_date}")
                        else:
                            st.write(f"**{period_name}:** داده کافی قبل از {input_date} وجود ندارد")

            # Calculate gains using existing functions
            with st.spinner(f"در حال محاسبه بازدهی {selected_ticker}..."):
                stock_results = {"نام": selected_ticker}
                stock_gains = {}

                for period_name, days_back in all_periods.items():
                    if days_back <= 20:
                        start_date = stock_get_trading_days(selected_ticker, input_date, days_back)
                    else:
                        end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                        calendar_start = (end_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
                        start_date = stock_find_nearest(selected_ticker, calendar_start)

                    if start_date:
                        df = stock_fetch_price_history(selected_ticker, start_date, input_date)
                        gain = stock_calculate_return(df)
                        if gain is not None:
                            stock_gains[period_name] = gain
                            stock_results[f"بازدهی ({period_name})"] = f"{gain:.2f}%"
                        else:
                            stock_gains[period_name] = None
                            stock_results[f"بازدهی ({period_name})"] = "N/A"
                    else:
                        stock_gains[period_name] = None
                        stock_results[f"بازدهی ({period_name})"] = "N/A"

            # Display results
            st.subheader(f"📊 نتایج تحلیل {selected_ticker}")

            cols = st.columns(2)
            col_idx = 0
            for period_name, gain in stock_gains.items():
                if gain is not None:
                    with cols[col_idx % 2]:
                        st.metric(label=f"**{period_name}**", value=f"{gain:.2f}%", delta="بازدهی")
                    col_idx += 1

            output = pd.DataFrame([stock_results])
            st.dataframe(output, use_container_width=True)

            csv = output.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 دانلود نتایج",
                data=csv,
                file_name=f"stock_gains_{selected_ticker}_{input_date}.csv",
                mime="text/csv"
            )

        except Exception as e:
            st.error(f"❌ خطا در پردازش: {e}")

    elif not input_date and show_gains:
        st.warning("⚠ لطفا یک تاریخ وارد کنید.")
    elif not selected_ticker and show_gains:
        st.warning("⚠ لطفا یک سهام انتخاب کنید.")

# ==================== ETF GAINER PAGE (USING IMPORTED FUNCTIONS) ====================

def etf_gainer_page():
    st.header("📈 تحلیل عملکرد صندوق‌ها")

    if not ETF_GAINER_AVAILABLE:
        st.error("❌ ماژول etf_gainer.py به درستی بارگذاری نشده است.")
        st.info("لطفا مطمئن شوید فایل etf_gainer.py در مسیر صحیح قرار دارد.")
        return

    # Get latest date using existing function
    latest_date = etf_get_latest_date()

    if latest_date:
        st.info(f"**آخرین تاریخ در پایگاه داده:** {latest_date}")
    else:
        st.warning("داده‌ای در پایگاه داده یافت نشد.")

    col1, col2 = st.columns(2)
    with col1:
        input_date = st.text_input(
            "تاریخ مورد نظر (YYYY-MM-DD):",
            value=latest_date if latest_date else "",
            key="etf_gainer_date"
        )
    with col2:
        compare_ticker = st.text_input(
            "نماد سهام برای مقایسه (اختیاری):",
            key="etf_gainer_ticker"
        ).strip()

    # ETF type selection
    st.subheader("📋 انتخاب نوع صندوق")
    etf_types = ["ثابت", "در سهام", "کالا", "مختلط"]
    selected_type = st.selectbox("دسته صندوق:", etf_types, index=1, key="etf_gainer_type")

    show_gains = st.button("📊 محاسبه بازدهی صندوق‌ها", type="primary", key="etf_gainer_btn")

    if show_gains and input_date:
        try:
            st.write(f"**تاریخ تحلیل: {input_date}**")

            # Validate date format
            try:
                datetime.strptime(input_date, "%Y-%m-%d")
            except ValueError:
                st.error("❌ فرمت تاریخ نامعتبر است. لطفا از فرمت YYYY-MM-DD استفاده کنید.")
                return

            # Fetch ETFs using existing function
            etfs = fetch_etf_names(selected_type)

            if not etfs:
                st.error("❌ صندوقی برای این نوع یافت نشد")
                return

            # Check if first ETF has data using existing function
            sample_ticker = etfs[0][1]
            if not etf_date_exists(sample_ticker, input_date):
                st.error(f"❌ تاریخ {input_date} برای {sample_ticker} در پایگاه داده یافت نشد.")
                return

            st.success(f"✅ تاریخ {input_date} در پایگاه داده یافت شد.")

            # Define periods (same as original etf_gainer.py)
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
                "360 Days": 360,
                "2 Years Ago to 1 Year Ago": "2y_to_1y",
                "3 Years Ago to 2 Years Ago": "3y_to_2y"
            }

            # Display date ranges
            with st.expander("📅 بازه‌های زمانی", expanded=True):
                for period_name, period_value in all_periods.items():
                    if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                        if period_name == "2 Years Ago to 1 Year Ago":
                            year_2 = calculate_same_date_previous_year(input_date, 2)
                            year_1 = calculate_same_date_previous_year(input_date, 1)
                            year_2_actual, _ = get_price_on_date(sample_ticker, year_2, use_nearest=True)
                            year_1_actual, _ = get_price_on_date(sample_ticker, year_1, use_nearest=True)
                            if year_2_actual and year_1_actual:
                                st.write(f"**{period_name}:** {year_2_actual} تا {year_1_actual}")
                            else:
                                st.write(f"**{period_name}:** داده کافی وجود ندارد")
                        else:
                            year_3 = calculate_same_date_previous_year(input_date, 3)
                            year_2 = calculate_same_date_previous_year(input_date, 2)
                            year_3_actual, _ = get_price_on_date(sample_ticker, year_3, use_nearest=True)
                            year_2_actual, _ = get_price_on_date(sample_ticker, year_2, use_nearest=True)
                            if year_3_actual and year_2_actual:
                                st.write(f"**{period_name}:** {year_3_actual} تا {year_2_actual}")
                            else:
                                st.write(f"**{period_name}:** داده کافی وجود ندارد")
                    else:
                        end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                        calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")

                        if period_value <= 20:
                            start_date = etf_get_trading_days(sample_ticker, input_date, period_value)
                            if start_date:
                                st.write(f"**{period_name}:** {start_date} تا {input_date}")
                            else:
                                st.write(f"**{period_name}:** داده کافی قبل از {input_date} وجود ندارد")
                        else:
                            actual_start = etf_find_nearest(sample_ticker, calendar_start)
                            if actual_start:
                                st.write(f"**{period_name}:** {actual_start} تا {input_date}")
                            else:
                                st.write(f"**{period_name}:** داده کافی قبل از {input_date} وجود ندارد")

            # Process ETFs
            output = pd.DataFrame()
            compare_gains = {}

            # Process comparison ticker if provided
            if compare_ticker:
                with st.spinner(f"در حال پردازش {compare_ticker}..."):
                    compare_results = {"نام": compare_ticker}

                    if not etf_date_exists(compare_ticker, input_date):
                        st.warning(f"⚠ {compare_ticker} داده‌ای برای {input_date} ندارد")
                    else:
                        for period_name, period_value in all_periods.items():
                            if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                                if period_name == "2 Years Ago to 1 Year Ago":
                                    gain, _, _ = calculate_year_to_year_gain(compare_ticker, input_date, 2)
                                else:
                                    gain, _, _ = calculate_year_to_year_gain(compare_ticker, input_date, 3)
                                compare_gains[period_name] = gain
                                compare_results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                            else:
                                if period_value <= 20:
                                    start_date = etf_get_trading_days(compare_ticker, input_date, period_value)
                                else:
                                    end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                                    calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                                    start_date = etf_find_nearest(compare_ticker, calendar_start)

                                if start_date:
                                    df = etf_fetch_price_history(compare_ticker, start_date, input_date)
                                    gain = etf_calculate_return(df)
                                    compare_gains[period_name] = gain
                                    compare_results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                                else:
                                    compare_gains[period_name] = None
                                    compare_results[f"Gain ({period_name})"] = "N/A"

                        output = pd.concat([output, pd.DataFrame([compare_results])], ignore_index=True)

            # Process all ETFs
            with st.spinner(f"در حال پردازش {len(etfs)} صندوق..."):
                for _, ticker in etfs:
                    try:
                        results = {"نام": ticker}

                        if not etf_date_exists(ticker, input_date):
                            results["نام"] = f"{ticker} (بدون داده)"
                            for period_name in all_periods.keys():
                                results[f"Gain ({period_name})"] = "N/A"
                            output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)
                            continue

                        for period_name, period_value in all_periods.items():
                            if period_name in ["2 Years Ago to 1 Year Ago", "3 Years Ago to 2 Years Ago"]:
                                if period_name == "2 Years Ago to 1 Year Ago":
                                    gain, _, _ = calculate_year_to_year_gain(ticker, input_date, 2)
                                else:
                                    gain, _, _ = calculate_year_to_year_gain(ticker, input_date, 3)
                                results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                            else:
                                if period_value <= 20:
                                    start_date = etf_get_trading_days(ticker, input_date, period_value)
                                else:
                                    end_dt = datetime.strptime(input_date, "%Y-%m-%d")
                                    calendar_start = (end_dt - timedelta(days=period_value)).strftime("%Y-%m-%d")
                                    start_date = etf_find_nearest(ticker, calendar_start)

                                if start_date:
                                    df = etf_fetch_price_history(ticker, start_date, input_date)
                                    gain = etf_calculate_return(df)
                                    results[f"Gain ({period_name})"] = f"{gain:.2f}%" if gain is not None else "N/A"
                                else:
                                    results[f"Gain ({period_name})"] = "N/A"

                        output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)

                    except Exception as e:
                        results = {"نام": f"{ticker} (خطا)"}
                        for period_name in all_periods.keys():
                            results[f"Gain ({period_name})"] = "خطا"
                        output = pd.concat([output, pd.DataFrame([results])], ignore_index=True)

            # Find top performers
            st.subheader("🏆 بهترین عملکردها")

            valid_etfs = output[~output["نام"].str.contains("(بدون داده|خطا)")]

            if not valid_etfs.empty:
                cols = st.columns(3)
                col_idx = 0

                for period_name in all_periods.keys():
                    col_name = f"Gain ({period_name})"
                    valid_etfs[f"{col_name}_num"] = valid_etfs[col_name].str.replace('%', '').str.replace('N/A', 'nan').astype(float)

                    max_idx = valid_etfs[f"{col_name}_num"].idxmax()
                    if pd.notna(max_idx) and not pd.isna(valid_etfs.loc[max_idx, f"{col_name}_num"]):
                        max_ticker = valid_etfs.loc[max_idx, "نام"]
                        max_gain = float(valid_etfs.loc[max_idx, f"{col_name}_num"])

                        with cols[col_idx % 3]:
                            st.metric(
                                label=f"**{period_name}**",
                                value=f"{max_gain:.2f}%",
                                delta=max_ticker
                            )
                        col_idx += 1

            # Display all results
            st.subheader("📋 نتایج تمام صندوق‌ها")

            for period_name in all_periods.keys():
                col_name = f"Gain ({period_name})_num"
                if col_name in output.columns:
                    output = output.drop(columns=[col_name])

            st.dataframe(output, use_container_width=True)

            # Download button
            csv = output.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 دانلود نتایج",
                data=csv,
                file_name=f"etf_gains_{input_date}_{selected_type}.csv",
                mime="text/csv"
            )

        except Exception as e:
            st.error(f"❌ خطا در پردازش: {e}")

    elif not input_date and show_gains:
        st.warning("⚠ لطفا یک تاریخ وارد کنید.")

# ==================== STOCK UPDATE PAGE ====================

def stock_update_page():
    st.header("🔄 به‌روزرسانی داده‌های سهام")

    if not STOCK_UPDATER_AVAILABLE:
        st.error("❌ ماژول به‌روزرسانی سهام یافت نشد. لطفا فایل stock_updater.py را بررسی کنید.")
        return

    # Get database info
    latest_date = stock_get_latest_date() if STOCK_GAINER_AVAILABLE else None
    yesterday_date = get_yesterday_jalali()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        <div class='info-box'>
            <h4>📊 اطلاعات پایگاه داده</h4>
        """, unsafe_allow_html=True)
        if latest_date:
            st.info(f"**آخرین تاریخ:** {latest_date}")
        else:
            st.warning("داده‌ای در پایگاه داده یافت نشد.")
        if yesterday_date:
            st.info(f"**تاریخ دیروز:** {yesterday_date}")
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        stocks = fetch_all_stocks() if STOCK_GAINER_AVAILABLE else []
        st.markdown("""
        <div class='info-box'>
            <h4>📈 آمار سهام</h4>
        """, unsafe_allow_html=True)
        st.info(f"**تعداد سهام:** {len(stocks)}")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("⚙️ تنظیمات به‌روزرسانی")

    update_option = st.radio(
        "نوع به‌روزرسانی:",
        ["🔄 همه سهام (از آخرین تاریخ تا دیروز)", "📅 بازه زمانی مشخص"],
        horizontal=True
    )

    if update_option == "📅 بازه زمانی مشخص":
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "تاریخ شروع:",
                value=datetime.now() - timedelta(days=30),
                key="stock_start_date"
            )
        with col2:
            end_date = st.date_input(
                "تاریخ پایان:",
                value=datetime.now(),
                key="stock_end_date"
            )
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        st.info(f"بازه انتخابی: {start_date_str} تا {end_date_str}")

        if st.button("🚀 شروع به‌روزرسانی", type="primary", use_container_width=True):
            with st.spinner("در حال به‌روزرسانی داده‌های سهام..."):
                try:
                    success, failure, total = update_stock_prices(
                        start_date=start_date_str,
                        end_date=end_date_str
                    )
                    if total > 0:
                        st.success(f"✅ به‌روزرسانی با موفقیت انجام شد!")
                        st.write(f"- موفق: {success}")
                        st.write(f"- ناموفق: {failure}")
                        st.write(f"- کل: {total}")
                    else:
                        st.warning("هیچ سهامی برای به‌روزرسانی یافت نشد.")
                except Exception as e:
                    st.error(f"خطا در به‌روزرسانی: {e}")

    else:  # همه سهام
        if latest_date and yesterday_date:
            update_start_date = get_next_day(latest_date)
            if update_start_date:
                st.info(f"**بازه به‌روزرسانی:** {update_start_date} تا {yesterday_date}")
                if st.button("🚀 شروع به‌روزرسانی همه سهام", type="primary", use_container_width=True):
                    with st.spinner("در حال به‌روزرسانی داده‌های سهام..."):
                        try:
                            success, failure, total = update_stock_prices(
                                start_date=update_start_date,
                                end_date=yesterday_date
                            )
                            if total > 0:
                                st.success(f"✅ به‌روزرسانی با موفقیت انجام شد!")
                                st.write(f"- موفق: {success}")
                                st.write(f"- ناموفق: {failure}")
                                st.write(f"- کل: {total}")
                            else:
                                st.warning("هیچ سهامی برای به‌روزرسانی یافت نشد.")
                        except Exception as e:
                            st.error(f"خطا در به‌روزرسانی: {e}")
            else:
                st.warning("امکان محاسبه بازه به‌روزرسانی وجود ندارد.")
        else:
            st.warning("برای به‌روزرسانی به آخرین تاریخ و تاریخ دیروز نیاز است.")

# ==================== ETF UPDATE PAGE ====================

def etf_update_page():
    st.header("🔄 به‌روزرسانی داده‌های صندوق‌ها")

    if not ETF_UPDATER_AVAILABLE:
        st.error("❌ ماژول به‌روزرسانی صندوق‌ها یافت نشد. لطفا فایل etf_updater.py را بررسی کنید.")
        return

    # Get database info
    latest_date = etf_get_latest_date() if ETF_GAINER_AVAILABLE else None
    yesterday_date = get_yesterday_jalali()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        <div class='info-box'>
            <h4>📊 اطلاعات پایگاه داده</h4>
        """, unsafe_allow_html=True)
        if latest_date:
            st.info(f"**آخرین تاریخ:** {latest_date}")
        else:
            st.warning("داده‌ای در پایگاه داده یافت نشد.")
        if yesterday_date:
            st.info(f"**تاریخ دیروز:** {yesterday_date}")
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        try:
            with psycopg2.connect(**DB_SETTINGS) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM ETF")
                    etf_count = cursor.fetchone()[0]
        except:
            etf_count = 0

        st.markdown("""
        <div class='info-box'>
            <h4>📊 آمار صندوق‌ها</h4>
        """, unsafe_allow_html=True)
        st.info(f"**تعداد صندوق‌ها:** {etf_count}")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("⚙️ تنظیمات به‌روزرسانی")

    update_option = st.radio(
        "نوع به‌روزرسانی:",
        ["🔄 همه صندوق‌ها (از آخرین تاریخ تا دیروز)", "📅 بازه زمانی مشخص"],
        horizontal=True
    )

    if update_option == "📅 بازه زمانی مشخص":
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "تاریخ شروع:",
                value=datetime.now() - timedelta(days=30),
                key="etf_start_date"
            )
        with col2:
            end_date = st.date_input(
                "تاریخ پایان:",
                value=datetime.now(),
                key="etf_end_date"
            )
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        st.info(f"بازه انتخابی: {start_date_str} تا {end_date_str}")

        if st.button("🚀 شروع به‌روزرسانی", type="primary", use_container_width=True):
            with st.spinner("در حال به‌روزرسانی داده‌های صندوق‌ها..."):
                try:
                    success, failure, total = update_etf_prices(
                        start_date=start_date_str,
                        end_date=end_date_str
                    )
                    if total > 0:
                        st.success(f"✅ به‌روزرسانی با موفقیت انجام شد!")
                        st.write(f"- موفق: {success}")
                        st.write(f"- ناموفق: {failure}")
                        st.write(f"- کل: {total}")
                    else:
                        st.warning("هیچ صندوقی برای به‌روزرسانی یافت نشد.")
                except Exception as e:
                    st.error(f"خطا در به‌روزرسانی: {e}")

    else:  # همه صندوق‌ها
        if latest_date and yesterday_date:
            update_start_date = get_next_day(latest_date)
            if update_start_date:
                st.info(f"**بازه به‌روزرسانی:** {update_start_date} تا {yesterday_date}")
                if st.button("🚀 شروع به‌روزرسانی همه صندوق‌ها", type="primary", use_container_width=True):
                    with st.spinner("در حال به‌روزرسانی داده‌های صندوق‌ها..."):
                        try:
                            success, failure, total = update_etf_prices(
                                start_date=update_start_date,
                                end_date=yesterday_date
                            )
                            if total > 0:
                                st.success(f"✅ به‌روزرسانی با موفقیت انجام شد!")
                                st.write(f"- موفق: {success}")
                                st.write(f"- ناموفق: {failure}")
                                st.write(f"- کل: {total}")
                            else:
                                st.warning("هیچ صندوقی برای به‌روزرسانی یافت نشد.")
                        except Exception as e:
                            st.error(f"خطا در به‌روزرسانی: {e}")
            else:
                st.warning("امکان محاسبه بازه به‌روزرسانی وجود ندارد.")
        else:
            st.warning("برای به‌روزرسانی به آخرین تاریخ و تاریخ دیروز نیاز است.")

# ==================== STOCK SEARCH PAGE ====================

def stock_search_page():
    st.header("🔍 جستجوی سهام")

    if 'stock_search_term' not in st.session_state:
        st.session_state.stock_search_term = ""

    with st.sidebar:
        st.subheader("⚙️ تنظیمات")
        record_option = st.radio(
            "تعداد رکوردها",
            options=["۱۰۰ تایی", "۵۰۰ تایی", "۱۰۰۰ تایی", "۵۰۰۰ تایی", "همه رکوردها"],
            index=4,
            key="stock_record_limit"
        )
        records_limit = {
            "۱۰۰ تایی": 100,
            "۵۰۰ تایی": 500,
            "۱۰۰۰ تایی": 1000,
            "۵۰۰۰ تایی": 5000,
            "همه رکوردها": None
        }[record_option]

    if not st.session_state.stock_search_term:
        st.markdown("### 🔥 سهام‌های محبوب")
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
                        st.session_state.stock_search_term = stock['ticker']
                        st.rerun()
        st.markdown("---")

    search_term = st.text_input(
        "🔍 جستجوی سهام (نام یا نماد)",
        value=st.session_state.stock_search_term,
        placeholder="مثال: فولاد, خودرو, وبملت..."
    )

    if search_term != st.session_state.stock_search_term:
        st.session_state.stock_search_term = search_term
        st.rerun()

    if st.session_state.stock_search_term:
        with st.spinner(f"در حال جستجوی '{st.session_state.stock_search_term}'..."):
            stocks_df = search_stocks(st.session_state.stock_search_term)

        if not stocks_df.empty:
            st.success(f"✅ {len(stocks_df)} سهام یافت شد")
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
                selected_stock_id = stocks_df.iloc[selected_index]['stockid']
                selected_ticker = stocks_df.iloc[selected_index]['ticker']
                selected_name = stocks_df.iloc[selected_index]['name']

                st.markdown(f"### 📈 {selected_ticker} - {selected_name}")

                with st.spinner(f"در حال بارگذاری تاریخچه قیمت {selected_ticker}..."):
                    history_df = get_stock_history(selected_stock_id, limit=records_limit)

                if not history_df.empty:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("نماد", selected_ticker)
                    with col2:
                        st.metric("تعداد رکوردها", format_number(len(history_df)))
                    with col3:
                        oldest_date = history_df['j_date'].iloc[-1] if len(history_df) > 0 else "N/A"
                        latest_date = history_df['j_date'].iloc[0] if not history_df.empty else "N/A"
                        st.metric("بازه تاریخی", f"{oldest_date} تا {latest_date}")
                    with col4:
                        latest_close = history_df['close'].iloc[0] if not history_df.empty else 0
                        st.metric("آخرین قیمت", format_price(latest_close))

                    st.markdown("---")

                    periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
                    period_names = ["۵ روزه", "۱۰ روزه", "۱۵ روزه", "۲۰ روزه", "۳۰ روزه", "۶۰ روزه", "۱۲۰ روزه", "۱۸۰ روزه", "۳۶۰ روزه"]

                    # Calculate gains using the data
                    if not history_df.empty and len(history_df) >= max(periods):
                        st.subheader("📈 بازدهی در بازه‌های زمانی مختلف")
                        html_table = """
                        <div style='direction: rtl;'>
                            <table style='width: 100%; border-collapse: collapse;'>
                                <tr style='background-color: #e3f2fd; font-weight: 700;'>
                        """
                        for period_name in period_names:
                            html_table += f"<th style='padding: 10px; text-align: center; border: 1px solid #ddd;'>{period_name}</th>"
                        html_table += "</tr><tr>"

                        # Calculate gains for each period
                        hist_asc = history_df.iloc[::-1].reset_index(drop=True)
                        latest_price = hist_asc['final'].iloc[-1]

                        for period in periods:
                            if len(hist_asc) > period:
                                past_price = hist_asc['final'].iloc[-(period+1)]
                                gain = ((latest_price - past_price) / past_price) * 100
                                gain_value, css_class = format_gain(gain)
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; font-size: 1.1rem;' class='{css_class}'>{gain_value}</td>"
                            else:
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; color: #999;'>ندارد</td>"
                        html_table += "</tr></table></div>"
                        st.markdown(html_table, unsafe_allow_html=True)

                    st.markdown("---")

                    display_df = history_df[['j_date', 'weekday_persian', 'volume', 'close', 'final', 'adj_close', 'adj_final', 'daily_gain']].copy()
                    display_df.columns = ['📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم', '💰 آخرین قیمت', '🏁 قیمت پایانی', '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده', '📈 درصد تغییر']

                    display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)
                    price_columns = ['💰 آخرین قیمت', '🏁 قیمت پایانی', '📊 آخرین قیمت تعدیل شده', '🎯 قیمت پایانی تعدیل شده']
                    for col in price_columns:
                        display_df[col] = display_df[col].apply(lambda x: format_price(x) if pd.notna(x) and x != "" else "")
                    display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(lambda x: format_percent_change(x) if pd.notna(x) else "")

                    st.subheader(f"📋 تاریخچه قیمت ({format_number(len(display_df))} رکورد)")
                    st.markdown('<div class="rtl">', unsafe_allow_html=True)
                    html_table = display_df.to_html(escape=False, index=False)
                    st.markdown(html_table, unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.warning(f"تاریخچه قیمتی برای {selected_ticker} یافت نشد")
        else:
            st.warning(f"❌ سهامی با عبارت '{st.session_state.stock_search_term}' یافت نشد")

# ==================== ETF SEARCH PAGE ====================

def etf_search_page():
    st.header("🔍 جستجوی صندوق‌ها")

    if 'etf_search_term' not in st.session_state:
        st.session_state.etf_search_term = ""

    with st.sidebar:
        st.subheader("⚙️ تنظیمات")
        record_option = st.radio(
            "تعداد رکوردها",
            options=["۱۰۰ تایی", "۵۰۰ تایی", "۱۰۰۰ تایی", "۵۰۰۰ تایی", "همه رکوردها"],
            index=4,
            key="etf_record_limit"
        )
        records_limit = {
            "۱۰۰ تایی": 100,
            "۵۰۰ تایی": 500,
            "۱۰۰۰ تایی": 1000,
            "۵۰۰۰ تایی": 5000,
            "همه رکوردها": None
        }[record_option]

        st.subheader("🏷️ فیلتر بر اساس نوع")
        etf_types = ["همه", "ثابت", "در سهام", "کالا", "مختلط"]
        selected_type_filter = st.selectbox("نوع صندوق", etf_types, index=0, key="etf_type_filter")

    etfs_by_type = get_etfs_by_type()

    if not st.session_state.etf_search_term and selected_type_filter == "همه":
        st.markdown("### 🔥 صندوق‌های محبوب بر اساس نوع")
        etf_tabs = st.tabs(["📈 همه", "💰 ثابت", "📊 در سهام", "🛢️ کالا", "🔄 مختلط"])

        with etf_tabs[0]:
            if etfs_by_type:
                all_etfs = []
                for etf_type, etfs in etfs_by_type.items():
                    for etf in etfs[:4]:
                        all_etfs.append(etf)
                cols = st.columns(4)
                for i, etf in enumerate(all_etfs[:12]):
                    with cols[i % 4]:
                        if st.button(
                            f"{etf['ticker']}",
                            key=f"pop_all_{etf['ticker']}",
                            use_container_width=True,
                            help=etf['name']
                        ):
                            st.session_state.etf_search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[1]:
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
                            st.session_state.etf_search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[2]:
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
                            st.session_state.etf_search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[3]:
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
                            st.session_state.etf_search_term = etf['ticker']
                            st.rerun()

        with etf_tabs[4]:
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
                            st.session_state.etf_search_term = etf['ticker']
                            st.rerun()

        st.markdown("---")

    search_term = st.text_input(
        "🔍 جستجوی صندوق (نام یا نماد)",
        value=st.session_state.etf_search_term,
        placeholder="مثال: آگاس, آکارد, آسام..."
    )

    if search_term != st.session_state.etf_search_term:
        st.session_state.etf_search_term = search_term
        st.rerun()

    if st.session_state.etf_search_term or selected_type_filter != "همه":
        search_query = st.session_state.etf_search_term

        with st.spinner(f"در حال جستجوی '{search_query}'..." if search_query else "در حال بارگذاری صندوق‌ها..."):
            if search_query:
                etfs_df = search_etfs(search_query)
            else:
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
            stock_options = []
            for _, row in etfs_df.iterrows():
                etf_type = row['type']
                display_text = f"{row['ticker']} - {row['name']}"
                stock_options.append(display_text)

            selected_etf = st.selectbox("انتخاب صندوق برای مشاهده تاریخچه:", stock_options, key="etf_selector")

            if selected_etf:
                selected_index = stock_options.index(selected_etf)
                selected_etf_id = etfs_df.iloc[selected_index]['id']
                selected_ticker = etfs_df.iloc[selected_index]['ticker']
                selected_name = etfs_df.iloc[selected_index]['name']
                selected_type = etfs_df.iloc[selected_index]['type']

                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"### 📊 {selected_ticker} - {selected_name}")
                with col2:
                    type_display = ETF_TYPE_DISPLAY.get(selected_type, selected_type)
                    st.markdown(f"<div class='etf-badge {get_etf_type_color(selected_type)}' style='text-align: center;'>{type_display}</div>", unsafe_allow_html=True)

                with st.spinner(f"در حال بارگذاری تاریخچه قیمت {selected_ticker}..."):
                    history_df = get_etf_history(selected_etf_id, limit=records_limit)

                if not history_df.empty:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("نماد", selected_ticker)
                    with col2:
                        st.metric("تعداد رکوردها", format_number(len(history_df)))
                    with col3:
                        oldest_date = history_df['j_date'].iloc[-1] if len(history_df) > 0 else "N/A"
                        latest_date = history_df['j_date'].iloc[0] if not history_df.empty else "N/A"
                        st.metric("بازه تاریخی", f"{oldest_date} تا {latest_date}")
                    with col4:
                        latest_close = history_df['close_price'].iloc[0] if not history_df.empty else 0
                        st.metric("آخرین قیمت", format_price(latest_close))

                    st.markdown("---")

                    periods = [5, 10, 15, 20, 30, 60, 120, 180, 360]
                    period_names = ["۵ روزه", "۱۰ روزه", "۱۵ روزه", "۲۰ روزه", "۳۰ روزه", "۶۰ روزه", "۱۲۰ روزه", "۱۸۰ روزه", "۳۶۰ روزه"]

                    # Calculate gains using the data
                    if not history_df.empty and len(history_df) >= max(periods):
                        st.subheader("📈 بازدهی در بازه‌های زمانی مختلف")
                        html_table = """
                        <div style='direction: rtl;'>
                            <table style='width: 100%; border-collapse: collapse;'>
                                <tr style='background-color: #e3f2fd; font-weight: 700;'>
                        """
                        for period_name in period_names:
                            html_table += f"<th style='padding: 10px; text-align: center; border: 1px solid #ddd;'>{period_name}</th>"
                        html_table += "</tr><tr>"

                        # Calculate gains for each period
                        hist_asc = history_df.iloc[::-1].reset_index(drop=True)
                        latest_price = hist_asc['final_price'].iloc[-1]

                        for period in periods:
                            if len(hist_asc) > period:
                                past_price = hist_asc['final_price'].iloc[-(period+1)]
                                gain = ((latest_price - past_price) / past_price) * 100
                                gain_value, css_class = format_gain(gain)
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; font-size: 1.1rem;' class='{css_class}'>{gain_value}</td>"
                            else:
                                html_table += f"<td style='padding: 10px; text-align: center; border: 1px solid #ddd; color: #999;'>ندارد</td>"
                        html_table += "</tr></table></div>"
                        st.markdown(html_table, unsafe_allow_html=True)

                    st.markdown("---")

                    display_df = history_df[['j_date', 'weekday_persian', 'volume', 'close_price', 'final_price', 'final_change']].copy()
                    display_df.columns = ['📅 تاریخ شمسی', '📆 روز هفته', '📊 حجم', '💰 آخرین قیمت', '🏁 قیمت پایانی', '📈 درصد تغییر']

                    display_df['📊 حجم'] = display_df['📊 حجم'].apply(format_number)
                    display_df['💰 آخرین قیمت'] = display_df['💰 آخرین قیمت'].apply(lambda x: format_price(x) if pd.notna(x) and x != "" else "")
                    display_df['🏁 قیمت پایانی'] = display_df['🏁 قیمت پایانی'].apply(lambda x: format_price(x) if pd.notna(x) and x != "" else "")
                    display_df['📈 درصد تغییر'] = display_df['📈 درصد تغییر'].apply(lambda x: format_percent_change(x) if pd.notna(x) else "")

                    st.subheader(f"📋 تاریخچه قیمت ({format_number(len(display_df))} رکورد)")
                    st.markdown('<div class="rtl">', unsafe_allow_html=True)
                    html_table = display_df.to_html(escape=False, index=False)
                    st.markdown(html_table, unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.warning(f"تاریخچه قیمتی برای {selected_ticker} یافت نشد")
        else:
            st.warning(f"❌ صندوقی با عبارت '{search_query}' یافت نشد")

# ==================== MAIN APP ====================

def main():
    st.title("📊 سامانه جامع تحلیل بازار سرمایه")
    st.markdown("---")

    st.sidebar.image("https://img.icons8.com/color/96/stock.png", width=80)
    st.sidebar.title("🚀 منوی اصلی")

    menu_options = [
        "🔍 جستجوی سهام",
        "🔍 جستجوی صندوق‌ها",
        "📈 تحلیل عملکرد سهام",
        "📈 تحلیل عملکرد صندوق‌ها",
        "🔄 به‌روزرسانی سهام",
        "🔄 به‌روزرسانی صندوق‌ها"
    ]

    # Show status of modules
    st.sidebar.markdown("---")
    st.sidebar.markdown("### وضعیت ماژول‌ها:")

    if STOCK_GAINER_AVAILABLE:
        st.sidebar.success("✅ stock_gainer.py")
    else:
        st.sidebar.error("❌ stock_gainer.py")

    if ETF_GAINER_AVAILABLE:
        st.sidebar.success("✅ etf_gainer.py")
    else:
        st.sidebar.error("❌ etf_gainer.py")

    if STOCK_UPDATER_AVAILABLE:
        st.sidebar.success("✅ stock_updater.py")
    else:
        st.sidebar.error("❌ stock_updater.py")

    if ETF_UPDATER_AVAILABLE:
        st.sidebar.success("✅ etf_updater.py")
    else:
        st.sidebar.error("❌ etf_updater.py")

    selected_page = st.sidebar.radio(
        "انتخاب بخش:",
        menu_options,
        index=0
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"آخرین بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    if selected_page == "🔍 جستجوی سهام":
        stock_search_page()
    elif selected_page == "🔍 جستجوی صندوق‌ها":
        etf_search_page()
    elif selected_page == "📈 تحلیل عملکرد سهام":
        stock_gainer_page()
    elif selected_page == "📈 تحلیل عملکرد صندوق‌ها":
        etf_gainer_page()
    elif selected_page == "🔄 به‌روزرسانی سهام":
        stock_update_page()
    elif selected_page == "🔄 به‌روزرسانی صندوق‌ها":
        etf_update_page()

    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; direction: rtl; color: #666;'>
        <p>📊 سامانه جامع تحلیل بازار سرمایه | نسخه ۳.۰</p>
        <p>تمامی حقوق محفوظ است © ۱۴۰۳</p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()