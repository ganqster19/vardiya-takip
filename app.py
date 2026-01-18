import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import calendar
import uuid
from datetime import datetime, timedelta, date

st.set_page_config(page_title="Vardiya & Talep Yönetimi", page_icon="⚡", layout="wide")

# --- CSS ---
st.markdown("""
<style>
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 8px; border-left: 5px solid #4CAF50; }
    .metric-card-loss { background-color: #ffebee; padding: 15px; border-radius: 8px; border-left: 5px solid #F44336; }
    .job-badge { padding: 2px 6px; border-radius: 4px; font-size: 11px; display: block; margin-bottom: 2px; }
    .status-confirmed { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .status-rejected { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; text-decoration: line-through; }
    .stButton button { width: 100%; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# --- GÜÇLENDİRİLMİŞ VERİTABANI BAĞLANTISI ---
@st.cache_resource(ttl=600) # Bağlantıyı 10 dk cache'de tut
def get_db_connection():
    try:
        return psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            port=st.secrets["supabase"]["port"],
            cursor_factory=RealDictCursor,
            sslmode='require',
            connect_timeout=60,  # Zaman aşımı süresini 60 saniyeye çıkardık
            keepalives=1,        # Bağlantıyı canlı tut
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
    except Exception as e:
        st.error(f"Veritabanı Bağlantı Hatası: {e}")
        st.stop()

# --- ANALİZ FONKSİYONU (CACHE İLE HIZLANDIRILDI) ---
@st.cache_data(ttl=60) # 60 saniye boyunca tekrar sorgu atmaz, hızlı çalışır
def get_advanced_stats(month, year):
    conn = get_db_connection()
    # Bağlantı kopmuşsa yenilemeyi dene
    if conn.closed:
        st.cache_resource.clear()
        conn = get_db_connection()
        
    c = conn.cursor()
    date_pattern = f"%.{month:02d}.{year}"
    
    # Tek seferde tüm gerekli verileri çek
    try:
        # 1. Toplam İstatistikler
        c.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status != 'REJECTED' THEN 1 ELSE 0 END) as confirmed_count,
                SUM(CASE WHEN status != 'REJECTED' THEN price_customer ELSE 0 END) as confirmed_rev,
                SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected_count,
                SUM(CASE WHEN status = 'REJECTED' THEN price_customer ELSE 0 END) as potential_loss
            FROM jobs 
            WHERE date LIKE %s
        """, (date_pattern,))
        
        res = c.fetchone()
        return res['total'], res['confirmed_count'], res['rejected_count'], (res['confirmed_rev'] or 0), (res['potential_loss'] or 0)
    except Exception as e:
        # Hata olursa cache temizle ve tekrar dene uyarısı ver
        st.cache_resource.clear()
        return 0,0,0,0,0

# --- YAN MENÜ & KPI ---
with st.sidebar:
    st.title("📊 Analiz Paneli")
    st.caption(f"Bugün: {datetime.now().strftime('%d.%m.%Y')}")
    
    sel_y = st.selectbox("Yıl", [2025, 2026], index=1)
    sel_m = st.selectbox("Ay", range(1,13), index=datetime.now().month-1)
    
    # İstatistikleri Çek
    tot_dem, conf_cnt, rej_cnt, conf_rev, pot_loss = get_advanced_stats(sel_m, sel_y)
    
    st.markdown("---")
    st.markdown(f"### 📅 {calendar.month_name[sel_m]} Özeti")
    
    # KPI KARTLARI
    st.markdown(f"""
    <div class="metric-card">
        <strong>✅ Gerçekleşen</strong><br>
        <span style="font-size:1.5em">{conf_cnt} Adet</span><br>
        <small>Ciro: {conf_rev:,.0f} TL</small>
    </div>
    <br>
    <div class="metric-card-loss">
        <strong>🚫 Reddedilen (Kayıp)</strong><br>
        <span style="font-size:1.5em">{rej_cnt} Adet</span><br>
        <small>Kaçan Ciro: ~{pot_loss:,.0f} TL</small>
    </div>
    """, unsafe_allow_html=True)
    
    conversion_rate = (conf_cnt / tot_dem * 100) if tot_dem > 0 else 0
    st.markdown(f"**Karşılama Oranı:** %{conversion_rate:.1f}")
    
    st.divider()
    if st.button("Yenile (Önbelleği Temizle)"): 
        st.cache_data.clear()
        st.rerun()

