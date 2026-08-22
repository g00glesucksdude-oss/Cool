import os
import sys
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff'}

class ImageSorterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Text Sorter (Left = Text, Right = No Text, Up = Maybe, Ctrl+Z = Undo)")
        self.root.geometry("800x680")

        # Automatically pick the folder where this script/exe resides
        if getattr(sys, 'frozen', False):
            self.source_dir = Path(sys.executable).parent
        else:
            self.source_dir = Path(__file__).parent.resolve()

        self.has_text_dir = self.source_dir / "has_text"
        self.no_text_dir = self.source_dir / "has_no_text"
        self.maybe_dir = self.source_dir / "maybe"

        # Create subfolders
        self.has_text_dir.mkdir(exist_ok=True)
        self.no_text_dir.mkdir(exist_ok=True)
        self.maybe_dir.mkdir(exist_ok=True)

        self.images = []
        self.current_index = 0
        self.history = []  # Stack for (original_path, destination_path, index)

        # Setup UI layout
        self.info_label = tk.Label(self.root, text="", font=("Arial", 11))
        self.info_label.pack(pady=10)

        # Control Frame
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=5)

        self.btn_undo = tk.Button(btn_frame, text="Undo (Ctrl+Z)", command=self.undo, font=("Arial", 10), state="disabled")
        self.btn_undo.pack(side="left", padx=5)

        self.image_label = tk.Label(self.root)
        self.image_label.pack(expand=True, fill="both", padx=10, pady=10)

        # Key Bindings
        self.root.bind("<Left>", lambda event: self.sort_image("text"))
        self.root.bind("<Right>", lambda event: self.sort_image("no_text"))
        self.root.bind("<Up>", lambda event: self.sort_image("maybe"))
        self.root.bind("<Control-z>", lambda event: self.undo())
        self.root.bind("<Control-Z>", lambda event: self.undo())

        # Auto-load images in directory
        self.load_directory_images()

    def load_directory_images(self):
        self.images = [
            f for f in self.source_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]

        if not self.images:
            self.info_label.config(text="No images found in the script directory.")
            messagebox.showinfo("No Images", f"No supported image files found in:\n{self.source_dir}")
            return

        self.current_index = 0
        self.show_current_image()

    def show_current_image(self):
        if self.current_index >= len(self.images):
            self.image_label.config(image='')
            self.info_label.config(text="Finished sorting all images!\nPress Undo (Ctrl+Z) to revert any move.")
            return

        img_path = self.images[self.current_index]
        self.info_label.config(
            text=f"Image {self.current_index + 1}/{len(self.images)}: {img_path.name}\n"
                 f"[← Left]: Text | [→ Right]: No Text | [↑ Up]: Maybe | [Ctrl+Z]: Undo"
        )

        try:
            pil_img = Image.open(img_path)
            pil_img.thumbnail((750, 480))
            tk_img = ImageTk.PhotoImage(pil_img)

            self.image_label.config(image=tk_img)
            self.image_label.image = tk_img
        except Exception as e:
            print(f"Error loading image {img_path}: {e}")
            self.current_index += 1
            self.show_current_image()

    def sort_image(self, destination_type):
        if not self.images or self.current_index >= len(self.images):
            return

        img_path = self.images[self.current_index]

        if destination_type == "text":
            target_dir = self.has_text_dir
        elif destination_type == "no_text":
            target_dir = self.no_text_dir
        elif destination_type == "maybe":
            target_dir = self.maybe_dir

        destination = target_dir / img_path.name

        try:
            shutil.move(str(img_path), str(destination))
            self.history.append((img_path, destination, self.current_index))
            self.update_undo_button()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to move {img_path.name}: {e}")
            return

        self.current_index += 1
        self.show_current_image()

    def undo(self):
        if not self.history:
            return

        original_path, current_destination, previous_index = self.history.pop()

        try:
            shutil.move(str(current_destination), str(original_path))
            self.current_index = previous_index
            self.update_undo_button()
            self.show_current_image()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to undo move for {original_path.name}: {e}")

    def update_undo_button(self):
        if self.history:
            self.btn_undo.config(state="normal")
        else:
            self.btn_undo.config(state="disabled")

if __name__ == "__main__":
    root = tk.Tk()
    app = ImageSorterApp(root)
    root.mainloop()