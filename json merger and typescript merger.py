import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox

# -------------------------------
# JSON merging
# -------------------------------
def deep_merge(dict1, dict2):
    """Recursively merge two dictionaries with smart conflict resolution."""
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dictionaries
                result[key] = deep_merge(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                # Smart list merging with deduplication
                if key in ['components', 'component_groups', 'events']:
                    # For component arrays, merge by unique identifiers
                    result[key] = merge_component_arrays(result[key], value)
                else:
                    # Simple deduplication for other arrays
                    result[key] = deduplicate_list(result[key], value)
            else:
                # For conflicting primitives, keep the newer value (dict2)
                result[key] = value
        else:
            result[key] = value
    return result

def merge_component_arrays(list1, list2):
    """Merge component arrays by checking for duplicate components."""
    result = list1.copy()
    existing_keys = set()
    
    # Extract keys from existing components
    for item in result:
        if isinstance(item, dict):
            # Get the component identifier (first key in the dict)
            if item:
                existing_keys.add(list(item.keys())[0])
    
    # Add new components that don't conflict
    for item in list2:
        if isinstance(item, dict) and item:
            component_key = list(item.keys())[0]
            if component_key not in existing_keys:
                result.append(item)
                existing_keys.add(component_key)
    
    return result

def deduplicate_list(list1, list2):
    """Deduplicate lists while preserving order and handling complex objects."""
    result = list1.copy()
    for item in list2:
        if isinstance(item, dict):
            # For dicts, check if equivalent dict exists
            if not any(are_dicts_equivalent(item, existing) for existing in result if isinstance(existing, dict)):
                result.append(item)
        elif item not in result:
            result.append(item)
    return result

def are_dicts_equivalent(dict1, dict2):
    """Check if two dictionaries are equivalent."""
    return json.dumps(dict1, sort_keys=True) == json.dumps(dict2, sort_keys=True)

def merge_json_files(file_list, output_file):
    merged = {}
    highest_format_version = "1.0.0"
    
    for file in file_list:
        with open(file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            # Track the highest format_version
            if 'format_version' in data:
                current_version = data['format_version']
                if compare_versions(current_version, highest_format_version) > 0:
                    highest_format_version = current_version
            
            merged = deep_merge(merged, data)
    
    # Set the highest format_version found
    if 'format_version' in merged:
        merged['format_version'] = highest_format_version
    
    with open(output_file, 'w', encoding='utf-8') as out:
        json.dump(merged, out, indent=4)
    
    messagebox.showinfo("Success", f"Merged {len(file_list)} JSON files into {output_file}\nFormat version: {highest_format_version}")

def compare_versions(v1, v2):
    """Compare two version strings. Returns 1 if v1>v2, -1 if v1<v2, 0 if equal."""
    try:
        parts1 = [int(x) for x in str(v1).split('.')]
        parts2 = [int(x) for x in str(v2).split('.')]
        
        # Pad shorter version with zeros
        max_len = max(len(parts1), len(parts2))
        parts1 += [0] * (max_len - len(parts1))
        parts2 += [0] * (max_len - len(parts2))
        
        for p1, p2 in zip(parts1, parts2):
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        return 0
    except:
        # If version comparison fails, treat as strings
        if v1 > v2:
            return 1
        elif v1 < v2:
            return -1
        return 0

# -------------------------------
# JS/TS merging
# -------------------------------
def rename_duplicate_functions(js_code, existing_funcs):
    """Rename duplicate function definitions to avoid collisions."""
    def replacer(match):
        func_name = match.group(1)
        if func_name in existing_funcs:
            new_name = func_name + "_merged"
            return f"function {new_name}"
        else:
            existing_funcs.add(func_name)
            return match.group(0)
    return re.sub(r'function\s+([a-zA-Z_]\w*)', replacer, js_code)

def extract_event_handlers(js_code, event_name):
    """Find all handlers for a given event and return them with balanced brackets."""
    # Match the subscribe call and capture the handler function
    pattern = re.compile(rf'{re.escape(event_name)}\.subscribe\s*\(\s*(\([^)]*\)\s*=>\s*\{{.*?\}}|\(.*?\)\s*\{{.*?\}}|function\s*\([^)]*\)\s*\{{.*?\}})', re.DOTALL)
    handlers = []
    
    for match in pattern.finditer(js_code):
        handler = match.group(1)
        # Count brackets to ensure we have the complete handler
        open_count = handler.count('{')
        close_count = handler.count('}')
        
        # If brackets are balanced, add the handler
        if open_count == close_count and open_count > 0:
            handlers.append(handler)
    
    return handlers

def deduplicate_imports(js_code):
    """Remove duplicate import lines and move them to the top."""
    seen = set()
    imports = []
    other_lines = []
    for line in js_code.splitlines():
        if line.strip().startswith("import "):
            if line not in seen:
                imports.append(line)
                seen.add(line)
        else:
            other_lines.append(line)
    return "\n".join(imports) + "\n\n" + "\n".join(other_lines)

def build_wrappers(event_handlers):
    """Generate clean wrapper code for collected event handlers."""
    wrapper_code = "\n\n// --- AUTO-GENERATED WRAPPERS ---\n\n"
    for event, handlers in event_handlers.items():
        if not handlers:
            continue
            
        wrapper_code += f"// Wrapping {event} with {len(handlers)} handler(s)\n"
        wrapper_code += f"{event}.subscribe((initEvent) => {{\n"
        
        for i, handler in enumerate(handlers):
            # Wrap each handler in a try-catch and IIFE to isolate execution
            wrapper_code += f"    // Handler {i + 1}\n"
            wrapper_code += f"    try {{\n"
            wrapper_code += f"        ({handler})(initEvent);\n"
            wrapper_code += f"    }} catch(e) {{\n"
            wrapper_code += f"        console.error('Handler {i + 1} for {event} failed:', e);\n"
            wrapper_code += f"    }}\n\n"
        
        wrapper_code += "});\n\n"
    
    return wrapper_code


def merge_script_files(file_list, output_file):
    existing_funcs = set()
    merged_code = ""
    event_handlers = {}

    for idx, file in enumerate(file_list):
        with open(file, 'r') as f:
            code = f.read()
            code = rename_duplicate_functions(code, existing_funcs)
            merged_code += f"\n\n// --- MERGED SCRIPT {idx+1}: {file} ---\n\n"
            merged_code += code

            # Collect event handlers
            for event in ["world.events.beforeChat", "world.events.tick", "system.run", "world.beforeEvents.worldInitialize"]:
                handlers = extract_event_handlers(code, event)
                if handlers:
                    event_handlers.setdefault(event, []).extend(handlers)

    # Build wrapper functions
    wrapper_code = build_wrappers(event_handlers)
    merged_code += wrapper_code

    # Deduplicate imports and move them to the top
    merged_code = deduplicate_imports(merged_code)

    with open(output_file, 'w') as out:
        out.write(merged_code)
    messagebox.showinfo("Success", f"Merged {len(file_list)} script files into {output_file} with event wrapping and import deduplication")

# -------------------------------
# Tkinter GUI
# -------------------------------
def select_json_files():
    files = filedialog.askopenfilenames(title="Select player.json files", filetypes=[("JSON files", "*.json")])
    if files:
        output = filedialog.asksaveasfilename(title="Save merged player.json", defaultextension=".json")
        if output:
            merge_json_files(files, output)

def select_script_files():
    files = filedialog.askopenfilenames(title="Select JS/TS files", filetypes=[("Script files", "*.js *.ts")])
    if files:
        output = filedialog.asksaveasfilename(title="Save merged script", defaultextension=".js")
        if output:
            merge_script_files(files, output)

root = tk.Tk()
root.title("MCBE Addon Merger")

tk.Label(root, text="Minecraft Bedrock Addon Merger", font=("Arial", 14)).pack(pady=10)
tk.Button(root, text="Merge player.json files", command=select_json_files, width=30).pack(pady=5)
tk.Button(root, text="Merge JS/TS script files", command=select_script_files, width=30).pack(pady=5)

root.mainloop()
