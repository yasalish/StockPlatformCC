import streamlit as st
import pandas as pd
import finpy_tse as fpy
from datetime import datetime
import io

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
        max-height: 600px;
        overflow-y: auto;
        border: 1px solid #ddd;
        border-radius: 5px;
    }
</style>
""", unsafe_allow_html=True)

def fetch_stock_list(bourse=True, farabourse=True, payeh=True, detailed_list=True, show_progress=True):
    """
    Fetch comprehensive stock list using finpy_tse Build_Market_StockList()

    Parameters:
    -----------
    bourse : bool
        Include Bourse (بورس) stocks
    farabourse : bool
        Include Farabourse (فرابورس) stocks
    payeh : bool
        Include Payeh (پایه) stocks
    detailed_list : bool
        Get detailed information from stock headers
    show_progress : bool
        Show progress during data collection

    Returns:
    --------
    pd.DataFrame: Complete stock list with all details
    """
    try:
        # Call the Build_Market_StockList function
        df = fpy.Build_Market_StockList(
            bourse=bourse,
            farabourse=farabourse,
            payeh=payeh,
            detailed_list=detailed_list,
            show_progress=show_progress
        )

        return df

    except Exception as e:
        st.error(f"خطا در دریافت لیست سهام: {e}")
        return pd.DataFrame()

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

    # Apply to numeric columns
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
    if 'is_fetching' not in st.session_state:
        st.session_state.is_fetching = False

    st.title("📋 دریافت لیست جامع سهام")

    # Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/stock.png", width=80)
        st.title("⚙️ تنظیمات")
        st.markdown("---")

        st.subheader("🎯 انتخاب بازارها")

        # Market selection checkboxes
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
            help="شامل سهام بازار پایه"
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

    # Main content
    if fetch_button:
        # Check if at least one market is selected
        if not (bourse or farabourse or payeh):
            st.warning("⚠️ لطفاً حداقل یک بازار را انتخاب کنید.")
        else:
            # Set fetching state
            st.session_state.is_fetching = True

            # Fetch data with spinner
            with st.spinner("در حال دریافت لیست سهام..."):
                df = fetch_stock_list(
                    bourse=bourse,
                    farabourse=farabourse,
                    payeh=payeh,
                    detailed_list=detailed_list,
                    show_progress=show_progress
                )

            st.session_state.is_fetching = False

            if not df.empty:
                # Store in session state
                st.session_state.stock_list = df
                st.session_state.fetch_time = datetime.now()

                # Rerun to display the data
                st.rerun()
            else:
                st.error("❌ خطا در دریافت لیست سهام. لطفاً دوباره تلاش کنید.")

    # Display data if available in session state
    if st.session_state.stock_list is not None and not st.session_state.stock_list.empty:
        df = st.session_state.stock_list

        # Display success message
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

            # Create columns for metrics
            cols = st.columns(min(len(market_counts), 4))

            for idx, (market, count) in enumerate(market_counts.items()):
                if idx < len(cols):
                    with cols[idx]:
                        st.metric(market, f"{count:,}")

        st.markdown("---")

        # Display ALL data (not just 100 records)
        st.subheader(f"📋 نمایش تمام داده‌ها ({len(df):,} رکورد)")

        # Convert Persian numbers for display
        display_df = df.copy()
        display_df = format_persian_numbers(display_df)

        # Show all data with scrollable container
        st.markdown('<div class="dataframe-container">', unsafe_allow_html=True)
        st.dataframe(display_df, use_container_width=True, height=500)
        st.markdown('</div>', unsafe_allow_html=True)

        # Display data info in expander
        with st.expander("📊 اطلاعات آماری"):
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
                columns_list = pd.DataFrame({
                    "ستون": list(df.columns)
                })
                st.dataframe(columns_list, use_container_width=True)

            # Show data types
            st.markdown("**نوع داده‌ها:**")
            dtypes_df = pd.DataFrame({
                "ستون": df.dtypes.index,
                "نوع داده": df.dtypes.values
            })
            st.dataframe(dtypes_df, use_container_width=True)

            # Show null values count
            st.markdown("**مقادیر خالی:**")
            null_df = pd.DataFrame({
                "ستون": df.isnull().sum().index,
                "تعداد مقادیر خالی": df.isnull().sum().values
            })
            st.dataframe(null_df, use_container_width=True)

        # Download options
        st.markdown("---")
        st.subheader("💾 دانلود فایل")

        # Create download buttons with proper implementation
        col1, col2, col3 = st.columns(3)

        with col1:
            # Excel download
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='StockList', index=False)
                excel_data = output.getvalue()

                st.download_button(
                    label="📥 دانلود Excel",
                    data=excel_data,
                    file_name=f"stock_list_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="download_excel"
                )
            except Exception as e:
                st.error(f"خطا در ایجاد فایل Excel: {e}")

        with col2:
            # CSV download
            try:
                csv_data = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 دانلود CSV",
                    data=csv_data,
                    file_name=f"stock_list_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="download_csv"
                )
            except Exception as e:
                st.error(f"خطا در ایجاد فایل CSV: {e}")

        with col3:
            # JSON download
            try:
                json_data = df.to_json(orient='records', force_ascii=False).encode('utf-8')
                st.download_button(
                    label="📥 دانلود JSON",
                    data=json_data,
                    file_name=f"stock_list_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    use_container_width=True,
                    key="download_json"
                )
            except Exception as e:
                st.error(f"خطا در ایجاد فایل JSON: {e}")

    else:
        # Show welcome message
        st.markdown("""
        <div style='text-align: center; padding: 50px 20px;'>
            <h2>👋 به دریافت‌کننده لیست جامع سهام خوش آمدید</h2>
            <p style='font-size: 1.1rem;'>
                برای شروع، بازارهای مورد نظر را در سمت راست انتخاب کنید و دکمه "دریافت لیست سهام" را بزنید.
            </p>
            <br>
            <h4>📌 ویژگی‌ها:</h4>
            <ul style='text-align: right; display: inline-block;'>
                <li>🏛️ دریافت لیست کامل سهام از بازارهای بورس، فرابورس و پایه</li>
                <li>📊 دریافت اطلاعات کامل از سربرگ شناسه هر سهم</li>
                <li>📋 نمایش تمام داده‌ها با قابلیت اسکرول</li>
                <li>💾 دانلود در فرمت‌های Excel، CSV و JSON</li>
                <li>📊 نمایش اطلاعات آماری کامل</li>
                <li>⚡ جمع‌آوری سریع با استفاده از درخواست‌های موازی</li>
            </ul>
            <br>
            <div class='info-box'>
                <p><strong>ℹ️ نکته مهم:</strong></p>
                <p>زمان جمع‌آوری اطلاعات به سرعت اینترنت و پاسخگویی سایت tsetmc بستگی دارد.</p>
                <p>در صورت مسدود شدن IP، حدود 15 دقیقه صبر کرده و دوباره تلاش کنید.</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Show example of what data looks like
        st.markdown("---")
        st.subheader("📋 نمونه اطلاعات دریافت شده")

        sample_data = {
            "Ticker": ["فولاد", "خودرو", "وبملت", "شپنا", "کگل"],
            "Name": ["فولاد مبارکه اصفهان", "ایران خودرو", "ملت", "پالایش نفت اصفهان", "گل گهر"],
            "Market": ["بورس", "بورس", "فرابورس", "بورس", "بورس"],
            "Sector": ["فلزات اساسی", "خودرو", "بانک", "فرآورده‌های نفتی", "سنگ آهن"],
            "Sub-Sector": ["فولاد", "خودرو سواری", "بانک", "پالایش", "سنگ آهن"],
        }
        sample_df = pd.DataFrame(sample_data)
        st.dataframe(sample_df, use_container_width=True)

if __name__ == "__main__":
    main()