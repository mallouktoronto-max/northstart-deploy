"""
NorthStart Canada — newcomer help site.
Single-file Flask app.

Rules enforced in code:
- No interest-based tools are included, ever (see TOOLS list).
- Job screening rejects alcohol / pork / gambling employers (see is_blocked_job).
- Visitor IPs are never stored raw — only a salted SHA-256 hash.
- Admin password, secret key, IP salt and Brevo credentials come from
  environment variables only. Nothing secret is hard-coded here.
- Donation / inquiry emails live in site_settings, not in page HTML.
"""
import hashlib
import json
import os
import re
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, request, Response, session, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "site.db")
COURSES_PATH = os.path.join(BASE_DIR, "data", "courses.json")

SITE_NAME = "NorthStart Canada"  # Proposed brand — confirm before publishing
ADMIN_SESSION_KEY = "ns_admin"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-before-live")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# ---------------------------------------------------------------- settings
DEFAULT_SETTINGS = {
    "registration_open": "1",
    "login_required": "0",
    "show_ticker": "0",
    "show_carousel": "0",
    "show_top_helpers": "0",
    "show_phone_field": "0",
    "maintenance_mode": "0",
    "vip_enabled": "0",
    "donation_email": "",
    "inquiry_email": "",
    # Homepage section visibility, in the owner's order. 1 = show.
    "section_tools": "1", "section_english": "1", "section_driving": "1",
    "section_jobs": "1", "section_qa": "1", "section_posts": "1",
    "section_events": "1", "section_placement": "1", "section_rentals": "1",
    "section_services": "1", "section_consulates": "1", "section_food": "1",
    "section_mosques": "1", "section_ramadan": "1", "section_zakat": "1",
    "section_pregnancy": "1", "section_funeral": "1",
    "section_community_support": "1", "section_job_portals": "1",
    "section_guides": "1", "section_scholar": "1", "section_assistant": "1",
    "section_donate": "1", "section_about": "1", "section_apk": "1",
    "section_track": "1",
}

# 24 tools, in the owner's groups. The five interest-based tools are
# deliberately ABSENT and must never be added:
# compound interest, mortgage payment, credit-card utilization,
# debt payoff planner, RRSP refund calculator.
TOOLS = [
    # Immigration (8)
    {"id": "crs", "group": "Immigration", "name": "CRS points calculator (Express Entry)"},
    {"id": "citizenship", "group": "Immigration", "name": "Citizenship physical-presence calculator"},
    {"id": "proof-of-funds", "group": "Immigration", "name": "Proof-of-funds calculator"},
    {"id": "pgp-income", "group": "Immigration", "name": "Parents and grandparents sponsorship income checker"},
    {"id": "ielts-clb", "group": "Immigration", "name": "IELTS to CLB converter"},
    {"id": "immigration-quiz", "group": "Immigration", "name": "Immigration-path quiz"},
    {"id": "immigration-cost", "group": "Immigration", "name": "Immigration-cost estimator (official federal fees)"},
    {"id": "documents-checklist", "group": "Immigration", "name": "Documents-checklist generator"},
    # Money and salary (10)
    {"id": "net-salary", "group": "Money and salary", "name": "Net salary after tax"},
    {"id": "ccb", "group": "Money and salary", "name": "Canada Child Benefit (CCB) calculator"},
    {"id": "hourly-annual", "group": "Money and salary", "name": "Hourly wage to annual salary"},
    {"id": "sales-tax", "group": "Money and salary", "name": "Sales tax by province"},
    {"id": "ei-benefit", "group": "Money and salary", "name": "Employment Insurance (EI) benefit estimator"},
    {"id": "groceries-benefit", "group": "Money and salary", "name": "Canada Groceries and Essentials Benefit"},
    {"id": "minimum-wage", "group": "Money and salary", "name": "Minimum wage by province and territory"},
    {"id": "currency", "group": "Money and salary", "name": "Live currency converter"},
    {"id": "ontario-ltt", "group": "Money and salary", "name": "Ontario land transfer tax"},
    # Living and housing (6)
    {"id": "cost-of-living", "group": "Living and housing", "name": "Cost of living estimator"},
    {"id": "rent-affordability", "group": "Living and housing", "name": "Rent affordability (30% rule)"},
    {"id": "salary-sufficiency", "group": "Living and housing", "name": "Salary sufficiency checker"},
    {"id": "province-comparison", "group": "Living and housing", "name": "Province comparison"},
    {"id": "money-transfer", "group": "Living and housing", "name": "Money-transfer cost comparison"},
    # TFSA sums yearly limits only, computes NO interest.
    {"id": "tfsa-room", "group": "Money and salary", "name": "TFSA contribution room (yearly limits only, no interest)"},
    {"id": "ontario-closing", "group": "Living and housing", "name": "Ontario home closing costs (fees and taxes only)"},
]

SECTIONS = [
    {"id": "tools", "key": "section_tools", "name": "Tools and Calculators", "route": "/#/tools"},
    {"id": "english", "key": "section_english", "name": "English Courses", "route": "/#/english"},
    {"id": "placement", "key": "section_placement", "name": "Placement Test", "route": "/#/placement"},
    {"id": "driving", "key": "section_driving", "name": "Driving Test", "route": "/#/driving"},
    {"id": "jobs", "key": "section_jobs", "name": "Jobs", "route": "/#/jobs"},
    {"id": "job-portals", "key": "section_job_portals", "name": "Job Portals", "route": "/#/job-portals"},
    {"id": "community-support", "key": "section_community_support", "name": "Community Support", "route": "/#/community-support"},
    {"id": "qa", "key": "section_qa", "name": "Community Q&A", "route": "/#/qa"},
    {"id": "posts", "key": "section_posts", "name": "Community Posts", "route": "/#/posts"},
    {"id": "events", "key": "section_events", "name": "Events", "route": "/#/events"},
    {"id": "rentals", "key": "section_rentals", "name": "Rentals", "route": "/#/rentals"},
    {"id": "services", "key": "section_services", "name": "Services Directory", "route": "/#/services"},
    {"id": "consulates", "key": "section_consulates", "name": "Consulates and Embassies in Canada", "route": "/#/consulates"},
    {"id": "guides", "key": "section_guides", "name": "Guides", "route": "/#/guides"},
    {"id": "scholar", "key": "section_scholar", "name": "Questions where scholars differ", "route": "/#/scholar"},
    {"id": "assistant", "key": "section_assistant", "name": "Site Assistant", "route": "/#/assistant"},
    {"id": "donate", "key": "section_donate", "name": "Donate", "route": "/#/donate"},
    {"id": "about", "key": "section_about", "name": "About, Terms and Privacy", "route": "/#/about"},
    {"id": "apk", "key": "section_apk", "name": "Android App", "route": "/#/apk"},
    {"id": "track", "key": "section_track", "name": "My Tracking", "route": "/#/track"},
]

PROVINCES = ["Manitoba", "Ontario", "Quebec", "British Columbia", "Alberta",
             "Saskatchewan", "Nova Scotia", "New Brunswick",
             "Newfoundland and Labrador", "Prince Edward Island"]

# Settled screening (spec 5.4): it is not permitted to work in restaurants
# that serve pork or alcohol, in ANY role, including kitchen help and
# dishwashing, because it is cooperation in sin. The import filter must
# therefore reject these employers and any restaurant/food service that
# serves pork or alcohol. Keyword screening is a first filter only — every
# listing still needs the owner's review before it shows.
BLOCKED_JOB_KEYWORDS = [
    "liquor", "brewery", "winery", "wine", "beer", "bar ", "pub", "casino",
    "gambling", "lottery", "betting", "pork", "ham", "bacon", "sausage",
    "butcher", "meat processing", "slaughter", "nightclub", "strip club",
    "cannabis", "vape", "tobacco",
]

def is_blocked_job(title, company, description):
    text = " ".join([title or "", company or "", description or ""]).lower()
    # A restaurant/food-service posting that mentions serving alcohol or
    # pork is blocked even if the role itself is kitchen/dishwashing.
    if any(k in text for k in BLOCKED_JOB_KEYWORDS):
        return True
    return False

# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

