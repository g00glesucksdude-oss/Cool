#Requires AutoHotkey v2.0
#SingleInstance Force
#MaxThreadsPerHotkey 1

if !A_IsAdmin {
    try Run '*RunAs "' A_ScriptFullPath '"'
    ExitApp
}

SendMode "Event"
CoordMode "Mouse", "Screen"
SetKeyDelay -1, -1
SetMouseDelay -1
ProcessSetPriority "High"

HoldMs  := 10
SwapMs  := 5
ClickMs := 5
LookMs  := 40      ; wait after a look move
LookPx  := 40      ; look distance in pixels, tune to your sensitivity
ChordMs := 25      ; window to catch Shift+L+R (adds this delay to every L/R click)
InvMs   := 250       ; wait for the inventory to open (raise if it still grabs the wrong item)
OvAlpha := 40      ; recording overlay darkness (0-255)
PlayOffY := 5      ; playback clicks land this many pixels below the recorded spot

; R mode 2 timing (much faster than the rest, raise these if the game starts missing inputs)
M2Hold := 2        ; key/button hold time
M2Wait := 4        ; wait after each swap/click
M2Look := 12       ; wait after the look-up before hitting the crystal

; F sequence
FSteps := [
    ["8",       SwapMs],
    ["RButton", ClickMs],
    ["7",       SwapMs],
    ["RButton", 0]
]

; G sequence
GSteps := [
    ["5",       SwapMs],
    ["RButton", ClickMs],
    ["1",       0]
]

; C sequence
CSteps := [
    ["6",       SwapMs],
    ["RButton", ClickMs],
    ["1",       0]
]

; Slots: 1 sword, 5 pearl, 6 obsidian, 7 glowstone, 8 anchor, 9 crystal

; Middle click: throw pearl, back to sword
PSteps := [
    ["5",       SwapMs],
    ["RButton", ClickMs],
    ["1",       0]
]

; R mode 1 (default): obsidian opener once, then crystal loop until R is pressed again
ROpen := [
    ["6",       SwapMs],
    ["RButton", ClickMs]
]

RPattern := [
    ["9",        SwapMs],
    ["RButton",  ClickMs],
    ["LookUp",   LookMs],
    ["LButton",  LookMs],
    ["LookDown", LookMs]
]

; R mode 2 (toggle with Shift+R): one-shot, obsidian x2, crystal, hit it, back to sword
RTwo := [
    ["6",        M2Wait],
    ["RButton",  M2Wait],
    ["6",        M2Wait],
    ["RButton",  M2Wait],
    ["9",        M2Wait],
    ["RButton",  M2Wait],
    ["LookUp",   M2Look],
    ["LButton",  M2Wait],
    ["LookDown", M2Wait],
    ["1",        0]
]

GameWin := "ahk_exe Minecraft.Windows.exe"
Enabled := false
Spam := false
RDown := false                    ; R is physically held (blocks key auto-repeat)
RMode2 := false                  ; Shift+R toggles: false = crystal loop, true = obsidian x2 one-shot
ViewUp := false
Armed := false                  ; true after F finishes, until your next right-click
Held := ""

Rec  := false                   ; recording mode
Busy := false                   ; inventory playback running
Locs := []                      ; recorded click positions [x, y]
Swal := Map("L", false, "R", false)   ; physical button down was swallowed -> swallow its release
Fwd  := Map("L", false, "R", false)   ; we forwarded a down -> forward its release

; Recording overlay: covers the game, catches clicks so the game never sees them
ov := Gui("+AlwaysOnTop -Caption +ToolWindow -DPIScale")
ov.BackColor := "000000"
OnMessage(0x201, OvClick)       ; WM_LBUTTONDOWN

DllCall("winmm\timeBeginPeriod", "UInt", 1)
OnExit(Cleanup)

+Esc:: ExitApp                  ; Shift+Esc, works anytime

~LControl Up:: {
    global Enabled := !Enabled
    ResetState()
    ToolTip("Macro " (Enabled ? "ON" : "OFF"))
    SetTimer(ToolTip, -800)
}

#HotIf Enabled && WinActive(GameWin)
*f:: {
    global Armed
    if !Spam {
        Armed := false
        RunSeq(FSteps, false)
        Armed := true
    }
}
*g:: {
    if !Spam
        RunSeq(GSteps, false)
}
*c:: {
    if !Spam
        RunSeq(CSteps, false)
}
*MButton:: {
    if !Spam
        RunSeq(PSteps, false)
}
*v:: Playback()
*r:: {
    global Spam, RMode2, RDown
    if RDown                                        ; ignore key auto-repeat
        return
    RDown := true
    if GetKeyState("Shift", "P") {
        RMode2 := !RMode2
        Spam := false                               ; switching modes always kills any running loop
        ToolTip("R mode: " (RMode2 ? "2 (one-shot)" : "1 (obsidian + crystal loop)"))
        SetTimer(ToolTip, -800)
    } else if Spam {
        Spam := false                               ; stop a running loop (mode 1 only)
    } else if RMode2 {
        RunSeq(RTwo, false, M2Hold)                 ; mode 2: run once, never loops
    } else {
        Spam := true                                ; mode 1: loop until R is pressed again
        SetTimer(SpamLoop, -1)
    }
}

