import os
import sqlite3
import hashlib
from datetime import datetime, date, timedelta
import pandas as pd
import streamlit as st
import stripe
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure Stripe API
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY", "")
if STRIPE_API_KEY and STRIPE_API_KEY != "sk_test_your_stripe_secret_key_here":
    stripe.api_key = STRIPE_API_KEY
    STRIPE_LIVE = True
else:
    STRIPE_LIVE = False

DB_FILE = "paytracker.db"

# ==========================================
# DATABASE SETUP & HELPER FUNCTIONS
# ==========================================
def init_db():
    """Initialize SQLite database tables for users, subscriptions, and payments."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Users Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    # Subscriptions Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            app_name TEXT NOT NULL,
            category TEXT NOT NULL,
            cost REAL NOT NULL,
            billing_cycle TEXT NOT NULL,
            next_billing_date TEXT NOT NULL,
            reminder_days INTEGER DEFAULT 3,
            status TEXT DEFAULT 'Active',
            stripe_sub_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # Payment History Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sub_id INTEGER NOT NULL,
            app_name TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_date TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, email, password):
    """Register a new user account."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, email, hash_password(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def authenticate_user(username, password):
    """Verify user login credentials."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT id, username, email FROM users WHERE username = ? AND password_hash = ?",
        (username, hash_password(password))
    )
    user = c.fetchone()
    conn.close()
    return user

def seed_demo_data(user_id):
    """Populate realistic subscriptions for instant app demonstration."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = date.today()
    
    demo_subs = [
        (user_id, "Netflix Premium", "Entertainment", 22.99, "Monthly", (today + timedelta(days=2)).strftime("%Y-%m-%d"), 3, "Active", "sub_demo_nflx99"),
        (user_id, "Spotify Duo", "Music", 14.99, "Monthly", (today + timedelta(days=1)).strftime("%Y-%m-%d"), 3, "Active", "sub_demo_spot42"),
        (user_id, "ChatGPT Plus", "AI & Productivity", 20.00, "Monthly", (today + timedelta(days=12)).strftime("%Y-%m-%d"), 5, "Active", "sub_demo_gpt88"),
        (user_id, "GitHub Copilot", "Developer Tools", 10.00, "Monthly", (today + timedelta(days=18)).strftime("%Y-%m-%d"), 3, "Active", "sub_demo_gh10"),
        (user_id, "Adobe Creative Cloud", "Design", 59.99, "Monthly", (today - timedelta(days=1)).strftime("%Y-%m-%d"), 5, "Active", "sub_demo_adbe55"),
    ]
    
    c.executemany("""
        INSERT INTO subscriptions 
        (user_id, app_name, category, cost, billing_cycle, next_billing_date, reminder_days, status, stripe_sub_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, demo_subs)
    conn.commit()
    conn.close()

# ==========================================
# SUBSCRIPTION & PAYMENT OPERATIONS
# ==========================================
def add_subscription(user_id, app_name, category, cost, billing_cycle, next_date, reminder_days):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    fake_stripe_id = f"sub_{hashlib.md5(f'{app_name}{datetime.now()}'.encode()).hexdigest()[:10]}"
    c.execute("""
        INSERT INTO subscriptions 
        (user_id, app_name, category, cost, billing_cycle, next_billing_date, reminder_days, status, stripe_sub_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?)
    """, (user_id, app_name, category, cost, billing_cycle, next_date.strftime("%Y-%m-%d"), reminder_days, fake_stripe_id))
    conn.commit()
    conn.close()

def get_user_subscriptions(user_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY next_billing_date ASC", conn, params=(user_id,))
    conn.close()
    return df

def process_subscription_payment(user_id, sub_row):
    """Pay for a subscription via Stripe API (or Sandbox simulator) and advance the billing date."""
    sub_id = int(sub_row["id"])
    app_name = sub_row["app_name"]
    amount = float(sub_row["cost"])
    cycle = sub_row["billing_cycle"]
    current_due = datetime.strptime(sub_row["next_billing_date"], "%Y-%m-%d").date()

    # 1. Execute API Call if Stripe Key is configured
    tx_id = f"pi_sim_{hashlib.md5(f'{sub_id}{datetime.now()}'.encode()).hexdigest()[:12]}"
    if STRIPE_LIVE:
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),  # Stripe uses cents
                currency="usd",
                description=f"PayTracker Renewal: {app_name}",
                payment_method="pm_card_visa", # Stripe test card token
                confirm=True,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"}
            )
            tx_id = intent.id
        except Exception as e:
            return False, f"Stripe API Error: {str(e)}"

    # 2. Advance Next Billing Date
    base_date = max(current_due, date.today())
    if cycle == "Weekly":
        next_due = base_date + timedelta(days=7)
    elif cycle == "Monthly":
        next_due = base_date + timedelta(days=30)
    else:  # Yearly
        next_due = base_date + timedelta(days=365)

    # 3. Update Database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE subscriptions SET next_billing_date = ?, status = 'Active' WHERE id = ?", (next_due.strftime("%Y-%m-%d"), sub_id))
    c.execute("""
        INSERT INTO payments (user_id, sub_id, app_name, amount, payment_date, transaction_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, sub_id, app_name, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tx_id))
    conn.commit()
    conn.close()
    return True, tx_id

