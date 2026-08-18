import asyncio
import sys
import warnings

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

warnings.filterwarnings("ignore", category=FutureWarning)

import finpy_tse as fpy

stock_list = fpy.Build_Market_StockList(
    bourse=True,
    farabourse=True,
    payeh=True,
    detailed_list=True,
    show_progress=True,
    save_excel=True,
    save_csv=True,
    save_path='.')
stock_list