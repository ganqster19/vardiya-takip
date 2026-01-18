import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import calendar
import uuid
from datetime import datetime, timedelta, date

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Vardiya ERP Ultimate", page_icon="🚀", layout="wide")

# CSS (Görsel Düzenlemeler)
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

# --- VERİTABANI BAĞLANTISI (CACHED) ---
@st.cache_resource
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=st.secrets["supabase"]["host"],
            database=st.secrets["supabase"]["dbname"],
            user=st.secrets["supabase"]["user"],
            password=st.secrets["supabase"]["password"],
            port=st.secrets["supabase"]["port"],
            cursor_factory=RealDictCursor,
            sslmode='require'
        )
        return conn
    except Exception as e:
        st.error(f"Veritabanı Hatası: {e}")
        st.stop()

def add_column_safe(cursor, table, column, type_def):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type_def}")
    except psycopg2.Error: pass

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Tablolar (Admin tablosu kaldırıldı)
    c.execute('''CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, location TEXT, default_note TEXT, is_regular INTEGER DEFAULT 0, frequency TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, name TEXT, phone TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cash_inflow (id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, amount REAL, description TEXT, customer_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS jobs (id SERIAL PRIMARY KEY, group_id TEXT, date TEXT, customer_id INTEGER, job_type TEXT DEFAULT 'student', status TEXT DEFAULT 'OPEN', assigned_student_id INTEGER, assigned_pro_id INTEGER, price_worker REAL DEFAULT 0, price_customer REAL DEFAULT 0, is_worker_paid INTEGER DEFAULT 0, is_collected INTEGER DEFAULT 0, is_prepaid INTEGER DEFAULT 0, job_note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS availability (user_phone TEXT, date TEXT, is_available INTEGER, UNIQUE(user_phone, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (id SERIAL PRIMARY KEY, date TEXT, description TEXT, amount REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_notes (date TEXT PRIMARY KEY, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS professionals (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, salary REAL DEFAULT 0, payment_day INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS salary_payments (id SERIAL PRIMARY KEY, pro_id INTEGER, amount REAL, payment_date TEXT, month_year TEXT, payment_type TEXT DEFAULT 'monthly')''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, date TEXT, type TEXT, category TEXT, amount REAL, description TEXT, related_id INTEGER)''')

    add_column_safe(c, "professionals", "weekly_salary", "REAL DEFAULT 0")
    add_column_safe(c, "salary_payments", "payment_type", "TEXT DEFAULT 'monthly'")
    
    conn.commit()
    # Bağlantıyı kapatmıyoruz, cache kullanıyoruz.

init_db()

# --- FİNANSAL MOTOR ---
def calculate_obligations():
    conn = get_db_connection()
    c = conn.cursor()
    today = datetime.now().date()
    
    c.execute("SELECT SUM(price_worker) FROM jobs WHERE is_worker_paid=0 AND price_worker > 0")
    res = c.fetchone()
    piece_debt_val = float(res['sum']) if res and res['sum'] else 0.0
    
    salary_debt_val = 0
    c.execute("SELECT id, salary, weekly_salary FROM professionals")
    pros = c.fetchall()
    
    first_day = today.replace(day=1)
    next_month = today.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    curr_month_key = f"{today.month:02d}-{today.year}"
    
    for p in pros:
        if p['salary'] > 0:
            c.execute("SELECT id FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='monthly'", (p['id'], curr_month_key))
            if not c.fetchone(): salary_debt_val += p['salary']
        if p['weekly_salary'] > 0:
            tmp = first_day
            while tmp <= last_day:
                if tmp.weekday() == 0:
                    wk = f"W{tmp.isocalendar()[1]}-{tmp.year}"
                    c.execute("SELECT id FROM salary_payments WHERE pro_id=%s AND month_year=%s AND payment_type='weekly'", (p['id'], wk))
                    if not c.fetchone(): salary_debt_val += p['weekly_salary']
                tmp += timedelta(days=1)
    return piece_debt_val, salary_debt_val