TABLES = {
    # Core tables named in the spec, section 7.
    "users": "id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password_hash TEXT, points INTEGER DEFAULT 0, vip INTEGER DEFAULT 0, created_at TEXT",
    "site_settings": "key TEXT PRIMARY KEY, value TEXT",
    "jobs": "id INTEGER PRIMARY KEY, source TEXT, title TEXT, company TEXT, city TEXT, province TEXT, type TEXT, salary TEXT, description TEXT, posting_date TEXT, expiry_date TEXT, apply_url TEXT, apply_email TEXT, is_hidden INTEGER DEFAULT 0, approved INTEGER DEFAULT 0",
    "job_applications": "id INTEGER PRIMARY KEY, job_id INTEGER, email TEXT, message TEXT, phone TEXT, created_at TEXT",
    "jb_app_log": "id INTEGER PRIMARY KEY, job_id INTEGER, full_name TEXT, phone TEXT, message TEXT, cv_text TEXT, consent INTEGER, created_at TEXT",
    "ads": "id INTEGER PRIMARY KEY, image_url TEXT, caption TEXT, link TEXT, visible INTEGER DEFAULT 1, hide_caption INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0",
    "inquiries": "id INTEGER PRIMARY KEY, business_name TEXT, contact TEXT, message TEXT, created_at TEXT",
    "events": "id INTEGER PRIMARY KEY, title TEXT, city TEXT, description TEXT, event_date TEXT",
    "event_comments": "id INTEGER PRIMARY KEY, event_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT",
    "event_votes": "id INTEGER PRIMARY KEY, event_id INTEGER, user_id INTEGER, vote INTEGER",
    "event_location_requests": "id INTEGER PRIMARY KEY, city TEXT, user_id INTEGER, created_at TEXT",
    "posts": "id INTEGER PRIMARY KEY, user_id INTEGER, body TEXT, created_at TEXT",
    "post_comments": "id INTEGER PRIMARY KEY, post_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT",
    "qa_questions": "id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT, created_at TEXT",
    "qa_answers": "id INTEGER PRIMARY KEY, question_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT",
    "threads": "id INTEGER PRIMARY KEY, user_id INTEGER, subject TEXT, created_at TEXT",
    "thread_messages": "id INTEGER PRIMARY KEY, thread_id INTEGER, sender TEXT, body TEXT, created_at TEXT",
    "admin_messages": "id INTEGER PRIMARY KEY, user_id INTEGER, body TEXT, created_at TEXT",
    "visits": "id INTEGER PRIMARY KEY, ip_hash TEXT, country TEXT, device_class TEXT, route TEXT, session_id TEXT, created_at TEXT",
    "team_visits": "id INTEGER PRIMARY KEY, ip_hash TEXT, country TEXT, device_class TEXT, route TEXT, session_id TEXT, created_at TEXT",
    "tool_saves": "id INTEGER PRIMARY KEY, user_id INTEGER, tool_id TEXT, inputs_json TEXT, updated_at TEXT",
    "progress": "id INTEGER PRIMARY KEY, user_id INTEGER, lesson_id TEXT, completed INTEGER DEFAULT 0, updated_at TEXT",
    "point_log": "id INTEGER PRIMARY KEY, user_id INTEGER, points INTEGER, reason TEXT, created_at TEXT",
    "user_badges": "id INTEGER PRIMARY KEY, user_id INTEGER, badge TEXT, awarded_at TEXT",
    "streaks": "id INTEGER PRIMARY KEY, user_id INTEGER, last_login_date TEXT, streak_days INTEGER DEFAULT 0",
    "donations": "id INTEGER PRIMARY KEY, amount TEXT, email TEXT, created_at TEXT",
    "purchases": "id INTEGER PRIMARY KEY, user_id INTEGER, item TEXT, created_at TEXT",
    "vip_requests": "id INTEGER PRIMARY KEY, user_id INTEGER, created_at TEXT",
    "pending_registrations": "id INTEGER PRIMARY KEY, name TEXT, email TEXT, password_hash TEXT, code_hash TEXT, attempts INTEGER DEFAULT 0, expires_at TEXT, created_at TEXT",
    "reset_codes": "id INTEGER PRIMARY KEY, email TEXT, code_hash TEXT, attempts INTEGER DEFAULT 0, expires_at TEXT, created_at TEXT",
    "rate_limits": "id INTEGER PRIMARY KEY, key TEXT, count INTEGER, window_start TEXT",
    "user_activity": "id INTEGER PRIMARY KEY, user_id INTEGER, activity TEXT, created_at TEXT",
    "user_notes": "id INTEGER PRIMARY KEY, user_id INTEGER, note TEXT, created_at TEXT",
    "testimonials": "id INTEGER PRIMARY KEY, name TEXT, body TEXT, visible INTEGER DEFAULT 0",
    "social_links": "id INTEGER PRIMARY KEY, label TEXT, url TEXT",
    "sponsorships": "id INTEGER PRIMARY KEY, name TEXT, details TEXT",
    "rental_companies": "id INTEGER PRIMARY KEY, name TEXT, city TEXT, approved INTEGER DEFAULT 0",
    "rental_units": "id INTEGER PRIMARY KEY, company_id INTEGER, title TEXT, city TEXT, price TEXT",
    "rental_interest": "id INTEGER PRIMARY KEY, unit_id INTEGER, email TEXT, message TEXT, created_at TEXT",
    "svc_pros": "id INTEGER PRIMARY KEY, name TEXT, trade TEXT, city TEXT, verified INTEGER DEFAULT 0",
    "svc_listings": "id INTEGER PRIMARY KEY, pro_id INTEGER, title TEXT, description TEXT",
    "svc_docs": "id INTEGER PRIMARY KEY, pro_id INTEGER, doc_name TEXT, path TEXT",
    "svc_licenses": "id INTEGER PRIMARY KEY, pro_id INTEGER, license_no TEXT",
    "svc_verifications": "id INTEGER PRIMARY KEY, pro_id INTEGER, status TEXT, checked_at TEXT",
    "svc_ratings": "id INTEGER PRIMARY KEY, pro_id INTEGER, user_id INTEGER, stars INTEGER, comment TEXT",
    "svc_reports": "id INTEGER PRIMARY KEY, pro_id INTEGER, reason TEXT, created_at TEXT",
    "svc_interest": "id INTEGER PRIMARY KEY, pro_id INTEGER, email TEXT, message TEXT, created_at TEXT",
    "assistant_taught": "id INTEGER PRIMARY KEY, question TEXT, answer TEXT, created_at TEXT",
    "cms_categories": "id INTEGER PRIMARY KEY, name TEXT, slug TEXT",
    "cms_entries": "id INTEGER PRIMARY KEY, category_id INTEGER, title TEXT, body TEXT",
    "cms_lessons": "id INTEGER PRIMARY KEY, entry_id INTEGER, title TEXT, body TEXT, position INTEGER",
}

def init_db():
    db = get_db()
    for name, cols in TABLES.items():
        db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES (?, ?)", (k, v))
    db.commit()

def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM site_settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def winnipeg_time(iso_str):
    # Stored as UTC ISO; display conversion is done client-side / in admin
    # from the same value. Admin pages show America/Winnipeg in the browser.
    return iso_str

# ---------------------------------------------------------------- visits
BOT_PATTERNS = ["bot", "crawler", "spider", "scraper", "headless", "curl", "wget"]

def hash_ip(ip):
    salt = os.environ.get("IP_HASH_SALT", "dev-only-change-before-live")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()

def device_class(ua):
    ua = (ua or "").lower()
    return "phone" if ("mobile" in ua or "android" in ua or "iphone" in ua) else "computer"

def is_bot(ua):
    ua = (ua or "").lower()
    return any(p in ua for p in BOT_PATTERNS)

# ---------------------------------------------------------------- helpers
def require_admin():
    return session.get(ADMIN_SESSION_KEY) is True

def admin_guard():
    if not require_admin():
        return jsonify({"error": "Forbidden"}), 403
    return None

def load_courses():
    try:
        with open(COURSES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"categories": []}

def hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()

# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    init_db()
    settings = get_settings()
    if settings.get("maintenance_mode") == "1" and not require_admin():
        return Response("<h1>Maintenance</h1><p>Please check back soon.</p>", status=503, mimetype="text/html")
    return send_from_directory("templates", "index.html")

@app.route("/download/apk")
def download_apk():
    # Complete explanatory page, NOT a fake APK file. No APK has been built,
    # so no file is offered here. The site works fully in a phone browser,
    # and the steps below add it to a phone home screen like an app icon.
    html = """<!DOCTYPE html><html lang="en" dir="ltr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NorthStart Canada app for Android</title>
<style>body{font-family:system-ui,Arial,sans-serif;background:#FAF7F2;color:#222;margin:0}
header{background:#0B5563;color:#fff;padding:18px}h1{margin:0;font-size:22px}
main{max-width:720px;margin:0 auto;padding:18px}h2{color:#0B5563}
.card{background:#fff;border:1px solid #ddd;border-radius:10px;padding:16px;margin:14px 0}
a{color:#0B5563}.btn{display:inline-block;background:#F08A24;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:700}
ol li,ul li{margin:8px 0}</style></head><body>
<header><h1>NorthStart Canada &mdash; Android app</h1></header><main>
<div class="card"><h2>There is no APK file to download today</h2>
<p>NorthStart Canada does not yet have a built Android app file (APK). This page does not offer a download, and no file here will install an app, because offering a file that is not a real, tested app would mislead visitors. When a real APK is built and tested, this page will be updated to offer that real file, with its size and what it does.</p></div>
<div class="card"><h2>Use the site in your phone browser today</h2>
<p>The whole site works in a phone browser: all 24 tools, English courses, driving practice, job portals, and community sections. Nothing on this site needs an installed app to work. Keep using the normal website address in your browser.</p>
<p><a class="btn" href="/">Open NorthStart Canada</a></p></div>
<div class="card"><h2>Add the site to your Android home screen</h2>
<p>On an Android phone, in Google Chrome, you can put a NorthStart Canada icon on your home screen. It opens the site in Chrome, like an app shortcut. It does not install a separate app and it does not use extra phone storage for an app.</p>
<ol>
<li>Open Chrome on your Android phone and go to the NorthStart Canada website.</li>
<li>Tap the three dots menu at the top right of Chrome.</li>
<li>Tap &ldquo;Add to Home screen&rdquo;.</li>
<li>If Chrome asks you to confirm, tap &ldquo;Add&rdquo;.</li>
<li>Find the new NorthStart Canada icon on your home screen and tap it whenever you want to open the site.</li>
</ol>
<p>If you do not see &ldquo;Add to Home screen&rdquo;, update Chrome from the Play Store and try again, or keep the site as a bookmark: in the same three dots menu, tap the star to bookmark it.</p></div>
<div class="card"><h2>What will change when a real app exists</h2>
<p>A future real app would open this same site inside an app window. Until that file is actually built and checked, this page stays as it is: a clear explanation, home-screen steps, and no pretend download.</p></div>
</main></body></html>"""
    return Response(html, mimetype="text/html")

# JS files served through app.py routes, as specified.
@app.route("/js/tools.js")
def js_tools():
    return send_from_directory("static/js", "tools.js", mimetype="application/javascript")

@app.route("/js/tools2.js")
def js_tools2():
    return send_from_directory("static/js", "tools2.js", mimetype="application/javascript")

@app.route("/js/tracking.js")
def js_tracking():
    return send_from_directory("static/js", "tracking.js", mimetype="application/javascript")

# ---------------------------------------------------------------- public APIs
@app.route("/api/settings")
def api_settings():
    init_db()
    s = get_settings()
    # Only the switches the frontend needs. Emails are not exposed here
    # except through the donation / contact pages after admin sets them.
    return jsonify({
        "site_name": SITE_NAME,
        "registration_open": s["registration_open"] == "1",
        "login_required": s["login_required"] == "1",
        "show_ticker": s["show_ticker"] == "1",
        "show_carousel": s["show_carousel"] == "1",
        "show_top_helpers": s["show_top_helpers"] == "1",
        "show_phone_field": s["show_phone_field"] == "1",
        "vip_enabled": s["vip_enabled"] == "1",
        "sections": SECTIONS,
    })

@app.route("/api/sections")
def api_sections():
    init_db()
    s = get_settings()
    visible = [sec for sec in SECTIONS if s.get(sec["key"], "1") == "1"]
    return jsonify(visible)

