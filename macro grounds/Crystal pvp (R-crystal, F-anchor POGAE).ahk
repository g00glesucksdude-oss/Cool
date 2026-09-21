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

HoldMs  := 35
SwapMs  := 65
ClickMs := 55
LookMs  := 40      ; wait after a look move
LookPx  := 40      ; look distance in pixels, tune to your sensitivity
ChordMs := 35      ; window to catch Shift+L+R (adds this delay to every L/R click)
InvMs   := 250     ; wait for the inventory to open (raise if it still grabs the wrong item)
OvAlpha := 40      ; recording overlay darkness (0-255)
PlayOffY := 5      ; playback clicks land this many pixels below the recorded spot

; A/D strafe: angle below horizontal (45 = even, 85 = almost straight down)
StrafeAng := 85
StrafePx  := 250   ; total flick length in pixels
Rad       := StrafeAng * 3.14159265358979 / 180
StrafeDX  := Round(StrafePx * Cos(Rad))
StrafeDY  := Round(StrafePx * Sin(Rad))

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

; R toggle: opener once, then pattern loops until R is pressed again
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

; Middle click special cases (A or D held): one-shot
MSpecialA := [                   ; A held: down-right
    ["DiagRight", LookMs],
    ["6",         SwapMs],
    ["RButton",   ClickMs],
    ["9",         SwapMs],
    ["RButton",   ClickMs],
    ["UpBack",    LookMs],
    ["LButton",   0]
]

MSpecialD := [                   ; D held: down-left
    ["DiagLeft",  LookMs],
    ["6",         SwapMs],
    ["RButton",   ClickMs],
    ["9",         SwapMs],
    ["RButton",   ClickMs],
    ["UpBack",    LookMs],
    ["LButton",   0]
]

GameWin := "ahk_exe Minecraft.Windows.exe"
Enabled := false
Spam := false
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
*v:: Playback()
*r:: {
    global Spam := !Spam
    if Spam
        SetTimer(SpamLoop, -1)
}
*MButton:: {
    if Spam
        return
    if GetKeyState("a", "P")
        RunSeq(MSpecialA, false)
    else if GetKeyState("d", "P")
        RunSeq(MSpecialD, false)
}

; Physical L/R clicks are swallowed here (no ~), then forwarded unless they form a Shift+L+R chord
*LButton:: ClickDown("L")
*RButton:: ClickDown("R")
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
    global Rec := false, Spam := false, Armed := false
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

RunSeq(steps, abortable) {
    for step in steps {
        if abortable && !Spam
            return
        Do(step[1])
        if step[2]
            Sleep step[2]
    }
}

Do(a) {
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
        case "DiagRight":
            Look(StrafeDX, StrafeDY)
        case "DiagLeft":
            Look(-StrafeDX, StrafeDY)
        case "UpBack":
            Look(0, -StrafeDY)
        default:
            Tap(a)
    }
}

Look(dx, dy) => DllCall("mouse_event", "UInt", 1, "Int", dx, "Int", dy, "UInt", 0, "UPtr", 0)

Tap(k) {
    global Held := k
    Send "{" k " down}"
    Sleep HoldMs
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
