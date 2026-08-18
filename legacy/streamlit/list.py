import streamlit as st
import pandas as pd
import finpy_tse as fpy
from datetime import datetime
import time
import traceback

# Page configuration
st.set_page_config(
    page_title="دریافت لیست جامع سهام",
    page_icon="📋",
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

    h1, h2, h3, h4, h5, h6 {
        font-family: 'Vazirmatn', sans-serif;
        font-weight: 700;
    }

    .stButton button {
        font-family: 'Vazirmatn', sans-serif;
        font-weight: 500;
        width: 100%;
    }

    .metric-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        direction: rtl;
    }

    .success-box {
        background-color: #e8f5e9;
        padding: 15px;
        border-radius: 10px;
        border-right: 4px solid #4caf50;
        margin: 10px 0;
    }

    .error-box {
        background-color: #ffebee;
        padding: 15px;
        border-radius: 10px;
        border-right: 4px solid #f44336;
        margin: 10px 0;
    }

    .warning-box {
        background-color: #fff3e0;
        padding: 15px;
        border-radius: 10px;
        border-right: 4px solid #ff9800;
        margin: 10px 0;
    }

    .info-box {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 10px;
        border-right: 4px solid #2196F3;
        margin: 10px 0;
    }

    .stCheckbox label {
        font-family: 'Vazirmatn', sans-serif;
    }

    .dataframe-container {
        max-height: 700px;
        overflow-y: auto;
        border: 1px solid #ddd;
        border-radius: 5px;
        margin: 10px 0;
    }

    .dataframe-container table {
        width: 100%;
        border-collapse: collapse;
    }

    .dataframe-container th {
        background-color: #f0f2f6;
        position: sticky;
        top: 0;
        z-index: 1;
    }
