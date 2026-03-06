import os
import requests
import sys
import subprocess

# Lock to current directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

POSSIBLE_NAMES = ["Print pdf a lot.py", "Print_pdf_a_lot.py"]
RAW_URL = "https://raw.githubusercontent.com/g00glesucksdude-oss/Cool/main/PDF/Print%20pdf%20a%20lot.py"
FINAL_NAME = "Print_pdf_a_lot.py"

def update_and_run():
    # 1. DELETE
    for name in POSSIBLE_NAMES:
        if os.path.exists(name):
            try:
                os.remove(name)
                print(f"TRASHED: {name}")
            except Exception as e:
                print(f"COULD NOT DELETE {name}: {e}")

    # 2. DOWNLOAD
    print("Downloading the latest update...")
    try:
        r = requests.get(RAW_URL, timeout=15)
        r.raise_for_status()
        with open(FINAL_NAME, "wb") as f:
            f.write(r.content)
        print(f"SAVED AS: {FINAL_NAME}")
    except Exception as e:
        print(f"DOWNLOAD FAILED: {e}")
        return

    # 3. RUN IT (The right way)
    print(f"LAUNCHING {FINAL_NAME}...\n" + "="*30)
    try:
        # Passing as a list prevents the shell from mangling the command
        subprocess.run([sys.executable, FINAL_NAME], check=True)
    except Exception as e:
        print(f"EXECUTION FAILED: {e}")

if __name__ == "__main__":
    update_and_run()
