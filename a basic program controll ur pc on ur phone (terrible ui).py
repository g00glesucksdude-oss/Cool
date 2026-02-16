import os
import time
import cv2
import numpy as np
import mss
import pyautogui
from flask import Flask, render_template_string, request, Response, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "night_shift_joystick_secure")

# --- CONFIGURATION ---
PASSWORD_HASH = generate_password_hash(os.environ.get("REMOTE_PASS", "1234"))
SCREEN_W, SCREEN_H = pyautogui.size()
pyautogui.FAILSAFE = False
zoom_factor = 1.0
last_click_time = 0

# --- HTML INTERFACE ---
INTERFACE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/nipplejs/0.10.1/nipplejs.min.js"></script>
    <style>
        body { background: #000; color: white; font-family: sans-serif; margin: 0; overflow: hidden; touch-action: none; }
        #viewer { width: 100vw; height: 50vh; background: #111; position: relative; }
        #stream { width: 100%; height: 100%; object-fit: contain; cursor: crosshair; }
        #joystick-zone { width: 100vw; height: 25vh; background: #222; position: relative; }
        .controls { display: grid; grid-template-columns: repeat(6, 1fr); gap: 5px; padding: 10px; background: #333; height: 25vh; }
        button { background: #444; color: white; border: 1px solid #555; border-radius: 5px; font-size: 11px; }
        .active { background: #ff4444 !important; }
        input { background:#222; color:#fff; border:1px solid #555; }
        .typing { padding:10px; background:#222; }
    </style>
</head>
<body>
    <div id="viewer"><img id="stream" src="{{ url_for('video_feed') }}"></div>
    <div id="joystick-zone"></div>
    <!-- Typing box directly below joystick -->
    <div class="typing">
        <input type="text" id="kb" placeholder="Type here..." style="width:70%;background:#111;color:#fff;">
        <button onclick="sendText()" style="width:25%;">SEND</button>
    </div>
    <div class="controls">
        <button onclick="fetch('/action?type=click')">LEFT CLICK</button>
        <button onclick="fetch('/action?type=right_click')">RIGHT</button>
        <button id="dragBtn" onclick="toggleDrag()">DRAG OFF</button>
        <button onclick="location.href='/logout'">LOCK</button>
        <button onclick="fetch('/action?type=zoom_in')">ZOOM IN</button>
        <button onclick="fetch('/action?type=zoom_reset')">RESET ZOOM</button>
    </div>
    <script>
        let isDragging = false;
        var manager = nipplejs.create({
            zone: document.getElementById('joystick-zone'),
            mode: 'static',
            position: {left: '50%', top: '50%'},
            color: 'blue'
        });

        let lastMove = 0;
        manager.on('move', function (evt, data) {
            let now = Date.now();
            if (data.direction && now - lastMove > 50) {
                lastMove = now;
                let speed = data.distance / 2;
                let angle = data.angle.radian;
                let vx = Math.cos(angle) * speed;
                let vy = -Math.sin(angle) * speed;
                fetch(`/action?type=move_joy&x=${vx}&y=${vy}`);
            }
        });

        function toggleDrag() {
            isDragging = !isDragging;
            const btn = document.getElementById('dragBtn');
            btn.innerText = isDragging ? "DRAG ON" : "DRAG OFF";
            btn.classList.toggle('active');
            fetch(`/action?type=${isDragging ? 'drag_start' : 'drag_end'}`);
        }

        function sendText() {
            const input = document.getElementById('kb');
            if (input.value.trim() !== "") {
                fetch(`/action?type=type&val=${encodeURIComponent(input.value)}`);
                input.value = '';
            }
        }

        // Tap/click on screen to move mouse
        document.getElementById('viewer').addEventListener('click', function(e) {
            const rect = e.target.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width * {{ screen_w }};
            const y = (e.clientY - rect.top) / rect.height * {{ screen_h }};
            fetch(`/action?type=move_abs&x=${x}&y=${y}`);
        });
    </script>
</body>
</html>
"""

# --- ROUTES ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pw = request.form.get('p')
        if pw and check_password_hash(PASSWORD_HASH, pw):
            session['auth'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(minutes=30)
            return redirect(url_for('remote'))
    return render_template_string("<body style='background:#000;color:#fff;text-align:center;padding:50px;'><form method='POST'>PW: <input type='password' name='p'><input type='submit'></form></body>")

@app.route('/remote')
def remote():
    if not session.get('auth'): return redirect(url_for('login'))
    return render_template_string(INTERFACE, screen_w=SCREEN_W, screen_h=SCREEN_H)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/video_feed')
def video_feed():
    def gen():
        global zoom_factor, last_click_time
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            while True:
                img = np.array(sct.grab(monitor))
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                # Zoom logic
                if zoom_factor > 1.0:
                    h, w = frame.shape[:2]
                    nh, nw = int(h/zoom_factor), int(w/zoom_factor)
                    y1, x1 = (h-nh)//2, (w-nw)//2
                    frame = frame[y1:y1+nh, x1:x1+nw]
                    frame = cv2.resize(frame, (960, 540))
                else:
                    frame = cv2.resize(frame, (960, 540))

                # Cursor overlay
                pos_x, pos_y = pyautogui.position()
                scale_x = 960 / SCREEN_W
                scale_y = 540 / SCREEN_H
                cx, cy = int(pos_x * scale_x), int(pos_y * scale_y)
                cv2.circle(frame, (cx, cy), 10, (0, 255, 0), 2)

                # Click highlight
                if time.time() - last_click_time < 0.3:
                    cv2.circle(frame, (cx, cy), 25, (0, 0, 255), 3)

                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 25])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

# --- ACTION HANDLER ---
def mark_click():
    global last_click_time
    last_click_time = time.time()

def zoom_in():
    global zoom_factor
    zoom_factor = min(zoom_factor + 0.25, 3.0)

def zoom_reset():
    global zoom_factor
    zoom_factor = 1.0

ACTIONS = {
    "click": lambda: (pyautogui.click(), mark_click()),
    "right_click": lambda: (pyautogui.rightClick(), mark_click()),
    "drag_start": lambda: pyautogui.mouseDown(),
    "drag_end": lambda: pyautogui.mouseUp(),
    "type": lambda val: pyautogui.write(val),
    "move_joy": lambda x, y: pyautogui.moveRel(float(x)*2, float(y)*2),
    "move_abs": lambda x, y: pyautogui.moveTo(float(x), float(y)),
    "zoom_in": zoom_in,
    "zoom_reset": zoom_reset,
}

@app.route('/action')
def action():
    if not session.get('auth'):
        return "Unauthorized", 401
    t = request.args.get('type')
    if t in ACTIONS:
        if t == "type":
            ACTIONS[t](request.args.get('val', ''))
        elif t == "move_joy":
            ACTIONS[t](request.args.get('x', 0), request.args.get('y', 0))
        elif t == "move_abs":
            ACTIONS[t](request.args.get('x', 0), request.args.get('y', 0))
        else:
            ACTIONS[t]()
    return "OK"

# --- MAIN ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