</style>
""", unsafe_allow_html=True)

def fetch_stock_list_separately(bourse=True, farabourse=True, payeh=True, detailed_list=True, show_progress=True):
    """
    Fetch stock list separately for each market to handle errors gracefully
    """
    all_dfs = []
    errors = []
    markets_status = {}

    # Fetch Bourse
    if bourse:
        try:
            df_bourse = fpy.Build_Market_StockList(
                bourse=True,
                farabourse=False,
                payeh=False,
                detailed_list=detailed_list,
                show_progress=show_progress
            )
            if not df_bourse.empty:
                all_dfs.append(df_bourse)
                markets_status['بورس'] = '✅ دریافت شد'
            else:
                markets_status['بورس'] = '❌ داده‌ای یافت نشد'
        except Exception as e:
            errors.append(f"خطا در دریافت بورس: {str(e)}")
            markets_status['بورس'] = f'❌ خطا: {str(e)[:50]}...'

    # Fetch Farabourse
    if farabourse:
        try:
            df_farabourse = fpy.Build_Market_StockList(
                bourse=False,
                farabourse=True,
                payeh=False,
                detailed_list=detailed_list,
                show_progress=show_progress
            )
            if not df_farabourse.empty:
                all_dfs.append(df_farabourse)
                markets_status['فرابورس'] = '✅ دریافت شد'
            else:
                markets_status['فرابورس'] = '❌ داده‌ای یافت نشد'
        except Exception as e:
            errors.append(f"خطا در دریافت فرابورس: {str(e)}")
            markets_status['فرابورس'] = f'❌ خطا: {str(e)[:50]}...'

    # Fetch Payeh (with special handling)
    if payeh:
        try:
            # Try with timeout handling
            df_payeh = fpy.Build_Market_StockList(
                bourse=False,
                farabourse=False,
                payeh=True,
                detailed_list=detailed_list,
                show_progress=show_progress
            )
            if not df_payeh.empty:
                all_dfs.append(df_payeh)
                markets_status['پایه'] = '✅ دریافت شد'
            else:
                markets_status['پایه'] = '❌ داده‌ای یافت نشد'
        except Exception as e:
            error_msg = str(e)
            if "Connection to www.ifb.ir timed out" in error_msg or "ConnectTimeoutError" in error_msg:
                markets_status['پایه'] = '⚠️ خطای اتصال - سرور پاسخ نمی‌دهد'
                errors.append("خطا در دریافت پایه: سرور فرابورس پاسخ نمی‌دهد")
            else:
                markets_status['پایه'] = f'❌ خطا: {error_msg[:50]}...'
                errors.append(f"خطا در دریافت پایه: {error_msg}")

    # Combine all dataframes
    if all_dfs:
        combined_df = pd.concat(all_dfs, ignore_index=True)
        return combined_df, errors, markets_status
    else:
        return pd.DataFrame(), errors, markets_status

def fetch_stock_list(bourse=True, farabourse=True, payeh=True, detailed_list=True, show_progress=True):
    """
    Fetch comprehensive stock list using finpy_tse Build_Market_StockList()
    (Fallback function - kept for compatibility)
    """
    try:
        df = fpy.Build_Market_StockList(
            bourse=bourse,
            farabourse=farabourse,
            payeh=payeh,
            detailed_list=detailed_list,
            show_progress=show_progress
        )
        return df, None, {}
    except Exception as e:
        return pd.DataFrame(), str(e), {}

def format_persian_numbers(df):
    """Convert English numbers to Persian in DataFrame"""
    persian_digits = {'0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
                     '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹'}

    def convert_number(num):
        if pd.isna(num) or num is None:
            return num
        if isinstance(num, (int, float)):
            num_str = str(int(num))
            for eng, per in persian_digits.items():
                num_str = num_str.replace(eng, per)
            return num_str
        return num

    for col in df.columns:
        if df[col].dtype in ['int64', 'float64']:
            df[col] = df[col].apply(convert_number)

    return df

def main():
    # Initialize session state
    if 'stock_list' not in st.session_state:
        st.session_state.stock_list = None
    if 'fetch_time' not in st.session_state:
        st.session_state.fetch_time = None
    if 'markets_status' not in st.session_state:
        st.session_state.markets_status = None
    if 'errors' not in st.session_state:
        st.session_state.errors = []

    st.title("📋 دریافت لیست جامع سهام")

    # Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/stock.png", width=80)
        st.title("⚙️ تنظیمات")
        st.markdown("---")

        st.subheader("🎯 انتخاب بازارها")

        bourse = st.checkbox(
            "🏛️ بورس",
            value=True,
            help="شامل سهام بازار بورس"
        )

        farabourse = st.checkbox(
            "🏢 فرابورس",
            value=True,
            help="شامل سهام بازار فرابورس"
        )

        payeh = st.checkbox(
            "📊 پایه",
            value=True,
            help="شامل سهام بازار پایه (زرد، نارنجی، قرمز)"
        )

        st.markdown("---")

        st.subheader("🔍 جزئیات")

        detailed_list = st.checkbox(
            "📋 دریافت اطلاعات کامل",
            value=True,
            help="دریافت اطلاعات کامل از سربرگ شناسه هر سهم"
        )

        show_progress = st.checkbox(
            "📊 نمایش پیشرفت",
            value=True,
            help="نمایش میزان پیشرفت در حین جمع‌آوری اطلاعات"
        )

        st.markdown("---")

        # Fetch button
        fetch_button = st.button(
            "📥 دریافت لیست سهام",
            type="primary",
            use_container_width=True
        )

        st.markdown("---")

        # Clear data button
        if st.button("🗑️ پاک کردن داده‌ها", use_container_width=True):
            st.session_state.stock_list = None
            st.session_state.fetch_time = None
            st.session_state.markets_status = None
            st.session_state.errors = []
            st.rerun()

        st.markdown("---")

        # Help section
        st.subheader("ℹ️ راهنما")
        st.markdown("""
        **توضیحات:**
        - **بورس:** شامل سهام بازار بورس تهران
        - **فرابورس:** شامل سهام بازار فرابورس
        - **پایه:** شامل سهام بازار پایه (زرد، نارنجی، قرمز)
        - **اطلاعات کامل:** جمع‌آوری اطلاعات از سربرگ شناسه هر سهم
        - **نمایش پیشرفت:** نمایش درصد پیشرفت در حین جمع‌آوری
        """)

        st.info("""
        ⏱️ زمان جمع‌آوری اطلاعات به سرعت اینترنت و پاسخگویی سایت tsetmc بستگی دارد.
        """)

        # Show tip for payeh issue
        st.markdown("---")
        st.warning("""
        💡 **نکته:**
        اگر در دریافت بازار پایه با خطا مواجه شدید،
        می‌توانید آن را غیرفعال کرده و فقط بورس و فرابورس را دریافت کنید.
        """)

    # Main content
    if fetch_button:
        if not (bourse or farabourse or payeh):
            st.warning("⚠️ لطفاً حداقل یک بازار را انتخاب کنید.")
        else:
            # Clear previous data
            st.session_state.errors = []

            # Fetch data separately for each market
            with st.spinner("در حال دریافت لیست سهام..."):
                df, errors, markets_status = fetch_stock_list_separately(
                    bourse=bourse,
                    farabourse=farabourse,
                    payeh=payeh,
                    detailed_list=detailed_list,
                    show_progress=show_progress
                )

            st.session_state.markets_status = markets_status
            st.session_state.errors = errors

            if not df.empty:
                st.session_state.stock_list = df
                st.session_state.fetch_time = datetime.now()
                st.rerun()
            else:
                st.error("❌ هیچ داده‌ای دریافت نشد. لطفاً تنظیمات را بررسی کنید.")

    # Display markets status if available
    if st.session_state.markets_status:
        st.markdown("---")
        st.subheader("📊 وضعیت دریافت بازارها")

        cols = st.columns(min(len(st.session_state.markets_status), 3))
        for idx, (market, status) in enumerate(st.session_state.markets_status.items()):
            if idx < len(cols):
                with cols[idx]:
                    if "✅" in status:
                        st.success(f"**{market}**\n\n{status}")
                    elif "⚠️" in status:
                        st.warning(f"**{market}**\n\n{status}")
                    else:
                        st.error(f"**{market}**\n\n{status}")

    # Display errors if any
    if st.session_state.errors:
        st.markdown("---")
        st.subheader("⚠️ خطاهای رخ داده")

        for error in st.session_state.errors:
            if "سرور فرابورس پاسخ نمی‌دهد" in error:
                st.markdown(f"""
                <div class='warning-box'>
                    <p>⚠️ {error}</p>
                    <p><strong>راه‌حل:</strong></p>
                    <ul>
                        <li>🔄 چند دقیقه صبر کرده و دوباره تلاش کنید</li>
                        <li>📊 گزینه "پایه" را غیرفعال کرده و فقط بورس و فرابورس را انتخاب کنید</li>
                        <li>⏱️ در ساعات غیر اوج (شب یا صبح زود) تلاش کنید</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class='error-box'>
                    <p>❌ {error}</p>
                </div>
                """, unsafe_allow_html=True)

    # Display data if available
    if st.session_state.stock_list is not None and not st.session_state.stock_list.empty:
        df = st.session_state.stock_list

        st.markdown(f"""
        <div class='success-box'>
            <h4>✅ لیست سهام با موفقیت دریافت شد</h4>
            <p>تعداد کل سهام: <strong>{len(df):,}</strong> سهم</p>
            <p>تعداد ستون‌ها: <strong>{len(df.columns)}</strong> ستون</p>
            <p>زمان دریافت: <strong>{st.session_state.fetch_time.strftime('%Y-%m-%d %H:%M:%S')}</strong></p>
        </div>
        """, unsafe_allow_html=True)

        # Display market distribution
        st.subheader("📊 توزیع بازارها")

        if 'Market' in df.columns:
            market_counts = df['Market'].value_counts()
            cols = st.columns(min(len(market_counts), 4))
            for idx, (market, count) in enumerate(market_counts.items()):
                if idx < len(cols):
                    with cols[idx]:
                        st.metric(market, f"{count:,}")

        st.markdown("---")

        # Display ALL data
        st.subheader(f"📋 تمام داده‌ها ({len(df):,} رکورد)")

        display_df = df.copy()
        display_df = format_persian_numbers(display_df)

        st.markdown('<div class="dataframe-container">', unsafe_allow_html=True)
        st.dataframe(display_df, use_container_width=True, height=600)
        st.markdown('</div>', unsafe_allow_html=True)

        # Display data info in expander
        with st.expander("📊 اطلاعات آماری", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**اطلاعات کلی:**")
                info_data = {
                    "شاخص": ["تعداد کل سهام", "تعداد ستون‌ها", "بازارها"],
                    "مقدار": [
                        f"{len(df):,}",
                        len(df.columns),
                        ", ".join(df['Market'].unique()) if 'Market' in df.columns else "نامشخص"
                    ]
                }
                info_df = pd.DataFrame(info_data)
                st.table(info_df)

            with col2:
                st.markdown("**ستون‌های موجود:**")
                columns_list = pd.DataFrame({"ستون": list(df.columns)})
                st.dataframe(columns_list, use_container_width=True)

            st.markdown("**نوع داده‌ها:**")
            dtypes_df = pd.DataFrame({
                "ستون": df.dtypes.index,
                "نوع داده": df.dtypes.values
            })
            st.dataframe(dtypes_df, use_container_width=True)

            st.markdown("**مقادیر خالی:**")
            null_df = pd.DataFrame({
                "ستون": df.isnull().sum().index,
                "تعداد مقادیر خالی": df.isnull().sum().values
            })
            st.dataframe(null_df, use_container_width=True)

            st.markdown("**نمونه رکوردها:**")
            sample_col1, sample_col2 = st.columns(2)
            with sample_col1:
                st.markdown("**۵ رکورد اول:**")
                st.dataframe(df.head(5), use_container_width=True)
            with sample_col2:
                st.markdown("**۵ رکورد آخر:**")
                st.dataframe(df.tail(5), use_container_width=True)

        # Show column descriptions
        st.markdown("---")
        st.subheader("📋 توضیحات ستون‌ها")

        column_descriptions = {
            "Ticker": "نماد سهام",
            "Name": "نام کامل فارسی شرکت",
            "Market": "بازار (بورس، فرابورس، پایه)",
            "Panel": "تابلوی بازار",
            "Sector": "گروه صنعت",
            "Sub-Sector": "زیرگروه صنعت",
            "Comment": "توضیحات وضعیت",
            "Name(EN)": "نام لاتین شرکت",
            "Company Code(12)": "کد ۱۲ رقمی شرکت",
            "Ticker(4)": "کد ۴ رقمی نماد",
            "Ticker(5)": "کد ۵ رقمی نماد",
            "Ticker(12)": "کد ۱۲ رقمی نماد",
            "Sector Code": "کد گروه صنعت",
            "Sub-Sector Code": "کد زیرگروه صنعت",
            "Panel Code": "کد مرتبط با Panel"
        }

        available_descriptions = {col: desc for col, desc in column_descriptions.items() if col in df.columns}

        if available_descriptions:
            desc_df = pd.DataFrame({
                "نام ستون": list(available_descriptions.keys()),
                "توضیح": list(available_descriptions.values())
            })
            st.dataframe(desc_df, use_container_width=True)

if __name__ == "__main__":
    main()