import time
import requests
import json
import mido
import subprocess
import vban_cmd

BROADCAST_EXE = r"C:\Program Files\Snap.Net\Snap.Net.Broadcast.exe"

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

# Matrix Indizes aus deinem Grid:
# Out: UR242 (0, 1) | In 1: Windows Direct (8, 9) | In 2: SnapClient (16, 17)
OUT_L, OUT_R = 0, 1
IN1_L, IN1_R = 8, 9
IN2_L, IN2_R = 16, 17

def toggle_multiroom():
    global multiroom_active
    multiroom_active = not multiroom_active

    if multiroom_active:
        print("--> MULTIROOM AKTIV (PC-Direct = -80dB, SnapClient = 0dB)")
        
        # 1. Direkten PC-Sound auf -80 dB
        vban.sendtext(f"Point(VAIO1.IN[1],ASIO128.OUT[1]).dBGain = -80.0;")
        vban.sendtext(f"Point(VAIO1.IN[2],ASIO128.OUT[2]).dBGain = -80.0;")
        
        # 2. SnapClient-Signal auf 0 dB
        vban.sendtext(f"Point(VAIO2.IN[1],ASIO128.OUT[1]).dBGain = 0.0;")
        vban.sendtext(f"Point(VAIO2.IN[2],ASIO128.OUT[2]).dBGain = 0.0;")
        
        # 3. Snap.Net Broadcast per CLI im Hintergrund starten
        subprocess.Popen([
            BROADCAST_EXE, 
            "-s", "6", 
            "-h", SNAPSERVER_IP, 
            "-p", "4953"
        ])

    else:
        print("--> LOKALER MODUS AKTIV (PC-Direct = 0dB, SnapClient = -80dB)")
        
        # 1. SnapClient auf -80 dB
        vban.sendtext(f"Point(VAIO2.IN[1],ASIO128.OUT[1]).dBGain = -80.0;")
        vban.sendtext(f"Point(VAIO2.IN[2],ASIO128.OUT[2]).dBGain = -80.0;")
        
        # 2. PCSound-Signal auf 0 dB
        vban.sendtext(f"Point(VAIO1.IN[1],ASIO128.OUT[1]).dBGain = -6.0;")
        vban.sendtext(f"Point(VAIO1.IN[2],ASIO128.OUT[2]).dBGain = -6.0;")
        
        # 3. Broadcast-Prozess beenden
        subprocess.run(["taskkill", "/f", "/im", "Snap.Net.Broadcast.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- KONFIGURATION ---
SNAPSERVER_IP = "192.168.100.150"
SNAPSERVER_PORT = 1780 

SEND_DELAY = 0.15 

# Multiroom Toggle Button (z.B. Pad 5 -> Note 39)
TOGGLE_NOTE = 39  

# 1. Lautstärke-Potis (CC 5 bis 8)
MIDI_TO_CLIENT = {
    5: "biggi", 
    6: "pi4",   
    7: "kueche", 
    8: "11:22:33:44:55:66" 
}

# 2. Mute-Pads (Dezimal-Notennummern: 35, 36, 37, 38)
NOTE_TO_MUTE = {
    35: "biggi", 
    36: "pi4",   
    37: "kueche", 
    38: "11:22:33:44:55:66" 
}

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

    try:
        with mido.open_input(lpd8_name) as inport:
            while True:
                now = time.time()
                msg = inport.poll()
                
                if msg is not None:
                    # A) Lautstärke-Poti wurde gedreht
                    if msg.type == 'control_change' and msg.control in MIDI_TO_CLIENT:
                        client_id = MIDI_TO_CLIENT[msg.control]
                        percent_vol = int((msg.value / 127.0) * 100)
                        pending_volumes[client_id] = (percent_vol, now)
                        print(f"-> Poti gedreht -> {client_id}: {percent_vol}%")

                    # B) Mute-Pad wurde gedrückt
                    elif hasattr(msg, 'note') and msg.note in NOTE_TO_MUTE:
                        client_id = NOTE_TO_MUTE[msg.note]
                        if msg.type == 'note_on' and msg.velocity > 0:
                            set_snapcast_mute(client_id, True)
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            set_snapcast_mute(client_id, False)

                    # C) TOGGLE-PAD FÜR MULTIROOM (z. B. Note 39)
                    elif hasattr(msg, 'note') and msg.note == TOGGLE_NOTE:
                        if msg.type == 'note_on' and msg.velocity > 0:
                            toggle_multiroom()
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            toggle_multiroom()


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

if __name__ == "__main__":
    main()