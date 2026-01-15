import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import calendar
import uuid
from datetime import datetime, timedelta, date

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Vardiya ERP Ultimate", page_icon="📆", layout="wide")

# CSS İLE GÖRSEL İYİLEŞTİRME
st.markdown("""
<style>
    .job-badge {
        background-color: #f0f2f6;
        border: 1px solid #d6d9ef;
        color: #31333F;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 11px;
        margin-bottom: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        display: block;
    }
    .net-profit { color: #008f39; font-size: 11px; font-weight: bold; }
    .net-loss { color: #d10000; font-size: 11px; font-weight: bold; }
    .stButton button { padding: 0px 10px; min-height: 0px; height: 30px; width: 100%; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# --- VERİTABANI BAĞLANTISI (SUPABASE) ---
def get_db_connection():
    # Streamlit Secrets'tan verileri çeker
    try:
        conn = psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            cursor_factory=RealDictCursor  # Sütun isimleriyle erişim için
        )
        return conn
    except Exception as e:
        st.error(f"Veritabanı Bağlantı Hatası: {e}")
        st.stop()

def add_column_safe(cursor, table, column, type_def):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type_def}")
    except psycopg2.Error:
        pass # Hata olursa (örn: sütun varsa) yoksay

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # POSTGRESQL TABLO YAPILARI (SERIAL PRIMARY KEY kullanıldı)
    c.execute('''CREATE TABLE IF NOT EXISTS admin (username TEXT PRIMARY KEY, password TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY, 
        name TEXT, phone TEXT, location TEXT, default_note TEXT, 
        is_regular INTEGER DEFAULT 0, frequency TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY, name TEXT, phone TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cash_inflow (
        id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, amount REAL, description TEXT, customer_id INTEGER
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, customer_id INTEGER, 
        job_type TEXT DEFAULT 'student', status TEXT DEFAULT 'OPEN', 
        assigned_student_id INTEGER, assigned_pro_id INTEGER, 
        price_worker REAL DEFAULT 0, price_customer REAL DEFAULT 0, 
        is_worker_paid INTEGER DEFAULT 0, is_collected INTEGER DEFAULT 0, 
        is_prepaid INTEGER DEFAULT 0, job_note TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS availability (
        user_phone TEXT, date TEXT, is_available INTEGER, UNIQUE(user_phone, date)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY, date TEXT, description TEXT, amount REAL
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS daily_notes (
        date TEXT PRIMARY KEY, note TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS professionals (
        id SERIAL PRIMARY KEY, name TEXT, phone TEXT, 
        salary REAL DEFAULT 0, payment_day INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS salary_payments (
        id SERIAL PRIMARY KEY, pro_id INTEGER, amount REAL, 
        payment_date TEXT, month_year TEXT, payment_type TEXT DEFAULT 'monthly'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY, date TEXT, type TEXT, category TEXT, 
        amount REAL, description TEXT, related_id INTEGER
    )''')

    # Migration (Sütun eklemeler)
    add_column_safe(c, "professionals", "weekly_salary", "REAL DEFAULT 0")
    add_column_safe(c, "salary_payments", "payment_type", "TEXT DEFAULT 'monthly'")

    # Varsayılan Admin (Çakışma kontrolü ile)
    c.execute("SELECT * FROM admin WHERE username=%s", ('admin',))
    if not c.fetchone():
        c.execute("INSERT INTO admin VALUES (%s, %s)", ('admin', '1234'))
    
    conn.commit()
    conn.close()

init_db()

# --- FİNANSAL MOTOR ---
def calculate_obligations():
    conn = get_db_connection()
    c = conn.cursor()
    today = datetime.now().date()
    
    # 1. Parça Başı (Not: Postgres return type decimal olabilir, float'a çeviriyoruz)
    c.execute("SELECT SUM(price_worker) FROM jobs WHERE is_worker_paid=0 AND price_worker > 0")
    res = c.fetchone()
    piece_debt_val = float(res['sum']) if res and res['sum'] else 0.0
    
    salary_debt_val = 0
    c.execute("SELECT id, salary, weekly_salary FROM professionals")
    pros = c.fetchall()
    
    next_month = today.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    curr_month_key = f"{today.month:02d}-{today.year}"
    
    for p in pros:
        # Aylık
        if p['salary'] > 0:
            c.execute("SELECT id FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='monthly'", (p['id'], curr_month_key))
            if not c.fetchone(): salary_debt_val += p['salary']
        # Haftalık
        if p['weekly_salary'] > 0:
            tmp = today
            while tmp <= last_day:
                if tmp.weekday() == 0:
                    wk = f"W{tmp.isocalendar()[1]}-{tmp.year}"
                    c.execute("SELECT id FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='weekly'", (p['id'], wk))
                    if not c.fetchone(): salary_debt_val += p['weekly_salary']
                tmp += timedelta(days=1)
                
    conn.close()
    return piece_debt_val, salary_debt_val

def get_financial_report_df():
    conn = get_db_connection()
    c = conn.cursor()
    data = []
    
    # Manuel İşlemler
    c.execute("SELECT * FROM transactions")
    trans = c.fetchall()
    for t in trans:
        f = 1 if t['type']=='income' else -1
        data.append({"Tarih": t['date'], "Tür": "Manuel İşlem", "Açıklama": t['description'], "Tutar": t['amount']*f})

    # İş Gelirleri
    c.execute("SELECT j.date, j.price_customer, c.name FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.is_collected=1 AND j.price_customer > 0")
    jobs_inc = c.fetchall()
    for j in jobs_inc:
        data.append({"Tarih": j['date'], "Tür": "İş Geliri", "Açıklama": f"Tahsilat: {j['name']}", "Tutar": j['price_customer']})
    
    # İş Giderleri
    c.execute("SELECT j.date, j.price_worker, j.job_type FROM jobs j WHERE j.is_worker_paid=1 AND j.price_worker > 0")
    jobs_exp = c.fetchall()
    for j in jobs_exp:
        data.append({"Tarih": j['date'], "Tür": "Personel Ödeme", "Açıklama": f"Ödeme ({j['job_type']})", "Tutar": -j['price_worker']})
    
    # Maaşlar
    c.execute("SELECT sp.payment_date, sp.amount, p.name, sp.payment_type FROM salary_payments sp JOIN professionals p ON sp.pro_id=p.id")
    sals = c.fetchall()
    for s in sals: 
        lbl = "Hafta" if s['payment_type']=='weekly' else "Ay"
        data.append({"Tarih": s['payment_date'], "Tür": "Maaş", "Açıklama": f"{s['name']} ({lbl})", "Tutar": -s['amount']})
    
    conn.close()
    
    df_res = pd.DataFrame(data)
    if not df_res.empty:
        df_res['Tarih_Obj'] = pd.to_datetime(df_res['Tarih'], format="%d.%m.%Y")
        df_res = df_res.sort_values(by='Tarih_Obj', ascending=False).drop(columns=['Tarih_Obj'])
    return df_res

# --- SESSION ---
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'wiz_dates' not in st.session_state: st.session_state.wiz_dates = []

# ==========================================
# GİRİŞ PANELİ
# ==========================================
if not st.session_state.logged_in:
    st.title("🔒 Yönetim Girişi")
    with st.form("login"):
        u = st.text_input("Kullanıcı"); p = st.text_input("Şifre", type="password")
        if st.form_submit_button("Giriş"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM admin WHERE username=%s AND password=%s", (u, p))
            if c.fetchone():
                st.session_state.logged_in=True; st.rerun()
            else: st.error("Hatalı")
            conn.close()
    st.info("Varsayılan: `admin` / `1234`")

# ==========================================
# ANA EKRAN
# ==========================================
else:
    with st.sidebar:
        st.write("👤 **Yönetici**")
        if st.button("Çıkış Yap"): st.session_state.logged_in=False; st.rerun()
            
    st.title("📊 Operasyonel Kontrol Paneli")
    
    df_report = get_financial_report_df()
    curr_cash = df_report['Tutar'].sum() if not df_report.empty else 0.0
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT SUM(price_customer) as sum FROM jobs WHERE is_collected=0")
    res = c.fetchone()
    pend_inc = float(res['sum']) if res and res['sum'] else 0.0
    conn.close()
    
    piece_d, sal_d = calculate_obligations()
    tot_debt = piece_d + sal_d
    
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("💰 Anlık Kasa", f"{curr_cash:,.2f} TL")
    k2.metric("💳 Alacaklar", f"{pend_inc:,.2f} TL")
    k3.metric("📉 Gelecek Borç", f"{tot_debt:,.2f} TL", help="Parça Başı + Maaşlar")
    k4.metric("🔮 Net Tahmin", f"{(curr_cash + pend_inc - tot_debt):,.2f} TL")
    
    st.divider()
    
    tabs = st.tabs(["⚡ İş Planla", "📅 Öğrenci", "📅 Profesyonel", "📂 Profiller", "📈 Finans", "💸 Ödemeler"])
    
    # --- TAB 1: SİHİRBAZ ---
    with tabs[0]:
        st.subheader("⚡ Hızlı İş Planlama")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM customers")
        custs = c.fetchall()
        c_opts = {c['name']:c['id'] for c in custs}
        
        if c_opts:
            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1:
                    sel_c = st.selectbox("Müşteri", list(c_opts.keys()), key="wc")
                    cid = c_opts[sel_c]
                    pay_m = st.radio("Ödeme Tipi", ["Peşin Alındı", "İş Sonu (Veresiye)"], horizontal=True)

                with c2:
                    d_mode = st.radio("Tarih Modu", ["Aralık Seç", "Manuel Seç"], horizontal=True)
                    f_dates = []
                    if d_mode.startswith("Aralık"):
                        d1 = st.date_input("Başlangıç"); d2 = st.date_input("Bitiş", value=datetime.now().date()+timedelta(30))
                        days = st.multiselect("Günler", ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"])
                        if d1<=d2 and days:
                            cur=d1; dm={"Pazartesi":0,"Salı":1,"Çarşamba":2,"Perşembe":3,"Cuma":4,"Cumartesi":5,"Pazar":6}
                            idx=[dm[x] for x in days]
                            while cur<=d2:
                                if cur.weekday() in idx: f_dates.append(cur)
                                cur+=timedelta(1)
                    else:
                        cp, cl = st.columns(2)
                        with cp:
                            pick = st.date_input("Tarih", key="pick")
                            if st.button("Ekle"): 
                                if pick not in st.session_state.wiz_dates: st.session_state.wiz_dates.append(pick)
                        with cl:
                            st.write(f"Seçilen: {len(st.session_state.wiz_dates)}")
                            if st.button("Temizle"): st.session_state.wiz_dates=[]
                        f_dates = st.session_state.wiz_dates

            st.write("---")
            pricing_mode = st.radio("💰 Fiyatlandırma", ["Gün Başına Ücret", "Toplam Proje Ücreti"], horizontal=True)
            
            c_cost, c_rev = st.columns(2)
            with c_cost:
                st.info("📉 **Personel**")
                ns = st.number_input("Öğrenci Sayısı", 0, 50, 0)
                ps = st.number_input("Öğrenciye Ödenecek", 0.0)
                np = st.number_input("Pro Sayısı", 0, 50, 0)
                pp = st.number_input("Proya Ödenecek (Maaşlıysa 0)", 0.0)
                
            with c_rev:
                st.success(f"📈 **Gelir**")
                if "Toplam" in pricing_mode:
                    total_project_price = st.number_input("Toplam İş Tutarı", 0.0, step=500.0)
                else:
                    daily_price = st.number_input("Günlük Tutar", 0.0, step=500.0)
                    total_project_price = daily_price * len(f_dates)

            if st.button("Oluştur", type="primary"):
                if not f_dates: st.error("Tarih yok.")
                else:
                    gid = str(uuid.uuid4())[:8]
                    is_pre = 1 if pay_m.startswith("Peşin") else 0
                    is_coll = 1 if is_pre else 0
                    
                    cnt = 0
                    first_record_done = False
                    
                    for fd in f_dates:
                        ds = fd.strftime("%d.%m.%Y")
                        
                        price_for_this_day = 0
                        if "Toplam" in pricing_mode:
                            price_for_this_day = total_project_price if not first_record_done else 0
                        else:
                            price_for_this_day = daily_price
                        
                        # Öğrenci
                        for _ in range(ns):
                            assigned_customer_price = 0
                            if "Toplam" in pricing_mode:
                                if not first_record_done:
                                    assigned_customer_price = total_project_price
                                    first_record_done = True
                            else:
                                tot_workers = ns + np
                                if tot_workers > 0: assigned_customer_price = daily_price / tot_workers

                            c.execute("INSERT INTO jobs (group_id, date, customer_id, job_type, price_worker, price_customer, is_collected, is_prepaid, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, 'OPEN')", 
                                      (gid, ds, cid, 'student', ps, assigned_customer_price, is_coll, is_pre))
                            cnt+=1
                            
                        # Pro
                        for _ in range(np):
                            assigned_customer_price = 0
                            if "Toplam" in pricing_mode:
                                if not first_record_done:
                                    assigned_customer_price = total_project_price
                                    first_record_done = True
                            else:
                                tot_workers = ns + np
                                if tot_workers > 0: assigned_customer_price = daily_price / tot_workers

                            c.execute("INSERT INTO jobs (group_id, date, customer_id, job_type, price_worker, price_customer, is_collected, is_prepaid, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, 'OPEN')", 
                                      (gid, ds, cid, 'pro', pp, assigned_customer_price, is_coll, is_pre))
                            cnt+=1
                    
                    conn.commit()
                    st.success(f"{cnt} iş oluşturuldu. Grup: {gid}")
                    st.session_state.wiz_dates = []
        else: st.warning("Müşteri ekleyin.")
        conn.close()

    # --- TAKVİM FONKSİYONU ---
    def render_cal(type_label):
        db_type = 'student' if type_label == 'Öğrenci' else 'pro'
        conn = get_db_connection()
        c = conn.cursor()
        
        c_cal, c_det = st.columns([2,1])
        with c_cal:
            ny, nm = datetime.now().year, datetime.now().month
            c_y, c_m = st.columns(2)
            y = c_y.selectbox(f"Yıl {type_label}", [ny, ny+1], key=f"y{db_type}")
            m = c_m.selectbox(f"Ay {type_label}", range(1,13), index=nm-1, key=f"m{db_type}")
            
            cal = calendar.monthcalendar(y, m)
            m_str = f"{m:02d}.{y}"
            
            c.execute("SELECT j.date, c.name, j.price_customer, j.price_worker FROM jobs j JOIN customers c ON j.customer_id = c.id WHERE j.date LIKE %s AND j.job_type = %s", (f"%{m_str}", db_type))
            jobs_data = c.fetchall()
            
            day_map = {}
            for row in jobs_data:
                d = row['date']
                if d not in day_map: day_map[d] = {'names': [], 'inc': 0, 'exp': 0}
                if row['name'] not in day_map[d]['names']: day_map[d]['names'].append(row['name'])
                day_map[d]['inc'] += row['price_customer']
                day_map[d]['exp'] += row['price_worker']

            cols = st.columns(7)
            for d in ["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]: 
                cols[list(["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]).index(d)].write(f"**{d}**")
            
            for week in cal:
                cols = st.columns(7)
                for i, day in enumerate(week):
                    with cols[i]:
                        if day != 0:
                            ds = f"{day:02d}.{m_str}"
                            with st.container(border=True):
                                if st.button(f"**{day}**", key=f"b{db_type}{ds}", use_container_width=True):
                                    st.session_state[f's{db_type}'] = ds
                                data = day_map.get(ds)
                                if data:
                                    for name in data['names'][:3]:
                                        st.markdown(f'<span class="job-badge">{name}</span>', unsafe_allow_html=True)
                                    if len(data['names']) > 3: st.caption("...")
                                    net = data['inc'] - data['exp']
                                    css = "net-profit" if net >= 0 else "net-loss"
                                    st.markdown(f'<div class="{css}">{net:,.0f}</div>', unsafe_allow_html=True)
                                else: st.markdown("<br>", unsafe_allow_html=True)

        with c_det:
            sd = st.session_state.get(f's{db_type}', datetime.now().strftime("%d.%m.%Y"))
            st.markdown(f"### 📅 {sd}")
            
            with st.expander("📝 Not / Ekstra"):
                c.execute("SELECT note FROM daily_notes WHERE date=%s", (sd,))
                n = c.fetchone()
                nn = st.text_area("Not", n['note'] if n else "", key=f"n{db_type}")
                if st.button("Kaydet", key=f"sn{db_type}"): 
                    c.execute("INSERT INTO daily_notes (date,note) VALUES (%s,%s) ON CONFLICT(date) DO UPDATE SET note=%s", (sd, nn, nn)); conn.commit(); st.success("ok")
                with st.form(f"q{db_type}"):
                    t = st.selectbox("Tip", ["Gelir", "Gider"])
                    a = st.number_input("Tutar", 0.0); d = st.text_input("Açıklama")
                    if st.form_submit_button("Ekle"):
                        c.execute("INSERT INTO transactions (date,type,category,amount,description) VALUES (%s,%s,%s,%s,%s)",
                                  (sd, 'income' if t=='Gelir' else 'expense', 'extra', a, d)); conn.commit(); st.rerun()

            c.execute("SELECT j.*, c.name, c.location FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.date=%s AND j.job_type=%s", (sd, db_type))
            jobs = c.fetchall()
            
            if jobs:
                for j in jobs:
                    with st.expander(f"📌 {j['name']}"):
                        st.caption(f"📍 {j['location']}")
                        st.write(f"💵 **Al:** {j['price_customer']} | **Ver:** {j['price_worker']}")
                        
                        col1, col2 = st.columns(2)
                        ic = col1.checkbox("Tahsilat", bool(j['is_collected']), key=f"cc{j['id']}")
                        iw = col2.checkbox("Ödeme", bool(j['is_worker_paid']), key=f"cw{j['id']}")
                        
                        if ic!=bool(j['is_collected']): c.execute("UPDATE jobs SET is_collected=%s WHERE id=%s", (int(ic), j['id'])); conn.commit(); st.rerun()
                        if iw!=bool(j['is_worker_paid']): c.execute("UPDATE jobs SET is_worker_paid=%s WHERE id=%s", (int(iw), j['id'])); conn.commit(); st.rerun()
                        
                        assigned = "???"
                        if j['assigned_student_id']: 
                            c.execute("SELECT name FROM students WHERE id=%s", (j['assigned_student_id'],))
                            res = c.fetchone()
                            if res: assigned = res['name']
                        elif j['assigned_pro_id']:
                            c.execute("SELECT name FROM professionals WHERE id=%s", (j['assigned_pro_id'],))
                            res = c.fetchone()
                            if res: assigned = res['name']
                        st.info(f"Personel: {assigned}")
                        
                        with st.popover("⚙️ Düzenle"):
                            if db_type=='student':
                                c.execute("SELECT * FROM students")
                                opts = {x['name']:x['id'] for x in c.fetchall()}
                                sel = st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"as{j['id']}")
                                cp = st.number_input("Ücret", value=j['price_worker'], key=f"acp{j['id']}")
                                if st.button("Kaydet", key=f"ab{j['id']}"):
                                    if sel!="-": c.execute("UPDATE jobs SET assigned_student_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],cp,j['id'])); conn.commit(); st.rerun()
                            else:
                                c.execute("SELECT * FROM professionals")
                                opts = {x['name']:x['id'] for x in c.fetchall()}
                                sel = st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"ap{j['id']}")
                                if sel!="-":
                                    c.execute("SELECT salary, weekly_salary FROM professionals WHERE id=%s", (opts[sel],))
                                    p_d = c.fetchone()
                                    is_sal = (p_d['salary']>0 or p_d['weekly_salary']>0)
                                    np = 0 if is_sal else j['price_worker']
                                    if not is_sal: np = st.number_input("Ücret", value=j['price_worker'] if j['price_worker']>0 else 1500.0, key=f"apnm{j['id']}")
                                    else: st.success("Maaşlı (0 TL)")
                                    if st.button("Kaydet", key=f"apb{j['id']}"):
                                        c.execute("UPDATE jobs SET assigned_pro_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],np,j['id'])); conn.commit(); st.rerun()
                        
                        if st.button("Sil", key=f"dl{j['id']}"): c.execute("DELETE FROM jobs WHERE id=%s", (j['id'],)); conn.commit(); st.rerun()
            else: st.info("İş yok.")
        conn.close()

    with tabs[1]: render_cal('Öğrenci')
    with tabs[2]: render_cal('Profesyonel')

    # --- TAB 4: PROFILLER ---
    with tabs[3]:
        conn=get_db_connection()
        c=conn.cursor()
        t1,t2,t3=st.tabs(["Müşteri","Öğrenci","Pro"])
        
        with t1:
            with st.form("nc"):
                n=st.text_input("Ad"); p=st.text_input("Tel"); l=st.text_input("Konum")
                if st.form_submit_button("Ekle"): c.execute("INSERT INTO customers (name,phone,location) VALUES (%s,%s,%s)",(n,p,l)); conn.commit(); st.rerun()
            c.execute("SELECT * FROM customers")
            custs = c.fetchall()
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in custs])
            if sel!="-":
                c.execute("SELECT * FROM customers WHERE name=%s", (sel,))
                cust = c.fetchone()
                with st.expander("Düzenle"):
                    with st.form("ec"):
                        en=st.text_input("Ad",cust['name']); ep=st.text_input("Tel",cust['phone']); el=st.text_input("Konum",cust['location'])
                        if st.form_submit_button("Güncelle"): c.execute("UPDATE customers SET name=%s, phone=%s, location=%s WHERE id=%s",(en,ep,el,cust['id'])); conn.commit(); st.rerun()
                st.write("**Geçmiş**")
                c.execute("SELECT * FROM jobs WHERE customer_id=%s ORDER BY date DESC", (cust['id'],))
                for j in c.fetchall(): st.write(f"📅 {j['date']} | 💵 {j['price_customer']} TL")
        
        with t2:
            with st.form("ns"):
                n=st.text_input("Ad"); p=st.text_input("Tel")
                if st.form_submit_button("Ekle"): c.execute("INSERT INTO students (name,phone) VALUES (%s,%s)",(n,p)); conn.commit(); st.rerun()
            c.execute("SELECT * FROM students")
            stus = c.fetchall()
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in stus])
            if sel!="-":
                c.execute("SELECT * FROM students WHERE name=%s", (sel,))
                stu = c.fetchone()
                with st.expander("Düzenle"):
                    with st.form("es"):
                        en=st.text_input("Ad",stu['name']); ep=st.text_input("Tel",stu['phone'])
                        if st.form_submit_button("Güncelle"): c.execute("UPDATE students SET name=%s, phone=%s WHERE id=%s",(en,ep,stu['id'])); conn.commit(); st.rerun()
                st.write("**İşler**")
                c.execute("SELECT * FROM jobs WHERE assigned_student_id=%s ORDER BY date DESC", (stu['id'],))
                for j in c.fetchall(): st.write(f"📅 {j['date']} | 💰 {j['price_worker']} TL")
        
        with t3:
            pt1, pt2 = st.tabs(["Maaşlı", "Ekstra"])
            with pt1:
                with st.form("npm"):
                    n=st.text_input("Ad"); p=st.text_input("Tel"); sa=st.number_input("Ay",0.0); we=st.number_input("Hafta",0.0); da=st.number_input("Gün",1)
                    if st.form_submit_button("Ekle"): c.execute("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,sa,we,da)); conn.commit(); st.rerun()
                c.execute("SELECT * FROM professionals WHERE salary>0 OR weekly_salary>0")
                pros = c.fetchall()
                sel = st.selectbox("Seç", ["-"]+[x['name'] for x in pros], key="msel")
                if sel!="-":
                    c.execute("SELECT * FROM professionals WHERE name=%s", (sel,))
                    pro = c.fetchone()
                    with st.expander("Düzenle"):
                        with st.form("epm"):
                            en=st.text_input("Ad",pro['name']); es=st.number_input("Ay",pro['salary']); ew=st.number_input("Hafta",pro['weekly_salary']); ed=st.number_input("Gün",pro['payment_day'])
                            if st.form_submit_button("Güncelle"): c.execute("UPDATE professionals SET name=%s, salary=%s, weekly_salary=%s, payment_day=%s WHERE id=%s",(en,es,ew,ed,pro['id'])); conn.commit(); st.rerun()
                    st.write("**İşler**")
                    c.execute("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (pro['id'],))
                    for j in c.fetchall(): st.write(f"📅 {j['date']}") 

            with pt2:
                with st.form("npe"):
                    n=st.text_input("Ad"); p=st.text_input("Tel")
                    if st.form_submit_button("Ekle"): c.execute("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,0,0,1)); conn.commit(); st.rerun()
                c.execute("SELECT * FROM professionals WHERE salary=0 AND weekly_salary=0")
                epros = c.fetchall()
                sel = st.selectbox("Seç", ["-"]+[x['name'] for x in epros], key="esel")
                if sel!="-":
                    c.execute("SELECT * FROM professionals WHERE name=%s", (sel,))
                    pro = c.fetchone()
                    with st.expander("Düzenle"):
                        with st.form("epe"):
                            en=st.text_input("Ad",pro['name']); ep=st.text_input("Tel",pro['phone'])
                            if st.form_submit_button("Güncelle"): c.execute("UPDATE professionals SET name=%s, phone=%s WHERE id=%s",(en,ep,pro['id'])); conn.commit(); st.rerun()
                    st.write("**İşler**")
                    c.execute("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (pro['id'],))
                    for j in c.fetchall(): st.write(f"📅 {j['date']} | 💰 {j['price_worker']} TL")
        conn.close()

    with tabs[4]:
        df = get_financial_report_df()
        st.dataframe(df, width="stretch")
        if not df.empty:
            st.download_button("İndir", df.to_csv(index=False).encode('utf-8'), "finans.csv", "text/csv")

    with tabs[5]:
        conn = get_db_connection()
        c = conn.cursor()
        p1,p2,p3 = st.tabs(["Aylık","Haftalık","Parça"])
        with p1:
            cm = f"{datetime.now().month:02d}-{datetime.now().year}"
            c.execute("SELECT * FROM professionals WHERE salary>0")
            for p in c.fetchall():
                c1,c2=st.columns([3,1])
                c1.write(f"{p['name']} ({p['salary']} TL)")
                c.execute("SELECT * FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='monthly'",(p['id'],cm))
                if c.fetchone(): c2.success("✅")
                else:
                    if c2.button("Öde", key=f"pm{p['id']}"):
                        c.execute("INSERT INTO salary_payments (pro_id,amount,payment_date,month_year,payment_type) VALUES (%s,%s,%s,%s,%s)",(p['id'],p['salary'],datetime.now().strftime("%d.%m.%Y"),cm,'monthly')); conn.commit(); st.rerun()
        with p2:
            today = datetime.now(); wn = today.isocalendar()[1]; wk = f"W{wn}-{today.year}"
            c.execute("SELECT * FROM professionals WHERE weekly_salary>0")
            for p in c.fetchall():
                c1,c2=st.columns([3,1])
                c1.write(f"{p['name']} ({p['weekly_salary']} TL)")
                c.execute("SELECT * FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='weekly'",(p['id'],wk))
                if c.fetchone(): c2.success("✅")
                else:
                    if c2.button("Öde", key=f"pw{p['id']}"):
                        c.execute("INSERT INTO salary_payments (pro_id,amount,payment_date,month_year,payment_type) VALUES (%s,%s,%s,%s,%s)",(p['id'],p['weekly_salary'],datetime.now().strftime("%d.%m.%Y"),wk,'weekly')); conn.commit(); st.rerun()
        with p3:
            c.execute("SELECT j.*, s.name as sn, p.name as pn FROM jobs j LEFT JOIN students s ON j.assigned_student_id=s.id LEFT JOIN professionals p ON j.assigned_pro_id=p.id WHERE j.is_worker_paid=0 AND j.price_worker>0 AND j.status='ASSIGNED'")
            unp = c.fetchall()
            if unp:
                for u in unp:
                    nm = u['sn'] if u['sn'] else u['pn']
                    c1,c2=st.columns([3,1])
                    c1.write(f"{u['date']} | {nm} | {u['price_worker']} TL")
                    if c2.button("Öde", key=f"pj{u['id']}"):
                        c.execute("UPDATE jobs SET is_worker_paid=1 WHERE id=%s",(u['id'],)); conn.commit(); st.rerun()
            else: st.info("Borç yok")
        conn.close()