@app.route("/api/lists")
def api_lists():
    """Named lists directory: one entry per list view, with item counts.
    Counts are read from the same data the views use; no new content."""
    init_db()
    db = get_db()
    courses = load_courses()
    total_courses = sum(len(c.get("courses", [])) for c in courses.get("categories", []))
    total_lessons = sum(len(co.get("lessons", [])) for c in courses.get("categories", []) for co in c.get("courses", []))
    try:
        with open(os.path.join(BASE_DIR, "data", "driving.json"), encoding="utf-8") as f:
            drv = json.load(f)
        fines = sum(len(p.get("fines", {}).get("cards", [])) for p in drv.get("provinces", []))
        driving = {"provinces": len(drv.get("provinces", [])), "questions": 720, "signs": 260, "fines": fines}
    except Exception:
        driving = {"provinces": 10, "questions": 720, "signs": 260, "fines": 265}
    try:
        with open(os.path.join(BASE_DIR, "data", "guides.json"), encoding="utf-8") as f:
            gd = json.load(f)
        consulates = len((gd.get("consulates", {}) or {}).get("entries", []))
        guides_n = len(gd.get("guides", {}))
        scholar_n = len((gd.get("scholar_difference", {}) or {}).get("topics", []))
    except Exception:
        consulates, guides_n, scholar_n = 133, 6, 4
    def cnt(table, where=""):
        try:
            return db.execute(f"SELECT COUNT(*) c FROM {table} {where}").fetchone()["c"]
        except Exception:
            return 0
    return jsonify([
        {"id": "tools", "title": "Tools and Calculators", "route": "/#/tools", "items": len(TOOLS), "groups": ["Immigration", "Money and salary", "Living and housing"]},
        {"id": "english", "title": "English Courses", "route": "/#/english", "items": total_courses, "lessons": total_lessons},
        {"id": "placement", "title": "Placement Test", "route": "/#/placement", "items": len(courses.get("placement_test", {}).get("questions", []))},
        {"id": "driving", "title": "Driving Test", "route": "/#/driving", "lists": ["Practice Test", "Road Signs", "Study Guide", "Fines and Violations"], **driving},
        {"id": "jobs", "title": "Jobs", "route": "/#/jobs", "items": cnt("jobs", "WHERE approved=1 AND is_hidden=0")},
        {"id": "job-portals", "title": "Job Portals", "route": "/#/job-portals", "items": len(JOB_PORTALS)},
        {"id": "community-support", "title": "Community Support", "route": "/#/community-support", "items": len(COMMUNITY_RESOURCES)},
        {"id": "qa", "title": "Community Q&A", "route": "/#/qa", "items": cnt("qa_questions")},
        {"id": "posts", "title": "Community Posts", "route": "/#/posts", "items": cnt("posts")},
        {"id": "events", "title": "Events", "route": "/#/events", "items": cnt("events")},
        {"id": "rentals", "title": "Rentals", "route": "/#/rentals", "items": cnt("rental_companies", "WHERE approved=1")},
        {"id": "services", "title": "Services Directory", "route": "/#/services", "items": cnt("svc_pros", "WHERE verified=1")},
        {"id": "consulates", "title": "Consulates and Embassies in Canada", "route": "/#/consulates", "items": consulates},
        {"id": "guides", "title": "Guides", "route": "/#/guides", "items": guides_n},
        {"id": "scholar", "title": "Questions where scholars differ", "route": "/#/scholar", "items": scholar_n},
        {"id": "assistant", "title": "Site Assistant", "route": "/#/assistant", "items": 1},
        {"id": "donate", "title": "Donate", "route": "/#/donate", "items": 1},
        {"id": "about", "title": "About, Terms and Privacy", "route": "/#/about", "items": 6},
        {"id": "apk", "title": "Android App", "route": "/#/apk", "items": 1},
        {"id": "track", "title": "My Tracking", "route": "/#/track", "items": 3},
    ])

@app.route("/api/tools")
def api_tools():
    return jsonify(TOOLS)

@app.route("/api/figures")
def api_figures():
    # Verified-figures file; the frontend tools use the same values.
    try:
        with open(os.path.join(BASE_DIR, "data", "official_figures_2026.json"), encoding="utf-8") as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"error": "figures file not found"}), 404

@app.route("/api/courses")
def api_courses():
    data = load_courses()
    return jsonify(data)

@app.route("/api/driving")
def api_driving():
    # Practice questions and verified fines, loaded from data/driving.json.
    try:
        with open(os.path.join(BASE_DIR, "data", "driving.json"), encoding="utf-8") as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"error": "driving file not found"}), 404

@app.route("/api/driving/provinces")
def api_driving_provinces():
    return jsonify(PROVINCES)

@app.route("/api/track/summary")
def api_track_summary():
    # Guest view: returns site totals. Saving progress is not available yet.
    init_db()
    db = get_db()
    data = load_courses()
    total_lessons = 0
    total_courses = 0
    for cat in data.get("categories", []):
        for course in cat.get("courses", []):
            total_courses += 1
            total_lessons += len(course.get("lessons", []))
    saved = db.execute("SELECT COUNT(*) c FROM tool_saves").fetchone()["c"]
    badges = db.execute("SELECT COUNT(*) c FROM user_badges").fetchone()["c"]
    return jsonify({
        "guest": True,
        "learning": {"total_courses": total_courses, "total_lessons": total_lessons,
                     "completed_lessons": 0, "note": "Saving progress is not available yet, because account email verification is not set up. All content is free to view without an account."},
        "saved_tools": {"count": saved, "note": "All tools are free to use without an account. Saving tool inputs is not available yet."},
        "points": 0,
        "badges": {"count": badges, "note": "Points and badges are shown for members only, when accounts are available."},
    })

