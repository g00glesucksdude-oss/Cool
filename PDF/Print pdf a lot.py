#!/usr/bin/env python3

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from PyPDF2 import PdfReader, PdfWriter

def split_pdf(file_path, chunk_size=95, reverse=True):
    # Create output folders
    base_dir = os.path.dirname(file_path)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(file_path)
    total_pages = len(reader.pages)

    # Reverse order if enabled
    page_numbers = list(range(total_pages))
    if reverse:
        page_numbers = page_numbers[::-1]

    # Split into chunks
    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        chunk_pages = page_numbers[start:end]

        # Separate odds and evens
        odd_writer = PdfWriter()
        even_writer = PdfWriter()

        for p in chunk_pages:
            if (p + 1) % 2 == 1:  # human-readable odd
                odd_writer.add_page(reader.pages[p])
            else:
                even_writer.add_page(reader.pages[p])

        # Save chunk files
        chunk_id = f"{start+1}-{end}"
        odd_path = os.path.join(odds_dir, f"odds_{chunk_id}.pdf")
        even_path = os.path.join(evens_dir, f"evens_{chunk_id}.pdf")

        if odd_writer.get_num_pages() > 0:
            with open(odd_path, "wb") as f:
                odd_writer.write(f)
        if even_writer.get_num_pages() > 0:
            with open(even_path, "wb") as f:
                even_writer.write(f)

    messagebox.showinfo("Done", f"PDF split into odds/evens chunks of {chunk_size} pages.")

def select_pdf():
    file_path = filedialog.askopenfilename(
        title="Select PDF",
        filetypes=[("PDF files", "*.pdf")]
    )
    if file_path:
        chunk_size = int(chunk_entry.get())
        reverse = reverse_var.get()
        split_pdf(file_path, chunk_size, reverse)

# GUI setup
root = tk.Tk()
root.title("PDF Splitter Tool")

tk.Label(root, text="Chunk size (default 95):").pack()
chunk_entry = tk.Entry(root)
chunk_entry.insert(0, "95")
chunk_entry.pack()

reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse order", variable=reverse_var).pack()

tk.Button(root, text="Select PDF", command=select_pdf).pack()

root.mainloop()
