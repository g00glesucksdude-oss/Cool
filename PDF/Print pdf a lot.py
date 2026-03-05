import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PyPDF2 import PdfReader, PdfWriter


def split_pdf(input_file, pages_per_chunk=95):
    reader = PdfReader(input_file)
    total_pages = len(reader.pages)
    for i in range(0, total_pages, pages_per_chunk):
        writer = PdfWriter()
        for page_num in range(i, min(i + pages_per_chunk, total_pages)):
            writer.add_page(reader.pages[page_num])
        output_filename = f"output_part_{i // pages_per_chunk + 1}.pdf"
        with open(output_filename, "wb") as out_file:
            writer.write(out_file)
    messagebox.showinfo("Done", "PDF split successfully!")


def split_odd_even(input_file, pages_per_chunk=95, reverse=True):
    base_dir = os.path.dirname(input_file)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    page_order = list(range(total_pages))
    if reverse:
        page_order = page_order[::-1]

    for i in range(0, total_pages, pages_per_chunk):
        chunk = page_order[i:i + pages_per_chunk]

        odd_writer = PdfWriter()
        even_writer = PdfWriter()

        for position, page_index in enumerate(chunk):
            if position % 2 == 0:
                odd_writer.add_page(reader.pages[page_index])
            else:
                even_writer.add_page(reader.pages[page_index])

        label = f"{i + 1}-{i + len(chunk)}"

        if len(odd_writer.pages) > 0:
            with open(os.path.join(odds_dir, f"odds_{label}.pdf"), "wb") as f:
                odd_writer.write(f)

        if len(even_writer.pages) > 0:
            with open(os.path.join(evens_dir, f"evens_{label}.pdf"), "wb") as f:
                even_writer.write(f)

    messagebox.showinfo("Done", f"Odd/even split complete!")


# ── Thread runners ─────────────────────────────────────────────────────────────

def run_split():
    input_file = entry_file.get()
    if not input_file:
        messagebox.showerror("Error", "Please select a PDF file.")
        return
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(target=split_pdf, args=(input_file, pages_per_chunk), daemon=True).start()


def run_odd_even():
    input_file = entry_file.get()
    if not input_file:
        messagebox.showerror("Error", "Please select a PDF file.")
        return
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(target=split_odd_even, args=(input_file, pages_per_chunk, reverse_var.get()), daemon=True).start()


def browse_file():
    filename = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
    if filename:
        entry_file.delete(0, tk.END)
        entry_file.insert(0, filename)


# ── GUI ────────────────────────────────────────────────────────────────────────

root = tk.Tk()
root.title("PDF Tools")

tk.Label(root, text="PDF File:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
entry_file = tk.Entry(root, width=45)
entry_file.grid(row=0, column=1, padx=8)
tk.Button(root, text="Browse", command=browse_file).grid(row=0, column=2, padx=8)

tk.Label(root, text="Pages per chunk:").grid(row=1, column=0, sticky="w", padx=8)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=1, column=1, sticky="w", padx=8)

reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse page order (odd/even only)", variable=reverse_var).grid(row=2, column=1, sticky="w", padx=8)

ttk.Separator(root, orient="horizontal").grid(row=3, columnspan=3, sticky="ew", padx=8, pady=8)

tk.Button(root, text="Split PDF", command=run_split, width=20).grid(row=4, column=1, sticky="w", padx=8, pady=4)
tk.Button(root, text="Split Odd/Even", command=run_odd_even, width=20).grid(row=5, column=1, sticky="w", padx=8, pady=4)

root.mainloop()
