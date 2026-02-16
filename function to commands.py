import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# ---------- Configuration defaults ----------
DEFAULT_MAX_DEPTH = float("inf")        # no recursion depth limit
DEFAULT_MAX_EXPANSIONS = float("inf")   # no line expansion limit

# ---------- Utility functions ----------
def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")

def write_text_file(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

# ---------- Parser / Inliner ----------
class Inliner:
    def __init__(self, functions_folder, max_depth=DEFAULT_MAX_DEPTH, max_expansions=DEFAULT_MAX_EXPANSIONS):
        self.functions_folder = functions_folder
        self.max_depth = max_depth
        self.max_expansions = max_expansions
        self.expansion_count = 0
        self.cache = {}
        self.active_stack = set()  # track functions currently being expanded

    def load_function_lines(self, func_name):
        if func_name in self.cache:
            return self.cache[func_name]

        # Normalize separators: allow colon or slash
        candidate = func_name.replace(":", os.sep).replace("/", os.sep)
        path1 = os.path.join(self.functions_folder, candidate + ".mcfunction")

        chosen = None
        if os.path.isfile(path1):
            chosen = path1
        else:
            fname = os.path.basename(candidate)
            for root, _, files in os.walk(self.functions_folder):
                if fname + ".mcfunction" in files:
                    chosen = os.path.join(root, fname + ".mcfunction")
                    break

        if not chosen:
            self.cache[func_name] = None
            return None

        text = read_text_file(chosen)
        lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
        self.cache[func_name] = lines
        return lines

    def find_function_call(self, line):
        m = re.search(r'\bfunction\s+([^\s]+)', line)
        if not m:
            return None
        func_name = m.group(1)
        before = line[:m.start()]
        run_match = re.search(r'(.*\brun\s+)$', before)
        prefix_exec = run_match.group(1) if run_match else ''
        return prefix_exec, func_name

    def combine_prefixes(self, outer_prefix, inner_prefix):
        if not outer_prefix:
            return inner_prefix or ''
        if not inner_prefix:
            return outer_prefix or ''
        return (outer_prefix + " " + inner_prefix).strip()

    def inline_function(self, func_name, current_prefix='', depth=0):
        # expansion limit check
        if self.max_expansions != float("inf") and self.expansion_count >= self.max_expansions:
            return [f"# expansion limit reached ({self.max_expansions}) - stopped inlining {func_name}"]

        # recursion depth check
        if self.max_depth != float("inf") and depth > self.max_depth:
            return [f"# max recursion depth {self.max_depth} reached for {func_name}"]

        # cycle detection
        if func_name in self.active_stack:
            return [f"# cycle detected: {func_name} already in expansion stack"]

        self.active_stack.add(func_name)

        lines = self.load_function_lines(func_name)
        if lines is None:
            self.active_stack.remove(func_name)
            return [f"# function not found: {func_name}"]

        out = [f"# inlined from {func_name}"]
        for line in lines:
            if not line.strip() or line.strip().startswith("#"):
                out.append((current_prefix + line).strip() if current_prefix else line)
                self.expansion_count += 1
                continue

            call = self.find_function_call(line)
            if call:
                inner_prefix_from_line, inner_func = call
                new_prefix = self.combine_prefixes(current_prefix, inner_prefix_from_line)
                inlined = self.inline_function(inner_func, current_prefix=new_prefix, depth=depth+1)
                out.extend(inlined)
            else:
                combined = (current_prefix + line).strip() if current_prefix else line
                out.append(combined)
                self.expansion_count += 1

        self.active_stack.remove(func_name)
        return out


    def find_function_call(self, line):
        m = re.search(r'\bfunction\s+([^\s]+)', line)
        if not m:
            return None
        func_name = m.group(1)
        before = line[:m.start()]
        run_match = re.search(r'(.*\brun\s+)$', before)
        prefix_exec = run_match.group(1) if run_match else ''
        return prefix_exec, func_name

    def combine_prefixes(self, outer_prefix, inner_prefix):
        if not outer_prefix:
            return inner_prefix or ''
        if not inner_prefix:
            return outer_prefix or ''
        return (outer_prefix + " " + inner_prefix).strip()

    def inline_function(self, func_name, current_prefix='', depth=0):
        if self.max_expansions != float("inf") and self.expansion_count >= self.max_expansions:
            return [f"# expansion limit reached ({self.max_expansions}) - stopped inlining {func_name}"]

        if self.max_depth != float("inf") and depth > self.max_depth:
            return [f"# max recursion depth {self.max_depth} reached for {func_name}"]

        if func_name in self.active_stack:
            return [f"# cycle detected: {func_name} already in expansion stack"]

        self.active_stack.add(func_name)

        lines = self.load_function_lines(func_name)
        if lines is None:
            self.active_stack.remove(func_name)
            return [f"# function not found: {func_name}"]

        out = [f"# inlined from {func_name}"]
        for line in lines:
            if not line.strip() or line.strip().startswith("#"):
                out.append((current_prefix + line).strip() if current_prefix else line)
                self.expansion_count += 1
                continue

            call = self.find_function_call(line)
            if call:
                inner_prefix_from_line, inner_func = call
                new_prefix = self.combine_prefixes(current_prefix, inner_prefix_from_line)
                inlined = self.inline_function(inner_func, current_prefix=new_prefix, depth=depth+1)
                out.extend(inlined)
            else:
                combined = (current_prefix + line).strip() if current_prefix else line
                out.append(combined)
                self.expansion_count += 1

        self.active_stack.remove(func_name)
        return out




    def find_function_call(self, line):
        m = re.search(r'\bfunction\s+([^\s]+)', line)
        if not m:
            return None
        func_name = m.group(1)
        before = line[:m.start()]
        run_match = re.search(r'(.*\brun\s+)$', before)
        if run_match:
            prefix_exec = run_match.group(1)
        else:
            prefix_exec = ''
        return prefix_exec, func_name

    def combine_prefixes(self, outer_prefix, inner_prefix):
        if not outer_prefix:
            return inner_prefix or ''
        if not inner_prefix:
            return outer_prefix or ''
        return (outer_prefix + " " + inner_prefix).strip()

def inline_function(self, func_name, current_prefix='', depth=0):
    # Just load the function and return its lines, no recursion
    lines = self.load_function_lines(func_name)
    if lines is None:
        return [f"# function not found: {func_name}"]

    out = [f"# inlined from {func_name}"]
    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            out.append(line)
            continue

        call = self.find_function_call(line)
        if call:
            # Instead of recursing, just keep the call line
            out.append(line)
        else:
            combined = (current_prefix + line).strip() if current_prefix else line
            out.append(combined)
    return out


# ---------- GUI ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title("MCFunction Inliner")

        frm = tk.Frame(root)
        frm.pack(fill="x", padx=8, pady=6)

        tk.Button(frm, text="Select functions folder", command=self.select_functions_folder).pack(side="left")
        tk.Button(frm, text="Select source .mcfunction", command=self.select_source).pack(side="left", padx=6)

        self.src_var = tk.StringVar()
        self.funcs_var = tk.StringVar()
        self.depth_var = tk.StringVar(value="inf")
        self.maxexp_var = tk.StringVar(value="inf")

        tk.Label(root, text="Functions folder:").pack(anchor="w", padx=8)
        tk.Entry(root, textvariable=self.funcs_var, width=120).pack(fill="x", padx=8)

        tk.Label(root, text="Source file:").pack(anchor="w", padx=8, pady=(6,0))
        tk.Entry(root, textvariable=self.src_var, width=120).pack(fill="x", padx=8)

        opts = tk.Frame(root)
        opts.pack(fill="x", padx=8, pady=6)
        tk.Label(opts, text="Max recursion depth (or 'inf'):").pack(side="left")
        tk.Entry(opts, textvariable=self.depth_var, width=8).pack(side="left", padx=6)
        tk.Label(opts, text="Max total expansions (or 'inf'):").pack(side="left", padx=(12,0))
        tk.Entry(opts, textvariable=self.maxexp_var, width=8).pack(side="left", padx=6)

        tk.Button(root, text="Expand and Save", command=self.expand_and_save).pack(padx=8, pady=6)

        tk.Label(root, text="Preview / Log:").pack(anchor="w", padx=8)
        self.preview = scrolledtext.ScrolledText(root, width=120, height=24)
        self.preview.pack(padx=8, pady=(0,8))

    def select_functions_folder(self):
        d = filedialog.askdirectory(title="Select functions folder (where referenced functions live)")
        if d:
            self.funcs_var.set(d)

    def select_source(self):
        funcs = self.funcs_var.get().strip()
        if not funcs or not os.path.isdir(funcs):
            messagebox.showerror("Error", "Select a valid functions folder first.")
            return
        p = filedialog.askopenfilename(
            initialdir=funcs,
            title="Select source .mcfunction",
            filetypes=[("MCFunction","*.mcfunction"),("All files","*.*")]
        )
        if p:
            self.src_var.set(p)
            self.preview.delete("1.0", tk.END)
            self.preview.insert(tk.END, read_text_file(p))

    def expand_and_save(self):
        src = self.src_var.get().strip()
        funcs = self.funcs_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("Error", "Select a valid source .mcfunction file.")
            return
        if not funcs or not os.path.isdir(funcs):
            messagebox.showerror("Error", "Select a valid functions folder.")
            return

        # Parse depth/expansion values
        depth_val = self.depth_var.get().strip()
        maxexp_val = self.maxexp_var.get().strip()
        max_depth = float("inf") if depth_val.lower() == "inf" else int(depth_val)
        max_exp = float("inf") if maxexp_val.lower() == "inf" else int(maxexp_val)

        inliner = Inliner(functions_folder=funcs, max_depth=max_depth, max_expansions=max_exp)

        src_text = read_text_file(src)
        src_lines = [ln.rstrip("\r\n") for ln in src_text.splitlines()]

        result_lines = []
        for line in src_lines:
            call = inliner.find_function_call(line)
            if call:
                prefix_exec, func_name = call
                new_prefix = prefix_exec or ''
                inlined = inliner.inline_function(func_name, current_prefix=new_prefix, depth=1)
                result_lines.extend(inlined)
            else:
                result_lines.append(line)
            if max_exp != float("inf") and inliner.expansion_count >= inliner.max_expansions:
                result_lines.append(f"# expansion limit reached ({inliner.max_expansions}) - stopped further inlining")
                break

        # Save output next to source file
        base, ext = os.path.splitext(src)
        out_path = base + "_expanded" + ext
        write_text_file(out_path, "\n".join(result_lines))

        # Show preview and notify
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, f"Wrote expanded file to:\n{out_path}\n\n--- Preview (first 2000 chars) ---\n")
        preview_text = "\n".join(result_lines)
        self.preview.insert(tk.END, preview_text[:2000])
        messagebox.showinfo("Done", f"Expanded file written to:\n{out_path}\nTotal lines produced: {inliner.expansion_count}")

# ---------- Run ----------
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
