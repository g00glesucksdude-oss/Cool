import ipywidgets as widgets
from IPython.display import display, clear_output
from google.colab import drive
import os, datetime, shutil, time, subprocess

def install_deps():
    if shutil.which("whisper") is None or shutil.which("yt-dlp") is None:
        print("🛠️ Installing Whisper and yt-dlp...")
        !pip install openai-whisper yt-dlp > /dev/null 2>&1
        print("✅ Dependencies ready.")

def setup_dirs():
    drive.mount('/content/drive', force_remount=True)
    base = "/content/drive/MyDrive/Whisper_Work"
    for d in ["chunks", "subtitles"]: os.makedirs(os.path.join(base, d), exist_ok=True)
    return base, os.path.join(base, "chunks"), os.path.join(base, "subtitles")

def start_process(b):
    with output:
        clear_output()
        install_deps() 
        url = url_input.value.strip()
        if not url:
            print("❌ Paste a link first!")
            return
        
        base_dir, chunk_dir, sub_dir = setup_dirs()
        raw_download = "downloaded_file"
        master_wav = "master_audio.wav"
        
        # 1. DOWNLOAD (yt-dlp is king)
        if not os.path.exists(master_wav):
            print(f"📥 Downloading and converting to Master WAV...")
            # This command downloads ANY audio/video and converts it to a standard WAV on the fly
            !yt-dlp -x --audio-format wav -o "{master_wav}" "{url}"
            time.sleep(2) 

        if not os.path.exists(master_wav) or os.path.getsize(master_wav) < 1000:
            print(f"❌ ERROR: Download failed. The link might be protected or dead.")
            return

        # 2. SPLIT (WAV segments are ultra-stable)
        if not os.listdir(chunk_dir):
            print(f"✂️ Splitting audio into 2-minute segments...")
            !ffmpeg -i {master_wav} -f segment -segment_time 120 "{chunk_dir}/part%03d.wav" -y
            time.sleep(2)

        # 3. TRANSCRIBE (Resume Logic)
        todo = sorted([f for f in os.listdir(chunk_dir) if f.endswith('.wav')])
        task_type = task_select.value
        print(f"🚀 Processing {len(todo)} parts | Model: {model_select.value} | Task: {task_type}")
        
        for i, c in enumerate(todo):
            srt_path = os.path.join(sub_dir, f"part{i:03d}.srt")
            if os.path.exists(srt_path):
                continue
            
            print(f"🎙️ Part {i+1}/{len(todo)}...")
            !whisper "{os.path.join(chunk_dir, c)}" --model {model_select.value} --task {task_type} --output_format srt --output_dir "{sub_dir}"
            
            gen = os.path.join(sub_dir, c.replace('.wav', '.srt'))
            if os.path.exists(gen): os.rename(gen, srt_path)

        # 4. MERGE
        print("🧵 Merging result...")
        srts = sorted([f for f in os.listdir(sub_dir) if f.endswith('.srt')])
        if not srts:
            print("❌ Transcription failed. No SRTs generated.")
            return

        final_text = []
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

        final_srt_path = os.path.join(base_dir, "FINAL_TRANSCRIPT.srt")
        with open(final_srt_path, "w", encoding="utf-8") as f: f.writelines(final_text)

        # 5. CLEANUP
        print(f"✅ SUCCESS! Transcript saved to Drive: {final_srt_path}")
        shutil.rmtree(chunk_dir); shutil.rmtree(sub_dir)
        if os.path.exists(master_wav): os.remove(master_wav)

# --- UI ---
url_input = widgets.Text(placeholder='Paste ANY Video/Audio Link', description='Link:', layout={'width': '95%'})
model_select = widgets.Dropdown(options=['base', 'small', 'medium', 'large'], value='medium', description='Model:')
task_select = widgets.Dropdown(options=['translate', 'transcribe'], value='translate', description='Task:')
start_btn = widgets.Button(description="Start Everything", button_style='success', icon='play')
output = widgets.Output()
start_btn.on_click(start_process)
display(widgets.VBox([url_input, widgets.HBox([model_select, task_select]), start_btn]), output)
