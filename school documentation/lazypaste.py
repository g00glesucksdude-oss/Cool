import json
import urllib.request
import os
import sys
import re
import base64


ENCRYPTED_URL = "aHR0cHM6Ly9kcml2ZS5nb29nbGUuY29tL3VjP2V4cG9ydD1kb3dubG9hZCZpZD0xOFFjemk4dXB6a3JTbU93Y3Q4RVA1M0l6aTViQ0Ywam0="
LOCAL_FILE = "_index.json"

def get_json_url():
    return base64.b64decode(ENCRYPTED_URL).decode('utf-8')

def extract_part_number(item):
    """Extracts the part number from titles like 'TESTING (part 15/17)' for sorting."""
    title = item.get("title", "")
    match = re.search(r'part\s+(\d+)/\d+', title, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 999999  # fallback if no part number pattern is found

def load_data():
    print("Fetching latest index from cloud on startup...")
    try:
        url = get_json_url()
        urllib.request.urlretrieve(url, LOCAL_FILE)
    except Exception as e:
        print(f"[Warning] Failed to download fresh index ({e}), checking local cache...")
    
    if not os.path.exists(LOCAL_FILE):
        print("[Error] No local index file found and network download failed.")
        return []
    
    with open(LOCAL_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    exports = data.get("exports", [])
    
    # Sort exports in ascending order based on the part number (e.g., 1/17, 2/17, etc.)[cite: 1]
    exports.sort(key=extract_part_number)
    return exports

def main():
    try:
        import keyboard
        import pyperclip
        import pyautogui
    except ImportError:
        print("Required libraries missing. Please install them via terminal:")
        print("pip install keyboard pyperclip pyautogui")
        sys.exit(1)

    exports = load_data()
    if not exports:
        print("No exports found in the JSON file.")
        return

    index = [0]

    print("=" * 60)
    print("JOB LINK NAVIGATOR MACRO INITIALIZED (OBFUSCATED & AUTO-RELOAD)")
    print("=" * 60)
    print(f"Loaded and sorted {len(exports)} items from exports.")
    print("Shortcuts:")
    print("  [Shift + G] : Paste NEXT link & show Job Title at mouse cursor")
    print("  [Ctrl + G]  : Move BACK one link (without pasting) & show Job Title at mouse cursor")
    print("  [Esc]       : Exit macro")
    print("=" * 60)

    def show_mouse_popup(title, url, action_type, current_display_idx):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes('-topmost', True)
            
            x, y = pyautogui.position()
            root.geometry(f"+{x + 15}+{y + 15}")
            
            bg_color = "#1e1e1e" if action_type == "PASTED" else "#2d2d2d"
            fg_color = "#4ec9b0" if action_type == "PASTED" else "#ce9178"
            
            frame = tk.Frame(root, bg=bg_color, bd=2, relief="solid")
            frame.pack()
            
            tk.Label(frame, text=f"[{action_type}] Item {current_display_idx} of {len(exports)}", font=("Arial", 9, "bold"), fg="#9cdcfe", bg=bg_color).pack(anchor="w", padx=5, pady=(5,0))
            tk.Label(frame, text=f"Title: {title}", font=("Arial", 10), fg=fg_color, bg=bg_color).pack(anchor="w", padx=5, pady=2)
            tk.Label(frame, text=f"URL: {url}", font=("Arial", 8), fg="#808080", bg=bg_color).pack(anchor="w", padx=5, pady=(0,5))
            
            root.after(2000, root.destroy)
            root.mainloop()
        except Exception as e:
            print(f"[Popup Error]: {e}")

    def on_shift_g():
        if index[0] < len(exports):
            current_item = exports[index[0]]
            title = current_item.get("title", "Unknown Title")
            url = current_item.get("driveUrl", "")
            
            display_num = index[0] + 1
            
            pyperclip.copy(url)
            keyboard.send("ctrl+v")
            
            print(f"\n[PASTED] Item {display_num}: {title} -> {url}")
            show_mouse_popup(title, url, "PASTED", display_num)
            
            index[0] += 1
        else:
            print("\n[INFO] Reached the end of the exports list!")

    def on_ctrl_g():
        if index[0] > 0:
            index[0] -= 1
            current_item = exports[index[0]]
            title = current_item.get("title", "Unknown Title")
            url = current_item.get("driveUrl", "")
            
            display_num = index[0] + 1
            
            print(f"\n[WENT BACK] Item {display_num}: {title}")
            show_mouse_popup(title, url, "WENT BACK", display_num)
        else:
            print("\n[INFO] Already at the beginning of the list!")

    keyboard.add_hotkey('shift+g', on_shift_g)
    keyboard.add_hotkey('ctrl+g', on_ctrl_g)

    print("\nMappings active. Press ESC to stop.")
    keyboard.wait('esc')
    print("\nMacro terminated.")

if __name__ == "__main__":
    main()
