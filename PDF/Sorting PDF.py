import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pypdf import PdfReader, PdfWriter
from pdf2image import convert_from_path

# Update this path if Poppler is not in your System Environment Variables
POPPLER_PATH = None  # Example: r"C:\Program Files\poppler\Library\bin"

def get_reordered_indices(num_pages):
    """
    Reorders pages in A-D-C-B pattern:
    For pages [0,1,2,3]: returns [0,3,2,1]
    For pages [0,1,2,3,4,5]: returns [0,5,4,1,2,3]
    """
    reordered = []
    left = 0
    right = num_pages - 1
    from_left = True
    
    while left <= right:
        if from_left:
            # Take two from left (A, B)
            reordered.append(left)
            left += 1
            if left <= right:
                reordered.append(right)
                right -= 1
        else:
            # Take two from right (C, D) but in reverse
            if left <= right:
                reordered.append(right)
                right -= 1
            if left <= right:
                reordered.append(left)
                left += 1
        from_left = not from_left
    
    return reordered

def process_pdf():
    input_path = file_path_var.get()
    if not input_path or not os.path.exists(input_path):
        messagebox.showerror("Error", "Please select a valid PDF file.")
        return

    try:
        # Update progress
        progress_var.set(0)
        status_var.set("Reading PDF...")
        root.update()
        
        reader = PdfReader(input_path)
        num_pages = len(reader.pages)
        
        if num_pages == 0:
            messagebox.showerror("Error", "PDF has no pages.")
            return
        
        is_advanced = advanced_var.get()
        do_jpeg = jpeg_var.get()
        
        # Get page sequence based on mode
        if is_advanced:
            sequence = get_reordered_indices(num_pages)
            status_var.set("Applying A-D-C-B pattern...")
        else:
            sequence = list(range(num_pages))
            status_var.set("Using standard order...")
        
        root.update()
        
        # Split into odd and even based on position in the new sequence
        odd_indices = []
        even_indices = []
        
        for position, original_idx in enumerate(sequence):
            if position % 2 == 0:  # Odd position (0-indexed, so 0, 2, 4...)
                odd_indices.append(original_idx)
            else:  # Even position (1, 3, 5...)
                even_indices.append(original_idx)
        
        base_path = os.path.splitext(input_path)[0]
        base_name = os.path.basename(base_path)
        mode_label = "ADCB" if is_advanced else "STANDARD"
        
        total_tasks = 2  # Odd and Even
        if do_jpeg:
            total_tasks = 4  # Odd PDF, Even PDF, Odd JPEG, Even JPEG
        
        current_task = 0
        
        # Process odd and even pages
        for label, indices in [("ODD", odd_indices), ("EVEN", even_indices)]:
            if not indices:
                continue
                
            # Create PDF
            status_var.set(f"Creating {label} PDF...")
            root.update()
            
            writer = PdfWriter()
            for idx in indices:
                writer.add_page(reader.pages[idx])
            
            out_pdf = f"{base_path}_{mode_label}_{label}.pdf"
            with open(out_pdf, "wb") as f:
                writer.write(f)
            
            current_task += 1
            progress_var.set((current_task / total_tasks) * 100)
            root.update()
            
            # Create JPEGs if requested
            if do_jpeg:
                status_var.set(f"Converting {label} pages to JPEG...")
                root.update()
                
                folder = f"{base_path}_{mode_label}_{label}_IMAGES"
                if not os.path.exists(folder):
                    os.makedirs(folder)
                
                for seq_pos, orig_idx in enumerate(indices, start=1):
                    # Create zero-padded filename for proper sorting
                    # Format: 001_pagename_original_page_X.jpg
                    padding = len(str(len(indices)))
                    file_name = f"{seq_pos:0{padding}d}_{base_name}_original_page_{orig_idx + 1:03d}.jpg"
                    
                    try:
                        images = convert_from_path(
                            input_path,
                            first_page=orig_idx + 1,
                            last_page=orig_idx + 1,
                            poppler_path=POPPLER_PATH,
                            dpi=150,  # Adjustable quality
                            fmt="jpeg"
                        )
                        
                        if images:
                            images[0].save(
                                os.path.join(folder, file_name), 
                                "JPEG",
                                quality=95  # High quality
                            )
                    except Exception as e:
                        print(f"Warning: Could not convert page {orig_idx + 1}: {e}")
                
                current_task += 1
                progress_var.set((current_task / total_tasks) * 100)
                root.update()
        
        progress_var.set(100)
        status_var.set("Complete!")
        
        # Show summary
        summary = f"Successfully processed {num_pages} pages.\n\n"
        summary += f"Mode: {mode_label}\n"
        summary += f"Odd pages: {len(odd_indices)}\n"
        summary += f"Even pages: {len(even_indices)}\n\n"
        summary += f"Files created in: {os.path.dirname(out_pdf)}"
        
        messagebox.showinfo("Success", summary)
        
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred:\n{str(e)}")
        status_var.set("Error occurred")
        progress_var.set(0)

