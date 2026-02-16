import sys
import os
import time
from threading import Thread
from scapy.all import *

# Shared state logic
current_ch = 1
# Logic: Map Routers to Names, and Clients to Routers
routers = {}  # {BSSID: SSID}
clients = {}  # {Client_MAC: BSSID}

def hopper(iface):
    global current_ch
    while True:
        for ch in range(1, 14):
            current_ch = ch
            os.system(f"iwconfig {iface} channel {ch}")
            time.sleep(1)

def callback(pkt):
    global current_ch
    if pkt.haslayer(Dot11):
        dot11 = pkt.getlayer(Dot11)
        
        # LOGIC: Identify Routers (Beacons)
        if pkt.haslayer(Dot11Beacon):
            bssid = dot11.addr3
            ssid = pkt[Dot11Elt].info.decode(errors="ignore")
            if bssid not in routers:
                routers[bssid] = ssid if ssid else "Hidden SSID"

        # LOGIC: Identify Connections (Data frames)
        # Type 2 = Data frames, which represent an active connection
        elif dot11.type == 2:
            src = dot11.addr2
            dst = dot11.addr1
            bssid = dot11.addr3
            
            # Filter out broadcast/multicast and router-to-router traffic
            if bssid and src != bssid and dst != "ff:ff:ff:ff:ff:ff":
                if src not in clients:
                    clients[src] = bssid
                    router_name = routers.get(bssid, "Unknown Router")
                    print(f"[FOUND] {src} is connected to: {router_name} ({bssid}) on CH {current_ch}")

def run_scanner(iface, duration):
    # Setup hardware (reuse your existing monitor mode logic here)
    t = Thread(target=hopper, args=(iface,), daemon=True)
    t.start()
    
    print(f"[*] Mapping network for {duration}s...")
    sniff(iface=iface, prn=callback, timeout=duration, store=0)
    
    # Final Summary Logic
    print("\n" + "="*50)
    print("FINAL NETWORK MAP")
    print("="*50)
    for client, bssid in clients.items():
        name = routers.get(bssid, "Unknown")
        print(f"DEVICE: {client} ---> ROUTER: {name} [{bssid}]")
    print("="*50)

if __name__ == "__main__":
    # Same sys.argv logic as before
    run_scanner(sys.argv[1], int(sys.argv[2]))