st.title("🚀 Operasyon Yönetimi")

tabs = st.tabs(["📝 Talep Girişi", "📅 Takvim", "📂 Müşteriler"])

# --- TAB 1: TALEP GİRİŞİ (SADELEŞTİRİLDİ) ---
with tabs[0]:
    st.subheader("⚡ Hızlı İş / Talep Girişi")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Müşteri Seçimi (Sadece İsim ve Semt görünür, Segmenti veritabanı bilir)
    c.execute("SELECT id, name, district FROM customers ORDER BY name")
    custs = c.fetchall()
    # Dropdown'da sadece isim ve bölge yazar, segment yazmaz.
    c_opts = {f"{x['name']} ({x['district'] or '-'})": x['id'] for x in custs}
    
    c1, c2 = st.columns(2)
    with c1:
        sel_c_name = st.selectbox("Müşteri Seç", ["-"] + list(c_opts.keys()))
        
    with c2:
        d_mode = st.radio("Tarih", ["Tek Gün", "Aralık"], horizontal=True)
        f_dates = []
        if d_mode == "Tek Gün":
            pick = st.date_input("Tarih")
            f_dates = [pick]
        else:
            d1 = st.date_input("Başlangıç"); d2 = st.date_input("Bitiş")
            if d1<=d2:
                cur = d1
                while cur<=d2:
                    f_dates.append(cur); cur+=timedelta(1)
    
    st.markdown("---")
    
    col_stat, col_det = st.columns(2)
    
    with col_stat:
        status = st.radio("Talep Durumu", ["✅ Onaylandı", "🚫 Reddedildi"], horizontal=True)
        job_status = 'CONFIRMED' if status.startswith("✅") else 'REJECTED'
        
    with col_det:
        # Segment sormuyoruz, sadece hizmet tipi soruyoruz
        if job_status == 'REJECTED':
            rej_reason = st.selectbox("Red Sebebi", ["Kapasite Dolu", "Tatil", "Mesafe", "Fiyat", "Diğer"])
            service_type = st.selectbox("Hizmet", ["Standart", "Boş Ev", "İnşaat Sonrası"])
        else:
            rej_reason = None
            service_type = st.selectbox("Hizmet Tipi", ["Standart", "Boş Ev", "İnşaat Sonrası"])
            
    c_price, c_cost = st.columns(2)
    with c_price:
        cust_price = st.number_input("Tutar (Ciro)", 0.0, step=100.0)
    with c_cost:
        if job_status == 'CONFIRMED':
            ns = st.number_input("Öğrenci Sayısı", 0, 10, 1)
            ps = st.number_input("Öğrenci Ücreti", 0.0)
            np = st.number_input("Pro Sayısı", 0, 10, 0)
            pp = st.number_input("Pro Ücreti", 0.0)
        else:
            st.info("Reddedilen iş için gider girilmez.")
            ns, ps, np, pp = 0, 0, 0, 0

    if st.button("Kaydet", type="primary"):
        if sel_c_name == "-":
            st.error("Müşteri seçiniz.")
        else:
            cid = c_opts[sel_c_name]
            gid = str(uuid.uuid4())[:8]
            
            jobs_data = []
            for fd in f_dates:
                ds = fd.strftime("%d.%m.%Y")
                
                if job_status == 'REJECTED':
                    jobs_data.append((gid, ds, cid, 'none', job_status, None, None, 0, cust_price, 0, 0, 0, f"RED: {rej_reason}", rej_reason, service_type))
                else:
                    # Onaylı İşler
                    for _ in range(ns):
                        jobs_data.append((gid, ds, cid, 'student', 'OPEN', None, None, ps, cust_price/(ns+np) if (ns+np)>0 else cust_price, 0, 0, 0, service_type, None, service_type))
                    for _ in range(np):
                        jobs_data.append((gid, ds, cid, 'pro', 'OPEN', None, None, pp, cust_price/(ns+np) if (ns+np)>0 else cust_price, 0, 0, 0, service_type, None, service_type))
            
            try:
                query = """
                    INSERT INTO jobs (group_id, date, customer_id, job_type, status, 
                    assigned_student_id, assigned_pro_id, price_worker, price_customer, 
                    is_worker_paid, is_collected, is_prepaid, job_note, rejection_reason, service_type) 
                    VALUES %s
                """
                execute_values(c, query, jobs_data)
                conn.commit()
                st.success("Kayıt Başarılı! 🚀")
                st.cache_data.clear() # İstatistikleri yenilemesi için cache temizle
            except Exception as e:
                st.error(f"Kayıt Hatası: {e}")

