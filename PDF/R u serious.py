import os
import requests
import subprocess

# Configuration
FILE_NAME = "Print_pdf_a_lot.py"
# Note: GitHub "blob" URLs must be converted to "raw" for downloading
RAW_URL = "https://raw.githubusercontent.com/g00glesucksdude-oss/Cool/main/PDF/Print%20pdf%20a%20lot.py"

def update_and_run():
    # 1. Delete the current version if it exists
    if os.path.exists(FILE_NAME):
        print(f"Removing old version of {FILE_NAME}...")
        os.remove(FILE_NAME)

    # 2. Download the latest version
    print(f"Downloading latest version from GitHub...")
    try:
        response = requests.get(RAW_URL)
        response.raise_for_status()  # Check for errors
        
        with open(FILE_NAME, "wb") as f:
            f.write(response.content)
        print("Download complete.")
        
    except Exception as e:
        print(f"Failed to download file: {e}")
        return

    # 3. Run the script
    print(f"Launching {FILE_NAME}...\n" + "-"*20)
    try:
        # This runs the downloaded script using the current python interpreter
        subprocess.run(["python", FILE_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"The script encountered an error during execution: {e}")
    except FileNotFoundError:
        # In some systems, the command is 'python3' instead of 'python'
        subprocess.run(["python3", FILE_NAME], check=True)

if __name__ == "__main__":
    update_and_run()
