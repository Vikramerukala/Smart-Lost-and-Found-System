import streamlit as st
import csv
import os
import json
import uuid
import hashlib
import secrets
import html
import io
import smtplib
import secrets as secret_tokens
from email.message import EmailMessage
from datetime import date, datetime
from urllib.parse import quote
from difflib import SequenceMatcher

try:
    import qrcode
except ImportError:
    qrcode = None

# ============================================================
# IT WARRIORS - SMART CAMPUS LOST & FOUND MANAGEMENT SYSTEM
# ============================================================

st.set_page_config(page_title="IT Warriors | Campus Portal", page_icon="🔎", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOST_FILE = os.path.join(BASE_DIR, "lost_items.csv")
FOUND_FILE = os.path.join(BASE_DIR, "found_items.csv")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
REWARDS_FILE = os.path.join(BASE_DIR, "rewards.json")
CLAIMS_FILE = os.path.join(BASE_DIR, "claim_requests.csv")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Original columns are retained; new columns are appended for backward compatibility.
ITEM_FIELDS = [
    "Item", "Category", "Location", "Date", "Description", "Status", "Image",
    "ItemID", "ReportedBy", "ReporterEmail", "CreatedAt", "ClaimedBy",
    "ClaimedEmail", "ClaimedAt", "StudentName", "Urgent"
]
CLAIM_FIELDS = [
    "ClaimID", "ItemID", "Item", "ClaimantName", "ClaimantEmail", "Proof",
    "RelatedLostID", "Status", "RequestedAt", "ReviewedAt", "ReviewedBy", "AdminNote"
]
CATEGORIES = ["ID Card", "Mobile", "Laptop", "Books", "Bag", "Keys", "Wallet", "Bottle", "Clothing", "Other"]

# ------------------------- Utilities -------------------------
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_text(value):
    return str(value or "").strip()


def write_csv_atomic(filename, fields, rows):
    temp_file = filename + ".tmp"
    try:
        with open(temp_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})
        os.replace(temp_file, filename)
        return True
    except (OSError, csv.Error):
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass
        return False


def read_csv(filename, fields, default_status=""):
    if not os.path.exists(filename):
        return []
    rows = []
    try:
        with open(filename, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                if not safe_text(raw.get("Item")):
                    continue
                row = {field: safe_text(raw.get(field, "")) for field in fields}
                if not row.get("Status"):
                    row["Status"] = default_status
                if not row.get("ItemID"):
                    row["ItemID"] = str(uuid.uuid4())[:8].upper()
                if not row.get("StudentName"):
                    row["StudentName"] = row.get("ReportedBy", "")
                if not row.get("Urgent"):
                    row["Urgent"] = "No"
                rows.append(row)
        return rows
    except (OSError, csv.Error, UnicodeError):
        return []


def load_items(filename, default_status):
    rows = read_csv(filename, ITEM_FIELDS, default_status)
    # Upgrade older CSV headers once so generated IDs stay stable between reruns.
    needs_upgrade = False
    if os.path.exists(filename):
        try:
            with open(filename, "r", newline="", encoding="utf-8-sig") as f:
                old_header = next(csv.reader(f), [])
            needs_upgrade = any(field not in old_header for field in ITEM_FIELDS)
        except (OSError, csv.Error):
            needs_upgrade = False
    if rows and needs_upgrade:
        write_csv_atomic(filename, ITEM_FIELDS, rows)
    return rows


def save_items(filename, rows):
    return write_csv_atomic(filename, ITEM_FIELDS, rows)


def load_claims():
    if not os.path.exists(CLAIMS_FILE):
        return []
    try:
        with open(CLAIMS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            return [
                {field: safe_text(row.get(field, "")) for field in CLAIM_FIELDS}
                for row in csv.DictReader(f)
                if safe_text(row.get("ClaimID"))
            ]
    except (OSError, csv.Error, UnicodeError):
        return []


def save_claims(rows):
    return write_csv_atomic(CLAIMS_FILE, CLAIM_FIELDS, rows)


def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_users(users):
    temp_file = USERS_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=4)
        os.replace(temp_file, USERS_FILE)
        return True
    except OSError:
        return False

# ------------------------- New feature helpers -------------------------
def load_rewards():
    try:
        with open(REWARDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_rewards(data):
    temp = REWARDS_FILE + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp, REWARDS_FILE)
        return True
    except OSError:
        return False


def award_points(email, points, reason, reference):
    if not email:
        return
    rewards = load_rewards()
    user = rewards.setdefault(email.lower(), {"points": 0, "history": []})
    if any(x.get("reference") == reference for x in user.get("history", [])):
        return
    user["points"] = int(user.get("points", 0)) + int(points)
    user.setdefault("history", []).append({"points": points, "reason": reason, "reference": reference, "date": now_text()})
    save_rewards(rewards)


def send_email_notification(to_email, subject, body):
    """SMTP is optional. Configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM."""
    host = os.getenv("SMTP_HOST", "")
    username = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", username)
    port = int(os.getenv("SMTP_PORT", "587"))
    if not all([host, username, password, sender, to_email]):
        return False, "Email is not configured. Set SMTP environment variables to enable notifications."
    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = to_email
        message.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(message)
        return True, "Email sent."
    except Exception as exc:
        return False, f"Email could not be sent: {exc}"


def email_possible_matches(new_report, kind, lost_rows, found_rows):
    if kind == "lost":
        candidates = [(similarity(new_report, x), x) for x in found_rows if x.get("Status") == "Available"]
    else:
        candidates = [(similarity(x, new_report), x) for x in lost_rows if x.get("Status") == "Open"]
    sent = set()
    for score, other in candidates:
        if score >= 55:
            recipients = {safe_text(new_report.get("ReporterEmail")).lower(), safe_text(other.get("ReporterEmail")).lower()}
            recipients.discard("")
            for recipient in recipients - sent:
                subject = "IT WARRIORS: Possible lost-and-found match"
                body = (f"A possible match was found for: {new_report.get('Item')}\n"
                        f"Matching report: {other.get('Item')}\nLocation: {other.get('Location')}\n"
                        f"Similarity estimate: {score}%\n\nPlease sign in to IT WARRIORS and review Smart Matches. "
                        "This is only a suggestion, not proof of ownership.")
                send_email_notification(recipient, subject, body)
                sent.add(recipient)


def create_qr_image(url):
    if qrcode is None:
        return None
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def duplicate_warning(report, existing):
    for old in existing:
        same_category = safe_text(old.get("Category")).lower() == safe_text(report.get("Category")).lower()
        item_score = SequenceMatcher(None, safe_text(old.get("Item")).lower(), safe_text(report.get("Item")).lower()).ratio()
        loc_score = SequenceMatcher(None, safe_text(old.get("Location")).lower(), safe_text(report.get("Location")).lower()).ratio()
        date_same = safe_text(old.get("Date")) == safe_text(report.get("Date"))
        if (item_score >= 0.82 and loc_score >= 0.75 and (date_same or same_category)):
            return old
    return None


# ------------------------- Authentication -------------------------
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 250000).hex()
    return salt + ":" + digest