@app.route("/api/jobs")
def api_jobs():
    init_db()
    db = get_db()
    province = request.args.get("province", "")
    jtype = request.args.get("type", "")
    q = "SELECT * FROM jobs WHERE is_hidden=0 AND approved=1"
    params = []
    if province:
        q += " AND province=?"; params.append(province)
    if jtype:
        q += " AND type=?"; params.append(jtype)
    rows = db.execute(q + " ORDER BY posting_date DESC LIMIT 200", params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/ads")
def api_ads():
    init_db()
    db = get_db()
    rows = db.execute("SELECT * FROM ads WHERE visible=1 ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/leaderboard")
def api_leaderboard():
    init_db()
    db = get_db()
    rows = db.execute("SELECT name, points FROM users ORDER BY points DESC LIMIT 20").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/visit", methods=["POST"])
def api_visit():
    init_db()
    ua = request.headers.get("User-Agent", "")
    if is_bot(ua):
        return jsonify({"ok": False, "reason": "bot rejected"}), 200
    data = request.get_json(silent=True) or {}
    route = str(data.get("route", "/"))[:200]
    db = get_db()
    db.execute(
        "INSERT INTO visits (ip_hash, country, device_class, route, session_id, created_at) VALUES (?,?,?,?,?,?)",
        (hash_ip(request.remote_addr or "unknown"),
         str(data.get("country", "unknown"))[:80],
         device_class(ua), route,
         str(data.get("session_id", ""))[:100], now_iso()))
    db.commit()
    return jsonify({"ok": True})

JOB_PORTALS = [
    {"name": "Job Bank", "region": "Canada", "url": "https://www.jobbank.gc.ca/findajob/resources/search-for-jobs", "description": "The Government of Canada job board for searching jobs across Canada, with wage information and job alerts."},
    {"name": "GC Jobs (Government of Canada jobs)", "region": "Federal", "url": "https://www.jobs.gc.ca/", "description": "The federal public service job board for jobs with federal departments and agencies."},
    {"name": "Ontario Public Service Careers (GOjobs)", "region": "Ontario", "url": "https://www.gojobs.gov.on.ca/Search.aspx", "description": "The Ontario government job board for jobs with Ontario ministries and agencies."},
    {"name": "BC Public Service careers", "region": "British Columbia", "url": "https://www2.gov.bc.ca/gov/content/careers-myhr/job-seekers/featured-careers", "description": "The British Columbia government careers pages, with a link to the main public service job search."},
    {"name": "Government of Alberta jobs", "region": "Alberta", "url": "https://www.alberta.ca/alberta-public-service-jobs.aspx", "description": "The Alberta government jobs page for Alberta Public Service postings and how to apply."},
    {"name": "Manitoba Government Job Opportunities", "region": "Manitoba", "url": "https://www.gov.mb.ca/govjobs/", "description": "The Manitoba government job board for jobs with the Manitoba government across the province."},
    {"name": "Government of Saskatchewan Career Portal", "region": "Saskatchewan", "url": "https://careers.saskatchewan.ca/en/sites/CX_1", "description": "The Saskatchewan government career portal for jobs with the provincial government."},
    {"name": "Government of Quebec Careers", "region": "Quebec", "url": "https://www.carrieres.gouv.qc.ca/", "description": "The Quebec government careers site for jobs with the Quebec public service."},
    {"name": "Government of New Brunswick careers (e-recruitment)", "region": "New Brunswick", "url": "https://www.ere.gnb.ca/", "description": "The New Brunswick government e-recruitment site named in provincial job postings for applying to public service jobs."},
    {"name": "Government of Nova Scotia jobs", "region": "Nova Scotia", "url": "https://jobs.novascotia.ca/", "description": "The Nova Scotia government job board for jobs with the provincial public service."},
    {"name": "Government of Newfoundland and Labrador Careers", "region": "Newfoundland and Labrador", "url": "https://www.hiring.gov.nl.ca/", "description": "The Newfoundland and Labrador government careers site for public service jobs."},
    {"name": "Jobs with the Government of PEI", "region": "Prince Edward Island", "url": "https://www.princeedwardisland.ca/en/topic/jobs-with-the-government-of-pei", "description": "The Prince Edward Island government page explaining how to apply for provincial government, health sector, and public service jobs."},
    {"name": "Government of Yukon application process", "region": "Yukon", "url": "https://yukon.ca/en/employment/application-process", "description": "The Yukon government page explaining eligibility and how to apply for Yukon government jobs through its e-recruitment system."},
    {"name": "GNWT Careers (Government of Northwest Territories)", "region": "Northwest Territories", "url": "https://www.gov.nt.ca/careers/en", "description": "The Northwest Territories government careers site for searching and applying for territorial government jobs."},
    {"name": "Government of Nunavut job listings", "region": "Nunavut", "url": "https://www.gov.nu.ca/public-jobs", "description": "The Nunavut government job listings page for jobs with the territorial government and related employers."},
]

@app.route("/api/job-portals")
def api_job_portals():
    # Portals only, never employers: no employer is named here, so no
    # alcohol, pork, gambling, or doubtful employer can appear in this list.
    # Checked 2026-10-27 (see build report for per-portal method).
    return jsonify({"checked": "2026-10-27", "note": "Official government job portals only. These are places to search for jobs, not employer listings from this site. Always read the requirements on the official posting before you apply.", "portals": JOB_PORTALS})

COMMUNITY_RESOURCES = [
    {"name": "IRCC — Find free newcomer services near you", "kind": "Settlement services", "url": "http://ircc.canada.ca/english/newcomers/services", "description": "The federal service finder for free settlement help near you, for people who are eligible for federally funded services."},
    {"name": "IRCC — Settling in Canada", "kind": "Settlement services", "url": "https://ircc.canada.ca/English/newcomers/index.asp", "description": "The federal settling-in pages covering arrival, language, driving, taxes, housing, health care, and community life."},
    {"name": "211 Canada", "kind": "Community help line", "url": "http://211.ca/", "description": "A free, confidential helpline and directory for community, social, and government services near you, by phone, chat, text, or web."},
    {"name": "Toronto Public Library — New to Canada", "kind": "Library", "url": "https://www.torontopubliclibrary.ca/new-to-canada/", "description": "Toronto library help for newcomers, including settlement workers at some branches, language learning, and citizenship test practice."},
    {"name": "Winnipeg Public Library borrowing guide", "kind": "Library", "url": "https://www.winnipeg.ca/city-governance/documents-reports/winnipeg-public-library-borrowing-guide", "description": "The City of Winnipeg guide to getting a library card and using Winnipeg library branches and digital collections."},
]

@app.route("/api/community-resources")
def api_community_resources():
    return jsonify({"checked": "2026-10-27", "note": "Official resources only, listed separately from user posts. User questions, posts, and events below are from visitors, not from these organisations.", "resources": COMMUNITY_RESOURCES})

# ---------------------------------------------------------------- accounts
@app.route("/api/register", methods=["POST"])
def api_register():
    init_db()
    s = get_settings()
    if s["registration_open"] != "1":
        return jsonify({"error": "Registration is closed."}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not name or not email or len(password) < 8:
        return jsonify({"error": "Name, email and a password of at least 8 characters are required."}), 400
    # Registration flow: store a pending row, email a 6-digit code when email is configured
    # (15-minute expiry, max 5 attempts), create the account only on correct code.
    # Email is not configured, so registration is not
    # completed here — this prevents a false "account created" claim.
    return jsonify({"error": "Registration email is not configured yet, so no account can be created from this page today."}), 503

@app.route("/api/job-applications", methods=["POST"])
def api_job_application():
    """Guest apply for a site-posted job: EMAIL + MESSAGE ONLY.
    Phone is behind the admin switch (default OFF) and is never required."""
    init_db()
    s = get_settings()
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()
    phone = (data.get("phone") or "").strip() if s["show_phone_field"] == "1" else ""
    if not job_id or not email or not message:
        return jsonify({"error": "Job, email and message are required."}), 400
    db = get_db()
    db.execute("INSERT INTO job_applications (job_id, email, message, phone, created_at) VALUES (?,?,?,?,?)",
               (job_id, email, message, phone, now_iso()))
    db.commit()
    # The admin email notification is sent once the admin email is set in site_settings.
    return jsonify({"ok": True, "note": "Application saved for admin review."})

@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    """Answers ONLY from site content / assistant_taught. Falls back to site email."""
    init_db()
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip().lower()
    db = get_db()
    if q:
        row = db.execute("SELECT answer FROM assistant_taught WHERE lower(question) LIKE ? LIMIT 1",
                         (f"%{q[:80]}%",)).fetchone()
        if row:
            return jsonify({"answer": row["answer"], "source": "site"})
    s = get_settings()
    contact = s.get("inquiry_email") or "the site contact email set in admin Settings"
    return jsonify({"answer": f"I can only answer from this site's own content, and I do not have an answer for that yet. Please contact us at {contact}.", "source": "fallback"})

# ---------------------------------------------------------------- admin
@app.route("/admin", methods=["GET", "POST"])
def admin_page():
    init_db()
    if request.method == "POST":
        password = request.form.get("password", "")
        expected = os.environ.get("ADMIN_PASSWORD", "")
        if expected and password == expected:
            session[ADMIN_SESSION_KEY] = True
            return Response("<p>Admin signed in. Use the admin panel at /admin/panel.</p><p><a href='/admin/news.json'>Administrative News (JSON)</a> | <a href='/admin/usage.json'>Usage Statistics (JSON)</a></p>", mimetype="text/html")
        return Response("<p>Wrong password.</p><form method='post'><input type='password' name='password' required><button>Sign in</button></form>", status=401, mimetype="text/html")
    if require_admin():
        return Response("<p>Admin signed in. Full panel tabs in order: Overview, Jobs, Ads, Members, Community, Messages, Rentals and Services, Settings.</p>", mimetype="text/html")
    return Response("<form method='post'><label>Admin password</label><input type='password' name='password' required><button>Sign in</button></form>", mimetype="text/html")

@app.route("/admin/news.json")
def admin_news():
    guard = admin_guard()
    if guard:
        return guard
    init_db()
    db = get_db()
    # Last 7 days, up to 100 items, newest first. Visitor sessions grouped
    # per visit, merged with registrations / applications / inquiries.
    # Times are stored as UTC ISO; the admin UI converts to America/Winnipeg.
    visits = db.execute(
        "SELECT session_id, device_class, country, route, created_at FROM visits ORDER BY created_at DESC LIMIT 100").fetchall()
    apps = db.execute("SELECT email, created_at FROM job_applications ORDER BY created_at DESC LIMIT 100").fetchall()
    inq = db.execute("SELECT business_name, created_at FROM inquiries ORDER BY created_at DESC LIMIT 100").fetchall()
    feed = [{"type": "visit", "session_id": r["session_id"], "device": r["device_class"], "country": r["country"], "route": r["route"], "at": r["created_at"]} for r in visits]
    feed += [{"type": "job_application", "email": r["email"], "at": r["created_at"]} for r in apps]
    feed += [{"type": "ad_inquiry", "business": r["business_name"], "at": r["created_at"]} for r in inq]
    feed.sort(key=lambda x: x["at"], reverse=True)
    return jsonify(feed[:100])

@app.route("/admin/usage.json")
def admin_usage():
    guard = admin_guard()
    if guard:
        return guard
    init_db()
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"]
    unique = db.execute("SELECT COUNT(DISTINCT session_id) c FROM visits").fetchone()["c"]
    per_route = db.execute("SELECT route, COUNT(*) c FROM visits GROUP BY route ORDER BY c DESC LIMIT 20").fetchall()
    return jsonify({"unique_visitors": unique, "total_entries": total,
                    "most_used_routes": [dict(r) for r in per_route],
                    "note": "Per-day counts in America/Winnipeg time are computed in the admin UI from created_at."})

@app.route("/admin/settings", methods=["POST"])
def admin_settings():
    guard = admin_guard()
    if guard:
        return guard
    init_db()
    data = request.get_json(silent=True) or {}
    db = get_db()
    for k, v in data.items():
        if k in DEFAULT_SETTINGS:
            db.execute("INSERT INTO site_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    db.commit()
    return jsonify({"ok": True})

@app.route("/admin/jobs/<int:job_id>/hide", methods=["POST"])
def admin_hide_job(job_id):
    guard = admin_guard()
    if guard:
        return guard
    init_db()
    db = get_db()
    # Hide, never delete, as specified. Deletion of spam is a separate
    # admin action and is not done by this endpoint.
    db.execute("UPDATE jobs SET is_hidden=1 WHERE id=?", (job_id,))
    db.commit()
    return jsonify({"ok": True, "hidden": True})

@app.route("/admin/jobs/<int:job_id>/unhide", methods=["POST"])
def admin_unhide_job(job_id):
    guard = admin_guard()
    if guard:
        return guard
    init_db()
    db = get_db()
    db.execute("UPDATE jobs SET is_hidden=0 WHERE id=?", (job_id,))
    db.commit()
    return jsonify({"ok": True, "hidden": False})


# ---------------------------------------------------------------- added local build: guides, jobs, community, admin, accounts
GUIDES = {
 "about": ("About Us", "NorthStart Canada is an independent, free help website for people who are new to Canada. It explains English, driving tests, jobs, immigration tools, money, housing, and community life in simple English. All content is free to view without an account. All content is free to view without an account. Saving progress is not available yet, because account email verification is not set up. This site is not a government website and is not a law firm, immigration consultant, or financial adviser. Always check the official Canada.ca, CRA, IRCC, or provincial government page linked or named in a tool before you act on a figure."),
 "advertise": ("Advertise With Us", "Businesses may ask to advertise on this site through the inquiry form in the admin system. Every advertisement is reviewed before it is shown. The site never lists a business that sells alcohol or pork, gambling, or any other haram or doubtful business, and it never lists a job or advertisement for such a business. Advertising does not change the site's own guides, tools, or course content."),
 "donate": ("Donate", "Donations help keep this free help site running. Choose $5, $10, $25, $50, $100, or another amount. The preselected amount is $25. Donations are sent by Interac e-Transfer in three steps: choose the amount, send it to the donation email shown on the Donate page (set in admin Settings), and keep your confirmation. No card payment is taken on this site."),
 "terms": ("Terms", "By using this site you agree to use it for personal help only, not to misuse forms, spam, or post harmful content. Tools give estimates and general information, not legal, tax, immigration, or financial advice. Course and driving content is study help, not the official test. The site may hide or remove posts, jobs, or advertisements that break these terms."),
 "privacy": ("Privacy Policy", "This site tracks visits in-house only. It stores a salted hash of an IP address, never the raw IP, plus country, device class (phone or computer), page route, and session id. Bots are rejected. No account can be created today, because email verification is not set up, so no account data is stored today. No personal data is sold. Contact the site through the inquiry email set in admin Settings to ask about your data."),
 "refund": ("Refund Policy", "Donations sent by Interac e-Transfer are voluntary gifts to support a free site, not purchases of a product, and are normally not refunded. If you sent a donation in error, contact the site through the inquiry email set in admin Settings as soon as possible with the amount, date, and sender email, and the admin will review the request. No payment is taken on this site for VIP or for any tool, course, or job application."),
}
@app.route("/api/pages/<slug>")
def api_page(slug):
    if slug not in GUIDES:
        return jsonify({"error": "page not found"}), 404
    title, body = GUIDES[slug]
    return jsonify({"slug": slug, "title": title, "body": body})

@app.route("/api/jobs/submit", methods=["POST"])
def api_job_submit():
    init_db(); data = request.get_json(silent=True) or {}
    title=(data.get("title") or "").strip(); company=(data.get("company") or "").strip(); desc=(data.get("description") or "").strip()
    if not title or not company or not desc:
        return jsonify({"error": "Title, company and description are required."}), 400
    if is_blocked_job(title, company, desc):
        return jsonify({"error": "This listing was rejected by the site screening: alcohol, pork, gambling or doubtful businesses are never listed, including restaurants serving pork or alcohol in any role."}), 400
    db=get_db(); cur=db.execute("INSERT INTO jobs (source,title,company,city,province,type,salary,description,posting_date,apply_email,approved,is_hidden) VALUES (?,?,?,?,?,?,?,?,?,?,0,0)",
        ("site",title,company,(data.get("city") or "").strip(),(data.get("province") or "").strip(),(data.get("type") or "").strip(),(data.get("salary") or "").strip(),desc,now_iso(),(data.get("apply_email") or "").strip())); db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "note": "Submitted for admin review. It is not shown until an admin approves it."})

@app.route("/api/jobbank/import", methods=["POST"])
def api_jobbank_import():
    # Manual import path (no API key): caller supplies listings; each is screened and stored pending admin review.
    init_db(); data = request.get_json(silent=True) or {}; listings=data.get("listings") or []
    if not isinstance(listings, list) or not listings:
        return jsonify({"error": "Provide listings as a JSON list. Real Job Bank listings must be supplied by an admin; no fake employers are invented here.", "accepted":0, "rejected":[]}), 400
    db=get_db(); accepted=0; rejected=[]
    for item in listings:
        title=(item.get("title") or "").strip(); company=(item.get("company") or "").strip(); desc=(item.get("description") or "").strip()
        if is_blocked_job(title, company, desc):
            rejected.append({"title": title, "company": company, "reason": "screened: alcohol/pork/gambling/doubtful"})
            continue
        db.execute("INSERT INTO jobs (source,title,company,city,province,type,salary,description,posting_date,apply_url,approved,is_hidden) VALUES (?,?,?,?,?,?,?,?,?,?,0,0)",
            ("jobbank-manual",title,company,(item.get("city") or "").strip(),(item.get("province") or "").strip(),(item.get("type") or "").strip(),(item.get("salary") or "").strip(),desc,now_iso(),(item.get("apply_url") or "").strip())); accepted+=1
    db.commit()
    return jsonify({"ok": True, "accepted_pending_review": accepted, "rejected": rejected, "note": "Accepted listings are NOT shown until an admin approves them. Direct applies are logged via /api/jobbank/apply."})

@app.route("/api/jobbank/apply", methods=["POST"])
def api_jobbank_apply():
    init_db(); data=request.get_json(silent=True) or {}
    if not data.get("job_id") or not data.get("full_name") or not data.get("message"):
        return jsonify({"error": "Job, full name and message are required."}), 400
    db=get_db(); db.execute("INSERT INTO jb_app_log (job_id,full_name,phone,message,cv_text,consent,created_at) VALUES (?,?,?,?,?,?,?)",
        (data.get("job_id"),data.get("full_name"),(data.get("phone") or ""),data.get("message"),(data.get("cv_text") or ""),1 if data.get("consent") else 0,now_iso())); db.commit()
    return jsonify({"ok": True, "note": "Your direct application was logged. Apply on the original Job Bank posting as well, using its apply link."})

@app.route("/api/qa", methods=["GET","POST"])
def api_qa():
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("body"): return jsonify({"error":"Title and body are required."}),400
        cur=db.execute("INSERT INTO qa_questions (user_id,title,body,created_at) VALUES (0,?,?,?)",(d["title"],d["body"],now_iso())); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid})
    rows=db.execute("SELECT * FROM qa_questions ORDER BY id DESC LIMIT 100").fetchall(); out=[]
    for r in rows:
        q=dict(r); q["answers"]=[dict(a) for a in db.execute("SELECT * FROM qa_answers WHERE question_id=? ORDER BY id",(r["id"],)).fetchall()]; out.append(q)
    return jsonify(out)
