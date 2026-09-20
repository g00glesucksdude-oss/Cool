#Persistent
#NoEnv
SendMode Input

; Initialize toggle variables for U, C, and Z keys
toggleU := 0
toggleC := 0
toggleZ := 0

; Hotkey to press Q, E, and R when T is pressed
t::
if (toggleC) {
    Send, q
    Send, r
} else {
    Send, q
    Send, e
    Send, r
}
return

; Hotkey to press H 3 times or 1 time when C is pressed
c::
toggleC := !toggleC
if (toggleC) {
    Send, hhh
} else {
    Send, h
}
return

; Hotkey to press G and H or G 3 times and H 4 times when Z is pressed
z::
toggleZ := !toggleZ
if (toggleZ) {
    Send, g
    Send, h
} else {
    Send, ggg
    Send, hhh
}
return

; Hotkey to press G, H, and J when Y is pressed
y::
Send, g
Send, h
Send, j
return

; Hotkey to toggle holding Q, E, and R when U is pressed
u::
toggleU := !toggleU
if (toggleU) {
    Send, {q down}
    Send, {e down}
    Send, {r down}
} else {
    Send, {q up}
    Send, {e up}
    Send, {r up}
}
return

; Hotkey to exit the script when 9 is pressed
9::ExitApp
