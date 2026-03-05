import tkinter as tk
from tkinter import filedialog, messagebox
from PyPDF2 import PdfReader, PdfWriter

def split_pdf(input_file, pages_per_chunk=95):
    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    for i in range(0, total_pages, pages_per_chunk):
        writer = PdfWriter()
        for page_num in range(i, min(i + pages_per_chunk, total_pages)):
            writer.add_page(reader.pages[page_num])

        output_filename = f"output_part_{i//pages_per_chunk + 1}.pdf"
        with open(output_filename, "wb") as out_file:
            writer.write(out_file)

    messagebox.showinfo("Done", "PDF split successfully!")

def browse_file():
    filename = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
    if filename:
        entry_file.delete(0, tk.END)
        entry_file.insert(0, filename)

def run_split():
    input_file = entry_file.get()
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95  # fallback default
    if not input_file:
        messagebox.showerror("Error", "Please select a PDF file.")
        return
    split_pdf(input_file, pages_per_chunk)

# GUI setup
root = tk.Tk()
root.title("PDF Splitter")

tk.Label(root, text="PDF File:").grid(row=0, column=0, sticky="w")
entry_file = tk.Entry(root, width=40)
entry_file.grid(row=0, column=1)
tk.Button(root, text="Browse", command=browse_file).grid(row=0, column=2)

tk.Label(root, text="Pages per chunk (default 95):").grid(row=1, column=0, sticky="w")
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=1, column=1)

tk.Button(root, text="Split PDF", command=run_split).grid(row=2, column=1)

root.mainloop()