def verify_password(password, stored):
    try:
        salt, old_hash = stored.split(":", 1)
        password_bytes = password.encode("utf-8")
        salt_bytes = salt.encode("utf-8")
        # 250,000 iterations for new accounts; 200,000 keeps existing accounts compatible.
        for iterations in (250000, 200000):
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password_bytes, salt_bytes, iterations
            ).hex()
            if secrets.compare_digest(candidate, old_hash):
                return True
        return False
    except (ValueError, AttributeError):
        return False


def admin_exists():
    return any(u.get("role") == "admin" for u in load_users().values())


def register_user(name, email, password, role="student"):
    name, email = safe_text(name), safe_text(email).lower()
    users = load_users()
    if len(name) < 2:
        return False, "Enter your full name."
    if "@" not in email or "." not in email.split("@")[-1]:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must contain at least 8 characters."
    if email in users:
        return False, "This email is already registered."
    if role == "admin" and admin_exists():
        return False, "An administrator account already exists."
    users[email] = {"name": name, "email": email, "password": hash_password(password), "role": role}
    return (True, "Registration successful.") if save_users(users) else (False, "Could not save the account.")


def login_user(email, password):
    email = safe_text(email).lower()
    user = load_users().get(email)
    if user and verify_password(password, user.get("password", "")):
        return user
    return None

# ------------------------- Image handling -------------------------
def save_uploaded_image(uploaded):
    if uploaded is None:
        return ""
    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        st.error("Upload a PNG, JPG or WEBP image.")
        return None
    if uploaded.size > 5 * 1024 * 1024:
        st.error("Image must be smaller than 5 MB.")
        return None
    filename = str(uuid.uuid4()) + ext
    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(uploaded.getbuffer())
        return os.path.join("uploads", filename)
    except OSError:
        st.error("Unable to save the image.")
        return None


def show_image(item):
    path = safe_text(item.get("Image"))
    if path:
        full = os.path.abspath(os.path.join(BASE_DIR, path))
        if full.startswith(os.path.abspath(UPLOAD_DIR) + os.sep) and os.path.isfile(full):
            st.image(full, width=220)

# ------------------------- Matching and report logic -------------------------
def is_duplicate(report, existing):
    return any(
        safe_text(x.get("Item")).lower() == safe_text(report.get("Item")).lower()
        and safe_text(x.get("Location")).lower() == safe_text(report.get("Location")).lower()
        and safe_text(x.get("Date")) == safe_text(report.get("Date"))
        for x in existing
    )


def similarity(lost, found):
    lost_name = safe_text(lost.get("Item")).lower()
    found_name = safe_text(found.get("Item")).lower()
    name_score = SequenceMatcher(None, lost_name, found_name).ratio()
    lost_desc = (lost_name + " " + safe_text(lost.get("Description"))).lower()
    found_desc = (found_name + " " + safe_text(found.get("Description"))).lower()
    desc_score = SequenceMatcher(None, lost_desc, found_desc).ratio()
    cat_score = 1.0 if lost.get("Category") == found.get("Category") else 0.0
    loc_score = SequenceMatcher(None, safe_text(lost.get("Location")).lower(), safe_text(found.get("Location")).lower()).ratio()
    return round((0.20 * name_score + 0.45 * desc_score + 0.25 * cat_score + 0.10 * loc_score) * 100, 1)


def add_item(kind, item_name, category, location, item_date, description, image, user, urgent=False):
    filename = LOST_FILE if kind == "lost" else FOUND_FILE
    status = "Open" if kind == "lost" else "Available"
    rows = load_items(filename, status)
    report = {
        "Item": safe_text(item_name), "Category": category, "Location": safe_text(location),
        "Date": str(item_date), "Description": safe_text(description), "Status": status,
        "Image": image or "", "ItemID": str(uuid.uuid4())[:8].upper(),
        "ReportedBy": user.get("name", ""), "ReporterEmail": user.get("email", ""),
        "CreatedAt": now_text(), "ClaimedBy": "", "ClaimedEmail": "", "ClaimedAt": "",
        "StudentName": user.get("name", ""), "Urgent": "Yes" if urgent else "No",
    }
    duplicate = duplicate_warning(report, rows)
    if duplicate:
        return False, f"Possible duplicate detected: report {duplicate.get('ItemID')} by {duplicate.get('StudentName') or duplicate.get('ReportedBy')}. Review existing reports before submitting."
    rows.append(report)
    if save_items(filename, rows):
        return True, "Your report was submitted successfully."
    return False, "Unable to save the report. Check folder permissions."


def make_claim(found, claimant, related_lost_id, proof):
    claims = load_claims()
    if found.get("Status") != "Available":
        return False, "This item is no longer available."
    if found.get("ReporterEmail", "").lower() == claimant.get("email", "").lower():
        return False, "You cannot claim an item that you reported as found."
    duplicate = any(c.get("ItemID") == found.get("ItemID") and c.get("ClaimantEmail", "").lower() == claimant.get("email", "").lower() and c.get("Status") == "Pending" for c in claims)
    if duplicate:
        return False, "You already have a pending claim for this item."
    claims.append({
        "ClaimID": str(uuid.uuid4())[:8].upper(), "ItemID": found.get("ItemID", ""),
        "Item": found.get("Item", ""), "ClaimantName": claimant.get("name", ""),
        "ClaimantEmail": claimant.get("email", ""), "Proof": safe_text(proof),
        "RelatedLostID": related_lost_id or "", "Status": "Pending", "RequestedAt": now_text(),
        "ReviewedAt": "", "ReviewedBy": "", "AdminNote": ""
    })
    return (True, "Claim request sent to the administrator.") if save_claims(claims) else (False, "Unable to save your claim request.")

