import os
import fitz  # PyMuPDF
import customtkinter as ctk
from tkinter import filedialog, messagebox

class PDFSplitterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Logic-Corrected PDF Splitter")
        self.geometry("500x400")

        self.chunk_size = ctk.IntVar(value=95)
        
        ctk.CTkLabel(self, text="Manual Duplex Logic Splitter", font=("Arial", 20, "bold")).pack(pady=20)
        ctk.CTkLabel(self, text="Sheets per Batch (e.g., 95 odds / 95 evens):").pack()
        ctk.CTkEntry(self, textvariable=self.chunk_size).pack(pady=5)

        ctk.CTkButton(self, text="Process Single PDF", command=self.process_single).pack(pady=10)
        ctk.CTkButton(self, text="Batch Process Folder", command=self.process_batch).pack(pady=10)
        
        self.status_label = ctk.CTkLabel(self, text="Ready", text_color="gray")
        self.status_label.pack(pady=20)

    def split_logic(self, pdf_path, output_root):
        doc = fitz.open(pdf_path)
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        pdf_dir = os.path.join(output_root, base_name)
        
        odd_dir = os.path.join(pdf_dir, "odd")
        even_dir = os.path.join(pdf_dir, "even")
        os.makedirs(odd_dir, exist_ok=True)
        os.makedirs(even_dir, exist_ok=True)

        # 1. Sort all pages into Odd and Even lists first
        all_odds = [i for i in range(len(doc)) if i % 2 == 0]  # Page 1, 3, 5...
        all_evens = [i for i in range(len(doc)) if i % 2 != 0] # Page 2, 4, 6...

        sheet_limit = self.chunk_size.get()

        # 2. Process ODD chunks (e.g., 95 sheets at a time)
        for i in range(0, len(all_odds), sheet_limit):
            chunk_num = (i // sheet_limit) + 1
            subset = all_odds[i : i + sheet_limit]
            
            temp_odd_doc = fitz.open()
            for pg_idx in subset:
                temp_odd_doc.insert_pdf(doc, from_page=pg_idx, to_page=pg_idx)

            # Reverse for correct duplex order
            reversed_odd = fitz.open()
            for p_idx in reversed(range(len(temp_odd_doc))):
                reversed_odd.insert_pdf(temp_odd_doc, from_page=p_idx, to_page=p_idx)

            reversed_odd.save(os.path.join(odd_dir, f"{base_name}_ODD_Batch_{chunk_num}_REVERSED.pdf"))
            temp_odd_doc.close()
            reversed_odd.close()

        # 3. Process EVEN chunks (e.g., 95 sheets at a time)
        for i in range(0, len(all_evens), sheet_limit):
            chunk_num = (i // sheet_limit) + 1
            subset = all_evens[i : i + sheet_limit]
            
            temp_even_doc = fitz.open()
            for pg_idx in subset:
                temp_even_doc.insert_pdf(doc, from_page=pg_idx, to_page=pg_idx)

            # Reverse for correct duplex order
            reversed_even = fitz.open()
            for p_idx in reversed(range(len(temp_even_doc))):
                reversed_even.insert_pdf(temp_even_doc, from_page=p_idx, to_page=p_idx)

            reversed_even.save(os.path.join(even_dir, f"{base_name}_EVEN_Batch_{chunk_num}_REVERSED.pdf"))
            temp_even_doc.close()
            reversed_even.close()

        doc.close()

    def process_single(self):
        file_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if file_path:
            dest = filedialog.askdirectory(title="Select Output Destination")
            if dest:
                self.split_logic(file_path, dest)
                messagebox.showinfo("Success", f"Done: {os.path.basename(file_path)}")

    def process_batch(self):
        source_folder = filedialog.askdirectory(title="Select Source Folder")
        if not source_folder: return
        dest_folder = filedialog.askdirectory(title="Select Output Destination")
        if not dest_folder: return

        count = 0
        for root, _, files in os.walk(source_folder):
            for file in files:
                if file.lower().endswith(".pdf"):
                    self.split_logic(os.path.join(root, file), dest_folder)
                    count += 1
        messagebox.showinfo("Success", f"Processed {count} PDFs.")

if __name__ == "__main__":
    app = PDFSplitterApp()
    app.mainloop()
