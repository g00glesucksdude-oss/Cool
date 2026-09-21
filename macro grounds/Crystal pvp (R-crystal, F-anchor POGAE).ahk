#Requires AutoHotkey v2.0
#SingleInstance Force
#MaxThreadsPerHotkey 1

if !A_IsAdmin {
    try Run '*RunAs "' A_ScriptFullPath '"'
    ExitApp
}

SendMode "Event"
SetKeyDelay -1, -1
SetMouseDelay -1
ProcessSetPriority "High"

HoldMs  := 35
SwapMs  := 65
ClickMs := 55
LookMs  := 40      ; wait after a look move
LookPx  := 40      ; look distance in pixels, tune to your sensitivity

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

DllCall("winmm\timeBeginPeriod", "UInt", 1)
OnExit(Cleanup)

+Esc:: ExitApp                  ; Shift+Esc, works anytime

~LControl Up:: {
    global Enabled := !Enabled, Spam := false, Armed := false
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
#HotIf

; Your first physical right-click after F -> press 1 (script clicks don't trigger this)
#HotIf Enabled && Armed && WinActive(GameWin)
~RButton Up:: {
    global Armed := false
    Tap("1")
}
#HotIf

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
    DllCall("winmm\timeEndPeriod", "UInt", 1)
}
