"""
PC Home v2 — browser-based Android-style launcher for your Windows PC.

New in this version:
  - "This PC" root: Desktop, Start Menu, and every drive letter (incl.
    external/USB drives), so nothing is locked to just Desktop anymore
  - Rename and delete (files & folders)
  - Download a file straight to your phone
  - Upload a file from your phone into the current folder
  - Password lock screen — required before any API call works

Setup (on the PC):
    pip install flask pyautogui

>>> SET YOUR PASSWORD BELOW before running <<<

Run:
    python pc_home.py

Then on your phone (same WiFi/LAN):
    http://<pc-ip>:4321
"""

from flask import Flask, request, jsonify, send_file, Response
import subprocess
import os
import sys
import string
import secrets
import shutil
import time
import io
import threading
import mimetypes
import winreg

# Ensure working directory is always this script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    import pyautogui
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None

try:
    import mss
except ImportError:
    mss = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

try:
    import pystray
except ImportError:
    pystray = None

app = Flask(__name__)

# ---- EDIT THIS ----
PASSWORD = "changeme123"
# --------------------

SESSION_TOKEN = secrets.token_hex(24)  # regenerated every time you start the server

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
START_MENU_USER = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"
)
START_MENU_COMMON = os.path.join(
    os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"
)

ICONS = {
    ".py": "🐍", ".js": "📜", ".exe": "⚙️", ".bat": "⚙️", ".cmd": "⚙️",
    ".lnk": "🔗", ".ahk": "🤖", ".msi": "📦",
    ".docx": "📄", ".doc": "📄", ".pdf": "📕", ".txt": "📝", ".md": "📝",
    ".jpg": "🖼️", ".jpeg": "🖼️", ".png": "🖼️", ".gif": "🖼️", ".webp": "🖼️",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵",
    ".mp4": "🎬", ".mkv": "🎬", ".mov": "🎬", ".avi": "🎬", ".webm": "🎬",
    ".zip": "🗜️", ".rar": "🗜️", ".7z": "🗜️",
    ".gguf": "🧠", ".safetensors": "🧠",
}


# ---------------- auth ----------------

def check_token():
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    return token == SESSION_TOKEN


@app.before_request
def require_auth():
    if request.path == "/" or request.path == "/api/auth":
        return
    if request.path.startswith("/api/") and not check_token():
        return jsonify(error="unauthorized"), 401


@app.route("/api/auth", methods=["POST"])
def api_auth():
    pw = (request.json or {}).get("password", "")
    if secrets.compare_digest(pw, PASSWORD):
        return jsonify(ok=True, token=SESSION_TOKEN)
    return jsonify(ok=False), 403


# ---------------- filesystem helpers ----------------

def list_drives():
    drives = []
    for letter in string.ascii_uppercase:
        path = f"{letter}:\\"
        if os.path.exists(path):
            drives.append(path)
    return drives


def resolve_virtual(path: str):
    """Map special virtual entries to real absolute paths."""
    if path in ("ROOT", ""):
        return None  # handled specially in api_list
    if path == "Desktop":
        return DESKTOP
    if path == "Start Menu":
        return START_MENU_USER if os.path.isdir(START_MENU_USER) else START_MENU_COMMON
    return path  # already an absolute path


# ---------------- listing / launching ----------------

