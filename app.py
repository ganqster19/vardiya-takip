import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import calendar
import uuid
from datetime import datetime, timedelta, date

st.set_page_config(page_title="Vardiya & Talep Yönetimi", page_icon="📈", layout="wide")

# --- CSS ---
st.markdown("""
<style>
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 8px; border-left: 5px solid #4CAF50; }
    .metric-card-loss { background-color: #ffebee; padding: 15px; border-radius: 8px; border-left: 5px solid #F44336; }
    .job-badge { padding: 2px 6px; border-radius: 4px; font-size: 11px; display: block; margin-bottom: 2px; }
    .status-confirmed { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .status-rejected { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; text-decoration: line-through; }
</style>
""", unsafe_allow_html=True)

# --- VERİTABANI BAĞLANTISI ---
@st.cache_resource
def get_db_connection():
    try:
        return psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            port=st.secrets["supabase"]["port"],
            cursor_factory=RealDictCursor,
            sslmode='require'
        )
    except Exception as e:
        st.error(f"Veritabanı Hatası: {e}")
        st.stop()

# --- INIT DB (GÜNCELLENMİŞ) ---
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Tabloları oluştur (Eksik sütunları ALTER ile yukarıda manuel ekledik varsayıyoruz veya burada create if not exists ile)
    # Kodun sade kalması için CREATE komutlarını buraya tekrar yazmıyorum, Supabase SQL editörden çalıştırman en sağlıklısı.
    conn.commit()

init_db()

# --- GELİŞMİŞ ANALİZ FONKSİYONU ---
def get_advanced_stats(month, year):
    conn = get_db_connection()
    c = conn.cursor()
    date_pattern = f"%.{month:02d}.{year}"
    
    # 1. Toplam Talep (Kabul + Red)
    c.execute("SELECT COUNT(*) FROM jobs WHERE date LIKE %s", (date_pattern,))
    total_demand_count = c.fetchone()['count']
    
    # 2. Gerçekleşen (Onaylı) İşler
    c.execute("SELECT COUNT(*), SUM(price_customer) FROM jobs WHERE date LIKE %s AND status != 'REJECTED'", (date_pattern,))
    res_confirmed = c.fetchone()
    confirmed_count = res_confirmed['count']
    confirmed_revenue = res_confirmed['sum'] if res_confirmed['sum'] else 0.0
    
    # 3. Kayıp Talep (Reddedilen)
    c.execute("SELECT COUNT(*), SUM(price_customer) FROM jobs WHERE date LIKE %s AND status = 'REJECTED'", (date_pattern,))
    res_rejected = c.fetchone()
    rejected_count = res_rejected['count']
    potential_loss = res_rejected['sum'] if res_rejected['sum'] else 0.0 # Kaçan para
    
    return total_demand_count, confirmed_count, rejected_count, confirmed_revenue, potential_loss

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
        <strong>✅ Gerçekleşen İş</strong><br>
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
    st.markdown(f"**Talep Karşılama Oranı:** %{conversion_rate:.1f}")
    
    if st.button("Yenile"): st.cache_data.clear(); st.rerun()

st.title("🚀 Talep ve Operasyon Yönetimi")

tabs = st.tabs(["📝 Talep Girişi (Sihirbaz)", "📅 Takvim & Durum", "📂 Müşteri Analizi"])

