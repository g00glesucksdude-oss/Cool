#Persistent
#NoEnv
SendMode Input

; Initialize variables
spamming := false

; Define the sequence with specific reload times
sequence := [["q", 200], ["e", 200], ["r", 500]]
currentIndex := 1

; Block Shift from being processed by other applications
*Shift::
    spamming := !spamming
    if (spamming) {
        SetTimer, SpamKeys, 10 ; Polling interval
    } else {
        SetTimer, SpamKeys, Off
    }
return

SpamKeys:
    Send, % sequence[currentIndex][1]
    Sleep, % sequence[currentIndex][2] ; Reload time
    currentIndex++
    if (currentIndex > sequence.MaxIndex()) {
        currentIndex := 1
    }
return

; Completely disable default Shift function
*Shift up::return

; Hotkey to exit the script when 9 is pressed
9::ExitApp