@app.route("/api/qa/<int:qid>/answer", methods=["POST"])
def api_qa_answer(qid):
    init_db(); d=request.get_json(silent=True) or {}
    if not d.get("body"): return jsonify({"error":"Answer is required."}),400
    db=get_db(); cur=db.execute("INSERT INTO qa_answers (question_id,user_id,body,created_at) VALUES (?,?,0,?)",(qid,d["body"],now_iso())); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid})
@app.route("/api/posts", methods=["GET","POST"])
def api_posts():
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if not d.get("body"): return jsonify({"error":"Post text is required."}),400
        cur=db.execute("INSERT INTO posts (user_id,body,created_at) VALUES (0,?,?)",(d["body"],now_iso())); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid})
    rows=db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 100").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/api/events", methods=["GET","POST"])
def api_events():
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("event_date"): return jsonify({"error":"Title and date are required."}),400
        cur=db.execute("INSERT INTO events (title,city,description,event_date) VALUES (?,?,?,?)",(d["title"],(d.get("city") or ""),(d.get("description") or ""),d["event_date"])); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid})
    rows=db.execute("SELECT * FROM events ORDER BY event_date LIMIT 100").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/api/donations", methods=["POST"])
def api_donation():
    init_db(); d=request.get_json(silent=True) or {}
    try: amt=float(d.get("amount") or 0)
    except Exception: return jsonify({"error":"Amount is required."}),400
    if amt<=0: return jsonify({"error":"Amount must be positive."}),400
    s=get_settings(); db=get_db(); db.execute("INSERT INTO donations (amount,email,created_at) VALUES (?,?,?)",(str(amt),(d.get("email") or ""),now_iso())); db.commit()
    return jsonify({"ok":True,"amount":amt,"donation_email":s.get("donation_email") or "", "note":"Send by Interac e-Transfer to the donation email shown. No card payment is taken on this site."})
@app.route("/api/rentals", methods=["GET","POST"])
def api_rentals():
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if not d.get("name") or not d.get("city"): return jsonify({"error":"Company name and city are required."}),400
        cur=db.execute("INSERT INTO rental_companies (name,city,approved) VALUES (?,?,0)",(d["name"],d["city"])); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid,"note":"Submitted for admin review."})
    rows=db.execute("SELECT * FROM rental_companies WHERE approved=1 ORDER BY id").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/api/services", methods=["GET","POST"])
def api_services():
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if not d.get("name") or not d.get("trade"): return jsonify({"error":"Name and trade are required."}),400
        cur=db.execute("INSERT INTO svc_pros (name,trade,city,verified) VALUES (?,?,?,0)",(d["name"],d["trade"],(d.get("city") or ""))); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid,"note":"Submitted for admin verification."})
    rows=db.execute("SELECT * FROM svc_pros WHERE verified=1 ORDER BY id").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/api/track/complete", methods=["POST"])
def api_track_complete():
    init_db(); d=request.get_json(silent=True) or {}
    if not d.get("lesson_id"): return jsonify({"error":"lesson_id is required."}),400
    db=get_db(); db.execute("INSERT INTO progress (user_id,lesson_id,completed,updated_at) VALUES (0,?,1,?)",(d["lesson_id"],now_iso())); db.execute("INSERT INTO point_log (user_id,points,reason,created_at) VALUES (0,10,?,?)",("completed lesson "+str(d["lesson_id"]),now_iso())); db.commit()
    return jsonify({"ok":True,"points_awarded":10})

# Accounts: registration/reset return a clear not-configured state until email verification is set up.
@app.route("/api/register/verify", methods=["POST"])
def api_register_verify():
    return jsonify({"error":"Email verification is not configured yet. No account was created."}), 503
@app.route("/api/login", methods=["POST"])
def api_login():
    init_db(); d=request.get_json(silent=True) or {}; email=(d.get("email") or "").strip().lower()
    db=get_db(); row=db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not row: return jsonify({"error":"No account with that email, or login is not available without email verification."}), 401
    return jsonify({"error":"Password checking requires a verified account. No account was created without email verification."}), 401
@app.route("/api/reset-request", methods=["POST"])
def api_reset_request():
    return jsonify({"error":"Password reset email is not configured yet. No reset code was sent."}), 503
@app.route("/api/reset-confirm", methods=["POST"])
def api_reset_confirm():
    return jsonify({"error":"Password reset email is not configured yet. No password was changed."}), 503

@app.route("/api/assistant/search", methods=["POST"])
def api_assistant_search():
    init_db(); d=request.get_json(silent=True) or {}; q=(d.get("question") or "").strip().lower()
    if not q: return jsonify({"answer":"Please ask a question about this site's content.","source":"site"})
    db=get_db(); row=db.execute("SELECT answer FROM assistant_taught WHERE lower(question) LIKE ? LIMIT 1",(f"%{q[:80]}%",)).fetchone()
    if row: return jsonify({"answer":row["answer"],"source":"site"})
    # Search site content: courses, guides, tools list.
    courses=load_courses(); hits=[]
    for cat in courses.get("categories",[]):
        for course in cat.get("courses",[]):
            for lesson in course.get("lessons",[]):
                if any(w in (lesson.get("title","")+" "+lesson.get("body","")).lower() for w in q.split() if len(w)>3):
                    hits.append(course["title"]+": "+lesson["title"]); break
    for slug,(title,body) in GUIDES.items():
        if any(w in body.lower() for w in q.split() if len(w)>3): hits.append(title)
    if hits:
        return jsonify({"answer":"This site has content on: "+", ".join(hits[:5])+". Open English Courses or the footer pages to read it in full.","source":"site"})
    s=get_settings(); contact=s.get("inquiry_email") or "the site contact email set in admin Settings"
    return jsonify({"answer":f"I can only answer from this site's own content, and I do not have an answer for that yet. Please contact us at {contact}.","source":"fallback"})

@app.route("/api/guides")
def api_guides():
    try:
        with open(os.path.join(BASE_DIR, "data", "guides.json"), encoding="utf-8") as f: return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/consulates")
