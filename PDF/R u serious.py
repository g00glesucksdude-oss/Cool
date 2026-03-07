import os
import requests
import sys
import subprocess
import time
import shutil

# 1. Lock to current directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

POSSIBLE_NAMES = ["Print pdf a lot.py", "Print_pdf_a_lot.py"]
RAW_URL = "https://raw.githubusercontent.com/g00glesucksdude-oss/Cool/main/PDF/Print%20pdf%20a%20lot.py"
FINAL_NAME = "Print_pdf_a_lot.py"

def clean_everything():
    """Nuke files and the pycache folder to prevent 'ghost' execution."""
    for name in POSSIBLE_NAMES:
        if os.path.exists(name):
            try:
                os.remove(name)
                print(f"CLEANED: {name}")
            except Exception as e:
                print(f"DEL ERROR: {e}")
    
    # Even if you don't see it, Python might have hidden it
    if os.path.exists("__pycache__"):
        shutil.rmtree("__pycache__", ignore_errors=True)
        print("NUKED: __pycache__")

    # Give the OS a heartbeat to realize the files are actually gone
    time.sleep(0.5)

def update_and_run():
    # Step 1: Clear the slate
    clean_everything()

    # Step 2: Download with extreme cache-busting
    print("Fetching fresh update...")
    
    # Headers to tell GitHub/CDNs/Proxies: "DO NOT GIVE ME CACHED DATA"
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    
    # Unique URL per second
    cache_buster_url = f"{RAW_URL}?nocache={int(time.time())}"
    
    try:
        # Using a Session to avoid socket-level reuse issues
        with requests.Session() as s:
            s.trust_env = False # Ignore system proxies
            r = s.get(cache_buster_url, headers=headers, timeout=15)
            r.raise_for_status()
            
            with open(FINAL_NAME, "wb") as f:
                f.write(r.content)
        print(f"SAVED FRESH: {FINAL_NAME}")
    except Exception as e:
        print(f"DOWNLOAD FAILED: {e}")
        return

    # Step 3: Run with Environment Overrides
    print(f"LAUNCHING {FINAL_NAME}...\n" + "="*30)
    
    # Force Python to NOT write or use bytecode (.pyc) for this run
    env_vars = os.environ.copy()
    env_vars["PYTHONDONTWRITEBYTECODE"] = "1"
    
    try:
        # -B flag is an extra layer of "Don't use cache"
        subprocess.run([sys.executable, "-B", FINAL_NAME], check=True, env=env_vars)
    except subprocess.CalledProcessError as e:
        print(f"\nSCRIPT CRASHED: {e}")
    except Exception as e:
        print(f"\nEXECUTION ERROR: {e}")
    finally:
        print("="*30 + "\nSession finished. Cleaning up...")
        clean_everything()

if __name__ == "__main__":
    update_and_run()
