from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from vnstock import Quote, Listing, Trading
import math
import pandas as pd
import yfinance as yf
import pandas_ta as ta
import json
import os
import asyncio
from datetime import datetime, timedelta

TOKEN = '8616332546:AAGwYZFfFQ-XgNAu8QWTz304FoD6Pvkj4K4'

# --- CẤU HÌNH ---
WATCHLIST_FILE = "watchlist.json"

# Hàm quản lý watchlist
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r') as f: 
            return json.load(f) # Trả về dictionary (ví dụ: {"VCI": [8015125068]})
    return {} # Trả về dictionary rỗng

def save_watchlist(watchlist):
    with open(WATCHLIST_FILE, 'w') as f: 
        json.dump(watchlist, f)
        
STATE_FILE = "bot_state.json"

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_phases": {}, "last_signals": {}}

# Tải trạng thái khi bot vừa khởi động
state = load_state()
last_phases = state["last_phases"]
last_signals = state["last_signals"]

async def run_wyckoff_analysis(ticker):
    try:
        # --- 1. LẤY DỮ LIỆU ---
        # 1. Tự động tính thời gian
        today = datetime.now()
        one_year_ago = today - timedelta(days=365)
        # Định dạng thành chuỗi 'YYYY-MM-DD' mà vnstock yêu cầu
        end_date = today.strftime('%Y-%m-%d')
        # Lấy dữ liệu 160 ngày gần nhất (đủ dùng cho hầu hết chỉ báo)
        start_date = (today - timedelta(days=160)).strftime('%Y-%m-%d')
        q = Quote(symbol=ticker, source='VCI')
        df = q.history(start=start_date, end=end_date)
        if df is None or df.empty: return None
            
        t = Trading(symbol=ticker, source='VCI')
        realtime_data = t.price_board() 
        
        if realtime_data is not None and not realtime_data.empty:
            last_price = realtime_data.loc[0, ('match', 'match_price')]
            last_vol = realtime_data.loc[0, ('match', 'accumulated_volume')]
            if last_price > 1000: last_price /= 1000
            df.iloc[-1, df.columns.get_loc('close')] = last_price
            df.iloc[-1, df.columns.get_loc('volume')] = last_vol
            
        # Chuẩn hóa tên cột
        df = df.rename(columns={
            'time': 'Date', 'open': 'Open', 'high': 'High', 
            'low': 'Low', 'close': 'Close', 'volume': 'Volume'
        })
        
        # --- 2. TÍNH TOÁN CÁC CHỈ BÁO ---
        # Đảm bảo dữ liệu là kiểu số
        for col in ['High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Tính toán
        st_short = df.ta.supertrend(length=8, multiplier=2)
        st_long = df.ta.supertrend(length=21, multiplier=2)
        col_short = [c for c in st_short.columns if 'd' in c.lower()][0]
        col_long = [c for c in st_long.columns if 'd' in c.lower()][0]
        # Lấy cột giá trị Supertrend (thường là cột kết thúc bằng _SUPERT_...)
        df['ST_Short'] = st_short[col_short]
        df['ST_Long'] = st_long[col_long]     
        df['MFI'] = ta.mfi(df['High'], df['Low'], df['Close'], df['Volume'], length=16)
        df['CMF'] = ta.cmf(df['High'], df['Low'], df['Close'], df['Volume'], length=18)
        df['Vol_HMA'] = ta.hma(df['Volume'], length=20)
        df['BB_Upper'] = df['Close'].rolling(25).mean() + (2 * df['Close'].rolling(25).std())
        
        # --- 3. XỬ LÝ DỮ LIỆU TRỐNG ---
        # Điền 0 vào các chỗ trống
        cols_to_fill = ['MFI', 'CMF', 'Vol_HMA', 'BB_Upper', 'ST_Short', 'ST_Long']
        df[cols_to_fill] = df[cols_to_fill].fillna(0.0)
        
        return df # <--- TRẢ VỀ DỮ LIỆU TẠI ĐÂY
        
    except Exception as e:
        print(f"Lỗi xử lý dữ liệu cho {ticker}: {e}")
        return None
          
    # Xử lý NaN
    for col in ['MFI', 'CMF', 'Vol_HMA', 'BB_Upper', 'ST_Short', 'ST_Long']:
        df[col] = df[col].fillna(0.0)
# --- HÀM XỬ LÝ LỆNH /theodoi ---
# --- LỆNH THEODOI ---
async def theodoi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Sử dụng: /theodoi [MÃ]. Ví dụ: /theodoi VCI")
        return
    
    ticker = context.args[0].upper().strip()
    chat_id = update.effective_chat.id
    
    # 1. Thêm vào Watchlist
    watchlist = load_watchlist()
    if ticker not in watchlist: watchlist[ticker] = []
    if chat_id not in watchlist[ticker]: watchlist[ticker].append(chat_id)
    save_watchlist(watchlist)
    
    # 2. Phân tích ngay lập tức
    await update.message.reply_text(f"✅ Đã thêm {ticker} vào danh sách theo dõi. Đang lấy thông tin hiện tại...")
    
    df = await run_wyckoff_analysis(ticker)
    if df is not None:
        latest = df.iloc[-1]
        msg = (f"📊 **Phân tích {ticker}**\n"
               f"💰 Giá: {latest['Close']:.2f}\n"
               f"📈 MFI: {latest['MFI']:.2f}\n"
               f"🚀 ST Short: {latest['ST_Short']:.2f}\n"
               f"Trend: {'Tăng' if latest['Close'] > latest['ST_Short'] else 'Giảm'}")
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ Không lấy được dữ liệu cho {ticker}.")

# --- HÀM CHẠY NGẦM (JOB QUEUE) ---
last_phases = {}
last_signals = {}

async def job_check_realtime(context: ContextTypes.DEFAULT_TYPE):
    watchlist = load_watchlist() 
    state_changed = False # Chỉ lưu file nếu có thay đổi
    
    for ticker, user_ids in watchlist.items():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {ticker}...")
        df = await run_wyckoff_analysis(ticker)
        
        # Kiểm tra độ dài dữ liệu để tránh IndexError
        if df is None or len(df) < 2: continue
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        mfi_now = float(curr['MFI'])
        cmf_now = float(curr['CMF'])
        cmf_change = float(curr['CMF'] - prev['CMF'])
        
        # 1. Xác định pha (Logic cũ của bạn)
        current_phase = "Pha C" if mfi_now < 30 else ("Pha D" if mfi_now > 50 else "Pha B")
        
        # 2. Xác định tín hiệu
        if mfi_now > 48 and cmf_now > -0.02 and cmf_change > 0:
            current_signal = "🚀 MUA MẠNH"
        elif mfi_now < 30 and cmf_now > -0.02 and cmf_change > 0:
            current_signal = "💰 MUA BẮT ĐÁY"
        elif mfi_now > 70 and cmf_now < 0 and cmf_change < 0:
            current_signal = "🩸 BÁN MẠNH"
        else:
            current_signal = "⚪ TRẠNG THÁI BÌNH THƯỜNG"

        # 3 & 4. Kiểm tra thay đổi và Gửi tin
        phase_changed = (ticker in last_phases) and (last_phases[ticker] != current_phase)
        signal_changed = (ticker in last_signals) and (last_signals[ticker] != current_signal)
        
        if phase_changed or (signal_changed and current_signal != "⚪ TRẠNG THÁI BÌNH THƯỜNG"):
            msg = f"🔔 CẬP NHẬT {ticker}:\n"
            if phase_changed:
                msg += f"• Wyckoff: {last_phases.get(ticker, 'N/A')} -> {current_phase}\n"
            if signal_changed and current_signal != "⚪ TRẠNG THÁI BÌNH THƯỜNG":
                msg += f"• Tín hiệu: {current_signal}"
            
            for chat_id in user_ids:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            
            # Cập nhật bộ nhớ
            last_phases[ticker] = current_phase
            last_signals[ticker] = current_signal
            state_changed = True
    
    # 5. Lưu một lần duy nhất sau khi duyệt hết watchlist
    if state_changed:
        save_state({"last_phases": last_phases, "last_signals": last_signals})

async def get_stock_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.upper().strip()
    if len(ticker) > 5: return 
    
    await update.message.reply_text(f"Đang lấy dữ liệu cho mã: {ticker}...")
    
    try:
        df = await run_wyckoff_analysis(ticker)
        if df is None:
            await update.message.reply_text(f"❌ Không tìm thấy mã {ticker}")
            return

        # --- 1. ĐỊNH NGHĨA HÀM HỖ TRỢ ---
        def to_scalar(val):
            return float(val.item() if hasattr(val, 'item') else val)

        def get_trend_arrow(now, prev):
            if now > prev: return "▲"
            elif now < prev: return "▼"
            else: return "▬"

        # --- 2. TRÍCH XUẤT DỮ LIỆU ---
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        mfi_now = to_scalar(curr['MFI'])
        mfi_prev = to_scalar(prev['MFI'])
        mfi_change = mfi_now - mfi_prev
        cmf_now = to_scalar(curr['CMF'])
        cmf_prev = to_scalar(prev['CMF'])
        cmf_change = cmf_now - cmf_prev
        
        vol_now = to_scalar(curr['Volume'])
        vol_prve = to_scalar(prev['Volume'])
        vol_hma20 = to_scalar(curr['Vol_HMA'])
        close_now = to_scalar(curr['Close'])
        close_prev = to_scalar(prev['Close'])
        bb_upper = to_scalar(curr['BB_Upper'])
        st_short_now = float(curr['ST_Short'])
        st_long_now = float(curr['ST_Long'])

        # Tính toán Volume Delta
        curr_vol = float(curr['Volume'])
        prev_vol = float(df.iloc[-2]['Volume'])
        vol_delta = (curr_vol - prev_vol) / prev_vol  # Tỷ lệ % thay đổi khối lượng
        vol_delta_pct = vol_delta * 100
        #vol_icon = "🟢" if vol_delta_pct > 0 else "🔴"
        
        # Xác định vùng giá 3 tháng (giả định 65 phiên giao dịch)
        lookback = 65
        if len(df) >= lookback:
            high_3m = df['High'].iloc[-lookback:].max()
            low_3m = df['Low'].iloc[-lookback:].min()
            
            # Kiểm tra tín hiệu
            is_new_high = close_now >= high_3m
            is_new_low = close_now <= low_3m
        else:
            is_new_high = False
            is_new_low = False

        # --- 3. NHẬN DIỆN XU HƯỚNG DÒNG TIỀN ---
        mfi_arrow = get_trend_arrow(mfi_now, mfi_prev)
        cmf_arrow = get_trend_arrow(cmf_now, cmf_prev)

        # 1. Pha E Tăng (Mark up - Xu hướng cực mạnh)
        if (mfi_now > 75 or (bb_upper > 0 and close_now > bb_upper)) and st_long_now == 1:
            wyckoff_phase = "Pha E (Đẩy giá - Mark up mạnh)"
            
        # 2. Pha E Giảm (Mark down - Xu hướng giảm cực mạnh)
        elif st_long_now == -1 and mfi_now < 30 and vol_now > vol_hma20:
            wyckoff_phase = "Pha E (Giảm giá - Mark down mạnh)"
            
        # 3. Pha D (Trend + Cảnh báo Phân phối - Rất quan trọng để tránh đu đỉnh)
        elif mfi_now > 50:
            if cmf_now < 0 and cmf_change < 0:
                if mfi_change < 0:
                    wyckoff_phase = "Pha D (⚠️ CẢNH BÁO: Phân phối đỉnh hoàn hảo)"
                else:
                    wyckoff_phase = "Pha D (🟡 CẢNH BÁO SỚM: Dòng tiền thông minh rút)"
            else:
                wyckoff_phase = "Pha D (Xu hướng tăng ổn định)"

        # 4. Pha C (Spring - Điểm đảo chiều tiềm năng)
        elif mfi_now < 30 and vol_now < vol_hma20:
            wyckoff_phase = "Pha C (Spring - Gom hàng)"
            
        # 5. Pha A (Climax - Dừng xu hướng giảm)
        elif mfi_now < 20 and vol_now > vol_hma20:
            wyckoff_phase = "Pha A (Dừng xu hướng giảm)"
            
        # 6. Pha B (Tích lũy - Đi ngang - Vùng nhiễu)
        else:
            wyckoff_phase = "Pha B (Tích lũy - Đi ngang)"

        # --- 5. LOGIC TÍN HIỆU TỐI ƯU ---
        signals = []
        is_risky = False  # Cờ cảnh báo rủi ro

        # 1. Kiểm tra Rủi ro trước (Ưu tiên cao nhất)
        # Phân kỳ âm là tín hiệu nguy hiểm, đặt lên đầu tiên
        if close_now > close_prev and mfi_now < mfi_prev and mfi_now > 60: 
            signals.append("⚠️ PHÂN KỲ ÂM (Cảnh báo đỉnh)")
            is_risky = True
        
        # Nếu đang ở Pha E giảm cũng là rủi ro
        if "Pha E" in wyckoff_phase and "Mark down" in wyckoff_phase:
            signals.append("🔴 RỦI RO: Pha giảm mạnh, không bắt đáy.")
            is_risky = True

        # 2. Các tín hiệu bổ trợ khác (luôn hiện để bạn nắm tình hình)
        if vol_hma20 > 0 and vol_now < (vol_hma20 * 0.5): 
            signals.append("🟢 Cạn cung (No Supply)")
        
        # 3. Chỉ xét tín hiệu Mua/Gia tăng khi KHÔNG CÓ RỦI RO
        if not is_risky:
            # --- NHÓM 1: DÒNG TIỀN (FLOW ANALYSIS) ---
            
            # 1. Trạng thái MUA - Ưu tiên điều kiện mạnh trước
            if mfi_now > 48 and cmf_now > -0.02 and cmf_change > 0:
                signals.append("🚀 MUA MẠNH: Dòng tiền bùng nổ, vào pha tăng tốc")
                
            elif mfi_now < 30 and cmf_now > -0.02 and cmf_change > 0:
                signals.append("💰 MUA BẮT ĐÁY: Dòng tiền hồi phục từ vùng quá bán")
                
            elif mfi_now < 50 and cmf_now > 0.05 and cmf_change > 0:
                signals.append("🟢 MUA GIA TỐC: Dòng tiền đang mạnh dần lên")
                
            elif mfi_now > 50 and cmf_now > 0.12:
                signals.append("💎 GIỮ: Dòng tiền cực khỏe (Strong Trend)")

            # 2. Trạng thái BÁN/CẢNH BÁO
            elif mfi_now > 70 and cmf_now < 0 and cmf_change < 0:
                signals.append("🩸 BÁN MẠNH: Dòng tiền thoát nhanh tại vùng quá mua")
                
            elif mfi_now > 50 and cmf_now < -0.05 and cmf_change < 0:
                signals.append("🟡 CẢNH BÁO: Dòng tiền suy yếu nhanh")
                
            elif mfi_now < 50 and cmf_now < -0.12 and cmf_change < 0:
                signals.append("⛔ THOÁT: Dòng tiền thoát hoàn toàn (Bearish Trend)")
            
            elif mfi_now < 50 and cmf_now < -0.12 and cmf_change > 0:
                signals.append("🕵️ CẢNH BÁO: Vùng quá bán, dòng tiền bắt đầu quay lại")
                
            # 3. Logic phát hiện "Lái gom hàng/Rũ bỏ"
            if mfi_change < 0 and cmf_now > 0 and cmf_change > 0:
                signals.append("🕵️ LÁI ĐANG GOM: Rũ bỏ nhỏ lẻ - Gom hàng giá thấp")

            # 4. Trạng thái TÍCH LŨY/ĐI NGANG
            else:
                if abs(cmf_change) < 0.01:
                    signals.append("⚪ TRẠNG THÁI: Dòng tiền đi ngang (Sideway)")
                else:
                    signals.append("⚖️ TRẠNG THÁI: Dòng tiền cân bằng")

            # --- NHÓM 4: XÁC NHẬN WYCKOFF (TỐI ƯU HÓA) ---
            
            if "Pha C" in wyckoff_phase:
                signals.append("💎 CƠ HỘI: Spring phát hiện! Canh mua thăm dò vị thế.")
                
            elif "Pha B" in wyckoff_phase:
                signals.append("🔍 QUAN SÁT: Đang tích lũy (Pha B), kiên nhẫn đợi tín hiệu rõ ràng.")
                
            elif "Pha D (Cảnh báo Phân phối đỉnh)" in wyckoff_phase:
                signals.append("⚠️ CẨN TRỌNG: Pha D đang phân phối, ưu tiên chốt lời/hạ tỷ trọng.")
                
            elif "Pha D (Xu hướng tăng ổn định)" in wyckoff_phase:
                signals.append("🔥 XÁC NHẬN: Pha đẩy giá, có thể gia tăng tỷ trọng.")
                
            elif "Pha A" in wyckoff_phase:
                signals.append("🔍 QUAN SÁT: Xu hướng giảm đã dừng, đợi tạo nền.")
                
        # Logic: Đẩy giá cần Volume tăng mạnh
        # --- HỢP NHẤT: XÁC NHẬN CHIẾN LƯỢC (WYCKOFF + VOLUME) ---

        # 1. Xử lý các tín hiệu cho Pha D (Tăng hoặc Phân phối)
        if "Pha D" in wyckoff_phase:
            # Nếu đang tăng ổn định
            if "Xu hướng tăng ổn định" in wyckoff_phase:
                if vol_delta > 0.5:
                    signals.append("🚀 XÁC NHẬN: Volume bùng nổ! Xu hướng tăng cực bền.")
                elif vol_delta > 0.2:
                    signals.append("🔥 XÁC NHẬN: Pha đẩy giá, gia tăng tỷ trọng.")
                else:
                    signals.append("🔥 XÁC NHẬN: Pha đẩy giá, nhưng Volume thấp (Cần theo dõi).")
            
            # Nếu đang phân phối
            else:
                if vol_delta > 0.5:
                    signals.append("🚨 CẢNH BÁO: Phân phối đỉnh với Volume cực đại - THOÁT NGAY.")
                else:
                    signals.append("⚠️ CẨN TRỌNG: Pha D đang phân phối, ưu tiên hạ tỷ trọng.")

        # 2. Xử lý các tín hiệu cho Pha C (Spring)
        elif "Pha C" in wyckoff_phase:
            if vol_delta < -0.5: # Cạn kiệt cung
                signals.append("💎 CƠ HỘI: Spring với Volume cạn kiệt - Điểm mua an toàn.")
            else:
                signals.append("💎 CƠ HỘI: Spring phát hiện! Canh mua thăm dò.")

        # 3. Xử lý các Pha khác (B, A, E)
        elif "Pha B" in wyckoff_phase:
            signals.append("🔍 QUAN SÁT: Đang tích lũy (Pha B), kiên nhẫn đợi tín hiệu.")
        elif "Pha E" in wyckoff_phase:
            signals.append("🚀 BÙNG NỔ: Đang tăng tốc, ưu tiên nắm giữ/gồng lời.") 

        # --- NHÓM 6: XÁC NHẬN PHÁ VỠ KÈM DÒNG TIỀN (BREAKOUT/BREAKDOWN) ---
        if is_new_high:
            # Kết hợp Volume để xác nhận Breakout thực hay giả
            vol_status = "với Volume bùng nổ" if vol_delta > 0.5 else "với Volume thấp"
            
            if "Pha D" in wyckoff_phase:
                signals.append(f"🚀 BREAKOUT: Vượt đỉnh 3 tháng {vol_status} + Pha D (Xác nhận xu hướng).")
            else:
                signals.append(f"🔔 CHÚ Ý: Giá vừa vượt đỉnh 3 tháng {vol_status}.")

        elif is_new_low:
            # Kết hợp Volume để xác nhận áp lực bán
            vol_status = "với Volume xả lớn" if vol_delta > 0.5 else "với Volume bình thường"
            
            if "Pha Phân phối" in wyckoff_phase:
                signals.append(f"🚨 BÁO ĐỘNG: Thủng đáy 3 tháng {vol_status} + Pha Phân phối (Thoát gấp).")
            else:
                signals.append(f"⚠️ CẨN TRỌNG: Giá vừa thủng đáy 3 tháng {vol_status}.")

        # Nhóm 7: SuperTrend
        if st_long_now == 1 and st_short_now == 1:
            st_text = "🟢 Tăng mạnh"
        elif st_long_now == 1 and st_short_now == -1:
            st_text = "🟡 Chỉnh kỹ thuật"
        elif st_long_now == -1 and st_short_now == -1:
            st_text = "🔴 Giảm mạnh"
        elif st_long_now == -1 and st_short_now == 1:
            st_text = "⚠️ Hồi kỹ thuật"
        else:
            # Ghi vào console để bạn debug, không cần hiện lên Telegram
            print(f"LỖI DỮ LIỆU: ST_Long={st_long_now}, ST_Short={st_short_now}")
            st_text = "⚪ Dữ liệu chưa sẵn sàng"
            
        # Nhóm 8. Volume Delta
        if vol_delta_pct > 50:
            vol_text = f"🚀 Tăng mạnh (+{vol_delta_pct:.1f}%) - Dòng tiền cực khỏe"
        elif vol_delta_pct > 0:
            vol_text = f"🟢 Tăng nhẹ (+{vol_delta_pct:.1f}%) - Dòng tiền ổn định"
        elif vol_delta_pct < -50:
            vol_text = f"🔻 Cạn kiệt (-{abs(vol_delta_pct):.1f}%) - Thiếu hụt cung cầu"
        else:
            vol_text = f"🟡 Giảm nhẹ ({vol_delta_pct:.1f}%) - Đi ngang"    

        final_signal = "\n".join(signals) if signals else "Đang tích lũy/Trung tính"

        # --- 6. TRẢ VỀ KẾT QUẢ ---
        final_signal = "\n".join(signals) if signals else "Đang tích lũy/Trung tính"
        
        response = (f"📈 **{ticker}**\n"
                    f"💰 Giá: {close_now:,.3f} VND (Giá đóng cửa hôm trước)\n"
                    f"📊 MFI: {mfi_now:.2f} {mfi_arrow} | CMF: {cmf_now:.2f} {cmf_arrow}\n"
                    f"🌀 **Pha Wyckoff:** {wyckoff_phase}\n"
                    f"🎚️ **Vol Delta:** {vol_text} {vol_delta_pct:+.2f}%\n"
                    f"🛡️ **SuperTrend:** {st_text}\n"  
                    f"--------------------------\n"
                    f"🎯 **TÍN HIỆU:**\n{final_signal}")
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        # Nếu có lỗi, in ra chi tiết lỗi để dễ debug
        await update.message.reply_text(f"❌ Lỗi xử lý: {str(e)}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Đăng ký Job: chạy mỗi 15 phút
    job_queue = application.job_queue
    job_queue.run_repeating(job_check_realtime, interval=300, first=10, chat_id=8015125068)
    
    application.add_handler(CommandHandler("theodoi", theodoi_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), get_stock_info))
    application.run_polling()