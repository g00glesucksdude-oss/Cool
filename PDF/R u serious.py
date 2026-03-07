import os
import requests
import sys
import subprocess
import time

# Lock to current directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

POSSIBLE_NAMES = ["Print pdf a lot.py", "Print_pdf_a_lot.py"]
# Use the base URL without query strings here
RAW_URL = "https://raw.githubusercontent.com/g00glesucksdude-oss/Cool/main/PDF/Print%20pdf%20a%20lot.py"
FINAL_NAME = "Print_pdf_a_lot.py"

def delete_files(names):
    """Cleanup helper to remove specific files if they exist."""
    for name in names:
        if os.path.exists(name):
            try:
                os.remove(name)
                print(f"CLEANED: {name}")
            except Exception as e:
                print(f"COULD NOT DELETE {name}: {e}")

def update_and_run():
    # 1. PRE-CLEAN: Clear any previous session remnants
    delete_files(POSSIBLE_NAMES)

    # 2. DOWNLOAD (With Cache-Buster)
    # Appending a timestamp (?t=12345) forces GitHub to serve the latest version
    cache_buster_url = f"{RAW_URL}?t={int(time.time())}"
    print(f"Fetching fresh update from GitHub...")
    
    try:
        r = requests.get(cache_buster_url, timeout=15)
        r.raise_for_status()
        with open(FINAL_NAME, "wb") as f:
            f.write(r.content)
        print(f"SAVED AS: {FINAL_NAME}")
    except Exception as e:
        print(f"DOWNLOAD FAILED: {e}")
        return

    # 3. RUN THE SCRIPT
    print(f"LAUNCHING {FINAL_NAME}...\n" + "="*30)
    try:
        # sys.executable ensures we use the same Python environment
        subprocess.run([sys.executable, FINAL_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"SCRIPT CRASHED OR EXITED WITH ERROR: {e}")
    except Exception as e:
        print(f"EXECUTION FAILED: {e}")
    finally:
        # 4. POST-CLEAN: Delete the file immediately after the process ends
        print("="*30 + "\nSession finished. Cleaning up...")
        delete_files([FINAL_NAME])

if __name__ == "__main__":
    update_and_run()