def browse_file():
    filename = filedialog.askopenfilename(
        title="Select PDF file",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
    )
    if filename:
        file_path_var.set(filename)
        # Display just the filename in the status
        status_var.set(f"Selected: {os.path.basename(filename)}")

# --- GUI Setup ---
root = tk.Tk()
root.title("PDF Splitter - Odd/Even with A-D-C-B Pattern")
root.geometry("500x400")
root.resizable(False, False)

# Configure style
style = ttk.Style()
style.theme_use('clam')

# Variables
file_path_var = tk.StringVar()
advanced_var = tk.BooleanVar(value=False)
jpeg_var = tk.BooleanVar(value=False)
progress_var = tk.DoubleVar()
status_var = tk.StringVar(value="Ready")

# Main frame
main_frame = ttk.Frame(root, padding="20")
main_frame.pack(expand=True, fill="both")

# Title
title_label = ttk.Label(main_frame, text="PDF Page Splitter", font=("Arial", 16, "bold"))
title_label.pack(pady=(0, 20))

# File selection frame
file_frame = ttk.LabelFrame(main_frame, text="Select PDF File", padding="10")
file_frame.pack(fill="x", pady=(0, 15))

entry_frame = ttk.Frame(file_frame)
entry_frame.pack(fill="x")

ttk.Entry(entry_frame, textvariable=file_path_var, width=50).pack(side="left", padx=(0, 10))
ttk.Button(entry_frame, text="Browse", command=browse_file).pack(side="left")

# Options frame
options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
options_frame.pack(fill="x", pady=(0, 15))

ttk.Checkbutton(
    options_frame, 
    text="Use A-D-C-B Pattern (Reorder pages before splitting)", 
    variable=advanced_var
).pack(anchor="w", pady=5)

ttk.Checkbutton(
    options_frame, 
    text="Export as JPEG images (with sorted naming)", 
    variable=jpeg_var
).pack(anchor="w", pady=5)

# Info label
info_text = "Standard: Splits odd/even pages as-is\nA-D-C-B: Reorders (1st, last, 2nd-last, 2nd...) then splits"
info_label = ttk.Label(options_frame, text=info_text, font=("Arial", 9), foreground="gray")
info_label.pack(anchor="w", pady=(10, 0))

# Progress frame
progress_frame = ttk.LabelFrame(main_frame, text="Progress", padding="10")
progress_frame.pack(fill="x", pady=(0, 15))

ttk.Progressbar(progress_frame, variable=progress_var, maximum=100).pack(fill="x", pady=(0, 5))
ttk.Label(progress_frame, textvariable=status_var).pack(anchor="w")

# Process button
process_btn = ttk.Button(
    main_frame, 
    text="Process PDF", 
    command=process_pdf, 
    style="Accent.TButton"
)
process_btn.pack(pady=10)

# Configure accent button style
style.configure("Accent.TButton", font=("Arial", 11, "bold"))

root.mainloop()
