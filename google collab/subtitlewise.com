from pathlib import Path

# Get the directory where the script is currently located
current_dir = Path(__file__).resolve().parent

# Track renamed files
renamed_count = 0

for file_path in current_dir.glob("*_en.srt"):
    # Create new file path replacing '_en.srt' with '.srt'
    new_name = file_path.name[:-7] + ".srt"
    new_path = file_path.with_name(new_name)

    # Avoid overwriting if target .srt file already exists
    if new_path.exists():
        print(f"[SKIP] Target already exists: {new_name}")

        continue

    file_path.rename(new_path)
    print(f"[RENAMED] {file_path.name} -> {new_name}")
    renamed_count += 1

print(f"\nDone! Renamed {renamed_count} file(s).")