# ------------------------- Session and data refresh -------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user" not in st.session_state:
    st.session_state.current_user = None

# Reload files on every Streamlit rerun so updates from another session are visible.
lost_items = load_items(LOST_FILE, "Open")
found_items = load_items(FOUND_FILE, "Available")
claims = load_claims()

# ------------------------- Styling -------------------------
st.markdown("""
<style>

.stApp {
    background: linear-gradient(
        135deg,
        #eef2ff,
        #f5f3ff,
        #e0f2fe
    );
}

[data-testid="stSidebar"] {
    background: linear-gradient(
        180deg,
        #101b3f,
        #243b78,
        #512da8
    );
}

[data-testid="stSidebar"] * {
    color: white;
}

/* Welcome card */

.welcome-card {
    background: linear-gradient(
        135deg,
        #dbeafe,
        #c4b5fd,
        #fbcfe8
    );
    padding: 18px 10px;
    margin: 15px 0;
    border-radius: 18px;
    text-align: center;
    border: 2px solid white;
    box-shadow: 0 5px 18px rgba(0,0,0,0.2);
}

.welcome-icon {
    font-size: 30px;
}

.welcome-title {
    color: #312e81 !important;
    font-size: 17px;
    font-weight: 900;
}

.welcome-name {
    color: #1e3a8a !important;
    font-size: 19px;
    font-weight: 900;
    margin-top: 6px;
    overflow-wrap: anywhere;
}

.welcome-role {
    display: inline-block;
    background: white;
    color: #6d28d9 !important;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 900;
    margin-top: 10px;
}

/* Sidebar brand */

.sidebar-brand-card {
    margin-top: 15px;
    padding: 18px 10px;
    text-align: center;
    border-radius: 16px;
    background: linear-gradient(
        135deg,
        #fef3c7,
        #fde68a,
        #fed7aa
    );
    border: 1px solid #f59e0b;
}

.sidebar-brand-card,
.sidebar-brand-card * {
    color: #422006 !important;
}

/* Buttons */

.stButton > button {
    background: linear-gradient(
        90deg,
        #4f46e5,
        #9333ea
    );
    color: white !important;
    border: none;
    border-radius: 12px;
    padding: 10px 20px;
    font-weight: bold;
    transition: 0.3s;
}

.stButton > button:hover {
    background: linear-gradient(
        90deg,
        #7c3aed,
        #2563eb
    );
    transform: scale(1.02);
}

/* Form buttons */

.stFormSubmitButton > button {
    background: linear-gradient(
        90deg,
        #059669,
        #0d9488
    );
    color: white !important;
    border-radius: 12px;
    font-weight: bold;
}

/* Headings */

h1 {
    color: #312e81;
    font-weight: 900;
}

h2, h3 {
    color: #4338ca;
}

/* Dashboard cards */

[data-testid="stMetric"] {
    background: linear-gradient(
        135deg,
        #ffffff,
        #dbeafe,
        #ede9fe
    );
    padding: 22px;
    border-radius: 18px;
    border: 1px solid #c4b5fd;
    box-shadow: 0 5px 15px rgba(0,0,0,0.08);
}

[data-testid="stMetricLabel"] {
    color: #4338ca;
    font-weight: bold;
}

[data-testid="stMetricValue"] {
    color: #4f46e5;
    font-weight: 900;
}

/* Report cards */

[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255,255,255,0.8);
    border-radius: 16px;
    border: 1px solid #c7d2fe;
}

/* Progress */

.stProgress > div > div > div {
    background: linear-gradient(
        90deg,
        #4f46e5,
        #06b6d4
    );
}

/* Footer */

.site-footer {
    width: 100%;
    box-sizing: border-box;
    padding: 25px 15px;
    margin: 10px 0 18px;
    border-radius: 18px;
    text-align: center;
    color: white !important;
    background: linear-gradient(
        110deg,
        #0f766e,
        #2563eb,
        #6d28d9
    );
    box-shadow: 0 8px 22px rgba(31,41,55,0.18);
}

.site-footer * {
    color: white !important;
}

.footer-title {
    font-size: 25px;
    font-weight: 900;
}

.footer-description {
    font-size: 14px;
    margin: 10px;
}

.footer-highlight {
    font-size: 13px;
    font-weight: 700;
}

.footer-tagline {
    font-size: 13px;
    font-weight: 600;
}



/* Extra UI polish: consistent inputs, tabs, forms and cards. */
.stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"],
.stDateInput input, .stNumberInput input {
    border-radius: 11px !important;
    border: 1px solid #c7d2fe !important;
    background: rgba(255,255,255,.92) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 2px rgba(99,102,241,.15) !important;
}
button[data-baseweb="tab"] { border-radius: 10px 10px 0 0; font-weight: 700; }
[data-testid="stForm"] {
    background: rgba(255,255,255,.72); border: 1px solid #c7d2fe;
    border-radius: 18px; padding: 18px;
}
[data-testid="stVerticalBlockBorderWrapper"] { transition: box-shadow .18s ease; }
[data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: 0 8px 22px rgba(79,70,229,.12); }
.stAlert { border-radius: 13px; }
.site-footer a, .footer a { color: #ffffff !important; font-weight: 800; text-decoration: underline; text-underline-offset: 3px; }

/* Feature-page title and footer use the original IT Warriors theme colors. */
.main-title {
    padding: 18px;
    border-radius: 18px;
    text-align: center;
    color: #ffffff !important;
    background: linear-gradient(110deg, #0f766e, #2563eb, #6d28d9);
    box-shadow: 0 8px 24px rgba(31, 41, 55, 0.18);
}
.main-title h1, .main-title p {
    color: #ffffff !important;
}
.welcome-card h3 {
    color: #1e3a8a !important;
}
.footer {
    background: linear-gradient(110deg, #0f766e, #2563eb, #6d28d9);
    padding: 25px 15px;
    border-radius: 16px;
    text-align: center;
    margin-top: 15px;
    margin-bottom: 15px;
    box-shadow: 0 8px 22px rgba(31, 41, 55, 0.18);
}
.footer, .footer * {
    color: #ffffff !important;
}
.footer h2 {
    font-size: 25px;
    font-weight: 900;
    margin-bottom: 10px;
}
.footer p {
    font-size: 14px;
    margin: 8px 0;
}

</style>
""", unsafe_allow_html=True)