# --- TAB 2: TAKVİM ---
with tabs[1]:
    c_cal, c_list = st.columns([2, 1])
    
    with c_cal:
        cal = calendar.monthcalendar(sel_y, sel_m)
        m_str = f"{sel_m:02d}.{sel_y}"
        
        c.execute("""
            SELECT j.date, c.name, j.status, j.rejection_reason 
            FROM jobs j JOIN customers c ON j.customer_id = c.id 
            WHERE j.date LIKE %s
        """, (f"%{m_str}",))
        all_jobs = c.fetchall()
        
        day_map = {}
        for j in all_jobs:
            d = j['date']
            if d not in day_map: day_map[d] = []
            day_map[d].append(j)

        cols = st.columns(7)
        for d in ["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]: cols[list(["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]).index(d)].write(f"**{d}**")
        
        for w in cal:
            cols = st.columns(7)
            for i, d in enumerate(w):
                with cols[i]:
                    if d != 0:
                        ds = f"{d:02d}.{m_str}"
                        with st.container(border=True):
                            st.write(f"**{d}**")
                            if ds in day_map:
                                for job in day_map[ds]:
                                    if job['status'] == 'REJECTED':
                                        st.markdown(f'<div class="job-badge status-rejected" title="{job["rejection_reason"]}">{job["name"]}</div>', unsafe_allow_html=True)
                                    else:
                                        st.markdown(f'<div class="job-badge status-confirmed">{job["name"]}</div>', unsafe_allow_html=True)
                            else:
                                st.write("-")

    with c_list:
        st.markdown("### 📝 Günlük Notlar")
        today_str = datetime.now().strftime("%d.%m.%Y")
        
        c.execute("SELECT note FROM daily_notes WHERE date=%s", (today_str,))
        note_res = c.fetchone()
        current_note = note_res['note'] if note_res else ""
        
        new_note = st.text_area(f"{today_str}", value=current_note, placeholder="Hava durumu, özel notlar...")
        if st.button("Notu Kaydet"):
            try:
                c.execute("INSERT INTO daily_notes (date, note) VALUES (%s, %s) ON CONFLICT(date) DO UPDATE SET note=%s", (today_str, new_note, new_note))
                conn.commit()
                st.success("Kaydedildi")
            except Exception as e:
                st.error("Hata oluştu.")

# --- TAB 3: MÜŞTERİLER ---
with tabs[2]:
    st.subheader("📂 Müşteri Yönetimi")
    
    with st.form("new_cust"):
        c1, c2, c3, c4 = st.columns(4)
        n = c1.text_input("Ad Soyad")
        p = c2.text_input("Telefon")
        l = c3.text_input("İlçe/Semt")
        s = c4.selectbox("Segment", ["Yeni", "Düzenli", "VIP"]) # Segment BURADA giriliyor
        
        if st.form_submit_button("Ekle"):
            try:
                c.execute("INSERT INTO customers (name, phone, district, segment) VALUES (%s, %s, %s, %s)", (n, p, l, s))
                conn.commit()
                st.success("Müşteri Eklendi")
            except Exception as e:
                st.error(f"Hata: {e}")
    
    st.divider()
    c.execute("SELECT * FROM customers ORDER BY id DESC")
    cust_df = pd.DataFrame(c.fetchall())
    if not cust_df.empty:
        st.dataframe(cust_df[['name', 'phone', 'district', 'segment']], use_container_width=True)
