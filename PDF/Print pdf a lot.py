#!/usr/bin/env python3
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pypdf import PdfReader, PdfWriter


def process_pdf(input_file, pages_per_chunk=95, printer_face_down=True):
    """
    printer_face_down=True  â†’ printer outputs 95â†’1 (last page first, face down).
      The stack, when flipped, is already in correct order.
      So both odds and evens are fed to the printer in NORMAL order (1,3,5... and 2,4,6...).

    printer_face_down=False â†’ printer outputs 1â†’95 (first page first, face up).
      The stack comes out in order, no natural flip happens.
      So evens must be REVERSED so that page 2 lands behind page 1 after you manually flip.
    """
    base_dir = os.path.dirname(input_file)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    # Natural document order, split by odd/even page number (1-based)
    odd_pages  = [p for p in range(total_pages) if (p + 1) % 2 == 1]
    even_pages = [p for p in range(total_pages) if (p + 1) % 2 == 0]

    # If printer is face-up (1â†’95), reverse evens so they align after flipping the odd stack
    if not printer_face_down:
        even_pages.reverse()

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

    order_desc = f"{pages_per_chunk}â†’1 (face-down)" if printer_face_down else f"1â†’{pages_per_chunk} (face-up)"
    messagebox.showinfo(
        "Done",
        f"Processed {total_pages} pages into {num_chunks} chunk(s) of up to "
        f"{pages_per_chunk} odds + {pages_per_chunk} evens.\n"
        f"Printer order: {order_desc}\n"
        f"Odds â†’ odds/\nEvens â†’ evens/",
    )


def toggle_printer_order():
    if printer_face_down_var.get():
        pages = entry_pages.get() or "95"
        btn_printer_order.config(text=f"ðŸ–¨ {pages}â†’1  (face-down)")
    else:
        pages = entry_pages.get() or "95"
        btn_printer_order.config(text=f"ðŸ–¨ 1â†’{pages}  (face-up)")


def on_pages_change(*_):
    # Keep the button label in sync when chunk size changes
    toggle_printer_order()


def browse_and_run():
    input_file = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
    if not input_file:
        return
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(
        target=process_pdf,
        args=(input_file, pages_per_chunk, printer_face_down_var.get()),
        daemon=True,
    ).start()


# â”€â”€ GUI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
root = tk.Tk()
root.title("PDF Odd/Even Splitter")

printer_face_down_var = tk.BooleanVar(value=True)

# Row 0 â€” chunk size
tk.Label(root, text="Pages per chunk\n(odds + evens separately):").grid(
    row=0, column=0, sticky="w", padx=8, pady=6
)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=0, column=1, sticky="w", padx=8)
entry_pages.bind("<KeyRelease>", on_pages_change)

# Row 1 â€” printer order toggle button
tk.Label(root, text="Printer outputs:").grid(row=1, column=0, sticky="w", padx=8)
btn_printer_order = tk.Checkbutton(
    root,
    text="ðŸ–¨ 95â†’1  (face-down)",
    variable=printer_face_down_var,
    command=toggle_printer_order,
    indicatoron=False,
    relief="raised",
    width=20,
    padx=6, pady=4,
)
btn_printer_order.grid(row=1, column=1, sticky="w", padx=8, pady=4)

ttk.Separator(root, orient="horizontal").grid(
    row=2, columnspan=2, sticky="ew", padx=8, pady=8
)

tk.Button(root, text="Select PDF & Run", command=browse_and_run, width=20).grid(
    row=3, column=1, padx=8, pady=8
)

root.mainloop()