# ------------------------- Login / Registration -------------------------
if not st.session_state.logged_in:
    st.markdown('<div class="main-title"><h1>🔎 IT WARRIORS</h1><p>Smart Campus Lost & Found Management System</p></div>', unsafe_allow_html=True)
    st.write("Report missing belongings, discover found items and track claims from one campus portal.")
    login_tab, student_tab, admin_tab = st.tabs(["🔐 Login", "📝 Student Registration", "👑 First Admin Setup"])

    with login_tab:
        st.subheader("Welcome back")
        with st.form("login_form"):
            email = st.text_input("Email address", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submit_login = st.form_submit_button("Login", type="primary", use_container_width=True)
        if submit_login:
            user = login_user(email, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.current_user = user
                st.rerun()
            else:
                st.error("Incorrect email or password.")

    with student_tab:
        st.subheader("Create student account")
        with st.form("student_register_form"):
            reg_name = st.text_input("Full name", key="reg_name")
            reg_email = st.text_input("Email address", key="reg_email")
            reg_password = st.text_input("Password (minimum 8 characters)", type="password", key="reg_password")
            reg_confirm = st.text_input("Confirm password", type="password", key="reg_confirm")
            submit_reg = st.form_submit_button("Register as Student", use_container_width=True)
        if submit_reg:
            if reg_password != reg_confirm:
                st.error("Passwords do not match.")
            else:
                ok, message = register_user(reg_name, reg_email, reg_password, "student")
                (st.success if ok else st.error)(message)

    with admin_tab:
        if admin_exists():
            st.warning("The first administrator account has already been created. Ask that administrator for access.")
        else:
            st.warning("Initial setup only: the first account created here receives administrator access. Do not expose this setup publicly.")
            with st.form("first_admin_form"):
                a_name = st.text_input("Administrator name")
                a_email = st.text_input("Administrator email")
                a_password = st.text_input("Administrator password", type="password")
                a_confirm = st.text_input("Confirm password", type="password")
                create_admin = st.form_submit_button("Create First Admin", use_container_width=True)
            if create_admin:
                if a_password != a_confirm:
                    st.error("Passwords do not match.")
                else:
                    ok, message = register_user(a_name, a_email, a_password, "admin")
                    (st.success if ok else st.error)(message)
    st.stop()

current_user = st.session_state.current_user or {}
user_email = safe_text(current_user.get("email")).lower()
is_admin = current_user.get("role") == "admin"

# ------------------------- Sidebar and navigation -------------------------
st.sidebar.markdown("<h1 style='text-align:center;'>🎓 IT WARRIORS</h1><p style='text-align:center;'>Campus Portal</p>", unsafe_allow_html=True)
st.sidebar.markdown(f"<div class='welcome-card'><div>👋 Welcome</div><h3>{current_user.get('name','Student')}</h3><b>{'ADMINISTRATOR' if is_admin else 'STUDENT'}</b></div>", unsafe_allow_html=True)
menu_options = ["Dashboard", "Report Lost Item", "Report Found Item", "Search Items", "Smart Matches", "Campus Map", "QR Reporting", "Rewards", "My Activity"]
if is_admin:
    menu_options += ["Claim Requests", "Manage Reports", "Analytics", "Registered Users", "Notification Settings"]
try:
    requested_page = st.query_params.get("page", "Dashboard")
except Exception:
    try:
        legacy_params = st.experimental_get_query_params()
        requested_page = legacy_params.get("page", ["Dashboard"])[0]
    except Exception:
        requested_page = "Dashboard"
menu_index = menu_options.index(requested_page) if requested_page in menu_options else 0
menu = st.sidebar.radio("MAIN MENU", menu_options, index=menu_index)
st.sidebar.divider()
if st.sidebar.button("🚪 Logout", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.rerun()
st.sidebar.caption("B.Tech IT Warriors | Built with Python + Streamlit")

st.markdown('<div class="main-title"><h1>🔎 IT WARRIORS</h1><p>Smart Campus Lost & Found Management System</p></div>', unsafe_allow_html=True)
st.caption(f"Signed in as {user_email} · Last refreshed: {datetime.now().strftime('%d %b %Y, %I:%M %p')}")

# ------------------------- Dashboard -------------------------
if menu == "Dashboard":
    st.header("📊 Campus Dashboard")
    total_lost = len(lost_items)
    total_found = len(found_items)
    recovered = sum(x.get("Status") == "Recovered" for x in lost_items)
    available = sum(x.get("Status") == "Available" for x in found_items)
    claimed = sum(x.get("Status") == "Claimed" for x in found_items)
    pending = sum(x.get("Status") == "Pending" for x in claims)
    c1, c2, c3 = st.columns(3)
    c1.metric("📕 Lost Reports", total_lost)
    c2.metric("🎒 Found Reports", total_found)
    c3.metric("✅ Recovered", recovered)
    c4, c5, c6 = st.columns(3)
    c4.metric("🔎 Available Items", available)
    c5.metric("🤝 Claimed Items", claimed)
    c6.metric("⏳ Pending Claims", pending)
    st.subheader("📈 Recovery progress")
    rate = (recovered / total_lost * 100) if total_lost else 0
    st.write(f"Lost-item recovery rate: **{rate:.1f}%**")
    st.progress(int(rate))
    st.info("Tip: Open Smart Matches to compare lost reports with available found items.")
    left, right = st.columns(2)
    with left:
        st.subheader("Latest lost reports")
        for item in reversed(lost_items[-4:]):
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Category']}" + (" 🚨 URGENT" if item.get("Urgent") == "Yes" else ""))
                st.caption(f"{item['Location']} · {item['Date']} · {item['Status']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')}")
    with right:
        st.subheader("Latest found items")
        for item in reversed(found_items[-4:]):
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Category']}" + (" 🚨 URGENT" if item.get("Urgent") == "Yes" else ""))
                st.caption(f"{item['Location']} · {item['Date']} · {item['Status']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')}")

# ------------------------- Report lost -------------------------
elif menu == "Report Lost Item":
    st.header("📝 Report a Lost Item")
    with st.form("lost_report_form", clear_on_submit=True):
        st.text_input("Student Name", value=safe_text(current_user.get("name")) or "Student", disabled=True, key="lost_student_name")
        name = st.text_input("Item name", placeholder="Example: Blue college ID card")
        category = st.selectbox("Category", CATEGORIES, key="lost_cat")
        location = st.text_input("Where did you lose it?", placeholder="Example: Library, Block A")
        item_date = st.date_input("Date lost", value=date.today(), max_value=date.today())
        description = st.text_area("Description / identifying details", placeholder="Brand, colour, stickers, marks, contents (do not post sensitive numbers)")
        urgent = st.checkbox("Mark as urgent (for important items such as ID card, phone or keys)")
        photo = st.file_uploader("Item photo (optional, max 5 MB)", type=["png", "jpg", "jpeg", "webp"], key="lost_photo")
        submitted = st.form_submit_button("Submit Lost Report", type="primary")
    if submitted:
        if not name.strip() or not location.strip():
            st.error("Item name and location are required.")
        else:
            image_path = save_uploaded_image(photo)
            if image_path is not None:
                ok, message = add_item("lost", name, category, location, item_date, description, image_path, current_user, urgent)
                (st.success if ok else st.error)(message)
                if ok:
                    email_possible_matches(load_items(LOST_FILE, "Open")[-1], "lost", load_items(LOST_FILE, "Open"), load_items(FOUND_FILE, "Available"))
                    st.rerun()
    st.subheader("Your recent lost reports")
    mine = [x for x in lost_items if x.get("ReporterEmail", "").lower() == user_email]
    if not mine:
        st.info("You have not submitted a lost-item report yet.")
    for item in reversed(mine):
        with st.container(border=True):
            st.write(f"**{item['Item']}** · {item['Status']}")
            st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · Report ID: {item['ItemID']}")
            show_image(item)

# ------------------------- Report found -------------------------
elif menu == "Report Found Item":
    st.header("🎒 Report a Found Item")
    with st.form("found_report_form", clear_on_submit=True):
        st.text_input("Student Name", value=safe_text(current_user.get("name")) or "Student", disabled=True, key="found_student_name")
        name = st.text_input("What item did you find?", placeholder="Example: Black wallet")
        category = st.selectbox("Category", CATEGORIES, key="found_cat")
        location = st.text_input("Where did you find it?", placeholder="Example: College canteen")
        item_date = st.date_input("Date found", value=date.today(), max_value=date.today())
        description = st.text_area("Describe the item", placeholder="Colour, brand, visible marks. Do not publish private contents.")
        urgent = st.checkbox("Mark as urgent / high-priority found item")
        photo = st.file_uploader("Found item photo (optional, max 5 MB)", type=["png", "jpg", "jpeg", "webp"], key="found_photo")
        submitted = st.form_submit_button("Submit Found Report", type="primary")
    if submitted:
        if not name.strip() or not location.strip():
            st.error("Item name and location are required.")
        else:
            image_path = save_uploaded_image(photo)
            if image_path is not None:
                ok, message = add_item("found", name, category, location, item_date, description, image_path, current_user, urgent)
                (st.success if ok else st.error)(message)
                if ok:
                    email_possible_matches(load_items(FOUND_FILE, "Available")[-1], "found", load_items(LOST_FILE, "Open"), load_items(FOUND_FILE, "Available"))
                    st.rerun()
    st.subheader("Available found reports")
    for item in reversed(found_items):
        if item.get("Status") == "Available":
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Category']}" + (" 🚨 URGENT" if item.get("Urgent") == "Yes" else ""))
                st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · Report ID: {item['ItemID']}")
                show_image(item)

# ------------------------- Search -------------------------
elif menu == "Search Items":
    st.header("🔍 Search Campus Items")
    query = st.text_input("Search by item, category, location or description", placeholder="ID card, library, black bag...")
    col1, col2 = st.columns(2)
    kind_filter = col1.selectbox("Report type", ["All", "Lost", "Found"])
    status_filter = col2.selectbox("Status", ["All", "Open", "Available", "Pending", "Claimed", "Recovered"])
    if query.strip():
        all_rows = []
        if kind_filter in ("All", "Lost"):
            all_rows += [("Lost", x) for x in lost_items]
        if kind_filter in ("All", "Found"):
            all_rows += [("Found", x) for x in found_items]
        terms = query.lower().split()
        results = [(kind, x) for kind, x in all_rows if all(term in " ".join(str(x.get(k, "")) for k in ["Item", "Category", "Location", "Description"]).lower() for term in terms) and (status_filter == "All" or x.get("Status") == status_filter)]
        st.write(f"**{len(results)} result(s) found**")
        for kind, item in results:
            with st.container(border=True):
                st.write(f"**{kind}: {item['Item']}**" + (" 🚨 URGENT" if item.get("Urgent") == "Yes" else ""))
                st.write(f"Category: {item['Category']} · Location: {item['Location']} · Status: {item['Status']}")
                st.caption(f"Date: {item['Date']} · Reported by: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · Report ID: {item['ItemID']}")
                show_image(item)
    else:
        st.info("Enter a keyword to search campus reports.")

# ------------------------- Smart matches -------------------------
elif menu == "Smart Matches":
    st.header("🤖 Smart Lost & Found Matches")
    active_lost = [x for x in lost_items if x.get("Status") == "Open"]
    active_found = [x for x in found_items if x.get("Status") == "Available"]
    if not active_lost or not active_found:
        st.info("Matching needs at least one open lost report and one available found report.")
    else:
        threshold = st.slider("Minimum similarity score", 20, 90, 35, 5)
        count = 0
        for lost in active_lost:
            matches = sorted([(similarity(lost, found), found) for found in active_found], key=lambda x: x[0], reverse=True)
            matches = [(score, found) for score, found in matches if score >= threshold][:3]
            if matches:
                st.subheader(f"Lost: {lost['Item']} · {lost['Location']}")
                st.caption(f"Reported by: {lost.get('StudentName') or lost.get('ReportedBy', 'Unknown')}" + (" · 🚨 URGENT" if lost.get("Urgent") == "Yes" else ""))
                for score, found in matches:
                    count += 1
                    with st.container(border=True):
                        col1, col2 = st.columns([2, 1])
                        with col1:
                            st.write(f"**Possible found item: {found['Item']}**")
                            st.write(f"Category: {found['Category']} · Location: {found['Location']}")
                            st.caption(f"Found by: {found.get('StudentName') or found.get('ReportedBy', 'Unknown')} · Report ID: {found['ItemID']}")
                            st.progress(int(score))
                            st.write(f"Similarity estimate: **{score}%**")
                        with col2:
                            show_image(found)
                        st.caption("This is a text-based suggestion, not proof that the item belongs to the student.")
        if count == 0:
            st.info("No matches above the selected threshold. Try lowering it.")

# ------------------------- QR reporting -------------------------
elif menu == "QR Reporting":
    st.header("📱 QR Code Reporting")
    st.write("Print these codes on campus notice boards. Scanning opens the selected reporting form.")
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
    base_url = st.text_input("Public app URL (use your deployed/LAN URL for other phones)", value=base_url)
    lost_url = base_url.rstrip("/") + "/?page=" + quote("Report Lost Item")
    found_url = base_url.rstrip("/") + "/?page=" + quote("Report Found Item")
    q1, q2 = st.columns(2)
    for column, label, url, filename in [
        (q1, "Report a Lost Item", lost_url, "it_warriors_lost_qr.png"),
        (q2, "Report a Found Item", found_url, "it_warriors_found_qr.png"),
    ]:
        with column:
            st.subheader(label)
            qr_image = create_qr_image(url)
            if qr_image is None:
                st.warning("QR generation package is not installed. Run: python -m pip install qrcode[pil]")
            else:
                st.image(qr_image, width=230)
                st.download_button("⬇️ Download QR", data=qr_image.getvalue(), file_name=filename, mime="image/png", key="download_" + filename)
    st.info("A localhost URL works only on this computer. For campus-wide scanning, deploy the app or use your computer's reachable LAN IP and configure APP_BASE_URL.")

# ------------------------- Campus map -------------------------
elif menu == "Campus Map":
    st.header("🗺️ Jayamukhi College Campus Map")
    st.write("Find the college campus on the map. Report forms no longer ask students for latitude or longitude.")
    college_query = "Jayamukhi Institute of Technological Sciences, Chennaraopet Mandal, Narsampet, Warangal, Telangana 506332"
    map_url = "https://www.google.com/maps?q=" + quote(college_query) + "&output=embed"
    directions_url = "https://www.google.com/maps/search/?api=1&query=" + quote(college_query)
    st.markdown(
        f'''<div style="background:linear-gradient(110deg,#0f766e,#2563eb,#6d28d9);padding:18px 20px;border-radius:16px;color:white;margin:8px 0 16px;">
          <div style="font-size:20px;font-weight:900;">🎓 Jayamukhi Institute of Technological Sciences</div>
          <div style="margin-top:6px;">Chennaraopet Mandal, Narsampet, Warangal, Telangana – 506332</div>
        </div>''',
        unsafe_allow_html=True,
    )
    st.components.v1.html(
        f'''<iframe src="{map_url}" width="100%" height="520" style="border:0;border-radius:16px;" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe>''',
        height=535,
        scrolling=False,
    )
    st.link_button("📍 Open directions in Google Maps", directions_url, use_container_width=True)
    st.info("This displays the college's public map location. It is not an indoor map of individual blocks, classrooms or labs. For a detailed campus layout, upload an official campus map image and mark each building on it.")

# ------------------------- Rewards -------------------------
elif menu == "Rewards":
    st.header("🏆 Student Reward Points")
    rewards = load_rewards()
    my_reward = rewards.get(user_email, {"points": 0, "history": []})
    c1, c2 = st.columns(2)
    c1.metric("Your reward points", int(my_reward.get("points", 0)))
    c2.metric("Successful contributions", len(my_reward.get("history", [])))
    st.caption("Points are awarded after an administrator approves a return: 10 points to the student who reported the found item and 5 points to the student who successfully recovers their lost item.")
    st.subheader("Your points history")
    if not my_reward.get("history"):
        st.info("No reward points yet. Help return found items to their owners.")
    for entry in reversed(my_reward.get("history", [])):
        st.write(f"+{entry.get('points', 0)} points · {entry.get('reason', '')}")
        st.caption(entry.get("date", ""))
    st.divider()
    st.subheader("Campus leaderboard")
    leaderboard = sorted([(u.get("name", email), int(rewards.get(email, {}).get("points", 0))) for email, u in load_users().items() if u.get("role") == "student"], key=lambda x: x[1], reverse=True)
    for rank, (student_name, points) in enumerate(leaderboard[:10], 1):
        st.write(f"**{rank}. {student_name}** — {points} points")

# ------------------------- Student activity and claims -------------------------
elif menu == "My Activity":
    st.header("👤 My Student Activity")
    my_lost = [x for x in lost_items if x.get("ReporterEmail", "").lower() == user_email]
    my_found = [x for x in found_items if x.get("ReporterEmail", "").lower() == user_email]
    my_claims = [x for x in claims if x.get("ClaimantEmail", "").lower() == user_email]
    a, b, c = st.columns(3)
    a.metric("My lost reports", len(my_lost))
    b.metric("My found reports", len(my_found))
    c.metric("My claims", len(my_claims))
    tab1, tab2, tab3 = st.tabs(["Lost reports", "Found reports", "Claim history"])
    with tab1:
        for item in my_lost:
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Status']}")
                st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · ID: {item['ItemID']}")
    with tab2:
        for item in my_found:
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Status']}")
                st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · ID: {item['ItemID']}")
    with tab3:
        if not my_claims:
            st.info("You have not made any claim requests yet.")
        for claim in reversed(my_claims):
            with st.container(border=True):
                status = claim.get("Status", "Pending")
                st.write(f"**{claim['Item']}** · {status}")
                st.caption(f"Requested: {claim['RequestedAt']} · Claim ID: {claim['ClaimID']}")
                if claim.get("AdminNote"):
                    st.write("Administrator note:", claim["AdminNote"])

    st.divider()
    st.subheader("Claim an available found item")
    available_items = [x for x in found_items if x.get("Status") == "Available" and x.get("ReporterEmail", "").lower() != user_email]
    if not available_items:
        st.info("There are no available items to claim right now.")
    else:
        choices = {f"{x['Item']} · {x['Location']} · ID {x['ItemID']}": x for x in available_items}
        with st.form("claim_request_form", clear_on_submit=True):
            selected_label = st.selectbox("Select the item you believe is yours", list(choices.keys()))
            related_options = {"Not linked to a lost report": ""}
            for x in my_lost:
                if x.get("Status") == "Open":
                    related_options[f"{x['Item']} · {x['Location']} · ID {x['ItemID']}"] = x["ItemID"]
            related_label = st.selectbox("Link to your lost report (optional)", list(related_options.keys()))
            proof = st.text_area("Ownership details", placeholder="Describe a private identifying detail that is not visible in the public photo.", help="Do not enter passwords, full ID numbers, bank details or other highly sensitive information.")
            send_claim = st.form_submit_button("Send Claim Request", type="primary")
        if send_claim:
            if len(proof.strip()) < 10:
                st.error("Provide at least 10 characters of identifying details so an administrator can review your request.")
            else:
                ok, message = make_claim(choices[selected_label], current_user, related_options[related_label], proof)
                (st.success if ok else st.error)(message)
                if ok:
                    st.rerun()

# ------------------------- Admin: claim queue -------------------------
elif menu == "Claim Requests":
    if not is_admin:
        st.error("Administrator access required.")
        st.stop()
    st.header("🧾 Claim Review Center")
    pending_claims = [x for x in claims if x.get("Status") == "Pending"]
    st.metric("Pending claim requests", len(pending_claims))
    if not pending_claims:
        st.success("No pending claim requests.")
    for claim in pending_claims:
        found = next((x for x in found_items if x.get("ItemID") == claim.get("ItemID")), None)
        with st.container(border=True):
            st.subheader(f"{claim['Item']} · Claim {claim['ClaimID']}")
            st.write(f"**Claimant:** {claim['ClaimantName']} ({claim['ClaimantEmail']})")
            st.write(f"**Requested:** {claim['RequestedAt']}")
            st.write("**Ownership details supplied:**")
            st.info(claim.get("Proof") or "No details supplied")
            if found:
                st.write(f"**Found report location:** {found['Location']} · **Current status:** {found['Status']}")
                show_image(found)
            else:
                st.error("The original found report could not be located. This claim cannot be approved automatically.")
            related = next((x for x in lost_items if x.get("ItemID") == claim.get("RelatedLostID")), None)
            if related:
                st.write(f"**Linked lost report:** {related['Item']} · {related['Location']} · {related['ReporterEmail']}")
            note = st.text_input("Admin note (optional)", key=f"claim_note_{claim['ClaimID']}")
            verified = st.checkbox("I verified the claimant's ownership details and confirmed the physical handover", key=f"verify_claim_{claim['ClaimID']}")
            col1, col2 = st.columns(2)
            if col1.button("✅ Verify & Approve Return", key=f"approve_{claim['ClaimID']}", disabled=(not found or found.get("Status") != "Available" or not verified)):
                found["Status"] = "Claimed"
                found["ClaimedBy"] = claim["ClaimantName"]
                found["ClaimedEmail"] = claim["ClaimantEmail"]
                found["ClaimedAt"] = now_text()
                claim["Status"] = "Approved"
                claim["ReviewedAt"] = now_text()
                claim["ReviewedBy"] = user_email
                claim["AdminNote"] = safe_text(note)
                # Only mark a linked lost report recovered when it belongs to this claimant.
                if related and related.get("ReporterEmail", "").lower() == claim.get("ClaimantEmail", "").lower() and related.get("Status") == "Open":
                    related["Status"] = "Recovered"
                if save_items(FOUND_FILE, found_items) and save_claims(claims) and save_items(LOST_FILE, lost_items):
                    award_points(found.get("ReporterEmail", ""), 10, "Returned a found item to its owner", "found-return-" + found.get("ItemID", ""))
                    award_points(claim.get("ClaimantEmail", ""), 5, "Successfully recovered a reported lost item", "claim-recovered-" + claim.get("ClaimID", ""))
                    st.success("Claim approved. Found item status is now Claimed.")
                    st.rerun()
                else:
                    st.error("Could not save all changes. Check file permissions and verify the CSV files.")
            if col2.button("❌ Reject Claim", key=f"reject_{claim['ClaimID']}"):
                claim["Status"] = "Rejected"
                claim["ReviewedAt"] = now_text()
                claim["ReviewedBy"] = user_email
                claim["AdminNote"] = safe_text(note) or "Claim request was not approved. Contact the administrator for details."
                if save_claims(claims):
                    st.warning("Claim request rejected.")
                    st.rerun()
                else:
                    st.error("Unable to save the decision.")

# ------------------------- Admin: manage reports -------------------------
elif menu == "Manage Reports":
    if not is_admin:
        st.error("Administrator access required.")
        st.stop()
    st.header("🛠️ Manage Campus Reports")
    lost_tab, found_tab = st.tabs(["Lost reports", "Found reports"])
    with lost_tab:
        for item in list(lost_items):
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Status']}")
                st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · {item.get('ReporterEmail','')} · ID {item['ItemID']}")
                show_image(item)
                opts = ["Open", "Recovered", "Closed"]
                selected = st.selectbox("Status", opts, index=opts.index(item["Status"]) if item["Status"] in opts else 0, key=f"lost_admin_status_{item['ItemID']}")
                c1, c2 = st.columns(2)
                if c1.button("Save status", key=f"save_lost_{item['ItemID']}"):
                    item["Status"] = selected
                    if save_items(LOST_FILE, lost_items):
                        st.success("Lost report updated.")
                        st.rerun()
                    st.error("Could not update report.")
                if c2.button("Delete report", key=f"del_lost_{item['ItemID']}"):
                    lost_items.remove(item)
                    if save_items(LOST_FILE, lost_items):
                        st.success("Lost report deleted.")
                        st.rerun()
                    st.error("Could not delete report.")
    with found_tab:
        for item in list(found_items):
            with st.container(border=True):
                st.write(f"**{item['Item']}** · {item['Status']}")
                st.caption(f"{item['Location']} · {item['Date']} · Student: {item.get('StudentName') or item.get('ReportedBy', 'Unknown')} · {item.get('ReporterEmail','')} · ID {item['ItemID']}")
                if item.get("Status") == "Claimed":
                    st.caption(f"Claimed by: {item.get('ClaimedBy','')} ({item.get('ClaimedEmail','')}) · {item.get('ClaimedAt','')}")
                show_image(item)
                opts = ["Available", "Claimed", "Closed"]
                selected = st.selectbox("Status", opts, index=opts.index(item["Status"]) if item["Status"] in opts else 0, key=f"found_admin_status_{item['ItemID']}")
                c1, c2 = st.columns(2)
                if c1.button("Save status", key=f"save_found_{item['ItemID']}"):
                    item["Status"] = selected
                    if selected != "Claimed":
                        item["ClaimedBy"] = item["ClaimedEmail"] = item["ClaimedAt"] = ""
                    if save_items(FOUND_FILE, found_items):
                        st.success("Found report updated.")
                        st.rerun()
                    st.error("Could not update report.")
                if c2.button("Delete report", key=f"del_found_{item['ItemID']}"):
                    found_items.remove(item)
                    if save_items(FOUND_FILE, found_items):
                        st.success("Found report deleted.")
                        st.rerun()
                    st.error("Could not delete report.")

# ------------------------- Admin: notification settings -------------------------
elif menu == "Notification Settings":
    if not is_admin:
        st.error("Administrator access required.")
        st.stop()
    st.header("✉️ Email Notification Setup")
    st.write("Configure these environment variables on the computer running Streamlit:")
    st.code("SMTP_HOST=smtp.gmail.com\nSMTP_PORT=587\nSMTP_USER=your-email@gmail.com\nSMTP_PASSWORD=your-16-character-app-password\nSMTP_FROM=your-email@gmail.com\nAPP_BASE_URL=http://YOUR-CAMPUS-IP:8501", language="text")
    st.warning("For Gmail, use a Google App Password, not your normal account password. Never paste your email password into the app or share it with students.")
    st.caption("When a new report has a possible match of 55% or higher, the app attempts to email the relevant student. Email is skipped safely if SMTP is not configured.")

# ------------------------- Admin: analytics -------------------------
elif menu == "Analytics":
    if not is_admin:
        st.error("Administrator access required.")
        st.stop()
    st.header("📈 Campus Analytics")

    # CSS charts avoid Streamlit's st.bar_chart() pandas dependency.
    # This works around Windows Application Control blocking pandas DLLs.
    def render_analytics_bars(title, values, bar_gradient):
        st.subheader(title)
        if not values:
            st.info("No data available yet.")
            return

        max_value = max(values.values()) if values else 0
        chart_rows = []
        for label, value in values.items():
            safe_label = html.escape(str(label))
            numeric_value = int(value)
            width = (numeric_value / max_value * 100) if max_value else 0
            min_width = "3px" if numeric_value else "0"
            row = (
                f'<div style="display:grid;grid-template-columns:minmax(125px,190px) 1fr 36px;'
                f'gap:12px;align-items:center;margin:13px 0;">'
                f'<div style="font-size:14px;font-weight:600;color:var(--text-color);'
                f'overflow-wrap:anywhere;">{safe_label}</div>'
                f'<div style="height:14px;background:rgba(148,163,184,.20);'
                f'border-radius:20px;overflow:hidden;">'
                f'<div style="height:100%;width:{width:.1f}%;background:{bar_gradient};'
                f'border-radius:20px;min-width:{min_width};"></div></div>'
                f'<div style="font-size:14px;font-weight:700;text-align:right;'
                f'color:var(--text-color);">{numeric_value}</div></div>'
            )
            chart_rows.append(row)

        chart_html = (
            '<div style="padding:16px 18px;border:1px solid rgba(99,102,241,.20);'
            'border-radius:16px;background:rgba(255,255,255,.62);">'
            + ''.join(chart_rows) + '</div>'
        )
        st.markdown(chart_html, unsafe_allow_html=True)

    statuses = {
        "Lost · Open": sum(x.get("Status") == "Open" for x in lost_items),
        "Lost · Recovered": sum(x.get("Status") == "Recovered" for x in lost_items),
        "Found · Available": sum(x.get("Status") == "Available" for x in found_items),
        "Found · Claimed": sum(x.get("Status") == "Claimed" for x in found_items),
        "Claims · Pending": sum(x.get("Status") == "Pending" for x in claims),
        "Claims · Rejected": sum(x.get("Status") == "Rejected" for x in claims),
    }
    render_analytics_bars(
        "Report and claim status", statuses,
        "linear-gradient(90deg, #4f46e5, #7c3aed, #a855f7)",
    )

    category_counts = {}
    for item in lost_items + found_items:
        category = item.get("Category") or "Other"
        category_counts[category] = category_counts.get(category, 0) + 1
    render_analytics_bars(
        "Reports by category", category_counts,
        "linear-gradient(90deg, #0f766e, #0d9488, #34d399)",
    )

    total_claims = len(claims)
    approved = sum(x.get("Status") == "Approved" for x in claims)
    approval_rate = (approved / total_claims * 100) if total_claims else 0
    st.metric("Claim approval rate", f"{approval_rate:.1f}%")

# ------------------------- Admin: users -------------------------
elif menu == "Registered Users":
    if not is_admin:
        st.error("Administrator access required.")
        st.stop()
    st.header("👥 Registered Users")
    users = load_users()
    students = [u for u in users.values() if u.get("role") == "student"]
    admins = [u for u in users.values() if u.get("role") == "admin"]
    c1, c2 = st.columns(2)
    c1.metric("Students", len(students))
    c2.metric("Administrators", len(admins))
    for user in users.values():
        with st.container(border=True):
            st.write(f"**{user.get('name','')}** · {user.get('role','student').upper()}")
            st.caption(user.get("email", ""))

# ------------------------- Footer -------------------------
st.divider()
st.markdown("""
<div class="footer">
  <h2>🎓 IT WARRIORS</h2>
  <p>Smart Campus Lost & Found Management System</p>
  <b>Find It · Report It · Recover It</b>
  <p>Built with Python and Streamlit</p>
  <p>Contact: <a href="mailto:vikramerukala@gmail.com">vikramerukala@gmail.com</a></p>
</div>
""", unsafe_allow_html=True)