def calculate_monthly_profit(month, year):
    conn = get_db_connection()
    c = conn.cursor()
    date_pattern = f"%.{month:02d}.{year}"
    
    c.execute("SELECT SUM(price_customer) FROM jobs WHERE date LIKE %s", (date_pattern,))
    res = c.fetchone()
    inc_jobs = float(res['sum']) if res and res['sum'] else 0.0
    
    c.execute("SELECT SUM(price_worker) FROM jobs WHERE date LIKE %s", (date_pattern,))
    res = c.fetchone()
    exp_jobs = float(res['sum']) if res and res['sum'] else 0.0
    
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='income' AND date LIKE %s", (date_pattern,))
    res = c.fetchone()
    inc_ext = float(res['sum']) if res and res['sum'] else 0.0
    
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='expense' AND date LIKE %s", (date_pattern,))
    res = c.fetchone()
    exp_ext = float(res['sum']) if res and res['sum'] else 0.0
    
    c.execute("SELECT salary, weekly_salary FROM professionals")
    pros = c.fetchall()
    
    sal_m = 0
    sal_w = 0
    num_days = calendar.monthrange(year, month)[1]
    mondays = sum(1 for day in range(1, num_days + 1) if date(year, month, day).weekday() == 0)
            
    for p in pros:
        if p['salary'] > 0: sal_m += p['salary']
        if p['weekly_salary'] > 0: sal_w += (p['weekly_salary'] * mondays)
            
    return (inc_jobs + inc_ext), (exp_jobs + exp_ext + sal_m + sal_w)

def get_financial_report_df():
    conn = get_db_connection()
    c = conn.cursor()
    data = []
    
    c.execute("SELECT * FROM transactions")
    for t in c.fetchall():
        f = 1 if t['type']=='income' else -1
        data.append({"Tarih": t['date'], "Tür": "Manuel", "Açıklama": t['description'], "Tutar": t['amount']*f})

    c.execute("SELECT j.date, j.price_customer, c.name FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.is_collected=1 AND j.price_customer > 0")
    for j in c.fetchall():
        data.append({"Tarih": j['date'], "Tür": "İş Geliri", "Açıklama": f"{j['name']}", "Tutar": j['price_customer']})
    
    c.execute("SELECT j.date, j.price_worker, j.job_type FROM jobs j WHERE j.is_worker_paid=1 AND j.price_worker > 0")
    for j in c.fetchall():
        data.append({"Tarih": j['date'], "Tür": "Personel", "Açıklama": f"Ödeme ({j['job_type']})", "Tutar": -j['price_worker']})
    
    c.execute("SELECT sp.payment_date, sp.amount, p.name, sp.payment_type FROM salary_payments sp JOIN professionals p ON sp.pro_id=p.id")
    for s in c.fetchall(): 
        lbl = "Hafta" if s['payment_type']=='weekly' else "Ay"
        data.append({"Tarih": s['payment_date'], "Tür": "Maaş", "Açıklama": f"{s['name']} ({lbl})", "Tutar": -s['amount']})
    
    df_res = pd.DataFrame(data)
    if not df_res.empty:
        df_res['Tarih_Obj'] = pd.to_datetime(df_res['Tarih'], format="%d.%m.%Y")
        df_res = df_res.sort_values(by='Tarih_Obj', ascending=False).drop(columns=['Tarih_Obj'])
    return df_res

# --- SESSION ---
if 'wiz_dates' not in st.session_state: st.session_state.wiz_dates = []

# ==========================================
# ANA UYGULAMA (DİREKT BAŞLANGIÇ)
# ==========================================
conn = get_db_connection()
c = conn.cursor()

with st.sidebar:
    st.title("📊 Yönetim")
    st.caption(f"Tarih: {datetime.now().strftime('%d.%m.%Y')}")
    st.divider()
    st.subheader("📅 Kâr Analizi")
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
    if st.button("Yenile (F5)"): st.rerun()

st.title("🚀 İşletme Kontrol Merkezi")

df_rep = get_financial_report_df()
curr_cash = df_rep['Tutar'].sum() if not df_rep.empty else 0.0

c.execute("SELECT SUM(price_customer) as sum FROM jobs WHERE is_collected=0")
res = c.fetchone()
pend_inc = float(res['sum']) if res and res['sum'] else 0.0

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