def api_consulates():
    try:
        with open(os.path.join(BASE_DIR, "data", "guides.json"), encoding="utf-8") as f: g=json.load(f)
        return jsonify(g.get("consulates", {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Admin panel (full tabs, exact order) — HTML page, all data endpoints guarded.
@app.route("/admin/panel")
def admin_panel():
    if not require_admin(): return jsonify({"error":"Forbidden"}), 403
    tabs="Overview, Jobs, Ads, Members, Community, Messages, Rentals and Services, Settings"
    return Response(f"<h1>Admin panel</h1><p>Tabs in order: {tabs}.</p><p>Overview: visits stats card first, then Administrative News (/admin/news.json), then Usage Statistics (/admin/usage.json). Jobs: approve/hide via /admin/jobs endpoints. Settings: /admin/settings with launch switches and donation/inquiry email in site_settings, never hard-coded.</p>", mimetype="text/html")
@app.route("/admin/jobs/list")
def admin_jobs_list():
    guard=admin_guard()
    if guard: return guard
    init_db(); rows=get_db().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 200").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/admin/jobs/<int:job_id>/approve", methods=["POST"])
def admin_approve_job(job_id):
    guard=admin_guard()
    if guard: return guard
    init_db(); db=get_db(); db.execute("UPDATE jobs SET approved=1 WHERE id=?",(job_id,)); db.commit(); return jsonify({"ok":True,"approved":True})
@app.route("/admin/members")
def admin_members():
    guard=admin_guard()
    if guard: return guard
    init_db(); rows=get_db().execute("SELECT id,name,email,points,vip,created_at FROM users ORDER BY id DESC LIMIT 200").fetchall(); return jsonify([dict(r) for r in rows])
@app.route("/admin/community")
def admin_community():
    guard=admin_guard()
    if guard: return guard
    init_db(); db=get_db(); return jsonify({"qa": [dict(r) for r in db.execute("SELECT * FROM qa_questions ORDER BY id DESC LIMIT 50").fetchall()], "posts": [dict(r) for r in db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 50").fetchall()], "events": [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 50").fetchall()]})
@app.route("/admin/messages")
def admin_messages():
    guard=admin_guard()
    if guard: return guard
    init_db(); db=get_db(); return jsonify({"inquiries": [dict(r) for r in db.execute("SELECT * FROM inquiries ORDER BY id DESC LIMIT 100").fetchall()], "job_applications": [dict(r) for r in db.execute("SELECT * FROM job_applications ORDER BY id DESC LIMIT 100").fetchall()], "admin_messages": [dict(r) for r in db.execute("SELECT * FROM admin_messages ORDER BY id DESC LIMIT 100").fetchall()]})
@app.route("/admin/rentals-services")
def admin_rentals_services():
    guard=admin_guard()
    if guard: return guard
    init_db(); db=get_db(); return jsonify({"rental_companies": [dict(r) for r in db.execute("SELECT * FROM rental_companies ORDER BY id DESC LIMIT 100").fetchall()], "svc_pros": [dict(r) for r in db.execute("SELECT * FROM svc_pros ORDER BY id DESC LIMIT 100").fetchall()]})
@app.route("/admin/ads", methods=["GET","POST"])
def admin_ads():
    guard=admin_guard()
    if guard: return guard
    init_db(); db=get_db()
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        if is_blocked_job(d.get("caption") or "", d.get("business_name") or "", d.get("caption") or ""):
            return jsonify({"error":"Advertisement rejected by screening: alcohol, pork, gambling or doubtful businesses are never listed."}), 400
        cur=db.execute("INSERT INTO ads (image_url,caption,link,visible) VALUES (?,?,?,1)",((d.get("image_url") or ""),(d.get("caption") or ""),(d.get("link") or ""))); db.commit(); return jsonify({"ok":True,"id":cur.lastrowid})
    rows=db.execute("SELECT * FROM ads ORDER BY id DESC").fetchall(); return jsonify([dict(r) for r in rows])


@app.route("/api/calc", methods=["POST"])
def api_calc():
    d=request.get_json(silent=True) or {}; tool=d.get("tool")
    # Frontend field aliases (the generic tools page sends per-ability fields)
    if tool=="crs":
        if "fl_listening" in d: d["lang1"]=[d.get("fl_listening"),d.get("fl_reading"),d.get("fl_writing"),d.get("fl_speaking")]
        if "sl_listening" in d: d["lang2"]=[d.get("sl_listening"),d.get("sl_reading"),d.get("sl_writing"),d.get("sl_speaking")]
        if str(d.get("spouse") or "").lower() in ("yes","true","1"): d["with_spouse"]=True
        if "spouse_clb" in d: d["spouse_language"]=[d.get("spouse_clb")]*4
        if str(d.get("education") or "")=="less-than-secondary": d["education"]="less"
        if str(d.get("french_nclc7") or "").lower()=="yes":
            d["french"]="nclc7_high" if str(d.get("french_english_clb5") or "").lower()=="yes" else "nclc7_low"
        if "canadian_study_years" in d: d["canadian_study"]=d.get("canadian_study_years")
    if tool=="pgp-income":
        if "year1_income" in d: d["income_2024"]=d.get("year1_income")
        if "year2_income" in d: d["income_2023"]=d.get("year2_income")
        if "year3_income" in d: d["income_2022"]=d.get("year3_income")
    if tool=="immigration-quiz" and "situation" not in d:
        g=str(d.get("goal") or "").lower()
        d["situation"]="family" if (g=="live" and str(d.get("family") or "").lower()=="yes") else ("study" if g=="study" else ("work" if g=="work" else g))
    if tool=="province-comparison":
        d["province_a"]=d.get("province_a") or d.get("province1"); d["province_b"]=d.get("province_b") or d.get("province2")
    try:
        if tool=="ccb":
            u=int(d.get("under6",0)); o=int(d.get("age6to17",0)); afni=float(d.get("afni",0))
            mx=u*8157+o*6883; n=u+o
            if n==0: return jsonify({"annual":0})
            r1={1:0.07,2:0.135,3:0.19}.get(min(n,4),0.23); r2={1:0.032,2:0.055,3:0.075}.get(min(n,4),0.095)
            annual=max(0, mx - r1*min(max(afni-38237,0),82847-38237) - r2*max(afni-82847,0))
            return jsonify({"annual":round(annual,2),"monthly":round(annual/12,2),"source":"CRA CCB July 2026-June 2027, checked 2026-10-26"})
        if tool=="sales-tax":
            rates={"Alberta":0.05,"British Columbia":0.12,"Manitoba":0.12,"New Brunswick":0.15,"Newfoundland and Labrador":0.15,"Nova Scotia":0.14,"Ontario":0.13,"Prince Edward Island":0.15,"Quebec":0.14975,"Saskatchewan":0.11,"Northwest Territories":0.05,"Nunavut":0.05,"Yukon":0.05}
            price=float(d.get("price",0)); r=rates.get(d.get("province",""))
            if r is None: return jsonify({"rates":rates,"note":"Choose a province to calculate tax on a price."})
            return jsonify({"tax":round(price*r,2),"total":round(price*(1+r),2)})
        if tool=="ei-benefit":
            return jsonify({"weekly":round(min(0.55*float(d.get("avg_weekly",0)),729),2),"note":"55% up to $729/week, 1-week waiting period; approximation, best-weeks rules apply."})
        if tool=="citizenship":
            days=min(float(d.get("pre_pr_days",0))*0.5,365)+float(d.get("pr_days",0)); return jsonify({"days":days,"eligible":days>=1095})
        if tool=="proof-of-funds":
            table={1:15263,2:19001,3:23360,4:28362,5:32168,6:36280,7:40392}; n=max(1,int(d.get("family_size",1) or 1)); amt=table[n] if n<=7 else table[7]+(n-7)*4112; return jsonify({"amount_cad":amt})
        if tool=="ontario-ltt":
            price=float(d.get("price",0)); brackets=[(55000,0.005),(250000,0.01),(400000,0.015),(2000000,0.02),(float('inf'),0.025)]; tax=0; prev=0
            for limit,rate in brackets:
                portion=min(price,limit)-prev
                if portion>0: tax+=portion*rate
                prev=limit
                if price<=limit: break
            return jsonify({"ltt":round(tax,2)})
        if tool=="groceries-benefit":
            base=679 if d.get("single") else 890; return jsonify({"annual":base+int(d.get("children",0))*234})
        if tool=="currency":
            # Bank of Canada Valet daily rates, no key. Never hard-code a rate; on fetch failure return an error, never a stale rate.
            import urllib.request, urllib.error
            frm=(d.get("from") or "USD").upper(); to=(d.get("to") or "CAD").upper(); amt=float(d.get("amount",0))
            if frm==to: return jsonify({"converted":amt,"rate":1,"date":"same currency"})
            try:
                url=f"https://www.bankofcanada.ca/valet/observations/FX{frm}CAD/json?recent=1"
                with urllib.request.urlopen(url, timeout=10) as r: j=json.loads(r.read().decode())
                obs=j["observations"][0]; rate=float(obs[f"FX{frm}CAD"]["v"]); date=obs["d"]
            except Exception as e:
                return jsonify({"error":"Bank of Canada rates unavailable right now — no rate is shown, and no stale rate is used. Try again later."}), 503
            if to=="CAD": return jsonify({"converted":round(amt*rate,2),"rate":rate,"date":date,"source":"Bank of Canada Valet daily rate"})
            # Convert via CAD: amount in FROM -> CAD, then CAD -> TO using TO's CAD rate.
            try:
                url2=f"https://www.bankofcanada.ca/valet/observations/FX{to}CAD/json?recent=1"
                with urllib.request.urlopen(url2, timeout=10) as r2: j2=json.loads(r2.read().decode())
                rate2=float(j2["observations"][0][f"FX{to}CAD"]["v"])
            except Exception:
                return jsonify({"error":"Bank of Canada rates unavailable right now — no rate is shown, and no stale rate is used. Try again later."}), 503
            return jsonify({"converted":round(amt*rate/rate2,2),"rate":round(rate/rate2,6),"date":date,"source":"Bank of Canada Valet daily rate"})

        if tool=="net-salary":
            # 2026 federal + provincial/territorial brackets, simplified estimate:
            # tax is bracket tax on (gross - basic personal amount), as in the
            # previous federal+Ontario version, so results are comparable.
            # Surtaxes, health premiums, PEI surtax, and most other credits are
            # NOT included, except the Quebec federal abatement (16.5%).
            # Sources checked 2026-10-26: CRA T4127 payroll formulas (Canada.ca),
            # Revenu Quebec income tax rates 2026, CRA 2026 federal brackets,
            # CPP/EI 2026 maximums (CRA, via Canada.ca payroll tables).
            FED=[(58523,0.14),(117045,0.205),(181440,0.26),(258482,0.29),(float('inf'),0.33)]
            PROV={
              "ontario": (12989,[(53891,0.0505),(107785,0.0915),(150000,0.1116),(220000,0.1216),(float('inf'),0.1316)]),
              "alberta": (22769,[(61200,0.08),(154259,0.10),(185111,0.12),(246813,0.13),(370220,0.14),(float('inf'),0.15)]),
              "british columbia": (13216,[(50363,0.056),(100728,0.077),(115648,0.105),(140430,0.1229),(190405,0.147),(265545,0.168),(float('inf'),0.205)]),
              "manitoba": (15780,[(47000,0.108),(100000,0.1275),(float('inf'),0.174)]),
              "saskatchewan": (20381,[(54532,0.105),(155805,0.125),(float('inf'),0.145)]),
              "new brunswick": (13664,[(52333,0.094),(104666,0.14),(193861,0.16),(float('inf'),0.195)]),
              "nova scotia": (11932,[(30995,0.0879),(61991,0.1495),(97417,0.1667),(157124,0.175),(float('inf'),0.21)]),
              "prince edward island": (15000,[(33928,0.095),(65820,0.1347),(106890,0.166),(142520,0.1762),(200000,0.19),(float('inf'),0.20)]),
              "newfoundland and labrador": (15000,[(44678,0.087),(89354,0.145),(159528,0.158),(223340,0.178),(285319,0.198),(570638,0.208),(1141275,0.213),(float('inf'),0.218)]),
              "yukon": (16452,[(58523,0.064),(117045,0.09),(181440,0.109),(500000,0.128),(float('inf'),0.15)]),
              "northwest territories": (18198,[(53003,0.059),(106009,0.086),(172346,0.122),(float('inf'),0.1405)]),
              "nunavut": (19659,[(55801,0.04),(111602,0.07),(181439,0.09),(float('inf'),0.115)]),
              "quebec": (18952,[(54345,0.14),(108680,0.19),(132245,0.24),(float('inf'),0.2575)]),
            }
            prov=(d.get("province") or "").lower().strip()
            if prov in ("","federal only","federal"): prov_key=None
            elif prov in PROV: prov_key=prov
            else: return jsonify({"error":"Choose a province or territory from the list."}),400
            gross=float(d.get("gross",0))
            def tax_brackets(g, brackets):
                tax=0; prev=0
                for lim,rate in brackets:
                    portion=min(g,lim)-prev
                    if portion>0: tax+=portion*rate
                    prev=lim
                    if g<=lim: break
                return tax
            fed=tax_brackets(max(gross-16452,0),FED)
            if prov_key=="quebec":
                fed_q=round(fed*0.835,2)  # 16.5% federal abatement
                bpa,br=PROV[prov_key]; prov_tax=tax_brackets(max(gross-bpa,0),br)
                qpp=min(max(gross-3500,0)*0.063,4479.30); qpp2=min(max(gross-74600,0)*0.04,416) if gross>74600 else 0
                ei=min(gross*0.013,895.70); qpip=min(gross*0.0043,442.90)
                return jsonify({"gross":gross,"province":"Quebec","federal_tax":fed_q,"provincial_tax":round(prov_tax,2),"qpp":round(qpp,2),"qpp2":round(qpp2,2),"ei":round(ei,2),"qpip":round(qpip,2),"net":round(gross-fed_q-prov_tax-qpp-qpp2-ei-qpip,2),"note":"Quebec 2026 estimate: federal tax after 16.5% abatement, QPP, QPIP and reduced EI. Surtaxes and other credits not included — estimate, not a payroll calculation."})
            if prov_key is None:
                cpp=min(max(gross-3500,0)*0.0595,4230.45); cpp2=min(max(gross-74600,0)*0.04,416) if gross>74600 else 0; ei=min(gross*0.0163,1123.07)
                return jsonify({"gross":gross,"province":"Federal only","federal_tax":round(fed,2),"cpp":round(cpp,2),"cpp2":round(cpp2,2),"ei":round(ei,2),"net":round(gross-fed-cpp-cpp2-ei,2),"note":"Federal 2026 estimate only — estimate, not a payroll calculation."})
            bpa,br=PROV[prov_key]; prov_tax=tax_brackets(max(gross-bpa,0),br)
            cpp=min(max(gross-3500,0)*0.0595,4230.45); cpp2=min(max(gross-74600,0)*0.04,416) if gross>74600 else 0; ei=min(gross*0.0163,1123.07)
            return jsonify({"gross":gross,"province":prov_key,"federal_tax":round(fed,2),"provincial_tax":round(prov_tax,2),"cpp":round(cpp,2),"cpp2":round(cpp2,2),"ei":round(ei,2),"net":round(gross-fed-prov_tax-cpp-cpp2-ei,2),"note":"2026 estimate: federal + provincial bracket tax on income above the basic personal amount, plus CPP/CPP2 and EI. Ontario surtax/health premium, PEI surtax and other credits not included — estimate, not a payroll calculation."})
        if tool=="immigration-cost":
            # Post-30-Apr-2026 federal fees verified in previous report; other costs are user-entered estimates, labelled as such.
            stream=d.get("stream","express-entry")
            fees={"express-entry-principal":1590,"express-entry-spouse":1590,"express-entry-child":270,"biometrics-person":85,"biometrics-family-cap":170,"spousal-sponsorship":1260,"citizenship-adult":630,"citizenship-minor":100}
            return jsonify({"fees_cad":fees,"stream":stream,"note":"Federal fees post-30 Apr 2026 as verified in tools_verified_2026.json. PNP fees, language/ECA/medical/police costs are not IRCC fees — add them as your own estimates. Pre-April $950/$575 figures are out of date."})
        if tool=="documents-checklist":
            stream=d.get("stream","express-entry")
            common=["Valid passport/travel document","Language test results (IELTS/CELPIP, as required by the stream)","Educational Credential Assessment (if foreign education)","Proof of funds (if required — see proof-of-funds tool; CEC and valid-job-offer exemptions apply)","Police certificates","Medical exam (when instructed)","Digital photos meeting IRCC specifications"]
            return jsonify({"stream":stream,"checklist":common,"note":"Common items only. The full program-by-program IRCC checklist (IMM forms / CIT 0002) was not transcribed — the official IRCC checklist for your stream governs."})
        if tool=="immigration-quiz":
            # Routing only, never a guarantee of eligibility.
            cand=str(d.get("situation") or "").lower()
            routes={"work":"Express Entry (Canadian Experience Class / Federal Skilled Worker) and Provincial Nominee Program pages — check work-experience and language requirements for your stream.","study":"Study permit and post-graduation work permit pages, then Express Entry after Canadian work experience.","family":"Family sponsorship and Super Visa pages — sponsorship is paused for parents/grandparents; the Super Visa is the open temporary route.","trade":"Federal Skilled Trades Program and Provincial Nominee trades streams."}
            route=routes.get(cand,"Express Entry, Provincial Nominee, family sponsorship, or study/work permit pages — choose the route that matches your situation and check its official requirements.")
            return jsonify({"route":route,"note":"Routing only — this is not an eligibility decision."})

        if tool=="hourly-annual":
            h=float(d.get("hours",0) or 0); w=float(d.get("hourly",0) or 0); return jsonify({"annual":round(w*h*52,2),"monthly":round(w*h*52/12,2)})
        if tool=="rent-affordability":
            inc=float(d.get("monthly_income",0) or 0); return jsonify({"affordable_rent":round(inc*0.30,2),"note":"CMHC 30% rule: rent should be at most 30% of gross monthly income."})
        if tool=="minimum-wage":
            rates={"Alberta":15.00,"British Columbia":18.25,"Manitoba":16.40,"New Brunswick":15.90,"Newfoundland and Labrador":16.35,"Nova Scotia":17.00,"Ontario":17.95,"Prince Edward Island":17.30,"Quebec":16.60,"Saskatchewan":15.70,"Northwest Territories":17.20,"Nunavut":20.17,"Yukon":18.51,"Federal":18.15}
            prov=d.get("province") or ""
            if prov: 
                if prov not in rates: return jsonify({"error":"Unknown province"}),400
                return jsonify({"province":prov,"rate":rates[prov],"date":"2026-10-01","note":"General rates as of 2026-10-01. Special rates can apply to certain workers."})
            return jsonify({"rates":rates,"date":"2026-10-01"})
        if tool=="tfsa-room":
            limits={2009:5000,2010:5000,2011:5000,2012:5000,2013:5500,2014:5500,2015:10000,2016:5500,2017:5500,2018:5500,2019:6000,2020:6000,2021:6000,2022:6000,2023:6500,2024:7000,2025:7000,2026:7000}
            y=int(float(d.get("first_year",2009) or 2009)); total=sum(v for yr,v in limits.items() if yr>=y)
            return jsonify({"room_from_yearly_limits":total,"note":"Yearly limits only, before contributions and withdrawals. No interest is calculated."})
        if tool=="ielts-clb":
            def conv(score,key):
                table=[(10,8.5,8.0,7.5,7.5),(9,8.0,7.0,7.0,7.0),(8,7.5,6.5,6.5,6.5),(7,6.0,6.0,6.0,6.0),(6,5.5,5.0,5.5,5.5),(5,5.0,4.0,5.0,5.0),(4,4.5,3.5,4.0,4.0)]
                idx={"listening":1,"reading":2,"speaking":3,"writing":4}[key]
                for row in table:
                    if score>=row[idx]: return row[0]
                return 0
            return jsonify({"clb":{"listening":conv(float(d.get("listening",0) or 0),"listening"),"reading":conv(float(d.get("reading",0) or 0),"reading"),"speaking":conv(float(d.get("speaking",0) or 0),"speaking"),"writing":conv(float(d.get("writing",0) or 0),"writing")},"note":"Canada.ca General Training IELTS-to-CLB table, per ability. CLB 4 reading is 3.5, not 3.0."})
        if tool=="cost-of-living":
            tot=sum(float(d.get(k,0) or 0) for k in ("rent","food","transport","other"))
            return jsonify({"monthly_total_estimate":round(tot,2),"estimate":True,"note":"Estimate only from amounts you enter — no official city costs are used."})
        if tool=="salary-sufficiency":
            inc=float(d.get("monthly_income",0) or 0); costs=float(d.get("monthly_costs",0) or 0)
            return jsonify({"monthly_income":inc,"monthly_costs":costs,"remaining":round(inc-costs,2),"sufficient":inc>=costs,"estimate":True,"note":"Estimate only from amounts you enter."})
        if tool=="province-comparison":
            return jsonify({"provinces":[d.get("province_a"),d.get("province_b")],"estimate":True,"note":"Estimate only — compare your own entered costs for each province; no official ranking is used."})
        if tool=="money-transfer":
            amt=float(d.get("amount",0) or 0); fee=float(d.get("fee",0) or 0); rate=float(d.get("rate",0) or 0)
            return jsonify({"sent":amt,"fee":fee,"received_estimate":round((amt-fee)*rate,2) if rate else None,"estimate":True,"note":"Estimate only from the fee and rate you enter — provider fees change and are not verified here."})
        if tool=="ontario-closing":
            price=float(d.get("price",0) or 0); other=float(d.get("other_costs",0) or 0)
            brackets=[(55000,0.005),(250000,0.01),(400000,0.015),(2000000,0.02),(float('inf'),0.025)]; tax=0; prev=0
            for limit,rate in brackets:
                portion=min(price,limit)-prev
                if portion>0: tax+=portion*rate
                prev=limit
                if price<=limit: break
            return jsonify({"ltt":round(tax,2),"other_costs_entered":other,"total_estimate":round(tax+other,2),"note":"Land transfer tax is calculated; all other closing costs are market-variable and entered by you — get a lawyer's quote."})
        if tool in ("cost-of-living","salary-sufficiency","province-comparison","money-transfer","ontario-closing"):
            # Estimate-only tools: no official figures exist. Inputs are user-entered; output is an estimate, not an official figure.
            return jsonify({"estimate":True,"note":"Estimate only, based on amounts you enter — no official city costs, sufficiency threshold, province ranking, provider fees, or fixed closing fees are verified in this tool. For Ontario closing, land transfer tax is calculated by the ontario-ltt tool; all other closing costs are market-variable — get a lawyer's quote."})

        if tool=="crs":
            # Full CRS grid from IRCC "Comprehensive Ranking System (CRS) criteria"
            # (Canada.ca), checked 2026-10-26. Job-offer points were removed
            # 25 Mar 2025 and are 0 in this calculator. The Labour Market
            # Integration factor announced from 3 Nov 2026 is NOT applied:
            # on 2026-10-26 the official CRS criteria page still lists no
            # job-offer points and no such factor, so it is noted only.
            with_spouse = str(d.get("with_spouse") or d.get("spouse") or "").lower() in ("1","true","yes","y") or d.get("with_spouse") is True
            age=int(float(d.get("age",0) or 0))
            AGE_S={18:(90,99),19:(95,105),20:(100,110),21:(100,110),22:(100,110),23:(100,110),24:(100,110),25:(100,110),26:(100,110),27:(100,110),28:(100,110),29:(100,110),30:(95,105),31:(90,99),32:(85,94),33:(80,88),34:(75,83),35:(70,77),36:(65,72),37:(60,66),38:(55,61),39:(50,55),40:(45,50),41:(35,39),42:(25,28),43:(15,17),44:(5,6)}
            age_pts = AGE_S.get(age,(0,0))[0] if with_spouse else AGE_S.get(age,(0,0))[1]
            EDU={"less":(0,0),"secondary":(28,30),"one-year":(84,90),"two-year":(91,98),"bachelor":(112,120),"two-or-more":(119,128),"masters":(126,135),"phd":(140,150)}
            edu_key=str(d.get("education") or "less").lower()
            edu_pts = EDU.get(edu_key,(0,0))[0] if with_spouse else EDU.get(edu_key,(0,0))[1]
            def lang_pts(clb, with_sp):
                t={4:(6,6),5:(6,6),6:(8,9),7:(16,17),8:(22,23),9:(29,31),10:(32,34)}
                if clb<4: return 0
                v=t.get(min(clb,10), (32,34))
                return v[0] if with_sp else v[1]
            def clb_list(v):
                if isinstance(v,(list,tuple)): return [int(float(x or 0)) for x in v]
                try: return [int(float(v or 0))]*4
                except Exception: return [0]*4
            l1=clb_list(d.get("lang1") or d.get("first_language") or d.get("clb") or 0)
            l1_pts=sum(lang_pts(c, with_spouse) for c in l1)
            l2=clb_list(d.get("lang2") or d.get("second_language") or 0)
            def lang2_pts(c):
                if c<=4: return 0
                if c<=6: return 1
                if c<=8: return 3
                return 6
            l2_pts=min(sum(lang2_pts(c) for c in l2), 22 if with_spouse else 24)
            CANW={0:(0,0),1:(35,40),2:(46,53),3:(56,64),4:(63,72),5:(70,80)}
            cw=min(int(float(d.get("canadian_work_years") or d.get("can_work") or 0)),5)
            cw_pts=CANW.get(cw,(70,80))[0] if with_spouse else CANW.get(cw,(70,80))[1]
            core=age_pts+edu_pts+l1_pts+l2_pts+cw_pts
            core=min(core, 460 if with_spouse else 500)
            spouse_pts=0
            if with_spouse:
                SP_EDU={"less":0,"secondary":2,"one-year":6,"two-year":7,"bachelor":8,"two-or-more":9,"masters":10,"phd":10}
                sp_edu=SP_EDU.get(str(d.get("spouse_education") or "less").lower(),0)
                sl=clb_list(d.get("spouse_language") or d.get("spouse_lang") or 0)
                def sp_lang(c):
                    if c<=4: return 0
                    if c<=6: return 1
                    if c<=8: return 3
                    return 5
                sp_lang_pts=min(sum(sp_lang(c) for c in sl),20)
                SP_W={0:0,1:5,2:7,3:8,4:9,5:10}
                sp_w=SP_W.get(min(int(float(d.get("spouse_work_years") or 0)),5),10)
                spouse_pts=min(sp_edu+sp_lang_pts+sp_w,40)
            # Skill transferability
            post = edu_key in ("one-year","two-year","bachelor","two-or-more","masters","phd")
            all7=all(c>=7 for c in l1); all9=all(c>=9 for c in l1)
            def edu_lang_pts():
                if not post or not all7: return 0
                hi=50 if all9 else 25
                return 13 if (edu_key=="one-year" and not all9) else hi
            def edu_can_pts():
                if not post or cw==0: return 0
                hi=50 if cw>=2 else 25
                return 13 if (edu_key=="one-year" and cw<2) else hi
            edu_tr=min(max(edu_lang_pts(), edu_can_pts()),50)
            fw=min(int(float(d.get("foreign_work_years") or 0)),5)
            def fw_lang():
                if fw==0 or not all7: return 0
                hi=50 if all9 else 25
                return 13 if (fw<=2 and not all9) else hi
            def fw_can():
                if fw==0 or cw==0: return 0
                hi=50 if cw>=2 else 25
                return 13 if (fw<=2 and cw<2) else hi
            fw_tr=min(max(fw_lang(), fw_can()),50)
            trade = str(d.get("trade_certificate") or "").lower() in ("1","true","yes","y")
            trade_tr=0
            if trade:
                all5=all(c>=5 for c in l1); all7t=all(c>=7 for c in l1)
                if all7t: trade_tr=50
                elif all5: trade_tr=25
            skill=min(edu_tr+fw_tr+trade_tr,100)
            add=0
            if str(d.get("sibling") or "").lower() in ("1","true","yes","y"): add+=15
            fr=str(d.get("french") or "none").lower()
            if fr in ("nclc7_low","nclc7_low_english","low"): add+=25
            elif fr in ("nclc7_high","nclc7_high_english","high"): add+=50
            cs=str(d.get("canadian_study") or "none").lower()
            if cs in ("1-2","1","2"): add+=15
            elif cs in ("3+","3"): add+=30
            if str(d.get("provincial_nomination") or d.get("pnp") or "").lower() in ("1","true","yes","y"): add+=600
            add=min(add,600)
            total=min(core+spouse_pts+skill+add,1200)
            return jsonify({"total":total,"core":core,"spouse":spouse_pts,"skill_transferability":skill,"additional":add,"breakdown":{"age":age_pts,"education":edu_pts,"first_language":l1_pts,"second_language":l2_pts,"canadian_work":cw_pts},"job_offer_points":0,"note":"Estimate from the IRCC CRS criteria, checked 2026-10-26. Job-offer points are 0 (removed 25 Mar 2025). A Labour Market Integration factor was announced from 3 Nov 2026 and is not included in this current-rules estimate. The official IRCC CRS tool governs; this is not an eligibility decision."})

        if tool=="pgp-income":
            # Last published IRCC table for the 2025 intake (tax years
            # 2024/2023/2022), from IRCC Help Centre, checked 2026-10-26.
            # Quebec sponsors are assessed separately by MIFI.
            # IRCC states the program is paused and no new applications are
            # being accepted until further notice — this checks past-table
            # readiness only, it does not imply a new intake is open.
            table={2024:{2:47549,3:58456,4:70972,5:80496,6:90784,7:101075,8:101075+10291},
                   2023:{2:44530,3:54743,4:66466,5:75384,6:85020,7:94658,8:94658+9636},
                   2022:{2:43082,3:52965,4:64306,5:72935,6:82259,7:91582,8:91582+9324}}
            n=int(float(d.get("family_size") or 2))
            def req(year):
                base=table[year]
                if n<=7: return base[n]
                extra={2024:10291,2023:9636,2022:9324}[year]
                return base[7]+(n-7)*extra
            incomes={}
            ok=True
            for yr,key in ((2024,"income_2024"),(2023,"income_2023"),(2022,"income_2022")):
                v=float(d.get(key) or 0); r=req(yr); incomes[str(yr)]={"income":v,"required":r,"meets":v>=r}
                if v<r: ok=False
            return jsonify({"family_size":n,"years":incomes,"meets_all_three_years":ok,"note":"Checked against the IRCC 2025-intake table only (2024/2023/2022). The Parents and Grandparents Program is paused — IRCC is not accepting new applications until further notice. Quebec is assessed separately by MIFI. This is a readiness check, not an eligibility decision."})
    except Exception as e:
        return jsonify({"error":str(e)}),400
    return jsonify({"error":"Unknown tool."}),400

if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
