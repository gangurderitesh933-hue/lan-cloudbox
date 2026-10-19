import os
import re
import hmac
import hashlib
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
import streamlit as st

APP_NAME = "LAN CloudBox"
ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CLOUDBOX_DB", str(ROOT / "cloudbox.db")))
LOCAL_DIR = Path(os.getenv("LOCAL_STORAGE_DIR", str(ROOT / "storage")))
STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower().strip()
HDFS_ROOT = os.getenv("HDFS_ROOT", "/cloudbox")
MAX_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))

st.set_page_config(page_title=APP_NAME, page_icon="☁️", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root { --cb-accent:#ff7849; --cb-purple:#a78bfa; }
html, body, [class*="css"] { font-family:'DM Sans',sans-serif; }
.block-container {max-width:1400px;padding-top:1.25rem;padding-bottom:2.5rem;}
h1,h2,h3 {font-family:'Space Grotesk',sans-serif;letter-spacing:-.035em;}
.hero {padding:30px 32px;border-radius:24px;background:radial-gradient(circle at 85% 15%,rgba(167,139,250,.35),transparent 28%),linear-gradient(120deg,#171923 0%,#27203b 55%,#45251f 100%);border:1px solid rgba(255,255,255,.12);color:#fff;margin-bottom:22px;box-shadow:0 18px 50px rgba(20,10,35,.22);}
.hero h1 {color:#fff;font-size:2.5rem;margin:5px 0 8px;}
.hero p {color:#e2e0eb;margin:0;font-size:1.02rem;}
.eyebrow {font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.16em;color:#ffad8d;}
div[data-testid="stMetric"] {background:linear-gradient(145deg,rgba(167,139,250,.10),rgba(255,120,73,.06));border:1px solid rgba(167,139,250,.22);padding:18px;border-radius:18px;}
div[data-testid="stMetricLabel"] {color:#b8b6c8;}
div.stButton > button, div.stDownloadButton > button {border-radius:12px;font-weight:650;min-height:2.6rem;transition:all .15s ease;}
div.stButton > button[kind="primary"] {background:linear-gradient(100deg,#ff7849,#e95e80);border:0;color:white;}
div[data-testid="stFileUploader"] {border:1px dashed #a78bfa;border-radius:16px;padding:10px;background:rgba(167,139,250,.04);}
section[data-testid="stSidebar"] {background:linear-gradient(180deg,#191724,#211c2b);}
section[data-testid="stSidebar"] * {color:#f3f0fa;}
div[data-testid="stTabs"] button {font-weight:650;}
.muted {color:#a7a3b7;font-size:.88rem;}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:rgba(167,139,250,.20)!important;border-radius:17px!important;}
</style>
""", unsafe_allow_html=True)

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            is_shared INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        # Recipient-based sharing; safe to add to existing databases.
        con.execute("""CREATE TABLE IF NOT EXISTS shares(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            shared_by INTEGER NOT NULL,
            shared_with INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(file_id, shared_with),
            FOREIGN KEY(file_id) REFERENCES files(id),
            FOREIGN KEY(shared_by) REFERENCES users(id),
            FOREIGN KEY(shared_with) REFERENCES users(id)
        )""")
        admin = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not admin:
            salt = os.urandom(16).hex()
            pwd = os.getenv("CLOUDBOX_ADMIN_PASSWORD", "Admin@123")
            ph = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 180_000).hex()
            con.execute("INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
                        ("admin", ph, salt, "admin", now()))
        # Admin-uploaded files are course/lab resources: make existing ones visible to students.
        con.execute("UPDATE files SET is_shared=1 WHERE owner_id IN (SELECT id FROM users WHERE role='admin')")
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 180_000).hex()

def record(username, action, details):
    with db() as con:
        con.execute("INSERT INTO activity(username,action,details,created_at) VALUES(?,?,?,?)",
                    (username, action, details[:250], now()))

def get_user(username):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

def authenticate(username, password):
    user = get_user(username.strip())
    if not user:
        return None
    candidate = hash_password(password, user["salt"])
    return user if hmac.compare_digest(candidate, user["password_hash"]) else None

def safe_filename(name):
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._() -]", "_", name).strip(" .")
    return name[:150] or "uploaded_file"

def hdfs_run(args):
    result = subprocess.run(["hdfs", "dfs"] + args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "HDFS command failed").strip())
    return result.stdout.strip()

def user_hdfs_dir(username):
    safe_user = re.sub(r"[^A-Za-z0-9_-]", "_", username)
    return f"{HDFS_ROOT.rstrip('/')}/{safe_user}"

def store_bytes(username, stored_name, data):
    if STORAGE_MODE == "hdfs":
        folder = user_hdfs_dir(username)
        hdfs_run(["-mkdir", "-p", folder])
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / stored_name
            local.write_bytes(data)
            path = f"{folder}/{stored_name}"
            hdfs_run(["-put", "-f", str(local), path])
        return path
    folder = LOCAL_DIR / username
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / stored_name
    path.write_bytes(data)
    return str(path)

def load_bytes(storage_path):
    if STORAGE_MODE == "hdfs":
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / Path(storage_path).name
            hdfs_run(["-get", storage_path, str(target)])
            return target.read_bytes()
    return Path(storage_path).read_bytes()

def remove_stored(storage_path):
    if STORAGE_MODE == "hdfs":
        hdfs_run(["-rm", "-f", storage_path])
    else:
        Path(storage_path).unlink(missing_ok=True)

def list_files(user):
    with db() as con:
        if user["role"] == "admin":
            rows = con.execute("""SELECT f.*,u.username AS owner FROM files f
                JOIN users u ON u.id=f.owner_id ORDER BY f.uploaded_at DESC""").fetchall()
        else:
            rows = con.execute("""SELECT DISTINCT f.*,u.username AS owner FROM files f
                JOIN users u ON u.id=f.owner_id
                LEFT JOIN shares s ON s.file_id=f.id
                WHERE f.owner_id=? OR f.is_shared=1 OR s.shared_with=?
                ORDER BY f.uploaded_at DESC""",
                (user["id"], user["id"])).fetchall()
    return rows

def delete_record(file_id):
    with db() as con:
        con.execute("DELETE FROM shares WHERE file_id=?", (file_id,))
        con.execute("DELETE FROM files WHERE id=?", (file_id,))


def share_with_user(file_id, owner_id, recipient_id):
    if int(owner_id) == int(recipient_id):
        raise ValueError("Choose a different account from your own.")
    with db() as con:
        owner = con.execute("SELECT owner_id FROM files WHERE id=?", (file_id,)).fetchone()
        if not owner or int(owner["owner_id"]) != int(owner_id):
            raise ValueError("Only the file owner can share this file.")
        con.execute("INSERT OR IGNORE INTO shares(file_id,shared_by,shared_with,created_at) VALUES(?,?,?,?)",
                    (file_id, owner_id, recipient_id, now()))

def received_shared_files(user):
    with db() as con:
        return con.execute("""SELECT DISTINCT f.*,u.username AS owner, s.created_at AS shared_at
            FROM files f JOIN users u ON u.id=f.owner_id
            LEFT JOIN shares s ON s.file_id=f.id AND s.shared_with=?
            WHERE (s.shared_with=? OR f.is_shared=1) AND f.owner_id<>?
            ORDER BY COALESCE(s.created_at,f.uploaded_at) DESC""",
            (user["id"], user["id"], user["id"])).fetchall()

init_db()

if "user" not in st.session_state:
    st.session_state.user = None

def login_page():
    st.markdown("""
    <div class="hero">
      <div class="eyebrow">Private cloud • Laboratory LAN</div>
      <h1>☁️ LAN CloudBox</h1>
      <p>A private workspace for your lab — upload, organize, and share files across the network.</p>
    </div>""", unsafe_allow_html=True)
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.subheader("A simpler way to manage lab files")
        st.markdown("""
        - **One place** for project reports, PDFs, code and datasets
        - **Browser access** from computers on the same LAN
        - **Private accounts** with student and administrator roles
        - **Flexible storage** with local demo mode or Hadoop HDFS
        """)
        st.info("For the first run, sign in with the demo administrator account shown in the README. Change the default password before using this on a shared network.")
    with right:
        login_tab, register_tab = st.tabs(["Sign in", "Create student account"])
        with login_tab:
            with st.form("login_form"):
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
            if submitted:
                user = authenticate(username, password)
                if user:
                    st.session_state.user = dict(user)
                    record(user["username"], "LOGIN", "Signed in")
                    st.rerun()
                st.error("Incorrect username or password.")
        with register_tab:
            with st.form("register_form"):
                new_user = st.text_input("Choose username", max_chars=30)
                p1 = st.text_input("Password", type="password")
                p2 = st.text_input("Confirm password", type="password")
                created = st.form_submit_button("Create account", type="primary", use_container_width=True)
            if created:
                uname = new_user.strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{3,30}", uname):
                    st.error("Use 3–30 letters, numbers, underscores, or hyphens.")
                elif uname.lower() == "admin":
                    st.error("That username is reserved.")
                elif len(p1) < 8:
                    st.error("Use a password with at least 8 characters.")
                elif p1 != p2:
                    st.error("Passwords do not match.")
                else:
                    salt = os.urandom(16).hex()
                    try:
                        with db() as con:
                            con.execute("INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
                                        (uname, hash_password(p1, salt), salt, "student", now()))
                        record(uname, "REGISTER", "Created student account")
                        st.success("Account created. Switch to Sign in.")
                    except sqlite3.IntegrityError:
                        st.error("That username already exists.")

def dashboard_page(user):
    is_admin = user["role"] == "admin"
    files = list_files(user)
    mine = [f for f in files if f["owner_id"] == user["id"]]
    total_size = sum(f["size_bytes"] for f in files)
    with db() as con:
        if is_admin:
            shared_count = con.execute("SELECT COUNT(DISTINCT file_id) FROM shares").fetchone()[0] + con.execute("SELECT COUNT(*) FROM files WHERE is_shared=1").fetchone()[0]
        else:
            shared_count = con.execute("SELECT COUNT(DISTINCT f.id) FROM files f LEFT JOIN shares s ON s.file_id=f.id WHERE f.owner_id=? AND (f.is_shared=1 OR s.id IS NOT NULL)", (user["id"],)).fetchone()[0]
    st.markdown(f"""
    <div class="hero">
      <div class="eyebrow">{"Administrator console" if is_admin else "Student workspace"}</div>
      <h1>Welcome, {user["username"]} 👋</h1>
      <p>One workspace for project files, team sharing, and your lab resources.</p>
    </div>""", unsafe_allow_html=True)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Visible files", len(files))
    c2.metric("Your files", len(mine))
    c3.metric("Visible storage", f"{total_size/(1024*1024):.2f} MB")
    c4.metric("Shared files", shared_count)
    # Students can browse and download files, but only admins can upload.
    pages = ["Overview", "My Files", "Shared Files"]
    if is_admin:
        pages += ["Upload", "Users", "Activity Log"]
    selected = st.radio("Navigation", pages, horizontal=True, label_visibility="collapsed")
    st.divider()

    if selected == "Overview":
        left, right = st.columns([1.15,1], gap="large")
        with left:
            st.subheader("Recent files")
            for f in files[:6]:
                with st.container(border=True):
                    a,b = st.columns([3,1])
                    a.markdown(f"**📄 {f['original_name']}**")
                    a.caption(f"Owner: {f['owner']} · {f['size_bytes']/1024:.1f} KB")
                    try:
                        b.download_button("Download", load_bytes(f["storage_path"]), file_name=f["original_name"],
                                          key=f"ov_dl_{f['id']}", use_container_width=True)
                    except Exception as exc:
                        b.error("Storage error")
                        st.caption(str(exc))
            if not files:
                st.info("No files yet. Use Upload to add your first file.")
        with right:
            st.subheader("Storage overview")
            st.write(f"**Backend:** {'Apache HDFS' if STORAGE_MODE == 'hdfs' else 'Local demo storage'}")
            st.write(f"**Account role:** {'Administrator' if is_admin else 'Student'}")
            st.write(f"**Upload limit:** {MAX_MB} MB per file")
            st.progress(min(total_size/(500*1024*1024), 1.0), text="Illustrative 500 MB dashboard meter")
            st.caption("The progress bar is a visual demo meter, not a measured HDFS capacity limit.")
            st.subheader("Quick guide")
            st.markdown("1. Upload a file.\\n2. Find it under My Files.\\n3. Mark it shared if others should access it.\\n4. Download or delete files from the file list.")

    elif selected == "Upload":
        if not is_admin:
            st.error("Upload is available to administrators only.")
            st.stop()
        st.subheader("Upload to CloudBox")
        st.caption(f"Maximum file size: {MAX_MB} MB")
        uploaded = st.file_uploader("Choose a file", key="upload_file")
        if user["role"] == "admin":
            st.checkbox("Automatically share this file with all student accounts", value=True, disabled=True,
                        help="Admin uploads are automatically available to students in My Files.")
            share = True
        else:
            share = st.checkbox("Share this file with other student accounts", value=False)
        if uploaded:
            st.write(f"**{uploaded.name}** · {uploaded.size/1024:.1f} KB")
            if st.button("Upload file", type="primary"):
                if uploaded.size > MAX_MB*1024*1024:
                    st.error(f"File exceeds the {MAX_MB} MB limit.")
                else:
                    try:
                        original = safe_filename(uploaded.name)
                        stored = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{original}"
                        path = store_bytes(user["username"], stored, uploaded.getvalue())
                        with db() as con:
                            con.execute("""INSERT INTO files(owner_id,original_name,stored_name,storage_path,size_bytes,is_shared,uploaded_at)
                                VALUES(?,?,?,?,?,?,?)""",
                                (user["id"], original, stored, path, uploaded.size, int(share), now()))
                        record(user["username"], "UPLOAD", original)
                        st.success("File uploaded successfully.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Upload failed: {exc}")

    elif selected in ("My Files", "Shared Files"):
        st.subheader("Shared with you" if selected == "Shared Files" else "Your file library")
        if selected == "Shared Files":
            if user["role"] == "admin":
                st.caption("Admin files published automatically for all student accounts.")
            else:
                st.caption("Files shared directly with your account, plus files the administrator made available to all students.")
        search_key = f"search_{selected}"
        def clear_search(key=search_key):
            # Streamlit requires widget state to be changed in a callback before
            # the widget is instantiated on the next rerun.
            st.session_state[key] = ""
        filter_col, clear_col = st.columns([5, 1])
        with filter_col:
            query = st.text_input("Search by filename", key=search_key, placeholder="Search by filename...")
        with clear_col:
            st.write("")
            st.button("Clear search", key=f"clear_{selected}", on_click=clear_search,
                      use_container_width=True)
        if selected == "My Files":
            if user["role"] == "admin":
                rows = [f for f in files if f["owner_id"] == user["id"]]
            else:
                # Students see their own files plus files published by the administrator.
                rows = [f for f in files if f["owner_id"] == user["id"] or
                        (f["owner"] == "admin" and f["is_shared"] == 1)]
        else:
            if user["role"] == "admin":
                # Let the admin verify which of their uploads are published to students.
                rows = [f for f in files if f["owner_id"] == user["id"] and f["is_shared"] == 1]
            else:
                rows = received_shared_files(user)
        type_filter = st.selectbox("File type", ["All types", "PDF", "Images", "Documents", "Code", "Archives", "Other"], key=f"type_{selected}")
        if type_filter != "All types":
            ext_groups = {"PDF": {".pdf"}, "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}, "Documents": {".doc", ".docx", ".txt", ".rtf", ".odt", ".ppt", ".pptx", ".xls", ".xlsx"}, "Code": {".py", ".java", ".js", ".ts", ".html", ".css", ".sql", ".cpp", ".c"}, "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"}}
            if type_filter == "Other":
                known = set().union(*ext_groups.values())
                rows = [f for f in rows if Path(f["original_name"]).suffix.lower() not in known]
            else:
                rows = [f for f in rows if Path(f["original_name"]).suffix.lower() in ext_groups[type_filter]]
        rows = [f for f in rows if query.lower() in f["original_name"].lower()]
        sort_mode = st.selectbox("Sort files", ["Newest first", "Oldest first", "Name A–Z", "Largest first"], key=f"sort_{selected}")
        if sort_mode == "Oldest first":
            rows = sorted(rows, key=lambda f: f["uploaded_at"])
        elif sort_mode == "Name A–Z":
            rows = sorted(rows, key=lambda f: f["original_name"].lower())
        elif sort_mode == "Largest first":
            rows = sorted(rows, key=lambda f: f["size_bytes"], reverse=True)
        else:
            rows = sorted(rows, key=lambda f: f["uploaded_at"], reverse=True)
        if not rows:
            if selected == "Shared Files":
                st.info("No shared files match these filters. Clear the search, check the file-type filter, or ask the owner to share a file with your account.")
                st.caption("Sharing test: sign in as the file owner → My Files → Share with a specific user → choose the recipient. Then sign out and sign in as that recipient. The owner's own files do not appear in their 'Shared with you' list.")
            else:
                st.info("No files match these filters. Clear the search or upload your first file.")
        for f in rows:
            with st.container(border=True):
                a,b,c = st.columns([3,1,1])
                a.markdown(f"**📄 {f['original_name']}**")
                a.caption(f"{f['size_bytes']/1024:.1f} KB · Owner: {f['owner']} · Uploaded: {f['uploaded_at']}")
                if f["is_shared"]:
                    a.caption("🌐 Shared with students")
                try:
                    b.download_button("Download", load_bytes(f["storage_path"]), file_name=f["original_name"],
                                      key=f"dl_{selected}_{f['id']}", use_container_width=True)
                except Exception as exc:
                    b.error("Read failed")
                    st.caption(str(exc))
                can_delete = is_admin or f["owner_id"] == user["id"]
                if selected == "My Files" and f["owner_id"] == user["id"]:
                    with st.expander("Share with a specific user", expanded=False):
                        with db() as con:
                            recipients = con.execute("SELECT id,username FROM users WHERE id<>? ORDER BY username", (user["id"],)).fetchall()
                            already = con.execute("SELECT u.username FROM shares s JOIN users u ON u.id=s.shared_with WHERE s.file_id=? ORDER BY u.username", (f["id"],)).fetchall()
                        if already:
                            st.caption("Already shared with: " + ", ".join(x["username"] for x in already))
                        if not recipients:
                            st.info("Create another student account before sharing files.")
                        else:
                            recipient_map = {r["username"]: r["id"] for r in recipients}
                            chosen = st.selectbox("Choose recipient", list(recipient_map.keys()), key=f"recipient_{f['id']}")
                            if st.button("Share file", key=f"share_{f['id']}", type="primary"):
                                try:
                                    share_with_user(f["id"], user["id"], recipient_map[chosen])
                                    record(user["username"], "SHARE", f"{f['original_name']} -> {chosen}")
                                    record(chosen, "FILE_SHARED_WITH_ME", f"{f['original_name']} from {user['username']}")
                                    st.success(f"Shared with {chosen}. They can now see it under Shared Files.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Could not share file: {exc}")
                if can_delete and c.button("Delete", key=f"del_{selected}_{f['id']}", use_container_width=True):
                    try:
                        remove_stored(f["storage_path"])
                        delete_record(f["id"])
                        record(user["username"], "DELETE", f["original_name"])
                        st.success("File deleted.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")

    elif selected == "Users" and is_admin:
        st.subheader("Registered accounts")
        with db() as con:
            users = con.execute("SELECT id,username,role,created_at FROM users ORDER BY created_at").fetchall()
        st.caption(f"{len(users)} registered account(s). Student accounts appear here after they complete registration.")
        if len(users) <= 1:
            st.info("Only the administrator account exists. Sign out and use Create account on the sign-in screen to register a student account before testing sharing.")
        for u in users:
            with st.container(border=True):
                a,b,c = st.columns([2,2,1])
                a.markdown(f"**{u['username']}**")
                b.caption(f"Role: {u['role']} · Created: {u['created_at']}")
                if u["username"] != "admin" and c.button("Delete user", key=f"userdel_{u['id']}"):
                    with db() as con:
                        owned = con.execute("SELECT * FROM files WHERE owner_id=?", (u["id"],)).fetchall()
                    try:
                        for f in owned:
                            remove_stored(f["storage_path"])
                        with db() as con:
                            con.execute("DELETE FROM files WHERE owner_id=?", (u["id"],))
                            con.execute("DELETE FROM users WHERE id=?", (u["id"],))
                        record(user["username"], "DELETE_USER", u["username"])
                        st.success("User and their files deleted.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete user: {exc}")
        st.warning("Deleting a user also deletes files owned by that account.")

    elif selected == "Activity Log" and is_admin:
        st.subheader("Recent activity")
        with db() as con:
            logs = con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100").fetchall()
        if logs:
            st.dataframe([dict(x) for x in logs], use_container_width=True, hide_index=True)
        else:
            st.info("No activity recorded yet.")

    st.divider()
    st.caption(f"Storage backend: {'Apache HDFS' if STORAGE_MODE == 'hdfs' else 'Local demo storage'} · LAN CloudBox academic prototype")

if st.session_state.user is None:
    login_page()
else:
    user = st.session_state.user
    with st.sidebar:
        st.markdown("## ◈ CloudBox")
        st.caption("PRIVATE LAB WORKSPACE")
        st.caption(f"Signed in as **{user['username']}**")
        st.write(f"Role: `{user['role']}`")
        st.divider()
        st.write(f"Storage: **{'HDFS' if STORAGE_MODE == 'hdfs' else 'Local'}**")
        if st.button("Sign out", use_container_width=True):
            record(user["username"], "LOGOUT", "Signed out")
            st.session_state.user = None
            st.rerun()
    dashboard_page(user)