# --- TAB 1: SİHİRBAZ (TURBO MODE) ---
with tabs[0]:
    st.subheader("⚡ Hızlı İş Planlama")
    c.execute("SELECT * FROM customers")
    custs = c.fetchall()
    c_opts = {c['name']:c['id'] for c in custs}
    
    if c_opts:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                sel_c = st.selectbox("Müşteri", list(c_opts.keys()))
                cid = c_opts[sel_c]
                pay_m = st.radio("Ödeme", ["Peşin", "Veresiye"], horizontal=True)
            with c2:
                d_mode = st.radio("Tarih", ["Aralık", "Manuel"], horizontal=True)
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
                        pick = st.date_input("Tarih")
                        if st.button("Ekle"): 
                            if pick not in st.session_state.wiz_dates: st.session_state.wiz_dates.append(pick)
                    with cl:
                        st.write(f"Seçilen: {len(st.session_state.wiz_dates)}")
                        if st.button("Temizle"): st.session_state.wiz_dates=[]
                    f_dates = st.session_state.wiz_dates

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
                tot_p = day_p * len(f_dates)

        if st.button("Oluştur", type="primary"):
            if not f_dates: st.error("Tarih yok.")
            else:
                gid = str(uuid.uuid4())[:8]
                is_pre = 1 if pay_m.startswith("Peşin") else 0
                is_coll = 1 if is_pre else 0
                
                # --- TOPLU INSERT (OPTIMIZATION) ---
                jobs_to_insert = []
                first_rec = False
                
                for fd in f_dates:
                    ds = fd.strftime("%d.%m.%Y")
                    # Fiyat mantığı
                    if "Toplam" in p_mode:
                        p_cust = tot_p if not first_rec else 0
                    else:
                        p_cust = day_p
                    
                    # Öğrenciler
                    for _ in range(ns):
                        final_p = 0
                        if "Toplam" in p_mode:
                            if not first_rec: final_p = tot_p; first_rec = True
                        else:
                            if (ns+np) > 0: final_p = day_p / (ns+np)

                        jobs_to_insert.append(
                            (gid, ds, cid, 'student', 'OPEN', None, None, ps, final_p, 0, is_coll, is_pre, None)
                        )
                    
                    # Prolar
                    for _ in range(np):
                        final_p = 0
                        if "Toplam" in p_mode:
                            if not first_rec: final_p = tot_p; first_rec = True
                        else:
                            if (ns+np) > 0: final_p = day_p / (ns+np)
                            
                        jobs_to_insert.append(
                            (gid, ds, cid, 'pro', 'OPEN', None, None, pp, final_p, 0, is_coll, is_pre, None)
                        )
                
                if jobs_to_insert:
                    query = """
                        INSERT INTO jobs (group_id, date, customer_id, job_type, status, 
                        assigned_student_id, assigned_pro_id, price_worker, price_customer, 
                        is_worker_paid, is_collected, is_prepaid, job_note) 
                        VALUES %s
                    """
                    execute_values(c, query, jobs_to_insert)
                    conn.commit()
                    st.success(f"{len(jobs_to_insert)} iş hızlıca oluşturuldu! 🚀")
                    st.session_state.wiz_dates = []
    else: st.warning("Önce müşteri ekleyin.")

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
            c.execute("SELECT note FROM daily_notes WHERE date=%s", (sd,))
            n = c.fetchone()
            nn = st.text_area("Not", n['note'] if n else "", key=f"n{db_type}")
            if st.button("Kaydet", key=f"sn{db_type}"): 
                c.execute("INSERT INTO daily_notes (date,note) VALUES (%s,%s) ON CONFLICT(date) DO UPDATE SET note=%s", (sd, nn, nn)); conn.commit(); st.success("ok")
            with st.form(f"q{db_type}"):
                t = st.selectbox("Tip", ["Gelir", "Gider"])
                a = st.number_input("Tutar", 0.0); desc = st.text_input("Açıklama")
                if st.form_submit_button("Ekle"):
                    c.execute("INSERT INTO transactions (date,type,category,amount,description) VALUES (%s,%s,%s,%s,%s)",(sd, 'income' if t=='Gelir' else 'expense', 'extra', a, desc)); conn.commit(); st.rerun()
        
        c.execute("SELECT j.*, c.name, c.location FROM jobs j JOIN customers c ON j.customer_id=c.id WHERE j.date=%s AND j.job_type=%s", (sd, db_type))
        jobs = c.fetchall()
        if jobs:
            for j in jobs:
                with st.expander(f"📌 {j['name']}"):
                    st.caption(f"📍 {j['location']}")
                    st.write(f"Al: {j['price_customer']} | Ver: {j['price_worker']}")
                    c_c, c_p = st.columns(2)
                    ic = c_c.checkbox("Tahsilat", bool(j['is_collected']), key=f"cc{j['id']}")
                    iw = c_p.checkbox("Ödeme", bool(j['is_worker_paid']), key=f"cw{j['id']}")
                    if ic!=bool(j['is_collected']): c.execute("UPDATE jobs SET is_collected=%s WHERE id=%s", (int(ic), j['id'])); conn.commit(); st.rerun()
                    if iw!=bool(j['is_worker_paid']): c.execute("UPDATE jobs SET is_worker_paid=%s WHERE id=%s", (int(iw), j['id'])); conn.commit(); st.rerun()
                    
                    asg = "???"
                    if j['assigned_student_id']: 
                        c.execute("SELECT name FROM students WHERE id=%s", (j['assigned_student_id'],)); r=c.fetchone()
                        if r: asg=r['name']
                    elif j['assigned_pro_id']:
                        c.execute("SELECT name FROM professionals WHERE id=%s", (j['assigned_pro_id'],)); r=c.fetchone()
                        if r: asg=r['name']
                    st.info(f"Personel: {asg}")
                    
                    with st.popover("⚙️ Düzenle"):
                        if db_type=='student':
                            c.execute("SELECT * FROM students"); opts={x['name']:x['id'] for x in c.fetchall()}
                            sel=st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"as{j['id']}")
                            cp=st.number_input("Ücret", value=j['price_worker'], key=f"cp{j['id']}")
                            if st.button("Kaydet", key=f"sb{j['id']}"):
                                if sel!="-": c.execute("UPDATE jobs SET assigned_student_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],cp,j['id'])); conn.commit(); st.rerun()
                        else:
                            c.execute("SELECT * FROM professionals"); opts={x['name']:x['id'] for x in c.fetchall()}
                            sel=st.selectbox("Seç", ["-"]+list(opts.keys()), key=f"ap{j['id']}")
                            if sel!="-":
                                c.execute("SELECT salary, weekly_salary FROM professionals WHERE id=%s", (opts[sel],)); pd=c.fetchone()
                                is_sal=(pd['salary']>0 or pd['weekly_salary']>0)
                                np=0 if is_sal else j['price_worker']
                                if not is_sal: np=st.number_input("Ücret", value=j['price_worker'], key=f"pnm{j['id']}")
                                if st.button("Kaydet", key=f"pb{j['id']}"):
                                    c.execute("UPDATE jobs SET assigned_pro_id=%s, status='ASSIGNED', price_worker=%s WHERE id=%s",(opts[sel],np,j['id'])); conn.commit(); st.rerun()
                    if st.button("🗑️ Sil", key=f"dl{j['id']}"): c.execute("DELETE FROM jobs WHERE id=%s", (j['id'],)); conn.commit(); st.rerun()
        else: st.info("İş yok")

with tabs[1]: render_cal('Öğrenci')
with tabs[2]: render_cal('Profesyonel')

# --- TAB 4: PROFİLLER ---
with tabs[3]:
    t1,t2,t3 = st.tabs(["Müşteri","Öğrenci","Pro"])
    with t1:
        with st.form("nc"):
            n=st.text_input("Ad"); p=st.text_input("Tel"); l=st.text_input("Konum")
            if st.form_submit_button("Ekle"): c.execute("INSERT INTO customers (name,phone,location) VALUES (%s,%s,%s)",(n,p,l)); conn.commit(); st.rerun()
        c.execute("SELECT * FROM customers"); cs=c.fetchall()
        sel = st.selectbox("Seç", ["-"]+[x['name'] for x in cs], key="sct")
        if sel!="-":
            c.execute("SELECT * FROM customers WHERE name=%s", (sel,)); cu=c.fetchone()
            with st.expander("Düzenle"):
                with st.form("ec"):
                    en=st.text_input("Ad",cu['name']); ep=st.text_input("Tel",cu['phone']); el=st.text_input("Yer",cu['location'])
                    if st.form_submit_button("Güncelle"): c.execute("UPDATE customers SET name=%s, phone=%s, location=%s WHERE id=%s",(en,ep,el,cu['id'])); conn.commit(); st.rerun()
            st.write("**Geçmiş**")
            c.execute("SELECT * FROM jobs WHERE customer_id=%s ORDER BY date DESC", (cu['id'],)); js=c.fetchall()
            for j in js: st.write(f"📅 {j['date']} | 💵 {j['price_customer']}")
    with t2:
        with st.form("ns"):
            n=st.text_input("Ad"); p=st.text_input("Tel")
            if st.form_submit_button("Ekle"): c.execute("INSERT INTO students (name,phone) VALUES (%s,%s)",(n,p)); conn.commit(); st.rerun()
        c.execute("SELECT * FROM students"); ss=c.fetchall()
        sel = st.selectbox("Seç", ["-"]+[x['name'] for x in ss], key="sst")
        if sel!="-":
            c.execute("SELECT * FROM students WHERE name=%s", (sel,)); s=c.fetchone()
            with st.expander("Düzenle"):
                with st.form("es"):
                    en=st.text_input("Ad",s['name']); ep=st.text_input("Tel",s['phone'])
                    if st.form_submit_button("Güncelle"): c.execute("UPDATE students SET name=%s, phone=%s WHERE id=%s",(en,ep,s['id'])); conn.commit(); st.rerun()
            st.write("**İşler**")
            c.execute("SELECT * FROM jobs WHERE assigned_student_id=%s ORDER BY date DESC", (s['id'],)); js=c.fetchall()
            for j in js: st.write(f"📅 {j['date']} | 💰 {j['price_worker']}")
    with t3:
        pt1, pt2 = st.tabs(["Maaşlı", "Ekstra"])
        with pt1:
            with st.form("npm"):
                n=st.text_input("Ad"); p=st.text_input("Tel"); sa=st.number_input("Ay",0.0); we=st.number_input("Hafta",0.0); da=st.number_input("Gün",1)
                if st.form_submit_button("Ekle"): c.execute("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,sa,we,da)); conn.commit(); st.rerun()
            c.execute("SELECT * FROM professionals WHERE salary>0 OR weekly_salary>0"); ps=c.fetchall()
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in ps], key="spt1")
            if sel!="-":
                c.execute("SELECT * FROM professionals WHERE name=%s", (sel,)); p=c.fetchone()
                with st.expander("Düzenle"):
                    with st.form("epm"):
                        en=st.text_input("Ad",p['name']); es=st.number_input("Ay",p['salary']); ew=st.number_input("Hafta",p['weekly_salary']); ed=st.number_input("Gün",p['payment_day'])
                        if st.form_submit_button("Güncelle"): c.execute("UPDATE professionals SET name=%s, salary=%s, weekly_salary=%s, payment_day=%s WHERE id=%s",(en,es,ew,ed,p['id'])); conn.commit(); st.rerun()
                st.write("**İşler**")
                c.execute("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (p['id'],)); js=c.fetchall()
                for j in js: st.write(f"📅 {j['date']}")
        with pt2:
            with st.form("npe"):
                n=st.text_input("Ad"); p=st.text_input("Tel")
                if st.form_submit_button("Ekle"): c.execute("INSERT INTO professionals (name,phone,salary,weekly_salary,payment_day) VALUES (%s,%s,%s,%s,%s)",(n,p,0,0,1)); conn.commit(); st.rerun()
            c.execute("SELECT * FROM professionals WHERE salary=0 AND weekly_salary=0"); eps=c.fetchall()
            sel = st.selectbox("Seç", ["-"]+[x['name'] for x in eps], key="spt2")
            if sel!="-":
                c.execute("SELECT * FROM professionals WHERE name=%s", (sel,)); p=c.fetchone()
                with st.expander("Düzenle"):
                    with st.form("epe"):
                        en=st.text_input("Ad",p['name']); ep=st.text_input("Tel",p['phone'])
                        if st.form_submit_button("Güncelle"): c.execute("UPDATE professionals SET name=%s, phone=%s WHERE id=%s",(en,ep,p['id'])); conn.commit(); st.rerun()
                st.write("**İşler**")
                c.execute("SELECT * FROM jobs WHERE assigned_pro_id=%s ORDER BY date DESC", (p['id'],)); js=c.fetchall()
                for j in js: st.write(f"📅 {j['date']} | 💰 {j['price_worker']}")

with tabs[4]:
    df = get_financial_report_df()
    st.dataframe(df, width="stretch")
    if not df.empty: st.download_button("İndir", df.to_csv(index=False).encode('utf-8'), "finans.csv", "text/csv")

with tabs[5]:
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
