#!/usr/bin/env python3
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pypdf import PdfReader, PdfWriter


def process_single_pdf(input_file, output_base_dir, pages_per_chunk=95, printer_face_down=True):
    """
    Face-down printer (95â†’1):
      Step 1 - reverse ALL pages to support printer's reverse output order
      Step 2 - reverse evens AGAIN so they align correctly behind odds when flipped

      Net effect:
        Odds:  reversed once  â†’ feeds highest-to-lowest
        Evens: reversed twice â†’ feeds lowest-to-highest (2, 4, 6...)

    Face-up printer (1â†’95):
      No reversals needed.
    """
    pdf_name  = os.path.splitext(os.path.basename(input_file))[0]
    out_dir   = os.path.join(output_base_dir, pdf_name)
    odds_dir  = os.path.join(out_dir, "odds")
    evens_dir = os.path.join(out_dir, "evens")
    os.makedirs(odds_dir,  exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    odd_pages  = [p for p in range(total_pages) if (p + 1) % 2 == 1]
    even_pages = [p for p in range(total_pages) if (p + 1) % 2 == 0]

    if printer_face_down:
        odd_pages.reverse()          # Step 1: reverse all (odds)
        even_pages.reverse()         # Step 1: reverse all (evens)
        even_pages.reverse()         # Step 2: reverse evens again

    num_chunks = max(
        (len(odd_pages)  + pages_per_chunk - 1) // pages_per_chunk,
        (len(even_pages) + pages_per_chunk - 1) // pages_per_chunk,
        1,
    )

    for chunk_idx in range(num_chunks):
        start = chunk_idx * pages_per_chunk
        end   = start + pages_per_chunk

        odd_chunk  = odd_pages[start:end]
        even_chunk = even_pages[start:end]

        label_start = start + 1
        label_end   = start + max(len(odd_chunk), len(even_chunk))
        label = f"{label_start}-{label_end}"

        if odd_chunk:
            odd_writer = PdfWriter()
            for page_index in odd_chunk:
                odd_writer.add_page(reader.pages[page_index])
            with open(os.path.join(odds_dir, f"odds_{label}.pdf"), "wb") as f:
                odd_writer.write(f)

        if even_chunk:
            even_writer = PdfWriter()
            for page_index in even_chunk:
                even_writer.add_page(reader.pages[page_index])
            with open(os.path.join(evens_dir, f"evens_{label}.pdf"), "wb") as f:
                even_writer.write(f)

    return total_pages, num_chunks


def run_processing(mode, pages_per_chunk, printer_face_down, status_var, btn_run):
    btn_run.config(state="disabled")

    if mode == "file":
        input_file = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not input_file:
            btn_run.config(state="normal")
            return
        pdf_files = [input_file]
        output_base = os.path.dirname(input_file)
    else:
        folder = filedialog.askdirectory(title="Select folder containing PDFs")
        if not folder:
            btn_run.config(state="normal")
            return
        pdf_files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".pdf")
        ]
        if not pdf_files:
            messagebox.showwarning("No PDFs", "No PDF files found in that folder.")
            btn_run.config(state="normal")
            return
        output_base = folder

    total_files = len(pdf_files)
    status_var.set(f"Processing 0 / {total_files}...")

    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        status_var.set(f"Processing {i} / {total_files}: {os.path.basename(pdf_path)}")
        try:
            pages, chunks = process_single_pdf(
                pdf_path, output_base, pages_per_chunk, printer_face_down
            )
            results.append(f"âœ“ {os.path.basename(pdf_path)}: {pages} pages, {chunks} chunk(s)")
        except Exception as e:
            results.append(f"âœ— {os.path.basename(pdf_path)}: ERROR â€” {e}")

    status_var.set("Done!")
    btn_run.config(state="normal")

    order_desc = f"{pages_per_chunk}â†’1 (face-down)" if printer_face_down else f"1â†’{pages_per_chunk} (face-up)"
    summary = "\n".join(results)
    messagebox.showinfo(
        "Done",
        f"Finished {total_files} file(s).\nPrinter order: {order_desc}\n\n{summary}"
    )


def start_processing(mode):
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(
        target=run_processing,
        args=(mode, pages_per_chunk, printer_face_down_var.get(), status_var, btn_run),
        daemon=True,
    ).start()


def toggle_printer_order():
    pages = entry_pages.get() or "95"
    if printer_face_down_var.get():
        btn_printer_order.config(text=f"ðŸ–¨  {pages}â†’1  (face-down)")
    else:
        btn_printer_order.config(text=f"ðŸ–¨  1â†’{pages}  (face-up)")


def on_pages_change(*_):
    toggle_printer_order()


# â”€â”€ GUI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
root = tk.Tk()
root.title("PDF Odd/Even Splitter")
root.resizable(False, False)

printer_face_down_var = tk.BooleanVar(value=True)
status_var = tk.StringVar(value="Ready.")

# Row 0 â€” chunk size
tk.Label(root, text="Pages per chunk\n(odds + evens separately):").grid(
    row=0, column=0, sticky="w", padx=8, pady=6
)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=0, column=1, sticky="w", padx=8)
entry_pages.bind("<KeyRelease>", on_pages_change)

# Row 1 â€” printer order toggle
tk.Label(root, text="Printer outputs:").grid(row=1, column=0, sticky="w", padx=8)
btn_printer_order = tk.Checkbutton(
    root,
    text="ðŸ–¨  95â†’1  (face-down)",
    variable=printer_face_down_var,
    command=toggle_printer_order,
    indicatoron=False,
    relief="raised",
    width=22,
    padx=6, pady=4,
)
btn_printer_order.grid(row=1, column=1, sticky="w", padx=8, pady=4)

ttk.Separator(root, orient="horizontal").grid(
    row=2, columnspan=2, sticky="ew", padx=8, pady=8
)

# Row 3 â€” action buttons
btn_run = tk.Button(
    root, text="ðŸ“„ Select PDF & Run",
    command=lambda: start_processing("file"),
    width=22
)
btn_run.grid(row=3, column=0, padx=8, pady=4)

tk.Button(
    root, text="ðŸ“ Select Folder & Run",
    command=lambda: start_processing("folder"),
    width=22
).grid(row=3, column=1, padx=8, pady=4)

# Row 4 â€” status
tk.Label(root, textvariable=status_var, fg="gray", anchor="w").grid(
    row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8)
)

root.mainloop()