; Physical L/R clicks are swallowed here (no ~), then forwarded unless they form a Shift+L+R chord
*LButton:: ClickDown("L")
*RButton:: ClickDown("R")
#HotIf Enabled
~*r Up:: {
    global RDown := false
}
#HotIf Enabled && WinActive(GameWin) && (Swal["L"] || Fwd["L"])
*LButton Up:: ClickUp("L")
#HotIf Enabled && WinActive(GameWin) && (Swal["R"] || Fwd["R"])
*RButton Up:: ClickUp("R")
#HotIf

; ---------------- click handling / recording / playback ----------------

ClickDown(b) {
    Swal[b] := true
    if Busy
        return
    bn := b "Button"
    ob := (b = "L" ? "R" : "L") "Button"
    t := A_TickCount
    while GetKeyState(bn, "P") && A_TickCount - t < ChordMs {
        if GetKeyState(ob, "P") && GetKeyState("Shift", "P") {
            if !Rec
                StartRec()
            return
        }
        Sleep 1
    }
    Swal[b] := false                                ; not a chord: pass it on as a normal click
    Fwd[b] := true
    Send "{" bn " down}"
    if !GetKeyState(bn, "P") {                      ; already released during the window
        Sleep HoldMs
        FwdUp(b)
    }
}

ClickUp(b) {
    if Fwd[b]
        FwdUp(b)
    else
        Swal[b] := false
}

FwdUp(b) {
    global Armed
    if !Fwd[b]
        return
    Send "{" b "Button up}"
    Fwd[b] := false
    if b = "R" && Armed {                           ; first right-click after F -> slot 1
        Armed := false
        Tap("1")
    }
}

StartRec() {
    global Rec := true
    Locs.Length := 0
    Swal["L"] := false                              ; game loses focus now, so its Up hotkeys go inactive
    Swal["R"] := false
    try
        WinGetPos &x, &y, &w, &h, GameWin
    catch {
        x := 0, y := 0, w := A_ScreenWidth, h := A_ScreenHeight
    }
    ov.Show("x" x " y" y " w" w " h" h)
    WinSetTransparent OvAlpha, "ahk_id " ov.Hwnd
    ToolTip("REC - click items, release Shift to stop")
    SetTimer(RecWatch, 15)
}

OvClick(wParam, lParam, msg, hwnd) {
    if !Rec || hwnd != ov.Hwnd
        return
    MouseGetPos &x, &y
    Locs.Push([x, y])
    ToolTip("REC: " Locs.Length)
}

RecWatch() {
    if GetKeyState("Shift", "P")
        return
    global Rec := false
    SetTimer(RecWatch, 0)
    ov.Hide()
    try WinActivate GameWin
    ToolTip("Saved " Locs.Length " click(s)")
    SetTimer(ToolTip, -1000)
}

Playback() {
    global Busy
    if Busy || Spam || Rec
        return
    if Locs.Length = 0 {
        ToolTip("No saved clicks")
        SetTimer(ToolTip, -800)
        return
    }
    Busy := true
    loc := Locs.RemoveAt(1)                         ; use it up, next one is ready for next time
    try {
        Tap("e")
        Sleep InvMs
        MouseMove loc[1], loc[2] + PlayOffY, 0
        Sleep ClickMs
        ShiftClick()
        Sleep ClickMs
        Tap("e")
    } finally
        Busy := false
    ToolTip(Locs.Length " left")
    SetTimer(ToolTip, -800)
}

ShiftClick() {
    global Held := "LShift"
    Send "{LShift down}"
    Sleep HoldMs
    Send "{LButton down}"
    Sleep HoldMs
    Send "{LButton up}"
    Sleep HoldMs
    Send "{LShift up}"
    Held := ""
}

ResetState() {
    global Rec := false, Spam := false, Armed := false, RDown := false
    SetTimer(RecWatch, 0)
    ov.Hide()
    for b in ["L", "R"] {
        if Fwd[b]
            Send "{" b "Button up}"
        Fwd[b] := false
        Swal[b] := false
    }
}

; ---------------- sequences ----------------

SpamLoop() {
    RunSeq(ROpen, true)
    while Spam
        RunSeq(RPattern, true)
    if ViewUp
        Do("LookDown")
}

RunSeq(steps, abortable, hold := "") {
    for step in steps {
        if abortable && !Spam
            return
        Do(step[1], hold)
        if step[2]
            Sleep step[2]
    }
}

Do(a, hold := "") {
    global ViewUp
    switch a {
        case "LookUp":
        {
            Look(0, -LookPx)
            ViewUp := true
        }
        case "LookDown":
        {
            Look(0, LookPx)
            ViewUp := false
        }
        default:
            Tap(a, hold)
    }
}

Look(dx, dy) => DllCall("mouse_event", "UInt", 1, "Int", dx, "Int", dy, "UInt", 0, "UPtr", 0)

Tap(k, hold := "") {
    global Held := k
    Send "{" k " down}"
    Sleep (hold = "" ? HoldMs : hold)
    Send "{" k " up}"
    Held := ""
}

Cleanup(*) {
    if Held != ""
        Send "{" Held " up}"
    for b in ["L", "R"]
        if Fwd[b]
            Send "{" b "Button up}"
    DllCall("winmm\timeEndPeriod", "UInt", 1)
}
