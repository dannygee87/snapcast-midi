import time
import requests
import json
import mido
import subprocess
import vban_cmd

broadcast_process = None

BROADCAST_EXE = r"C:\Program Files\Snap.Net\Snap.Net.Broadcast.exe"

# --- KONFIGURATION ---
SNAPSERVER_IP = "192.168.100.150"
SNAPSERVER_PORT = 1780 

SEND_DELAY = 0.15 

# Multiroom Toggle Button (Pad 5 -> Note 39)
TOGGLE_NOTE = 39  

# 1. Lautstärke-Potis (CC 5 bis 8)
MIDI_TO_CLIENT = {
    5: "d8:43:ae:12:04:17#11", 
    6: "pi4",   
    7: "kueche", 
    8: "11:22:33:44:55:66" 
}

# 2. Mute-Pads (Dezimal-Notennummern: 35, 36, 37, 38)
NOTE_TO_MUTE = {
    35: "d8:43:ae:12:04:17#11", 
    36: "pi4",   
    37: "kueche", 
    38: "11:22:33:44:55:66" 
}

# VBAN-Verbindung zur lokalen VB-Matrix aufbauen
vban = vban_cmd.api(
    'matrix', 
    host='127.0.0.1', 
    port=6980, 
    streamname='Matrix'
)
vban.login()

# Flag für den aktuellen Status
multiroom_active = False

def set_multiroom_on():
    global multiroom_active, broadcast_process
    if multiroom_active:
        return
    
    multiroom_active = True
    print("--> MULTIROOM AKTIV (PC-Direct = -80dB, SnapClient = 0dB)")
    
    try:
        # 1. Matrix schalten
        vban.sendtext("Point(VAIO1.IN[1],ASIO128.OUT[1]).dBGain = -80.0;")
        vban.sendtext("Point(VAIO1.IN[2],ASIO128.OUT[2]).dBGain = -80.0;")
        vban.sendtext("Point(VAIO2.IN[1],ASIO128.OUT[1]).dBGain = 0.0;")
        vban.sendtext("Point(VAIO2.IN[2],ASIO128.OUT[2]).dBGain = 0.0;")
        
        # 2. Broadcast starten
        if broadcast_process is None:
            broadcast_process = subprocess.Popen(
                [
                    BROADCAST_EXE, 
                    "-s", "6", 
                    "-h", SNAPSERVER_IP, 
                    "-p", "4953"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
    except Exception as e:
        print(f"[FEHLER set_multiroom_on]: {e}")

def set_multiroom_off():
    global multiroom_active, broadcast_process
    if not multiroom_active:
        return
        
    multiroom_active = False
    print("--> LOKALER MODUS AKTIV (PC-Direct = -6dB, SnapClient = -80dB)")
    
    try:
        # 1. Matrix schalten
        vban.sendtext("Point(VAIO2.IN[1],ASIO128.OUT[1]).dBGain = -80.0;")
        vban.sendtext("Point(VAIO2.IN[2],ASIO128.OUT[2]).dBGain = -80.0;")
        vban.sendtext("Point(VAIO1.IN[1],ASIO128.OUT[1]).dBGain = -6.0;")
        vban.sendtext("Point(VAIO1.IN[2],ASIO128.OUT[2]).dBGain = -6.0;")
        
        # 2. Broadcast sofort beenden
        if broadcast_process is not None:
            try:
                broadcast_process.kill()
            except Exception:
                pass
            broadcast_process = None
        
        subprocess.Popen(["taskkill", "/f", "/im", "Snap.Net.Broadcast.exe"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[FEHLER set_multiroom_off]: {e}")

http_session = requests.Session()
pending_volumes = {}

def set_snapcast_volume(client_id, percent_volume):
    url = f"http://{SNAPSERVER_IP}:{SNAPSERVER_PORT}/jsonrpc"
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "Client.SetVolume",
        "params": {
            "id": client_id,
            "volume": {"percent": int(percent_volume)}
        }
    }
    try:
        http_session.post(url, json=payload, timeout=0.5)
        print(f"[GESENDET] Volume -> {client_id}: {percent_volume}%")
    except Exception as e:
        print(f"[NETZWERK-FEHLER Volume]: {e}")

def set_snapcast_mute(client_id, is_muted):
    url = f"http://{SNAPSERVER_IP}:{SNAPSERVER_PORT}/jsonrpc"
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "Client.SetVolume",
        "params": {
            "id": client_id,
            "volume": {"muted": bool(is_muted)}
        }
    }
    try:
        http_session.post(url, json=payload, timeout=0.5)
        print(f"[GESENDET] Mute -> {client_id}: {is_muted}")
    except Exception as e:
        print(f"[NETZWERK-FEHLER Mute]: {e}")

def main():
    input_names = mido.get_input_names()
    lpd8_name = next((name for name in input_names if "LPD8" in name), None)
    
    if not lpd8_name:
        print("AKAI LPD8 nicht gefunden! Verfügbare Geräte:", input_names)
        return

    print(f"Erfolgreich verbunden mit: {lpd8_name}")
    print("Drücke Strg + C zum Beenden.\n")

    inport = mido.open_input(lpd8_name)

    try:
        while True:
            now = time.time()
            
            for msg in inport.iter_pending():
                if msg is not None:
                    # A) Lautstärke-Poti wurde gedreht
                    if msg.type == 'control_change' and msg.control in MIDI_TO_CLIENT:
                        client_id = MIDI_TO_CLIENT[msg.control]
                        percent_vol = int((msg.value / 127.0) * 100)
                        pending_volumes[client_id] = (percent_vol, now)
                        print(f"-> Poti gedreht -> {client_id}: {percent_vol}%")

                    # B) Mute-Pads (35, 36, 37, 38)
                    elif hasattr(msg, 'note') and msg.note in NOTE_TO_MUTE:
                        client_id = NOTE_TO_MUTE[msg.note]
                        
                        # 1. Druck (Pad leuchtet) -> MUTE AUS (Sound an)
                        if msg.type == 'note_on' and msg.velocity > 0:
                            set_snapcast_mute(client_id, False)
                            
                        # 2. Druck (Pad dunkel) -> MUTE AN (Stumm)
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            set_snapcast_mute(client_id, True)
                            
                    # C) HARDWARE-TOGGLE FÜR MULTIROOM (Pad 5 / Note 39)
                    elif hasattr(msg, 'note') and msg.note == TOGGLE_NOTE:
                        # 1. Druck (Pad leuchtet auf) -> Multiroom EIN
                        if msg.type == 'note_on' and msg.velocity > 0:
                            set_multiroom_on()
                        # 2. Druck (Pad geht aus) -> Lokal (AUS)
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            set_multiroom_off()

            # --- Debounce-Puffer abarbeiten ---
            clients_to_remove = []
            for client_id, (vol, last_time) in pending_volumes.items():
                if now - last_time >= SEND_DELAY:
                    set_snapcast_volume(client_id, vol)
                    clients_to_remove.append(client_id)
            
            for client_id in clients_to_remove:
                del pending_volumes[client_id]

            time.sleep(0.005)

    except KeyboardInterrupt:
        print("\nSkript beendet.")

    finally:
        try:
            inport.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()