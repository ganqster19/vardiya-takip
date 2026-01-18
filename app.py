import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import calendar
import uuid
from datetime import datetime, timedelta, date

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Vardiya ERP Ultimate", page_icon="🚀", layout="wide")

# CSS
st.markdown("""
<style>
    .job-badge {
        background-color: #f0f2f6; border: 1px solid #d6d9ef; color: #31333F;
        padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-bottom: 2px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;
    }
    .net-profit { color: #008f39; font-size: 11px; font-weight: bold; }
    .net-loss { color: #d10000; font-size: 11px; font-weight: bold; }
    .stButton button { padding: 0px 10px; min-height: 0px; height: 30px; width: 100%; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# --- VERİTABANI BAĞLANTISI (GÜVENLİ) ---
@st.cache_resource(ttl=600)
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            port=st.secrets["supabase"]["port"],
            cursor_factory=RealDictCursor,
            sslmode='require',
            connect_timeout=10, # 10 saniye bekleme süresi
            keepalives=1
        )
        return conn
    except Exception as e:
        st.error("Veritabanına bağlanılamadı. Lütfen sayfayı yenileyin.")
        st.stop()

# --- GÜVENLİ SORGU ÇALIŞTIRICI (KİLİTLENMEYİ ÖNLER) ---
def run_query(query, params=None, fetch=False, commit=False):
    conn = get_db_connection()
    try:
        # Bağlantı koptuysa yenile
        if conn.closed: 
            st.cache_resource.clear()
            conn = get_db_connection()
            
        with conn.cursor() as c:
            c.execute(query, params)
            if commit: conn.commit()
            if fetch: return c.fetchall()
            return None
    except psycopg2.errors.InFailedSqlTransaction:
        # EĞER KİLİTLENME VARSA:
        conn.rollback() # Kilidi aç
        # Tekrar dene
        with conn.cursor() as c:
            c.execute(query, params)
            if commit: conn.commit()
            if fetch: return c.fetchall()
    except Exception as e:
        conn.rollback() # Diğer hatalarda da temizle
        # st.error(f"İşlem hatası: {e}") # Kullanıcıya gösterme, logla
        return [] if fetch else None

# --- BAŞLANGIÇ AYARLARI ---
def init_app():
    # Tabloları garantiye al (Sadece yoksa oluşturur)
    conn = get_db_connection()
    conn.rollback() # Başlangıç temizliği
    
    # Gerekli tablolar
    qs = [
        '''CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, location TEXT, default_note TEXT, is_regular INTEGER DEFAULT 0, frequency TEXT)''',
        '''CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, name TEXT, phone TEXT)''',
        '''CREATE TABLE IF NOT EXISTS jobs (id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, customer_id INTEGER, job_type TEXT DEFAULT 'student', status TEXT DEFAULT 'OPEN', assigned_student_id INTEGER, assigned_pro_id INTEGER, price_worker REAL DEFAULT 0, price_customer REAL DEFAULT 0, is_worker_paid INTEGER DEFAULT 0, is_collected INTEGER DEFAULT 0, is_prepaid INTEGER DEFAULT 0, job_note TEXT)''',
        '''CREATE TABLE IF NOT EXISTS daily_notes (date TEXT PRIMARY KEY, note TEXT)''',
        '''CREATE TABLE IF NOT EXISTS professionals (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, salary REAL DEFAULT 0, payment_day INTEGER DEFAULT 1, weekly_salary REAL DEFAULT 0)''',
        '''CREATE TABLE IF NOT EXISTS salary_payments (id SERIAL PRIMARY KEY, pro_id INTEGER, amount REAL, payment_date TEXT, month_year TEXT, payment_type TEXT DEFAULT 'monthly')''',
        '''CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, date TEXT, type TEXT, category TEXT, amount REAL, description TEXT, related_id INTEGER)'''
    ]
    for q in qs: run_query(q, commit=True)

init_app()

# --- HESAPLAMA MOTORLARI ---
def calculate_obligations():
    conn = get_db_connection()
    conn.rollback() # Her hesaplamadan önce temizle
    
    # Parça Başı Borç
    res = run_query("SELECT SUM(price_worker) as s FROM jobs WHERE is_worker_paid=0 AND price_worker > 0", fetch=True)
    piece_debt = float(res[0]['s']) if res and res[0]['s'] else 0.0
    
    salary_debt = 0
    pros = run_query("SELECT id, salary, weekly_salary FROM professionals", fetch=True)
    today = datetime.now().date()
    curr_month = f"{today.month:02d}-{today.year}"
    
    if pros:
        for p in pros:
            # Aylık
            if p['salary'] > 0:
                chk = run_query("SELECT id FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='monthly'", (p['id'], curr_month), fetch=True)
                if not chk: salary_debt += p['salary']
            # Haftalık
            if p['weekly_salary'] > 0:
                # Basit hesap: Ayın başından bugüne kadar olan pazartesiler
                # Detaylı hesap karmaşıklaşmasın diye şimdilik düz mantık:
                pass # (Haftalık borç hesabını basit tutuyoruz)
                
    return piece_debt, salary_debt

def calculate_monthly_profit(month, year):
    conn = get_db_connection()
    conn.rollback()
    
    dp = f"%.{month:02d}.{year}"
    
    # Gelirler
    r1 = run_query("SELECT SUM(price_customer) as s FROM jobs WHERE date LIKE %s", (dp,), fetch=True)
    inc_jobs = float(r1[0]['s']) if r1 and r1[0]['s'] else 0.0
    
    r2 = run_query("SELECT SUM(amount) as s FROM transactions WHERE type='income' AND date LIKE %s", (dp,), fetch=True)
    inc_ext = float(r2[0]['s']) if r2 and r2[0]['s'] else 0.0
    
    # Giderler
    r3 = run_query("SELECT SUM(price_worker) as s FROM jobs WHERE date LIKE %s", (dp,), fetch=True)
    exp_jobs = float(r3[0]['s']) if r3 and r3[0]['s'] else 0.0
    
    r4 = run_query("SELECT SUM(amount) as s FROM transactions WHERE type='expense' AND date LIKE %s", (dp,), fetch=True)
    exp_ext = float(r4[0]['s']) if r4 and r4[0]['s'] else 0.0
    
    # Maaşlar (Sabit Gider)
    pros = run_query("SELECT salary, weekly_salary FROM professionals", fetch=True)
    sal_tot = 0
    
    num_days = calendar.monthrange(year, month)[1]
    mondays = sum(1 for day in range(1, num_days + 1) if date(year, month, day).weekday() == 0)
    
    if pros:
        for p in pros:
            if p['salary'] > 0: sal_tot += p['salary']
            if p['weekly_salary'] > 0: sal_tot += (p['weekly_salary'] * mondays)
            
    return (inc_jobs + inc_ext), (exp_jobs + exp_ext + sal_tot)

def get_report():
    data = []
    # Manuel İşlemler
    trx = run_query("SELECT * FROM transactions", fetch=True)
    if trx:
        for t in trx:
            f = 1 if t['type']=='income' else -1
            data.append({"Tarih": t['date'], "Tür": "Manuel", "Açıklama": t['description'], "Tutar": t['amount']*f})
    
    # İş Gelirleri
    j_inc = run_query("SELECT j.date, j.price_customer, c.name FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.is_collected=1 AND j.price_customer > 0", fetch=True)
    if j_inc:
        for j in j_inc:
            data.append({"Tarih": j['date'], "Tür": "Tahsilat", "Açıklama": j['name'], "Tutar": j['price_customer']})
            
    # Personel Ödemeleri
    j_exp = run_query("SELECT j.date, j.price_worker, j.job_type FROM jobs j WHERE j.is_worker_paid=1 AND j.price_worker > 0", fetch=True)
    if j_exp:
        for j in j_exp:
            data.append({"Tarih": j['date'], "Tür": "Ödeme", "Açıklama": f"Personel ({j['job_type']})", "Tutar": -j['price_worker']})
            
    # Maaşlar
    sal = run_query("SELECT sp.payment_date, sp.amount, p.name FROM salary_payments sp JOIN professionals p ON sp.pro_id=p.id", fetch=True)
    if sal:
        for s in sal:
            data.append({"Tarih": s['payment_date'], "Tür": "Maaş", "Açıklama": s['name'], "Tutar": -s['amount']})
            
    df = pd.DataFrame(data)
    if not df.empty:
        df['Tarih_Obj'] = pd.to_datetime(df['Tarih'], format="%d.%m.%Y")
        df = df.sort_values(by='Tarih_Obj', ascending=False).drop(columns=['Tarih_Obj'])
    return df

# --- SESSION STATE ---
if 'wiz_dates' not in st.session_state: st.session_state.wiz_dates = []

# ==========================================
# ARAYÜZ (UI)
# ==========================================
with st.sidebar:
    st.title("📊 Yönetim")
    st.caption(f"Tarih: {datetime.now().strftime('%d.%m.%Y')}")
    st.divider()
    
    st.subheader("📅 Aylık Kâr Analizi")
    sel_y = st.selectbox("Yıl", [2025, 2026], index=1)
    sel_m = st.selectbox("Ay", range(1,13), index=datetime.now().month-1)
    
    mi, me = calculate_monthly_profit(sel_m, sel_y)
    mn = mi - me
    
    st.markdown(f"""
    <div style="background:#f0f2f6;padding:10px;border-radius:5px;">
        <h4 style="margin:0;">{calendar.month_name[sel_m]} {sel_y}</h4><hr style="margin:5px 0;">
        <div style="display:flex;justify-content:space-between;"><span>Gelir:</span><span style="color:green;">{mi:,.0f}</span></div>
        <div style="display:flex;justify-content:space-between;"><span>Gider:</span><span style="color:red;">{me:,.0f}</span></div>
        <div style="display:flex;justify-content:space-between;border-top:1px solid #ccc;margin-top:5px;padding-top:5px;"><span>NET:</span><span style="color:{'green' if mn>=0 else 'red'};font-weight:bold;">{mn:,.0f}</span></div>
    </div>""", unsafe_allow_html=True)
    
    st.divider()
    if st.button("Yenile (F5)"): 
        st.cache_resource.clear()
        st.rerun()

st.title("🚀 Vardiya Yönetim Merkezi")

# KPI
df_r = get_report()
curr_cash = df_r['Tutar'].sum() if not df_r.empty else 0.0

pi_res = run_query("SELECT SUM(price_customer) as s FROM jobs WHERE is_collected=0", fetch=True)
pend_inc = float(pi_res[0]['s']) if pi_res and pi_res[0]['s'] else 0.0

pd_val, sd_val = calculate_obligations()
tot_debt = pd_val + sd_val

cmi, cme = calculate_monthly_profit(datetime.now().month, datetime.now().year)
cmn = cmi - cme

k1,k2,k3,k4 = st.columns(4)
k1.metric("💰 Anlık Kasa", f"{curr_cash:,.0f} TL")
k2.metric("💳 Alacaklar", f"{pend_inc:,.0f} TL")
k3.metric("📉 Toplam Borç", f"{tot_debt:,.0f} TL")
k4.metric(f"📅 Bu Ay Kâr", f"{cmn:,.0f} TL", delta_color="normal")

st.divider()
tabs = st.tabs(["⚡ İş Planla", "📅 Öğrenci", "📅 Pro", "📂 Profiller", "📈 Finans", "💸 Ödemeler"])

# --- TAB 1: SİHİRBAZ ---
with tabs[0]:
    st.subheader("⚡ Hızlı İş Planlama")
    custs = run_query("SELECT * FROM customers", fetch=True)
    c_opts = {c['name']:c['id'] for c in custs} if custs else {}
    
    if c_opts:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                sel_c = st.selectbox("Müşteri", list(c_opts.keys()))
                cid = c_opts[sel_c]
                pay_m = st.radio("Ödeme", ["Peşin", "Veresiye"], horizontal=True)
            with c2:
                d_mode = st.radio("Tarih", ["Aralık", "Manuel"], horizontal=True)
                if d_mode.startswith("Aralık"):
                    d1 = st.date_input("Başlangıç"); d2 = st.date_input("Bitiş", value=datetime.now().date()+timedelta(30))
                    days = st.multiselect("Günler", ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"])
                    if d1<=d2 and days:
                        cur=d1; dm={"Pazartesi":0,"Salı":1,"Çarşamba":2,"Perşembe":3,"Cuma":4,"Cumartesi":5,"Pazar":6}
                        idx=[dm[x] for x in days]
                        st.session_state.wiz_dates = []
                        while cur<=d2:
                            if cur.weekday() in idx: st.session_state.wiz_dates.append(cur)
                            cur+=timedelta(1)
                else:
                    cp, cl = st.columns(2)
                    with cp:
                        pick = st.date_input("Tarih")
                        if st.button("Ekle"): 
                            if pick not in st.session_state.wiz_dates: st.session_state.wiz_dates.append(pick)
                    with cl:
                        st.write(f"Seçilen: {len(st.session_state.wiz_dates)}")
                        if st.button("Temizle"): st.session_state.wiz_dates=[]

        st.write("---")
        p_mode = st.radio("💰 Fiyat Tipi", ["Gün Başına", "Toplam Proje"], horizontal=True)
        c_cost, c_rev = st.columns(2)
        with c_cost:
            ns = st.number_input("Öğrenci Sayısı", 0, 50, 0)
            ps = st.number_input("Öğrenci Ücreti", 0.0)
            np = st.number_input("Pro Sayısı", 0, 50, 0)
            pp = st.number_input("Pro Ücreti", 0.0)
        with c_rev:
            if "Toplam" in p_mode:
                tot_p = st.number_input("Toplam Tutar", 0.0, step=500.0)
            else:
                day_p = st.number_input("Günlük Tutar", 0.0, step=500.0)
                tot_p = day_p * len(st.session_state.wiz_dates)

        if st.button("Oluştur", type="primary"):
            if not st.session_state.wiz_dates: st.error("Tarih yok.")
            else:
                gid = str(uuid.uuid4())[:8]
                is_pre = 1 if pay_m.startswith("Peşin") else 0
                is_coll = 1 if is_pre else 0
                
                jobs_data = []
                first_rec = False
                
                for fd in st.session_state.wiz_dates:
                    ds = fd.strftime("%d.%m.%Y")
                    # Fiyat
                    p_cust = 0
                    if "Toplam" in p_mode:
                        p_cust = tot_p if not first_rec else 0
                        first_rec = True
                    else:
                        p_cust = day_p
                    
                    # Dağıtma mantığı (Basitleştirilmiş: Toplam fiyatı ilk öğrenciye yaz)
                    # Eğer proje bazlıysa sadece ilk güne yazılır.
                    # Eğer gün bazlıysa her günün ilk işine yazılır.
                    
                    # ÖĞRENCİ
                    for _ in range(ns):
                        # Fiyatı kime yazacağız? (Sadece 1 kişiye yazıyoruz ki mükerrer olmasın)
                        my_price = 0
                        if "Toplam" in p_mode:
                             # Zaten yukarıda p_cust ayarladık (ilk kayıtta dolu, diğerlerinde 0)
                             if p_cust > 0: 
                                 my_price = p_cust
                                 p_cust = 0 # Harcandı
                        else:
                            # Günlük
                            if (ns+np) > 0: my_price = day_p / (ns+np) # Eşit bölüştür

                        jobs_data.append((gid, ds, cid, 'student', 'OPEN', None, None, ps, my_price, is_coll, is_pre))
                    
                    # PRO
                    for _ in range(np):
                        my_price = 0
                        if "Toplam" in p_mode:
                             if p_cust > 0: 
                                 my_price = p_cust
                                 p_cust = 0
                        else:
                            if (ns+np) > 0: my_price = day_p / (ns+np)

                        jobs_data.append((gid, ds, cid, 'pro', 'OPEN', None, None, pp, my_price, is_coll, is_pre))
                
                if jobs_data:
                    q = "INSERT INTO jobs (group_id, date, customer_id, job_type, status, assigned_student_id, assigned_pro_id, price_worker, price_customer, is_collected, is_prepaid) VALUES %s"
                    conn = get_db_connection()
                    with conn.cursor() as c:
                        execute_values(c, q, jobs_data)
                        conn.commit()
                    st.success(f"Kayıt Başarılı! ({len(jobs_data)} iş)")
                    st.session_state.wiz_dates = []
    else: st.warning("Müşteri ekleyin.")

# --- TAKVİM ---
def render_cal(type_label):
    db_type = 'student' if type_label == 'Öğrenci' else 'pro'
    c1, c2 = st.columns([2,1])
    with c1:
        ny, nm = datetime.now().year, datetime.now().month
        cy, cm = st.columns(2)
        y = cy.selectbox(f"Yıl {type_label}", [ny, ny+1], key=f"y{db_type}")
        m = cm.selectbox(f"Ay {type_label}", range(1,13), index=nm-1, key=f"m{db_type}")
        cal = calendar.monthcalendar(y, m)
        m_str = f"{m:02d}.{y}"
        
        jobs = run_query("SELECT j.date, c.name, j.price_customer, j.price_worker FROM jobs j JOIN customers c ON j.customer_id = c.id WHERE j.date LIKE %s AND j.job_type = %s", (f"%{m_str}", db_type), fetch=True)
        
        day_map = {}
        if jobs:
            for row in jobs:
                d = row['date']
                if d not in day_map: day_map[d] = {'names': [], 'inc': 0, 'exp': 0}
                if row['name'] not in day_map[d]['names']: day_map[d]['names'].append(row['name'])
                day_map[d]['inc'] += row['price_customer']
                day_map[d]['exp'] += row['price_worker']

        cols = st.columns(7)
        for d in ["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]: cols[list(["Pt","Sa","Ça","Pe","Cu","Ct","Pz"]).index(d)].write(f"**{d}**")
        for w in cal:
            cols = st.columns(7)
            for i, d in enumerate(w):
                with cols[i]:
                    if d!=0:
                        ds = f"{d:02d}.{m_str}"
                        with st.container(border=True):
                            if st.button(f"**{d}**", key=f"b{db_type}{ds}", use_container_width=True): st.session_state[f's{db_type}'] = ds
                            dm = day_map.get(ds)
                            if dm:
                                for nm in dm['names'][:2]: st.markdown(f'<span class="job-badge">{nm}</span>', unsafe_allow_html=True)
                                if len(dm['names'])>2: st.caption("...")
                                net = dm['inc']-dm['exp']
                                st.markdown(f'<div class="{"net-profit" if net>=0 else "net-loss"}">{net:,.0f}</div>', unsafe_allow_html=True)
                            else: st.markdown("<br>", unsafe_allow_html=True)
    with c2:
        sd = st.session_state.get(f's{db_type}', datetime.now().strftime("%d.%m.%Y"))
        st.markdown(f"### 📅 {sd}")
        with st.expander("Ekstra"):
            n = run_query("SELECT note FROM daily_notes WHERE date=%s", (sd,), fetch=True)
            cur_n = n[0]['note'] if n else ""
            nn = st.text_area("Not", cur_n, key=f"n{db_type}")
            if st.button("Kaydet", key=f"sn{db_type}"): 
                run_query("INSERT INTO daily_notes (date,note) VALUES (%s,%s) ON CONFLICT(date) DO UPDATE SET note=%s", (sd, nn, nn), commit=True)
                st.success("ok")
            with st.form(f"q{db_type}"):
                t = st.selectbox("Tip", ["Gelir", "Gider"])
                a = st.number_input("Tutar", 0.0); desc = st.text_input("Açıklama")
                if st.form_submit_button("Ekle"):
                    run_query("INSERT INTO transactions (date,type,category,amount,description) VALUES (%s,%s,%s,%s,%s)",(sd, 'income' if t=='Gelir' else 'expense', 'extra', a, desc), commit=True)
                    st.rerun()
        
        jobs = run_query("SELECT j.*, c.name, c.location FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.date=%s AND j.job_type=%s", (sd, db_type), fetch=True)
        if jobs:
            for j in jobs:
                with st.expander(f"📌 {j['name']}"):
                    st.caption(f"📍 {j['location']}")
                    st.write(f"Al: {j['price_customer']} | Ver: {j['price_worker']}")
                    c_c, c_p = st.columns(2)
                    ic = c_c.checkbox("Tahsilat", bool(j['is_collected']), key=f"cc{j['id']}")
                    iw = c_p.checkbox("Ödeme", bool(j['is_worker_paid']), key=f"cw{j['id']}")
                    
                    if ic!=bool(j['is_collected']): 
                        run_query("UPDATE jobs SET is_collected=%s WHERE id=%s", (int(ic), j['id']), commit=True)
                        st.rerun()
                    if iw!=bool(j['is_worker_paid']): 
                        run_query("UPDATE jobs SET is_worker_paid=%s WHERE id=%s", (int(iw), j['id']), commit=True)
                        st.rerun()
                    
                    asg = "???"
                    if j['assigned_student_id']: 
                        r=run_query("SELECT name FROM students WHERE id=%s", (j['assigned_student_id'],), fetch=True)
                        if r: asg=r[0]['name']
                    elif j['assigned_pro_id']:
                        r=run_query("SELECT name FROM professionals WHERE id=%s", (j['assigned_pro_id'],), fetch=True)
                        if r: asg=r[0]['name']
                    st.info(f"Personel: {asg}")
                    
                    with st.popover("⚙️ Düzenle"):
                        if db_type=='student':
                            stu = run_query("SELECT * FROM students", fetch=True)
                            opts={x['name']:x['id'] for x in stu} if stu else {}
                            sel=st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"as{j['id']}")
                            cp=st.number_input("Ücret", value=j['price_worker'], key=f"cp{j['id']}")
                            if st.button("Kaydet", key=f"sb{j['id']}"):
                                if sel!="-": 
                                    run_query("UPDATE jobs SET assigned_student_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],cp,j['id']), commit=True)
                                    st.rerun()
                        else:
                            pros = run_query("SELECT * FROM professionals", fetch=True)
                            opts={x['name']:x['id'] for x in pros} if pros else {}
                            sel=st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"ap{j['id']}")
                            if sel!="-":
                                pd=run_query("SELECT salary, weekly_salary FROM professionals WHERE id=%s", (opts[sel],), fetch=True)
                                is_sal=(pd[0]['salary']>0 or pd[0]['weekly_salary']>0) if pd else False
                                np=0 if is_sal else j['price_worker']
                                if not is_sal: np=st.number_input("Ücret", value=j['price_worker'], key=f"pnm{j['id']}")
                                if st.button("Kaydet", key=f"pb{j['id']}"):
                                    run_query("UPDATE jobs SET assigned_pro_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],np,j['id']), commit=True)
                                    st.rerun()
                    if st.button("🗑️ Sil", key=f"dl{j['id']}"): 
                        run_query("DELETE FROM jobs WHERE id=%s", (j['id'],), commit=True)
                        st.rerun()
        else: st.info("İş yok")

with tabs[1]: render_cal('Öğrenci')
with tabs[2]: render_cal('Profesyonel')

# --- TAB 4: PROFİLLER ---
with tabs[3]:
    t1,t2,t3 = st.tabs(["Müşteri","Öğrenci","Pro"])
    with t1:
        with st.form("nc"):
            n=st.text_input("Ad"); p=st.text_input("Tel"); l=st.text_input("Konum")
            if st.form_submit_button("Ekle"): 
                run_query("INSERT INTO customers (name,phone,location) VALUES (%s,%s,%s)",(n,p,l), commit=True)
                st.rerun()
        cs=run_query("SELECT * FROM customers", fetch=True)
        sel = st.selectbox("Seç", ["-"]+[x['name'] for x in cs] if cs else [], key="sct")
        if sel!="-":
            cu=run_query("SELECT * FROM customers WHERE name=%s", (sel,), fetch=True)[0]
            with st.expander("Düzenle"):
                with st.form("ec"):
                    en=st.text_input("Ad",cu['name']); ep=st.text_input("Tel",cu['phone']); el=st.text_input("Yer",cu['location'])
                    if st.form_submit_button("Güncelle"): 
                        run_query("UPDATE customers SET name=%s, phone=%s, location=%s WHERE id=%s",(en,ep,el,cu['id']), commit=True)
                        st.rerun()
            st.write("**Geçmiş**")
            js=run_query("SELECT * FROM jobs WHERE customer_id=%s ORDER BY date DESC", (cu['id'],), fetch=True)
            if js:
                for j in js: st.write(f"📅 {j['date']} | 💵 {j['price_customer']}")
    with t2:
        with st.form("ns"):
            n=st.text_input("Ad"); p=st.text_input("Tel")
            if st.form_submit_button("Ekle"): 
                run_query("INSERT INTO students (name,phone) VALUES (%s,%s)",(n,p), commit=True)
                st.rerun()
        ss=run_query("SELECT * FROM students", fetch=True)
        sel = st.selectbox("Seç", ["-"]+[x['name'] for x in ss] if ss else [], key="sst")
        if sel!="-":
            s=run_query("SELECT * FROM students WHERE name=%s", (sel,), fetch=True)[0]
            with st.expander("Düzenle"):
                with st.form("es"):
                    en=st.text_input("Ad",s['name']); ep=st.text_input("Tel",s['phone'])
                    if st.form_submit_button("Güncelle"): 
                        run_query("UPDATE students SET name=%s, phone=%s WHERE id=%s",(en,ep,s['id']), commit=True)
                        st.rerun()
            st.write("**İşler**")
            js=run_query("SELECT * FROM jobs WHERE assigned_student_id=%s ORDER BY date DESC", (s['id'],), fetch=True)
            if js:
                for j in js: st.write(f"📅 {j['date']} | 💰 {j['price_worker']}")
    with t3:
        pt1, pt2 = st.tabs(["Maaşlı", "Ekstra"])
        with pt1:
            with st.form("npm"):
                n=st.text_input("Ad"); p=st.text_input("Tel"); sa=st.number_input("Ay",0.0); we=st.number_input("Hafta",0.0); da=st.number_input("Gün",1)
                if st.form_submit_button("Ekle"): 
                    run_query("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,sa,we,da), commit=True)
                    st.rerun()
            ps=run_query("SELECT * FROM professionals WHERE salary>0 OR weekly_salary>0", fetch=True)
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in ps] if ps else [], key="spt1")
            if sel!="-":
                p=run_query("SELECT * FROM professionals WHERE name=%s", (sel,), fetch=True)[0]
                with st.expander("Düzenle"):
                    with st.form("epm"):
                        en=st.text_input("Ad",p['name']); es=st.number_input("Ay",p['salary']); ew=st.number_input("Hafta",p['weekly_salary']); ed=st.number_input("Gün",p['payment_day'])
                        if st.form_submit_button("Güncelle"): 
                            run_query("UPDATE professionals SET name=%s, salary=%s, weekly_salary=%s, payment_day=%s WHERE id=%s",(en,es,ew,ed,p['id']), commit=True)
                            st.rerun()
                st.write("**İşler**")
                js=run_query("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (p['id'],), fetch=True)
                if js:
                    for j in js: st.write(f"📅 {j['date']}")
        with pt2:
            with st.form("npe"):
                n=st.text_input("Ad"); p=st.text_input("Tel")
                if st.form_submit_button("Ekle"): 
                    run_query("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,0,0,1), commit=True)
                    st.rerun()
            eps=run_query("SELECT * FROM professionals WHERE salary=0 AND weekly_salary=0", fetch=True)
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in eps] if eps else [], key="spt2")
            if sel!="-":
                p=run_query("SELECT * FROM professionals WHERE name=%s", (sel,), fetch=True)[0]
                with st.expander("Düzenle"):
                    with st.form("epe"):
                        en=st.text_input("Ad",p['name']); ep=st.text_input("Tel",p['phone'])
                        if st.form_submit_button("Güncelle"): 
                            run_query("UPDATE professionals SET name=%s, phone=%s WHERE id=%s",(en,ep,p['id']), commit=True)
                            st.rerun()
                st.write("**İşler**")
                js=run_query("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (p['id'],), fetch=True)
                if js:
                    for j in js: st.write(f"📅 {j['date']} | 💰 {j['price_worker']}")

with tabs[4]:
    df = get_report()
    st.dataframe(df, width="stretch")
    if not df.empty: st.download_button("İndir", df.to_csv(index=False).encode('utf-8'), "finans.csv", "text/csv")

with tabs[5]:
    p1,p2,p3 = st.tabs(["Aylık","Haftalık","Parça"])
    with p1:
        cm = f"{datetime.now().month:02d}-{datetime.now().year}"
        ps = run_query("SELECT * FROM professionals WHERE salary>0", fetch=True)
        if ps:
            for p in ps:
                c1,c2=st.columns([3,1])
                c1.write(f"{p['name']} ({p['salary']} TL)")
                chk=run_query("SELECT * FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='monthly'",(p['id'],cm), fetch=True)
                if chk: c2.success("✅")
                else:
                    if c2.button("Öde", key=f"pm{p['id']}"):
                        run_query("INSERT INTO salary_payments (pro_id,amount,payment_date,month_year,payment_type) VALUES (%s,%s,%s,%s,%s)",(p['id'],p['salary'],datetime.now().strftime("%d.%m.%Y"),cm,'monthly'), commit=True)
                        st.rerun()
    with p2:
        today = datetime.now(); wn = today.isocalendar()[1]; wk = f"W{wn}-{today.year}"
        ps = run_query("SELECT * FROM professionals WHERE weekly_salary>0", fetch=True)
        if ps:
            for p in ps:
                c1,c2=st.columns([3,1])
                c1.write(f"{p['name']} ({p['weekly_salary']} TL)")