def update_subscription_status(sub_id, new_status):
    """Cancel or Reactivate a subscription."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE subscriptions SET status = ? WHERE id = ?", (new_status, int(sub_id)))
    conn.commit()
    conn.close()

def delete_subscription(sub_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM subscriptions WHERE id = ?", (int(sub_id),))
    conn.commit()
    conn.close()

def get_payment_history(user_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT app_name, amount, payment_date, transaction_id FROM payments WHERE user_id = ? ORDER BY payment_date DESC", conn, params=(user_id,))
    conn.close()
    return df

# ==========================================
# STREAMLIT UI & APPLICATION LOGIC
# ==========================================
st.set_page_config(page_title="PayTracker", page_icon="💳", layout="wide")
init_db()

if "user" not in st.session_state:
    st.session_state.user = None

# ------------------------------------------
# LOGIN & REGISTRATION VIEW
# ------------------------------------------
if st.session_state.user is None:
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.title("💳 PayTracker")
        st.subheader("All-in-One Subscription Manager & Payment Hub")
        
        tab_login, tab_signup = st.tabs(["🔐 Login", "📝 Create Account"])
        
        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submit_login = st.form_submit_button("Sign In", use_container_width=True)
                
                if submit_login:
                    user = authenticate_user(username, password)
                    if user:
                        st.session_state.user = {"id": user[0], "username": user[1], "email": user[2]}
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
                        
        with tab_signup:
            with st.form("signup_form"):
                new_user = st.text_input("Choose a Username")
                new_email = st.text_input("Email Address")
                new_pass = st.text_input("Create Password", type="password")
                load_sample = st.checkbox("Pre-load sample subscriptions for live demo", value=True)
                submit_signup = st.form_submit_button("Create Account", use_container_width=True)
                
                if submit_signup:
                    if not new_user or not new_email or not new_pass:
                        st.warning("Please fill out all fields.")
                    elif create_user(new_user, new_email, new_pass):
                        user = authenticate_user(new_user, new_pass)
                        if load_sample:
                            seed_demo_data(user[0])
                        st.session_state.user = {"id": user[0], "username": user[1], "email": user[2]}
                        st.success("Account created! Logging you in...")
                        st.rerun()
                    else:
                        st.error("Username already exists. Please choose another.")

# ------------------------------------------
# MAIN DASHBOARD VIEW (AUTHENTICATED)
# ------------------------------------------
else:
    user_id = st.session_state.user["id"]
    username = st.session_state.user["username"]
    
    # Sidebar Navigation & Controls
    with st.sidebar:
        st.title("💳 PayTracker")
        st.write(f"Logged in as: **{username}**")
        st.caption(f"Email: {st.session_state.user['email']}")
        
        if STRIPE_LIVE:
            st.success("🟢 Stripe API: Connected")
        else:
            st.info("🧪 Payment Mode: Sandbox Demo (Add Stripe Key in `.env` for live API calls)")
            
        st.divider()
        if st.button("➕ Load Demo Subscriptions", use_container_width=True):
            seed_demo_data(user_id)
            st.toast("Sample subscriptions added!", icon="✅")
            st.rerun()
            
        if st.button("🚪 Log Out", use_container_width=True):
            st.session_state.user = None
            st.rerun()

    # Load User Subscriptions
    subs_df = get_user_subscriptions(user_id)
    today = date.today()

    # ------------------------------------------
    # SMART REMINDER BANNER
    # ------------------------------------------
    st.header("🔔 Subscription Reminders & Alerts")
    reminders_found = False
    
    if not subs_df.empty:
        active_subs = subs_df[subs_df["status"] == "Active"]
        for _, row in active_subs.iterrows():
            due_date = datetime.strptime(row["next_billing_date"], "%Y-%m-%d").date()
            days_left = (due_date - today).days
            
            if days_left < 0:
                reminders_found = True
                st.error(f"⚠️ **OVERDUE:** Your **{row['app_name']}** subscription (${row['cost']:.2f}) was due {abs(days_left)} day(s) ago on {row['next_billing_date']}!")
            elif days_left <= int(row["reminder_days"]):
                reminders_found = True
                st.warning(f"⏰ **UPCOMING RENEWAL:** **{row['app_name']}** (${row['cost']:.2f}) renews in **{days_left} day(s)** on {row['next_billing_date']}.")
                
    if not reminders_found:
        st.success("🎉 You have no overdue or urgent renewals due in the next few days!")

    st.divider()

    # ------------------------------------------
    # METRICS SUMMARY
    # ------------------------------------------
    active_df = subs_df[subs_df["status"] == "Active"] if not subs_df.empty else pd.DataFrame()
    cancelled_df = subs_df[subs_df["status"] == "Cancelled"] if not subs_df.empty else pd.DataFrame()

    monthly_spend = 0.0
    if not active_df.empty:
        for _, r in active_df.iterrows():
            if r["billing_cycle"] == "Weekly":
                monthly_spend += r["cost"] * 4.33
            elif r["billing_cycle"] == "Monthly":
                monthly_spend += r["cost"]
            elif r["billing_cycle"] == "Yearly":
                monthly_spend += r["cost"] / 12.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Active Subscriptions", len(active_df))
    m2.metric("Est. Monthly Spend", f"${monthly_spend:,.2f}")
    m3.metric("Est. Yearly Spend", f"${monthly_spend * 12:,.2f}")
    m4.metric("Cancelled Apps", len(cancelled_df))

    st.divider()

    # ------------------------------------------
    # TABS FOR ACTIONS
    # ------------------------------------------
    tab_manage, tab_add, tab_history = st.tabs([
        "🎛️ Pay or Cancel Subscriptions", 
        "➕ Add New Subscription", 
        "🧾 Payment Receipts"
    ])

    # TAB 1: PAY OR CANCEL SUBSCRIPTIONS
    with tab_manage:
        if subs_df.empty:
            st.info("No subscriptions tracked yet. Click 'Add New Subscription' or 'Load Demo Subscriptions' in the sidebar.")
        else:
            for _, row in subs_df.iterrows():
                sub_id = row["id"]
                status_badge = "🟢 Active" if row["status"] == "Active" else "🔴 Cancelled"
                
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2.5, 2, 2, 2.5])
                    
                    with c1:
                        st.subheader(f"{row['app_name']}")
                        st.caption(f"Category: {row['category']} | API ID: `{row['stripe_sub_id']}`")
                    
                    with c2:
                        st.markdown(f"**${row['cost']:.2f}** / {row['billing_cycle']}")
                        st.caption(f"Status: {status_badge}")
                        
                    with c3:
                        due_dt = datetime.strptime(row["next_billing_date"], "%Y-%m-%d").date()
                        diff = (due_dt - today).days
                        st.write(f"**Next Bill:** {row['next_billing_date']}")
                        if row["status"] == "Active":
                            st.caption(f"({diff} days remaining | Remind {row['reminder_days']}d prior)")
                        else:
                            st.caption("Billing paused")

                    with c4:
                        btn_col1, btn_col2, btn_col3 = st.columns(3)
                        
                        # Pay Now Button
                        with btn_col1:
                            if row["status"] == "Active":
                                if st.button("💳 Pay", key=f"pay_{sub_id}", use_container_width=True, help="Pay renewal now"):
                                    ok, msg = process_subscription_payment(user_id, row)
                                    if ok:
                                        st.toast(f"Paid ${row['cost']:.2f} for {row['app_name']}! Tx: {msg}", icon="💳")
                                        st.rerun()
                                    else:
                                        st.error(msg)
                                        
                        # Cancel / Reactivate Button
                        with btn_col2:
                            if row["status"] == "Active":
                                if st.button("🛑 Cancel", key=f"cancel_{sub_id}", use_container_width=True, help="Cancel subscription"):
                                    update_subscription_status(sub_id, "Cancelled")
                                    st.toast(f"Cancelled {row['app_name']} subscription.", icon="🛑")
                                    st.rerun()
                            else:
                                if st.button("🔄 Resume", key=f"resume_{sub_id}", use_container_width=True, help="Reactivate subscription"):
                                    update_subscription_status(sub_id, "Active")
                                    st.toast(f"Reactivated {row['app_name']}!", icon="🟢")
                                    st.rerun()

                        # Delete Button
                        with btn_col3:
                            if st.button("🗑️", key=f"del_{sub_id}", use_container_width=True, help="Remove from tracker"):
                                delete_subscription(sub_id)
                                st.rerun()

    # TAB 2: ADD NEW SUBSCRIPTION
    with tab_add:
        st.subheader("Add an App Subscription")
        with st.form("add_sub_form", clear_on_submit=True):
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                app_name = st.text_input("App / Service Name (e.g., Netflix, Gym, AWS)")
                category = st.selectbox("Category", ["Entertainment", "Music", "AI & Productivity", "Developer Tools", "Cloud & Hosting", "Gaming", "Design", "Fitness", "Other"])
                cost = st.number_input("Cost ($ USD)", min_value=0.01, value=9.99, step=1.00)
            with f_col2:
                billing_cycle = st.selectbox("Billing Cycle", ["Monthly", "Yearly", "Weekly"])
                next_date = st.date_input("Next Billing Date", value=date.today() + timedelta(days=7))
                reminder_days = st.slider("Remind me how many days before billing?", min_value=1, max_value=14, value=3)

            submitted = st.form_submit_button("➕ Save Subscription", use_container_width=True)
            if submitted:
                if not app_name:
                    st.error("Please enter the name of the app.")
                else:
                    add_subscription(user_id, app_name, category, cost, billing_cycle, next_date, reminder_days)
                    st.success(f"Added **{app_name}** to your subscriptions!")
                    st.rerun()

    # TAB 3: PAYMENT RECEIPTS
    with tab_history:
        st.subheader("In-App Payment History")
        payments_df = get_payment_history(user_id)
        if payments_df.empty:
            st.info("No payments processed yet. Click the '💳 Pay' button on any active subscription to test in-app billing.")
        else:
            st.dataframe(
                payments_df.rename(columns={
                    "app_name": "Subscription",
                    "amount": "Amount Paid ($)",
                    "payment_date": "Timestamp",
                    "transaction_id": "Stripe / Transaction ID"
                }),
                use_container_width=True,
                hide_index=True
            )