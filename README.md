# LAN CloudBox — Full Mini Project

A browser-based SaaS file hub designed for an existing laboratory LAN. The project has student accounts, an administrator role, file upload/download/search/delete, file sharing, a SQLite metadata database, and local/HDFS storage modes.

## Important distinction
- `STORAGE_MODE=local` is the easiest fully runnable demo and stores actual files in `storage/<username>/`.
- `STORAGE_MODE=hdfs` uses the Hadoop `hdfs dfs` CLI and requires a configured, running HDFS environment. Local mode is not HDFS; do not claim HDFS integration is verified until you test against a real cluster.

## Run on Windows (first run)
1. Extract the ZIP.
2. Open the extracted folder in File Explorer.
3. Click the address bar, type `cmd`, press Enter.
4. Run one command at a time:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open `http://localhost:8501`.

If `python` is not recognized, install Python 3.10+ and select "Add Python to PATH", then reopen Command Prompt.

## Demo admin login
- Username: `admin`
- Password: `Admin@123`

Change the default admin password before using this on a shared network. For this student prototype, set a custom password before the first run by setting `CLOUDBOX_ADMIN_PASSWORD` in the same terminal, e.g. in PowerShell:

```powershell
$env:CLOUDBOX_ADMIN_PASSWORD="Use-A-Strong-Password-Here"
python -m streamlit run app.py
```

The initial admin account is created only if it does not already exist. If you already ran the app, changing the environment variable will not change the existing password; reset the demo database only if you are comfortable losing its metadata, or implement a password-change feature.

## Access from other computers on the same LAN
1. Connect the server and client devices to the same trusted LAN/Wi-Fi.
2. On the server, run `ipconfig` and note the IPv4 address.
3. Start the app:

```bat
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

4. From another computer, visit `http://SERVER-IP:8501`.
5. If Windows Firewall asks, allow access only on a trusted Private network. Do not expose this prototype directly to the public internet.

## Enable HDFS mode (Linux recommended)
Prerequisites: Apache Hadoop is installed and configured, NameNode/DataNodes are running, and the `hdfs` command works as the same OS user running Streamlit.

```bash
hdfs dfs -mkdir -p /cloudbox
hdfs dfs -ls /
export STORAGE_MODE=hdfs
export HDFS_ROOT=/cloudbox
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

HDFS commands to verify a test file:

```bash
hdfs dfs -mkdir -p /cloudbox/test
echo "CloudBox HDFS test" > sample.txt
hdfs dfs -put -f sample.txt /cloudbox/test/
hdfs dfs -ls /cloudbox/test
hdfs dfs -cat /cloudbox/test/sample.txt
```

Each account uses its own HDFS directory under `/cloudbox/<username>`. HDFS permissions and the Hadoop OS identity must be configured appropriately. This prototype does not provide production-grade HDFS authorization.

## Files
- `app.py` — UI, login/register, controller logic, local/HDFS storage adapter
- `requirements.txt` — dependencies
- `Mini_Project_Report.md` — report draft
- `README.md` — this setup guide
- `storage/` — local-mode files (created automatically)

## File sharing
Open **My Files**, expand **Share with a specific user** on a file you own, select a registered student, and click **Share file**. The recipient must sign in with their own account and open **Shared Files**. This is recipient-based sharing; the uploader checkbox can still publish a file to all student accounts.

## Testing checklist
- [ ] Sign in as admin.
- [ ] Create a student account.
- [ ] Sign in as student.
- [ ] Upload a PDF or text file.
- [ ] Download it and compare the contents.
- [ ] In My Files, expand **Share with a specific user**, choose a second student account, and click **Share file**.
- [ ] Sign in as that recipient and verify the file appears under **Shared Files** and downloads correctly.
- [ ] Verify a third student cannot see a file shared only with the recipient.
- [ ] Verify students cannot see another student's private file.
- [ ] Verify student can delete their own file and admin can delete files.
- [ ] From another LAN computer, open `http://SERVER-IP:8501`.
- [ ] If using HDFS, verify uploaded files with `hdfs dfs -ls /cloudbox/<username>`.

## Security and academic limitations
This is a classroom prototype, not production-ready cloud software. It lacks HTTPS/TLS, password reset, rate limiting, CSRF hardening, virus scanning, robust HDFS per-user ACL enforcement, and production audit controls. Use only on a trusted lab LAN and avoid sensitive files.

## Version 2 update (2026-10-18)
- Admin Users page now includes an **Create a student account here** form. This gives the administrator a direct way to add test/student accounts even if the registration tab is overlooked.
- The Users page shows total registered accounts and clarifies that this is not a live online-user count.
- To test private sharing: sign in as admin, open Users, create `student1`, open My Files and share a file with `student1`, then sign in as `student1` and open Shared Files.
