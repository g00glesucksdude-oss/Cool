import ipywidgets as widgets
from IPython.display import display, clear_output
from google.colab import drive
import os, datetime, shutil, time, subprocess

# --- 1. INSTALLATION BLOCK ---
def install_whisper():
    if shutil.which("whisper") is None:
        print("🛠️ Installing OpenAI Whisper and requirements... (Please wait)")
        !pip install openai-whisper setuptools-rust > /dev/null 2>&1
        print("✅ Installation Complete.")

# --- 2. Directory Setup ---
def setup_dirs():
    drive.mount('/content/drive', force_remount=True)
    base = "/content/drive/MyDrive/Whisper_Work"
    chunks = os.path.join(base, "chunks")
    subs = os.path.join(base, "subtitles")
    for d in [chunks, subs]: os.makedirs(d, exist_ok=True)
    return base, chunks, subs

# --- 3. The Core Engine ---
def start_process(b):
    with output:
        clear_output()
        install_whisper() # Ensure whisper exists

        url = url_input.value.strip()
        if not url:
            print("❌ Please paste a valid video link.")
            return

        base_dir, chunk_dir, sub_dir = setup_dirs()
        raw = "downloaded_raw.mp4"
        fixed = "fixed_source.mp4"

        # DOWNLOAD
        if not os.path.exists(raw):
            print("📥 Downloading video...")
            !curl -L -o {raw} "{url}"
            time.sleep(2)

        # REPAIR
        if not os.path.exists(fixed):
            print("🔧 Repairing MP4 headers...")
            !ffmpeg -i {raw} -c copy -map 0 -movflags +faststart {fixed} -y
            time.sleep(2)

        # SPLIT
        if not os.listdir(chunk_dir):
            print("✂️ Splitting into 2-minute segments...")
            !ffmpeg -i {fixed} -f segment -segment_time 120 -c copy "{chunk_dir}/part%03d.mp4" -y
            time.sleep(2)

        # TRANSCRIBE (Resume Logic)
        todo = sorted([f for f in os.listdir(chunk_dir) if f.endswith('.mp4')])
        print(f"🚀 Found {len(todo)} segments. Starting translation...")

        for i, c in enumerate(todo):
            srt_path = os.path.join(sub_dir, f"part{i:03d}.srt")
            if os.path.exists(srt_path):
                print(f"✅ Part {i+1} exists. Skipping.")
                continue

            print(f"🎙️ Translating Part {i+1}/{len(todo)}...")
            # Using !whisper directly now that it's installed
            !whisper "{os.path.join(chunk_dir, c)}" --model {model_select.value} --task translate --output_format srt --output_dir "{sub_dir}"

            gen = os.path.join(sub_dir, c.replace('.mp4', '.srt'))
            if os.path.exists(gen): os.rename(gen, srt_path)

        # MERGE
        print("🧵 Stitching subtitles...")
        final_text = []
        srts = sorted([f for f in os.listdir(sub_dir) if f.endswith('.srt')])

        if not srts:
            print("❌ Error: No SRT files were generated. Whisper might have failed.")
            return

        for i, s in enumerate(srts):
            with open(os.path.join(sub_dir, s), 'r', encoding='utf-8-sig') as f:
                for line in f:
                    if " --> " in line:
                        t = line.strip().split(" --> ")
                        off = i * 120
                        s_o = datetime.datetime.strptime(t[0].replace('.', ','), "%H:%M:%S,%f") + datetime.timedelta(seconds=off)
                        e_o = datetime.datetime.strptime(t[1].replace('.', ','), "%H:%M:%S,%f") + datetime.timedelta(seconds=off)
                        final_text.append(f"{s_o.strftime('%H:%M:%S,%f')[:-3]} --> {e_o.strftime('%H:%M:%S,%f')[:-3]}\n")
                    else: final_text.append(line)

        with open("merged.srt", "w", encoding="utf-8") as f: f.writelines(final_text)

        # FINAL MUX & CLEANUP
        final_out = os.path.join(base_dir, "FINAL_RESULT.mp4")
        print("🎬 Finalizing video...")
        !ffmpeg -i {fixed} -i merged.srt -c copy -c:s mov_text "{final_out}" -y

        if os.path.exists(final_out):
            shutil.rmtree(chunk_dir); shutil.rmtree(sub_dir)
            for f in [raw, fixed, "merged.srt"]:
                if os.path.exists(f): os.remove(f)
            print(f"✅ COMPLETE! Check your Drive: {final_out}")

# --- DASHBOARD SETUP ---
url_input = widgets.Text(placeholder='Paste Video URL here', description='Link:', layout={'width': '90%'})
model_select = widgets.Dropdown(options=['base', 'small', 'medium', 'large'], value='medium', description='Model:')
start_btn = widgets.Button(description="Start Everything", button_style='success', icon='play')
output = widgets.Output()

start_btn.on_click(start_process)

print("--- WHISPER DASHBOARD v3.0 (Auto-Fix) ---")
display(widgets.VBox([url_input, model_select, start_btn]), output)