# --- TAB 1: TALEP GİRİŞİ (GELİŞTİRİLMİŞ SİHİRBAZ) ---
with tabs[0]:
    st.subheader("⚡ Yeni Talep Kaydı")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Müşteri Seçimi
    c.execute("SELECT id, name, district, segment FROM customers ORDER BY name")
    custs = c.fetchall()
    c_opts = {f"{x['name']} ({x['district'] or '?'}) - {x['segment']}": x['id'] for x in custs}
    
    c1, c2 = st.columns(2)
    with c1:
        sel_c_name = st.selectbox("Müşteri Seç", ["-"] + list(c_opts.keys()))
        
    # Tarih ve Hizmet
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
    
    # TALEP DURUMU (Kritik Bölüm)
    col_stat, col_det = st.columns(2)
    
    with col_stat:
        status = st.radio("Talep Durumu", ["✅ Onaylandı (İşi Aldık)", "🚫 Reddedildi (Kayıp)"], horizontal=True)
        job_status = 'CONFIRMED' if status.startswith("✅") else 'REJECTED'
        
    with col_det:
        if job_status == 'REJECTED':
            rej_reason = st.selectbox("Red Sebebi", ["Kapasite Dolu", "Tatil Günü", "Mesafe Uzak", "Fiyat Anlaşmazlığı", "Diğer"])
            service_type = st.selectbox("İstenen Hizmet", ["Standart", "Boş Ev", "İnşaat Sonrası", "Tadilat"])
            st.warning(f"📉 Bu iş '{rej_reason}' sebebiyle kayıp hanesine yazılacak.")
        else:
            rej_reason = None
            service_type = st.selectbox("Hizmet Tipi", ["Standart", "Boş Ev", "İnşaat Sonrası"])
            
    # Fiyatlandırma
    c_price, c_cost = st.columns(2)
    with c_price:
        cust_price = st.number_input("Müşteri Fiyatı (Tahmini Ciro)", 0.0, step=100.0)
    with c_cost:
        if job_status == 'CONFIRMED':
            # Personel atama sadece onaylıysa mantıklı
            ns = st.number_input("Öğrenci Sayısı", 0, 10, 1)
            ps = st.number_input("Öğrenci Başına Ücret", 0.0)
            np = st.number_input("Pro Sayısı", 0, 10, 0)
            pp = st.number_input("Pro Başına Ücret", 0.0)
        else:
            st.info("Reddedilen iş için personel gideri girilmez.")
            ns, ps, np, pp = 0, 0, 0, 0

    if st.button("Talebi Kaydet", type="primary"):
        if sel_c_name == "-":
            st.error("Müşteri seçiniz.")
        else:
            cid = c_opts[sel_c_name]
            gid = str(uuid.uuid4())[:8]
            
            jobs_data = []
            for fd in f_dates:
                ds = fd.strftime("%d.%m.%Y")
                
                # Eğer Reddedildiyse sadece 1 kayıt atarız (Personel ataması yok)
                if job_status == 'REJECTED':
                    jobs_data.append((gid, ds, cid, 'none', job_status, None, None, 0, cust_price, 0, 0, 0, f"RED: {rej_reason}", rej_reason, service_type))
                else:
                    # Onaylıysa personele göre kayıt aç (Eski mantık)
                    # Öğrenciler
                    for _ in range(ns):
                        jobs_data.append((gid, ds, cid, 'student', 'OPEN', None, None, ps, cust_price/(ns+np) if (ns+np)>0 else cust_price, 0, 0, 0, service_type, None, service_type))
                    # Prolar
                    for _ in range(np):
                        jobs_data.append((gid, ds, cid, 'pro', 'OPEN', None, None, pp, cust_price/(ns+np) if (ns+np)>0 else cust_price, 0, 0, 0, service_type, None, service_type))
            
            query = """
                INSERT INTO jobs (group_id, date, customer_id, job_type, status, 
                assigned_student_id, assigned_pro_id, price_worker, price_customer, 
                is_worker_paid, is_collected, is_prepaid, job_note, rejection_reason, service_type) 
                VALUES %s
            """
            execute_values(c, query, jobs_data)
            conn.commit()
            st.success(f"Talep sisteme işlendi! Durum: {job_status}")

# --- TAB 2: TAKVİM & DURUM ---
with tabs[1]:
    c_cal, c_list = st.columns([2, 1])
    
    with c_cal:
        st.markdown("### 📅 Takvim Görünümü")
        cal = calendar.monthcalendar(sel_y, sel_m)
        m_str = f"{sel_m:02d}.{sel_y}"
        
        # Verileri çek (Status ve Service Type dahil)
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
        days_tr = ["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]
        for i, d in enumerate(days_tr): cols[i].write(f"**{d}**")
        
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
        st.markdown("### 📝 Günlük Notlar (Hava Durumu vb.)")
        today_str = datetime.now().strftime("%d.%m.%Y")
        
        # Günlük Not Kaydı
        c.execute("SELECT note FROM daily_notes WHERE date=%s", (today_str,))
        note_res = c.fetchone()
        current_note = note_res['note'] if note_res else ""
        
        new_note = st.text_area(f"{today_str} İçin Notlar", value=current_note, placeholder="Örn: Hava yağmurlu, trafik yoğun, maç günü...")
        if st.button("Notu Kaydet"):
            c.execute("INSERT INTO daily_notes (date, note) VALUES (%s, %s) ON CONFLICT(date) DO UPDATE SET note=%s", (today_str, new_note, new_note))
            conn.commit()
            st.success("Kaydedildi")

# --- TAB 3: MÜŞTERİ ANALİZİ (SEGMENTASYON) ---
with tabs[2]:
    st.subheader("📂 Müşteri Yönetimi & Segmentasyon")
    
    with st.form("new_cust"):
        c1, c2, c3, c4 = st.columns(4)
        n = c1.text_input("Ad Soyad")
        p = c2.text_input("Telefon")
        l = c3.text_input("İlçe/Semt", placeholder="Örn: Karşıyaka")
        s = c4.selectbox("Segment", ["Yeni", "Düzenli", "VIP", "Sorunlu"])
        
        if st.form_submit_button("Müşteri Ekle"):
            c.execute("INSERT INTO customers (name, phone, district, segment) VALUES (%s, %s, %s, %s)", (n, p, l, s))
            conn.commit()
            st.success("Eklendi")
    
    st.divider()
    
    # Müşteri Listesi
    c.execute("SELECT * FROM customers ORDER BY id DESC")
    cust_df = pd.DataFrame(c.fetchall())
    if not cust_df.empty:
        st.dataframe(cust_df[['name', 'phone', 'district', 'segment']], use_container_width=True)

conn.close()
