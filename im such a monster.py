import uuid
import hashlib

# Instruction display for launch
def print_instructions():
    print("="*60)
    print(" MINECRAFT BEDROCK COMBO GENERATOR v2.0 (Official)")
    print("="*60)
    print("SYNTAX RULES:")
    print(" 1. Items: slot item_name (e.g., mainhand diamond)")
    print(" 2. Slots: mainhand, offhand, hotbar [idx], armor_head, etc.")
    print(" 3. Flags: COMMAND: say hi, SCOREBOARD: mySB, START: 0")
    print(" 4. Logic: STRICT: true (Resets progress if item is dropped)")
    print(" 5. UUID:  Enabled by default. Use 'USE_UUID: false' to disable.")
    print("-" * 60)
    print("EXAMPLE INPUT:")
    print(" mainhand diamond, mainhand emerald, STRICT: true, COMMAND: say Success")
    print("="*60 + "\n")

SLOT_MAP = {
    "mainhand": "slot.weapon.mainhand", "offhand": "slot.weapon.offhand",
    "hotbar": "slot.hotbar", "armor_head": "slot.armor.head",
    "armor_chest": "slot.armor.chest", "armor_legs": "slot.armor.legs",
    "armor_feet": "slot.armor.feet", "inventory": "slot.inventory"
}

def parse_input(user_input):
    parts = [p.strip() for p in user_input.split(",") if p.strip()]
    sequence, command, debug, scoreboard, start_val, strict, use_uuid = [], "say done", False, "comboSB", 0, False, True
    
    merged = []
    i = 0
    while i < len(parts):
        curr = parts[i]
        if curr.lower() in SLOT_MAP and (i + 1) < len(parts) and parts[i+1].lower() not in SLOT_MAP:
            merged.append(f"{curr} {parts[i+1]}")
            i += 2
        else:
            merged.append(curr)
            i += 1

    for p in merged:
        up = p.upper()
        if "COMMAND" in up: command = p.split(":", 1)[1].strip() if ":" in p else p.split(" ", 1)[1].strip()
        elif "DEBUG" in up: debug = "TRUE" in up
        elif "STRICT" in up: strict = "TRUE" in up
        elif "USE_UUID" in up: use_uuid = "FALSE" not in up
        elif "SCOREBOARD" in up: scoreboard = p.split(":", 1)[1].strip() if ":" in p else p.split(" ", 1)[1].strip()
        elif "START" in up:
            val = p.split(":", 1)[1].strip() if ":" in p else p.split(" ", 1)[1].strip()
            if val.isdigit(): start_val = int(val)
        else:
            tokens = p.split()
            if not tokens: continue
            loc_key, idx = "mainhand", None
            if tokens[0].lower() in SLOT_MAP:
                loc_key = tokens[0].lower()
                tokens = tokens[1:]
                if loc_key in ["hotbar", "inventory"] and tokens:
                    idx, tokens = tokens[0], tokens[1:]
            if tokens:
                item = tokens[0]
                extra = tokens[1:]
                data = next((t.split('=')[1] for t in extra if "data=" in t.lower()), None)
                sequence.append({"slot": SLOT_MAP[loc_key], "item": item, "idx": idx, "data": data})
    return sequence, command, debug, scoreboard, start_val, strict, use_uuid

def build_selector(step, force_zero=False):
    q = "0" if force_zero else "1.."
    parts = [f"item={step['item']}", f"location={step['slot']}"]
    if step.get('idx'): parts.append(f"slot={step['idx']}")
    if step.get('data'): parts.append(f"data={step['data']}")
    parts.append(f"quantity={q}")
    return f"hasitem={{{','.join(parts)}}}"

def generate_chain(sequence, command, debug, scoreboard, start_val, strict, use_uuid):
    total_steps = len(sequence)
    max_score = start_val + total_steps
    session_id = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:8]
    
    tags = []
    for i, s in enumerate(sequence):
        item_name = s['item'].split(':')[-1]
        suffix = f"_{session_id}" if use_uuid else f"_{i+1}"
        tags.append((f"ishold_{item_name}{suffix}", f"hasheld_{item_name}{suffix}"))
    
    output = ["### 1. INITIALIZATION", f"scoreboard objectives add {scoreboard} dummy {scoreboard}", f"scoreboard players add @a {scoreboard} 0\n"]

    output.append(f"### 2. COMBO STEPS (Forward Order: 1 -> {total_steps})")
    for i in range(total_steps):
        step, ishold, hasheld = sequence[i], tags[i][0], tags[i][1]
        curr, nxt = start_val + i, start_val + i + 1
        sel = build_selector(step)
        output.append(f"tag @a[{sel}] add {ishold}")
        output.append(f"tag @a[tag={ishold}] add {hasheld}")
        output.append(f"execute as @a[tag={ishold},scores={{{scoreboard}={curr}}}] run scoreboard players set @s {scoreboard} {nxt}")
        if debug: output.append(f"title @a[tag={ishold},scores={{{scoreboard}={nxt}}}] actionbar Step {nxt}: {step['item']}")
        output.append(f"tag @a remove {ishold}")
    output.append("")

    output.append("### 3. FINAL EXECUTION")
    output.append(f"execute as @a[scores={{{scoreboard}={max_score}}}] run {command}")
    for _, hasheld in tags:
        output.append(f"execute as @a[scores={{{scoreboard}={max_score}}}] run tag @s remove {hasheld}")
    output.append(f"scoreboard players set @a[scores={{{scoreboard}={max_score}}}] {scoreboard} {start_val}\n")

    if strict:
        output.append("### 4. STRICT RESET LOGIC")
        for i in range(total_steps):
            step, hasheld = sequence[i], tags[i][1]
            lvl_score = start_val + i + 1
            sel_v, sel_n = build_selector(step), build_selector(step, True)
            prev_tags = " ".join([f"if entity @s[tag={tags[j][1]}]" for j in range(i+1)])
            prefix = f"execute as @a {prev_tags} at @s run "
            output.append(f"{prefix}execute unless entity @s[{sel_v}] run scoreboard players set @s[{sel_n},scores={{{scoreboard}={lvl_score}}}] {scoreboard} {start_val}")
            output.append(f"execute as @a[scores={{{scoreboard}={start_val}}}] run tag @s remove {hasheld}")
        output.append("")

    output.append("### 5. FUNCTION CLEANUP (Paste into your cleanup.mcfunction)")
    output.append(f"scoreboard players set @a {scoreboard} {start_val}")
    for _, hasheld in tags:
        output.append(f"tag @a remove {hasheld}")

    return output

if __name__=="__main__":
    print_instructions()
    inp = input("Sequence > ")
    try:
        res = parse_input(inp)
        for c in generate_chain(*res): print(c)
    except Exception as e: print(f"Error: {e}")
