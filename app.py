import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import calendar
import uuid
from datetime import datetime, timedelta, date

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Vardiya ERP", page_icon="🛠", layout="wide")

st.title("🛠 Sistem Başlatıcı")

# --- HATA AYIKLAMA PENCERESİ ---
status_container = st.container(border=True)
status_container.info("Sistem hazır. Veritabanı bağlantısı bekleniyor...")

# --- VERİTABANI BAĞLANTISI (GÜVENLİ) ---
def get_db_connection():
    # Secrets kontrolü
    if "supabase" not in st.secrets:
        st.error("Secrets dosyası bulunamadı!")
        st.stop()
        
    try:
        conn = psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            port=st.secrets["supabase"]["port"],
            cursor_factory=RealDictCursor,
            sslmode='require',
            connect_timeout=5  # 5 saniye cevap gelmezse zorla kapat
        )
        return conn
    except Exception as e:
        status_container.error(f"BAĞLANTI HATASI: {e}")
        return None

# --- VERİTABANI BAŞLATMA ---
def init_db(conn):
    try:
        conn.rollback() # Önceki hataları temizle
        c = conn.cursor()
        
        # Tabloları Tek Tek ve Güvenli Oluştur
        queries = [
            '''CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, location TEXT, default_note TEXT, is_regular INTEGER DEFAULT 0, frequency TEXT)''',
            '''CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, name TEXT, phone TEXT)''',
            '''CREATE TABLE IF NOT EXISTS jobs (id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, customer_id INTEGER, job_type TEXT DEFAULT 'student', status TEXT DEFAULT 'OPEN', assigned_student_id INTEGER, assigned_pro_id INTEGER, price_worker REAL DEFAULT 0, price_customer REAL DEFAULT 0, is_worker_paid INTEGER DEFAULT 0, is_collected INTEGER DEFAULT 0, is_prepaid INTEGER DEFAULT 0, job_note TEXT)''',
            '''CREATE TABLE IF NOT EXISTS daily_notes (date TEXT PRIMARY KEY, note TEXT)''',
            '''CREATE TABLE IF NOT EXISTS professionals (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, salary REAL DEFAULT 0, payment_day INTEGER DEFAULT 1)''',
            '''CREATE TABLE IF NOT EXISTS salary_payments (id SERIAL PRIMARY KEY, pro_id INTEGER, amount REAL, payment_date TEXT, month_year TEXT, payment_type TEXT DEFAULT 'monthly')''',
            '''CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, date TEXT, type TEXT, category TEXT, amount REAL, description TEXT, related_id INTEGER)'''
        ]
        
        for q in queries:
            c.execute(q)
            conn.commit()
            
        # Sütun Kontrolleri
        try:
            c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS district TEXT")
            c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS segment TEXT DEFAULT 'Yeni'")
            c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'CONFIRMED'")
            c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rejection_reason TEXT")
            c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'Standart'")
            c.execute("ALTER TABLE professionals ADD COLUMN IF NOT EXISTS weekly_salary REAL DEFAULT 0")
            c.execute("ALTER TABLE salary_payments ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'monthly'")
            conn.commit()
        except:
            conn.rollback()
            
        return True
    except Exception as e:
        status_container.error(f"TABLO HATASI: {e}")
        return False

# --- UYGULAMA MANTIĞI ---
if 'db_ready' not in st.session_state:
    st.session_state.db_ready = False

# BAŞLATMA BUTONU (OTOMATİK ÇALIŞMAYI ENGELLER)
if not st.session_state.db_ready:
    st.warning("⚠️ Veritabanı bağlantısı henüz kurulmadı.")
    if st.button("🚀 SİSTEMİ BAŞLAT (BAĞLAN)", type="primary"):
        with st.spinner("Bağlanılıyor..."):
            conn = get_db_connection()
            if conn:
                status_container.success("Bağlantı Başarılı!")
                if init_db(conn):
                    status_container.success("Tablolar Hazır!")
                    st.session_state.db_ready = True
                    conn.close()
                    st.rerun()
                else:
                    status_container.error("Tablo oluşturulurken hata çıktı.")
            else:
                status_container.error("Sunucuya erişilemedi. Lütfen Secrets ayarlarını (Port: 6543) kontrol edin.")
    st.stop() # DB hazır değilse aşağıyı okuma

# ==========================================
# ANA EKRAN (SADECE BAĞLANTI BAŞARILIYSA ÇALIŞIR)
# ==========================================

# Ana Fonksiyonlar (Önbelleksiz - Cache sorununu önlemek için)
def run_query(query, params=None, fetch=True):
    conn = get_db_connection()
    if not conn: return [] if fetch else None
    try:
        c = conn.cursor()
        c.execute(query, params)
        if fetch:
            res = c.fetchall()
            conn.close()
            return res
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Sorgu Hatası: {e}")
        return [] if fetch else None

# Arayüz
st.success("✅ Sistem Aktif")

with st.sidebar:
    st.header("Yönetim Paneli")
    if st.button("Önbelleği Temizle & Yenile"):
        st.cache_resource.clear()
        st.rerun()

# Basit İstatistikler
try:
    totals = run_query("SELECT COUNT(*) as cnt FROM jobs")
    job_count = totals[0]['cnt'] if totals else 0
    st.metric("Toplam İş Kaydı", job_count)
except:
    st.error("Veri okunamadı.")

# Sekmeler
t1, t2 = st.tabs(["📅 Takvim", "➕ İş Ekle"])

with t1:
    st.subheader("Takvim Görünümü")
    sel_y = st.selectbox("Yıl", [2025, 2026])
    sel_m = st.selectbox("Ay", range(1,13))
    
    m_str = f"{sel_m:02d}.{sel_y}"
    jobs = run_query("SELECT date, status, price_customer FROM jobs WHERE date LIKE %s", (f"%{m_str}",))
    
    if jobs:
        df = pd.DataFrame(jobs)
        st.dataframe(df)
    else:
        st.info("Kayıt yok.")

with t2:
    st.subheader("Yeni İş Ekle")
    with st.form("add_job"):
        c_name = st.text_input("Müşteri Adı (Manuel)")
        c_date = st.date_input("Tarih")
        c_price = st.number_input("Fiyat", 0.0)
        
        if st.form_submit_button("Kaydet"):
            # Önce müşteri var mı bak, yoksa ekle
            cust = run_query("SELECT id FROM customers WHERE name=%s", (c_name,))
            if not cust:
                run_query("INSERT INTO customers (name) VALUES (%s)", (c_name,), fetch=False)
                cust = run_query("SELECT id FROM customers WHERE name=%s", (c_name,))
            
            cid = cust[0]['id']
            ds = c_date.strftime("%d.%m.%Y")
            
            run_query("""
                INSERT INTO jobs (group_id, date, customer_id, price_customer, status) 
                VALUES (%s, %s, %s, %s, 'CONFIRMED')
            """, (str(uuid.uuid4())[:8], ds, cid, c_price), fetch=False)
            
            st.success("Eklendi!")
            st.rerun()
