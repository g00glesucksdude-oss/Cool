import uuid

# Supported slot locations for Bedrock Edition
slot_map = {
    "mainhand": "slot.weapon.mainhand",
    "offhand": "slot.weapon.offhand",
    "hotbar": "slot.hotbar",          # requires slot=<index>
    "armor_head": "slot.armor.head",
    "armor_chest": "slot.armor.chest",
    "armor_legs": "slot.armor.legs",
    "armor_feet": "slot.armor.feet",
    "inventory": "slot.inventory"     # requires slot=<index>
}

def tag_name(prefix: str) -> str:
    """Generates a unique tag to prevent cross-combo interference."""
    return f"{prefix}_{str(uuid.uuid4())[:8]}"

def build_selector(item, slot, data=None, quantity=None, hotbar_index=None):
    """Constructs the @a[hasitem=...] selector for Bedrock."""
    if slot in ["slot.hotbar", "slot.inventory"] and hotbar_index is not None:
        inner = f"hasitem={{item={item},location={slot},slot={hotbar_index}"
    else:
        inner = f"hasitem={{item={item},location={slot}"
    
    if data is not None:
        inner += f",data={data}"
    if quantity is not None:
        inner += f",quantity={quantity}"
    
    inner += "}"
    return f"@a[{inner}]"

def parse_input(user_input):
    """Parses the shorthand user string into structured data."""
    parts = [p.strip() for p in user_input.split(",") if p.strip()]
    sequence, command, debug, scoreboard, start_val = [], None, False, "comboStep", 0
    
    for p in parts:
        up = p.upper()
        if up.startswith("COMMAND:"):
            command = p.split(":", 1)[1].strip()
        elif up.startswith("DEBUG:"):
            debug = p.split(":", 1)[1].strip().lower() == "true"
        elif up.startswith("SCOREBOARD:"):
            scoreboard = p.split(":", 1)[1].strip()
        elif up.startswith("START:"):
            start_val = int(p.split(":", 1)[1].strip())
        else:
            tokens = p.split()
            if len(tokens) < 2:
                raise ValueError(f"Invalid step: '{p}'")
            loc = tokens[0].lower()
            idx = None
            if loc in ["hotbar", "inventory"]:
                if len(tokens) < 3 or not tokens[1].isdigit():
                    raise ValueError(f"{loc} requires numeric slot index (e.g., 'hotbar 0 diamond')")
                idx = int(tokens[1])
                item = tokens[2]
                extra = tokens[3:]
            else:
                item = tokens[1]
                extra = tokens[2:]
            
            slot = slot_map.get(loc)
            if slot is None:
                raise ValueError(f"Unknown location '{loc}'")
            sequence.append((slot, item, idx, extra))
            
    return sequence, command, debug, scoreboard, start_val

def generate_chain(sequence, command, debug, scoreboard, start_val):
    """Generates the Bedrock command list with reverse-tick logic."""
    commands = []
    
    # 1. INITIAL SETUP
    commands.append(f"### COMBO SETUP ###")
    commands.append(f"scoreboard objectives add {scoreboard} dummy {scoreboard}")
    commands.append(f"scoreboard players add @a {scoreboard} {start_val}")

    steps = []
    for i, (slot, item, hotbar_index, extra) in enumerate(sequence):
        ishold = tag_name("ishold")
        hasheld = tag_name("hasheld")
        
        data, quantity = None, None
        for tok in extra:
            if tok.startswith("data="):
                data = int(tok.split("=")[1])
            elif tok.startswith("quantity="):
                quantity = int(tok.split("=")[1])

        sel_hold = build_selector(item, slot, data, quantity, hotbar_index)
        sel_not  = build_selector(item, slot, data, 0, hotbar_index)
        
        steps.append({
            "target_score": start_val + i + 1,
            "prev_score": start_val + i,
            "ishold": ishold,
            "hasheld": hasheld,
            "sel_hold": sel_hold,
            "sel_not": sel_not,
            "item": item
        })

    # 2. FINAL EXECUTION (Run before reset)
    final_score = start_val + len(sequence)
    commands.append(f"\n### FINAL EXECUTION ###")
    commands.append(f"execute as @a[scores={{{scoreboard}={final_score}}}] at @s run {command}")

    # 3. RESET MECHANISM
    commands.append(f"\n### RESET & CLEANUP ###")
    for s in steps:
        commands.append(f"execute as @a[scores={{{scoreboard}={final_score}}}] at @s run tag @s remove {s['ishold']}")
        commands.append(f"execute as @a[scores={{{scoreboard}={final_score}}}] at @s run tag @s remove {s['hasheld']}")
    
    commands.append(f"tag @a remove {steps[-1]['hasheld']}")
    commands.append(f"scoreboard players set @a[scores={{{scoreboard}={final_score}}}] {scoreboard} {start_val}")

    # 4. STEP LOGIC IN REVERSE (Prevents multi-step skipping in one tick)
    commands.append(f"\n### STEP LOGIC (REVERSED) ###")
    for s in reversed(steps):
        commands.append(f"tag {s['sel_hold']} add {s['ishold']}")
        commands.append(f"execute as @a[tag={s['ishold']},scores={{{scoreboard}={s['prev_score']}}}] at @s run tag @s add {s['hasheld']}")
        commands.append(f"execute as @a[tag={s['ishold']},scores={{{scoreboard}={s['prev_score']}}}] at @s run scoreboard players set @s {scoreboard} {s['target_score']}")
        commands.append(f"tag {s['sel_not']} remove {s['ishold']}")

        if debug:
            commands.append(f"title @a[tag={s['ishold']}] actionbar Step {s['target_score']} ({s['item']})")

    return commands

if __name__=="__main__":
    print("Example: mainhand diamond, hotbar 0 emerald, COMMAND: setblock ~ ~ ~ fire, START: 1")
    try:
        user_input = input("\n> ")
        seq, cmd, dbg, score, start = parse_input(user_input)
        chain = generate_chain(seq, cmd, dbg, score, start)
        
        print("\n--- GENERATED MC FUNCTION ---")
        for c in chain:
            print(c)
    except Exception as e:
        print(f"Error: {e}")
