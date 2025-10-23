import os
import base64
import qrcode
import textwrap
import hashlib
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import platform

def zip_to_qr_chunks(zip_path, output_dir='qr_zip_chunks', chunk_chars=1200):
    if not os.path.isfile(zip_path):
        messagebox.showerror("Error", f"File not found:\n{zip_path}")
        return

    # Read and base64-encode the zip file
    with open(zip_path, 'rb') as f:
        raw_data = f.read()
        b64_data = base64.b64encode(raw_data).decode('utf-8')
        sha256 = hashlib.sha256(raw_data).hexdigest()

    # Split into safe chunks
    chunks = textwrap.wrap(b64_data, width=chunk_chars)
    total_chunks = len(chunks)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Generate QR codes with indexing and checksum
    for i, chunk in enumerate(chunks, start=1):
        payload = f"{i}/{total_chunks}:{sha256}:{chunk}"

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img_path = os.path.join(output_dir, f'qr_zip_{i:03}_of_{total_chunks}.png')
        img.save(img_path)

    # Show success message
    messagebox.showinfo("Done", f"✅ Generated {total_chunks} QR codes in:\n{output_dir}")

    # Open folder automatically
    if platform.system() == "Windows":
        subprocess.run(["explorer", os.path.abspath(output_dir)])
    elif platform.system() == "Darwin":  # macOS
        subprocess.run(["open", output_dir])
    else:  # Linux
        subprocess.run(["xdg-open", output_dir])

def launch_gui():
    root = tk.Tk()
    root.withdraw()
    zip_path = filedialog.askopenfilename(title="Select ZIP file", filetypes=[("ZIP files", "*.zip")])
    if zip_path:
        zip_to_qr_chunks(zip_path)
    else:
        messagebox.showinfo("Cancelled", "No file selected.")

if __name__ == "__main__":
    launch_gui()