@app.route("/api/list")
def api_list():
    raw = request.args.get("path", "ROOT")

    if raw in ("ROOT", ""):
        items = [
            {"name": "Desktop", "type": "folder", "icon": "🖥️", "path": "Desktop"},
            {"name": "Start Menu", "type": "folder", "icon": "📋", "path": "Start Menu"},
        ]
        for d in list_drives():
            items.append({"name": d, "type": "folder", "icon": "💽", "path": d})
        return jsonify(items=items, current=raw)

    full = resolve_virtual(raw)
    if not full or not os.path.isdir(full):
        return jsonify(error="not a folder"), 404

    items = []
    try:
        entries = sorted(os.scandir(full), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        return jsonify(error="permission denied"), 403

    for entry in entries:
        if entry.name.startswith("."):
            continue
        child_path = os.path.join(full, entry.name)
        if entry.is_dir():
            items.append({"name": entry.name, "type": "folder", "icon": "📁", "path": child_path})
        else:
            ext = os.path.splitext(entry.name)[1].lower()
            items.append({"name": entry.name, "type": "file", "icon": ICONS.get(ext, "▫️"), "path": child_path})

    return jsonify(items=items, current=full)


@app.route("/api/launch", methods=["POST"])
def api_launch():
    full = request.json.get("path", "")
    if not os.path.exists(full):
        return jsonify(ok=False, error="not found"), 404
    try:
        os.startfile(full)  # native "open with default handler" — same as double-click
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/rename", methods=["POST"])
def api_rename():
    d = request.json
    old_path, new_name = d.get("path", ""), d.get("new_name", "").strip()
    if not new_name or not os.path.exists(old_path):
        return jsonify(ok=False, error="invalid"), 400
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    try:
        os.rename(old_path, new_path)
        return jsonify(ok=True, new_path=new_path)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/delete", methods=["POST"])
def api_delete():
    path = request.json.get("path", "")
    if not os.path.exists(path):
        return jsonify(ok=False, error="not found"), 404
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/download")
def api_download():
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        return jsonify(error="not found"), 404
    return send_file(path, as_attachment=True)


@app.route("/api/view")
def api_view():
    """Serve a file inline (not as a download) so the phone's browser can
    render text/html, show images, or stream audio/video directly."""
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        return jsonify(error="not found"), 404
    mimetype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return send_file(path, mimetype=mimetype, as_attachment=False, conditional=True)


@app.route("/api/search")
def api_search():
    """Recursively search filenames under `base` (or Desktop + all drives
    if base isn't a real folder yet, i.e. still at the virtual root)."""
    q = request.args.get("q", "").strip().lower()
    base = request.args.get("base", "")
    if not q:
        return jsonify(items=[], truncated=False)

    if not base or base in ("ROOT", "Desktop", "Start Menu") or not os.path.isdir(base):
        roots = [DESKTOP] + list_drives()
    else:
        roots = [base]

    results = []
    visited = 0
    start = time.time()
    TIME_LIMIT = 6.0
    MAX_RESULTS = 150
    MAX_VISITED = 20000
    truncated = False

    for root in roots:
        if truncated:
            break
        for dirpath, dirnames, filenames in os.walk(root):
            visited += 1
            if visited > MAX_VISITED or time.time() - start > TIME_LIMIT:
                truncated = True
                break
            for name in dirnames + filenames:
                if q in name.lower():
                    full = os.path.join(dirpath, name)
                    is_dir = os.path.isdir(full)
                    ext = os.path.splitext(name)[1].lower()
                    results.append({
                        "name": name,
                        "path": full,
                        "type": "folder" if is_dir else "file",
                        "icon": "📁" if is_dir else ICONS.get(ext, "▫️"),
                    })
                    if len(results) >= MAX_RESULTS:
                        truncated = True
                        break
            if truncated:
                break

    return jsonify(items=results, truncated=truncated)


def gen_screen_frames():
    """MJPEG generator: grabs the screen a few times a second and streams
    it as a multipart JPEG feed for the trackpad overlay to display."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        while True:
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            w, h = img.size
            new_w = 900
            new_h = int(h * (new_w / w))
            img = img.resize((new_w, new_h))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=45)
            frame = buf.getvalue()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.18)  # ~5-6 fps, kept low for LAN bandwidth


@app.route("/api/screen")
def api_screen():
    if mss is None or Image is None:
        return jsonify(error="mss and pillow must be installed on the PC (pip install mss pillow)"), 500
    return Response(gen_screen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/combo", methods=["POST"])
def api_combo():
    err = _require_pyautogui()
    if err:
        return err
    keys = request.json.get("keys", [])
    if not keys:
        return jsonify(ok=False, error="no keys given"), 400
    try:
        pyautogui.hotkey(*keys)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/upload", methods=["POST"])
def api_upload():
    dest_dir = request.form.get("dest", "")
    f = request.files.get("file")
    if not f or not os.path.isdir(dest_dir):
        return jsonify(ok=False, error="invalid dest or file"), 400
    save_path = os.path.join(dest_dir, f.filename)
    f.save(save_path)
    return jsonify(ok=True, path=save_path)


# ---------------- mouse / keyboard ----------------

def _require_pyautogui():
    if pyautogui is None:
        return jsonify(ok=False, error="pyautogui not installed on PC"), 500
    return None


@app.route("/api/mouse/move", methods=["POST"])
def mouse_move():
    err = _require_pyautogui()
    if err:
        return err
    d = request.json
    pyautogui.moveRel(float(d.get("dx", 0)), float(d.get("dy", 0)), duration=0)
    return jsonify(ok=True)


@app.route("/api/mouse/click", methods=["POST"])
def mouse_click():
    err = _require_pyautogui()
    if err:
        return err
    pyautogui.click(button=request.json.get("button", "left"))
    return jsonify(ok=True)


SPECIAL_KEYS = {"backspace": "backspace", "enter": "enter", "space": "space", "tab": "tab", "escape": "esc"}


@app.route("/api/key", methods=["POST"])
def api_key():
    err = _require_pyautogui()
    if err:
        return err
    d = request.json
    if d.get("text"):
        pyautogui.typewrite(d["text"], interval=0)
    elif d.get("special") in SPECIAL_KEYS:
        pyautogui.press(SPECIAL_KEYS[d["special"]])
    return jsonify(ok=True)


# ---------------- page ----------------

PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>PC Home</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: linear-gradient(160deg, #14141a, #0c0c10); color: #eee; padding: env(safe-area-inset-top,0px) 16px env(safe-area-inset-bottom,0px); overflow-x: hidden; min-height: 100%; display: flex; flex-direction: column; }
  .topbar { display: flex; justify-content: space-between; align-items: center; padding: 14px 4px 10px; font-size: 0.8rem; color: #888; }
  .crumb { display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: #ddd; flex-wrap: wrap; }
  .crumb button { background: none; border: none; color: #7aa2ff; font-size: 0.82rem; padding: 4px 6px; cursor: pointer; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr)); gap: 18px 10px; padding: 10px 2px 100px; }
  .app { position: relative; display: flex; flex-direction: column; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
  .app-icon { width: 56px; height: 56px; border-radius: 16px; display: flex; align-items: center; justify-content: center; font-size: 1.6rem; background: #1e1e26; border: 1px solid #2c2c36; }
  .app:active .app-icon { transform: scale(0.9); background: #2a2a34; }
  .app-name { font-size: 0.68rem; color: #ccc; text-align: center; max-width: 74px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .folder .app-icon { background: #2a2416; border-color: #4a3f22; }
  .dock { position: fixed; bottom: 0; left: 0; right: 0; padding: 12px 16px calc(14px + env(safe-area-inset-bottom,0px)); background: rgba(18,18,22,0.85); backdrop-filter: blur(10px); border-top: 1px solid #2a2a32; display: flex; justify-content: center; gap: 10px; }
  .dock-btn { flex: 1; max-width: 130px; padding: 10px 6px; border-radius: 14px; background: #1e1e26; border: 1px solid #333; color: #ddd; font-size: 0.72rem; display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; }
  .dock-btn.active { background: #2a3f5f; border-color: #4a6fa5; color: #cfe0ff; }
  .dock-btn .ic { font-size: 1.2rem; }
  .overlay { position: fixed; inset: 0; background: rgba(8,8,10,0.95); display: none; flex-direction: column; padding: env(safe-area-inset-top,0px) 16px env(safe-area-inset-bottom,0px); z-index: 60; }
  .overlay.show { display: flex; }
  .ov-header { display: flex; justify-content: space-between; align-items: center; padding: 16px 4px; color: #999; font-size: 0.85rem; }
  .ov-header button { background: #262630; border: 1px solid #3a3a44; color: #eee; border-radius: 10px; padding: 8px 14px; font-size: 0.85rem; }
  .tp-surface { flex: 1; margin: 4px 0 14px; border-radius: 20px; background: repeating-linear-gradient(45deg, #1a1a20, #1a1a20 10px, #17171c 10px, #17171c 20px); border: 1px solid #2c2c36; touch-action: none; }
  .tp-buttons { display: flex; gap: 10px; margin-bottom: 14px; }
  .tp-buttons button { flex: 1; padding: 16px; border-radius: 14px; background: #1e1e26; border: 1px solid #333; color: #ddd; font-size: 0.85rem; }
  .kb-bar { position: fixed; left: 16px; right: 16px; bottom: 78px; background: #1e1e26; border: 1px solid #3a3a44; border-radius: 14px; padding: 10px 14px; display: none; align-items: center; gap: 10px; z-index: 60; }
  .kb-bar.show { display: flex; }
  .kb-bar input { flex: 1; background: transparent; border: none; outline: none; color: #eee; font-size: 0.95rem; }
  .kb-bar .kb-tag { font-size: 0.7rem; color: #7aa2ff; white-space: nowrap; }
  #toast { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); background: #2a3f5f; color: #cfe0ff; padding: 8px 16px; border-radius: 20px; font-size: 0.8rem; z-index: 100; display: none; max-width: 85%; text-align: center; }
  .lock { position: fixed; inset: 0; background: #0c0c10; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; z-index: 200; }
  .lock input { background: #1e1e26; border: 1px solid #3a3a44; color: #eee; padding: 14px; border-radius: 12px; font-size: 1rem; width: 220px; text-align: center; }
  .lock button { background: #2a3f5f; border: 1px solid #4a6fa5; color: #cfe0ff; padding: 12px 28px; border-radius: 12px; font-size: 0.95rem; }
  .lock p { color: #666; font-size: 0.8rem; }
  .menu { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: none; align-items: flex-end; z-index: 80; }
  .menu.show { display: flex; }
  .menu-sheet { background: #1c1c24; border-radius: 20px 20px 0 0; width: 100%; padding: 10px 16px calc(20px + env(safe-area-inset-bottom,0px)); }
  .menu-item { padding: 16px 8px; font-size: 0.95rem; border-bottom: 1px solid #2a2a32; }
  .menu-item.danger { border-bottom: none; color: #ff6b6b; }
  .choose-item { padding: 18px 8px; font-size: 1rem; border-bottom: 1px solid #2a2a32; text-align: center; }
  .search-list { padding: 6px 2px 40px; overflow-y: auto; flex: 1; }
  .search-row { display: flex; align-items: center; gap: 12px; padding: 12px 6px; border-bottom: 1px solid #22222a; cursor: pointer; }
  .search-row .ic { font-size: 1.4rem; }
  .search-row .info { display: flex; flex-direction: column; overflow: hidden; }
  .search-row .nm { font-size: 0.9rem; color: #eee; }
  .search-row .pth { font-size: 0.68rem; color: #777; word-break: break-all; }
  .search-input { flex: 1; background: #1e1e26; border: 1px solid #333; border-radius: 10px; padding: 10px 12px; color: #eee; margin-right: 10px; font-size: 0.9rem; }
  .key-row { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; }
  .key-btn { flex: 1; min-width: 34px; padding: 10px 4px; border-radius: 10px; background: #1e1e26; border: 1px solid #333; color: #ddd; font-size: 0.75rem; text-transform: uppercase; }
  .key-btn.staged { background: #2a3f5f; border-color: #4a6fa5; color: #cfe0ff; }
</style>
</head>
<body>

<div class="lock" id="lock">
  <p>🔒 PC Home</p>
  <input type="password" id="pwInput" placeholder="Password">
  <button id="pwBtn">Unlock</button>
  <p id="pwErr" style="color:#ff6b6b; height: 1em;"></p>
</div>

<div class="topbar">
  <span>PC Home</span>
  <div style="display:flex; gap:14px; align-items:center;">
    <span id="searchIcon" style="cursor:pointer; font-size:1rem;">🔎</span>
    <span id="clock"></span>
  </div>
</div>
<div class="crumb" id="crumb"></div>
<div class="grid" id="grid"></div>

<div class="dock">
  <div class="dock-btn" id="tpBtn"><span class="ic">🖱️</span>Trackpad</div>
  <div class="dock-btn" id="kbBtn"><span class="ic">⌨️</span>Keyboard</div>
  <div class="dock-btn" id="keysBtn"><span class="ic">🎹</span>Keys</div>
  <div class="dock-btn" id="upBtn"><span class="ic">⬆️</span>Upload</div>
</div>

<div class="overlay" id="tpOverlay">
  <div class="ov-header"><span>Drag to move · buttons to click</span><button id="tpClose">Close</button></div>
  <div class="tp-surface" id="tpSurface"><img id="tpScreenImg" src="" style="width:100%;height:100%;object-fit:contain;display:block;pointer-events:none;"></div>
  <div class="tp-buttons"><button id="tpLeft">Left click</button><button id="tpRight">Right click</button></div>
</div>

<div class="overlay" id="searchOverlay">
  <div class="ov-header">
    <input id="searchInput" class="search-input" placeholder="Search files & folders…">
    <button id="searchClose">Close</button>
  </div>
  <div class="search-list" id="searchResults"></div>
</div>

<div class="overlay" id="keysOverlay">
  <div class="ov-header">
    <span id="comboDisplay" style="color:#7aa2ff; font-size:0.85rem;">Tap keys, then Send…</span>
    <button id="keysClose">Close</button>
  </div>
  <div id="keyGrid" style="flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding:6px 2px;"></div>
  <div style="display:flex; gap:10px; padding:10px 0 4px;">
    <button id="comboClear" style="flex:1; padding:14px; border-radius:12px; background:#1e1e26; border:1px solid #333; color:#ddd;">Clear</button>
    <button id="comboSend" style="flex:2; padding:14px; border-radius:12px; background:#2a3f5f; border:1px solid #4a6fa5; color:#cfe0ff; font-weight:600;">Send combo</button>
  </div>
</div>

<div class="kb-bar" id="kbBar">
  <span class="kb-tag">→ PC</span>
  <input id="kbInput" placeholder="Type with your phone keyboard…" autocomplete="off" autocapitalize="off" autocorrect="off">
</div>

<input type="file" id="fileInput" style="display:none">

<div class="menu" id="ctxMenu">
  <div class="menu-sheet">
    <div class="menu-item" id="mOpen">Open</div>
    <div class="menu-item" id="mDownload">Download</div>
    <div class="menu-item" id="mRename">Rename</div>
    <div class="menu-item danger" id="mDelete">Delete</div>
  </div>
</div>

<div class="menu" id="chooseMenu">
  <div class="menu-sheet" id="chooseSheet"></div>
</div>

<div id="toast"></div>

<script>
let TOKEN = localStorage.getItem('pchome_token') || null;
let currentPath = "ROOT";
let ctxTarget = null;
let chooseTarget = null;

const PHONE_VIEWABLE = new Set([
  "txt","md","py","js","json","csv","log",      // text
  "html","htm",                                   // web
  "jpg","jpeg","png","gif","webp",                 // images
  "mp3","wav","flac",                              // audio
  "mp4","mkv","mov","webm",                        // video
  "pdf"
]);
function extOf(name) { const i = name.lastIndexOf('.'); return i === -1 ? "" : name.slice(i+1).toLowerCase(); }
function isPhoneViewable(name) { return PHONE_VIEWABLE.has(extOf(name)); }

function authHeaders(extra) {
  return Object.assign({"X-Auth-Token": TOKEN}, extra || {});
}

document.getElementById('pwBtn').onclick = doLogin;
document.getElementById('pwInput').addEventListener('keydown', e => { if (e.key === "Enter") doLogin(); });

async function doLogin() {
  const pw = document.getElementById('pwInput').value;
  const res = await fetch('/api/auth', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({password: pw})});
  const data = await res.json();
  if (data.ok) {
    TOKEN = data.token;
    localStorage.setItem('pchome_token', TOKEN);
    document.getElementById('lock').style.display = 'none';
    loadDir();
  } else {
    document.getElementById('pwErr').textContent = 'Wrong password';
  }
}

if (TOKEN) { document.getElementById('lock').style.display = 'none'; loadDir(); }

async function loadDir(path) {
  currentPath = path || currentPath;
  const res = await fetch("/api/list?path=" + encodeURIComponent(currentPath), {headers: authHeaders()});
  if (res.status === 401) { logout(); return; }
  const data = await res.json();
  const grid = document.getElementById('grid');
  grid.innerHTML = "";
  if (data.error) { toast("Error: " + data.error); return; }

  data.items.forEach(item => {
    const el = document.createElement('div');
    el.className = 'app ' + item.type;
    el.innerHTML = `<div class="app-icon">${item.icon}</div><div class="app-name">${item.name}</div>`;
    el.onclick = () => handleOpen(item);
    let pressTimer;
    el.addEventListener('touchstart', () => { pressTimer = setTimeout(() => openMenu(item), 480); });
    el.addEventListener('touchend', () => clearTimeout(pressTimer));
    grid.appendChild(el);
  });
  renderCrumb(data.current);
}

function logout() {
  localStorage.removeItem('pchome_token');
  location.reload();
}

function renderCrumb(current) {
  const crumb = document.getElementById('crumb');
  crumb.innerHTML = "";
  const rootBtn = document.createElement('button');
  rootBtn.textContent = "🏠 This PC";
  rootBtn.onclick = () => loadDir("ROOT");
  crumb.appendChild(rootBtn);
  if (current && current !== "ROOT") {
    const span = document.createElement('span');
    span.textContent = " › " + current;
    span.style.color = "#999"; span.style.fontSize = "0.75rem";
    crumb.appendChild(span);
  }
}

function closeChoose() { document.getElementById('chooseMenu').classList.remove('show'); }

function handleOpen(item) {
  if (item.type === "folder") { loadDir(item.path); return; }
  chooseTarget = item;
  const sheet = document.getElementById('chooseSheet');
  let html = "";
  if (isPhoneViewable(item.name)) html += '<div class="choose-item" id="cPhone">📱 Open on Phone</div>';
  html += '<div class="choose-item" id="cDownload">⬇️ Download</div>';
  html += '<div class="choose-item" id="cPC">🖥️ Open on PC</div>';
  html += '<div class="choose-item" id="cCancel" style="color:#999;">Cancel</div>';
  sheet.innerHTML = html;
  document.getElementById('chooseMenu').classList.add('show');
  const p = document.getElementById('cPhone');
  if (p) p.onclick = () => { closeChoose(); openOnPhone(item); };
  document.getElementById('cDownload').onclick = () => { closeChoose(); downloadFile(item); };
  document.getElementById('cPC').onclick = () => { closeChoose(); launch(item.path, item.name); };
  document.getElementById('cCancel').onclick = closeChoose;
}

function openOnPhone(item) {
  window.open('/api/view?path=' + encodeURIComponent(item.path) + '&token=' + TOKEN, '_blank');
}

function downloadFile(item) {
  window.open('/api/download?path=' + encodeURIComponent(item.path) + '&token=' + TOKEN, '_blank');
}

document.getElementById('chooseMenu').addEventListener('click', (e) => { if (e.target.id === 'chooseMenu') closeChoose(); });

async function launch(path, name) {
  toast("Launching " + name + "…");
  const res = await fetch("/api/launch", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({path})});
  const data = await res.json();
  if (!data.ok) toast("Error: " + data.error);
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.style.display = 'none', 2000);
}

function tick() { document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }
tick(); setInterval(tick, 30000);

// ---- context menu (long-press) ----
const ctxMenu = document.getElementById('ctxMenu');
function openMenu(item) { ctxTarget = item; ctxMenu.classList.add('show'); }
ctxMenu.addEventListener('click', (e) => { if (e.target === ctxMenu) ctxMenu.classList.remove('show'); });

document.getElementById('mOpen').onclick = () => { ctxMenu.classList.remove('show'); handleOpen(ctxTarget); };
document.getElementById('mDownload').onclick = () => { ctxMenu.classList.remove('show'); if (ctxTarget.type !== 'folder') window.open('/api/download?path=' + encodeURIComponent(ctxTarget.path) + '&token=' + TOKEN); else toast("Can't download a folder"); };
document.getElementById('mRename').onclick = async () => {
  ctxMenu.classList.remove('show');
  const newName = prompt("New name:", ctxTarget.name);
  if (!newName) return;
  const res = await fetch('/api/rename', {method:'POST', headers: authHeaders({'Content-Type':'application/json'}), body: JSON.stringify({path: ctxTarget.path, new_name: newName})});
  const data = await res.json();
  if (data.ok) loadDir(); else toast("Error: " + data.error);
};
document.getElementById('mDelete').onclick = async () => {
  ctxMenu.classList.remove('show');
  if (!confirm("Delete " + ctxTarget.name + "? This can't be undone.")) return;
  const res = await fetch('/api/delete', {method:'POST', headers: authHeaders({'Content-Type':'application/json'}), body: JSON.stringify({path: ctxTarget.path})});
  const data = await res.json();
  if (data.ok) loadDir(); else toast("Error: " + data.error);
};

// ---- upload ----
document.getElementById('upBtn').onclick = () => {
  if (currentPath === "ROOT") { toast("Open a folder first"); return; }
  document.getElementById('fileInput').click();
};
document.getElementById('fileInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('dest', currentPath);
  toast("Uploading " + file.name + "…");
  const res = await fetch('/api/upload', {method:'POST', headers: authHeaders(), body: fd});
  const data = await res.json();
  toast(data.ok ? "Uploaded " + file.name : "Error: " + data.error);
  if (data.ok) loadDir();
});

// ---- trackpad ----
const tpBtn = document.getElementById('tpBtn');
const tpOverlay = document.getElementById('tpOverlay');
const tpSurface = document.getElementById('tpSurface');
tpBtn.onclick = () => {
  tpOverlay.classList.add('show'); tpBtn.classList.add('active');
  document.getElementById('tpScreenImg').src = '/api/screen?token=' + TOKEN + '&t=' + Date.now();
};
document.getElementById('tpClose').onclick = () => {
  tpOverlay.classList.remove('show'); tpBtn.classList.remove('active');
  document.getElementById('tpScreenImg').src = ''; // stops the MJPEG stream
};

const SENS = 1.6;
let lastX, lastY, pending = {dx:0, dy:0}, flushTimer = null;
function flushMove() {
  if (pending.dx === 0 && pending.dy === 0) return;
  fetch("/api/mouse/move", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify(pending)});
  pending = {dx:0, dy:0};
}
tpSurface.addEventListener('touchstart', e => { const t = e.touches[0]; lastX = t.clientX; lastY = t.clientY; });
tpSurface.addEventListener('touchmove', e => {
  e.preventDefault();
  const t = e.touches[0];
  pending.dx += (t.clientX - lastX) * SENS; pending.dy += (t.clientY - lastY) * SENS;
  lastX = t.clientX; lastY = t.clientY;
  if (!flushTimer) flushTimer = setTimeout(() => { flushMove(); flushTimer = null; }, 30);
}, {passive:false});
document.getElementById('tpLeft').onclick = () => fetch("/api/mouse/click", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({button:"left"})});
document.getElementById('tpRight').onclick = () => fetch("/api/mouse/click", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({button:"right"})});

// ---- keyboard passthrough ----
const kbBtn = document.getElementById('kbBtn');
const kbBar = document.getElementById('kbBar');
const kbInput = document.getElementById('kbInput');
kbBtn.onclick = () => { kbBar.classList.toggle('show'); kbBtn.classList.toggle('active'); if (kbBar.classList.contains('show')) kbInput.focus(); };
kbInput.addEventListener('beforeinput', (e) => {
  if (e.inputType === "insertText" || e.inputType === "insertCompositionText") {
    fetch("/api/key", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({text: e.data})});
  } else if (e.inputType === "deleteContentBackward") {
    fetch("/api/key", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({special:"backspace"})});
  } else if (e.inputType === "insertLineBreak") {
    fetch("/api/key", {method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({special:"enter"})});
  }
});
kbInput.addEventListener('keydown', (e) => { if (e.key === "Enter") { e.preventDefault(); kbInput.value = ""; } });

// ---- search ----
let searchTimer;
document.getElementById('searchIcon').onclick = () => {
  document.getElementById('searchOverlay').classList.add('show');
  document.getElementById('searchInput').focus();
};
document.getElementById('searchClose').onclick = () => document.getElementById('searchOverlay').classList.remove('show');
document.getElementById('searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const results = document.getElementById('searchResults');
  if (!q) { results.innerHTML = ""; return; }
  searchTimer = setTimeout(() => doSearch(q), 350);
});

async function doSearch(q) {
  const results = document.getElementById('searchResults');
  results.innerHTML = '<div style="padding:14px; color:#666; font-size:0.8rem; text-align:center;">Searching…</div>';
  const res = await fetch('/api/search?q=' + encodeURIComponent(q) + '&base=' + encodeURIComponent(currentPath), {headers: authHeaders()});
  const data = await res.json();
  renderSearchResults(data.items || [], data.truncated);
}

function renderSearchResults(items, truncated) {
  const c = document.getElementById('searchResults');
  c.innerHTML = "";
  if (!items.length) { c.innerHTML = '<div style="padding:14px; color:#666; font-size:0.8rem; text-align:center;">No matches</div>'; return; }
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'search-row';
    row.innerHTML = `<span class="ic">${item.icon}</span><div class="info"><span class="nm">${item.name}</span><span class="pth">${item.path}</span></div>`;
    row.onclick = () => { document.getElementById('searchOverlay').classList.remove('show'); handleOpen(item); };
    c.appendChild(row);
  });
  if (truncated) {
    const note = document.createElement('div');
    note.style.cssText = "padding:14px; color:#666; font-size:0.72rem; text-align:center;";
    note.textContent = "Showing partial results — type more to narrow it down.";
    c.appendChild(note);
  }
}

// ---- combo keyboard ----
const KEY_ROWS = [
  ["ctrl", "alt", "shift", "win"],
  ["esc", "tab", "enter", "space", "backspace", "delete"],
  ["1","2","3","4","5","6","7","8","9","0"],
  ["q","w","e","r","t","y","u","i","o","p"],
  ["a","s","d","f","g","h","j","k","l"],
  ["z","x","c","v","b","n","m"],
  ["up","down","left","right"],
  ["f1","f2","f3","f4","f5","f6","f7","f8","f9","f10","f11","f12"],
];
let stagedKeys = [];

function buildKeyGrid() {
  const grid = document.getElementById('keyGrid');
  grid.innerHTML = "";
  KEY_ROWS.forEach(row => {
    const rowEl = document.createElement('div');
    rowEl.className = 'key-row';
    row.forEach(k => {
      const btn = document.createElement('button');
      btn.textContent = k;
      btn.className = 'key-btn' + (stagedKeys.includes(k) ? ' staged' : '');
      btn.onclick = () => toggleKey(k);
      rowEl.appendChild(btn);
    });
    grid.appendChild(rowEl);
  });
}

function toggleKey(k) {
  const i = stagedKeys.indexOf(k);
  if (i === -1) stagedKeys.push(k); else stagedKeys.splice(i, 1);
  document.getElementById('comboDisplay').textContent = stagedKeys.length ? stagedKeys.join(' + ') : 'Tap keys, then Send…';
  buildKeyGrid();
}

document.getElementById('keysBtn').onclick = () => {
  buildKeyGrid();
  document.getElementById('keysOverlay').classList.add('show');
  document.getElementById('keysBtn').classList.add('active');
};
document.getElementById('keysClose').onclick = () => {
  document.getElementById('keysOverlay').classList.remove('show');
  document.getElementById('keysBtn').classList.remove('active');
};
document.getElementById('comboClear').onclick = () => {
  stagedKeys = [];
  document.getElementById('comboDisplay').textContent = 'Tap keys, then Send…';
  buildKeyGrid();
};
document.getElementById('comboSend').onclick = async () => {
  if (!stagedKeys.length) return;
  const combo = stagedKeys.slice();
  const res = await fetch('/api/combo', {method:'POST', headers: authHeaders({'Content-Type':'application/json'}), body: JSON.stringify({keys: combo})});
  const data = await res.json();
  toast(data.ok ? 'Sent: ' + combo.join('+') : 'Error: ' + data.error);
  stagedKeys = [];
  document.getElementById('comboDisplay').textContent = 'Tap keys, then Send…';
  buildKeyGrid();
};
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return PAGE


def run_flask():
    app.run(host="0.0.0.0", port=4321, debug=False, use_reloader=False)


APP_NAME = "PCHome"
REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_pythonw_executable():
    """Find pythonw.exe in the same directory as python.exe to run without a console."""
    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else sys.executable


def cleanup_legacy_vbs():
    """Remove the old VBS autostart file if it exists to avoid duplicate launches."""
    try:
        startup_dir = os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
        )
        old_vbs = os.path.join(startup_dir, "pc_home_autostart.vbs")
        if os.path.exists(old_vbs):
            os.remove(old_vbs)
            print(f"Cleaned up legacy autostart script: {old_vbs}")
    except OSError:
        pass


def set_autostart(enabled: bool = True):
    """
    Registers or unregisters the script in the Windows Registry (HKCU Run).
    Automatically cleans up any legacy VBScript startup files.
    """
    cleanup_legacy_vbs()

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                pythonw = get_pythonw_executable()
                script_path = os.path.abspath(__file__)
                cmd = f'"{pythonw}" "{script_path}"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                print(f"Autostart enabled via Registry: {APP_NAME} -> {cmd}")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    print(f"Autostart disabled for {APP_NAME}.")
                except FileNotFoundError:
                    pass
    except OSError as e:
        print(f"Failed to update autostart setting: {e}")


def run_tray_icon():
    img = Image.new("RGB", (64, 64), color=(20, 24, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 56, 56], outline=(122, 162, 255), width=5)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("PC Home — running on :4321", None, enabled=False),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("pc_home", img, "PC Home", menu)
    icon.run()  # blocks — this becomes the "main loop" that keeps the process alive


if __name__ == "__main__":
    print("PC Home v3 starting — http://<this-pc-ip>:4321")
    print("Password is set in the PASSWORD variable at the top of this file.")

    set_autostart(True)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if pystray is not None and Image is not None:
        run_tray_icon()  # blocks; Quit from the tray menu ends the process
    else:
        print("Tip: pip install pystray for a quittable system tray icon.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
