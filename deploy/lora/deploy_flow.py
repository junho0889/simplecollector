"""Deploy LoRa dashboard flow to Node-RED."""
import json
import urllib.request

NODERED_URL = "http://localhost:1880"

# 1. Get existing flows
req = urllib.request.Request(f"{NODERED_URL}/flows")
with urllib.request.urlopen(req) as resp:
    flows = json.loads(resp.read())

# 2. Remove old LoRa tab + nodes + config
lora_tabs = [f for f in flows if f.get("type") == "tab" and "LoRa" in f.get("label", "")]
if lora_tabs:
    tab_id = lora_tabs[0]["id"]
    lora_ids = set(f["id"] for f in flows if f.get("z") == tab_id)
    lora_ids.add(tab_id)

    # Config nodes
    config_ids = set()
    for n in flows:
        if n.get("z") == tab_id:
            for key in ("broker", "group"):
                if n.get(key):
                    config_ids.add(n[key])
    for n in flows:
        if n["id"] in config_ids and n.get("tab"):
            config_ids.add(n["tab"])

    # Don't remove shared broker
    for n in flows:
        if n.get("z") == tab_id and n.get("broker"):
            bid = n["broker"]
            other = [f for f in flows if f.get("broker") == bid and f.get("z") != tab_id]
            if other:
                config_ids.discard(bid)

    remove_ids = lora_ids | config_ids
    flows = [f for f in flows if f["id"] not in remove_ids]
    print(f"Removed {len(remove_ids)} old LoRa nodes")

# 3. Find existing RPi MQTT broker
rpi_broker = [f for f in flows if f.get("type") == "mqtt-broker" and f.get("broker") == "192.168.1.2"]
broker_id = rpi_broker[0]["id"] if rpi_broker else "mqtt_rpi_lora"

# 4. Build new flow
SPLIT_FUNC = r'''const TAGS = {
    1:['temperature','\u2103','v_float'], 2:['humidity','%','v_float'],
    3:['pressure','hPa','v_float'], 4:['battery','%','v_int'],
    5:['version','','v_int'], 6:['mode','','v_int'],
    7:['accel_x','g','v_float'], 8:['accel_y','g','v_float'],
    9:['accel_z','g','v_float'], 10:['velocity','mm/s','v_float'],
    11:['accel_1k5k','g','v_float'], 12:['vib_peak','','v_int'],
    13:['harm_low','','v_int'], 14:['harm_high','','v_int'],
    15:['gravity','','v_int'], 16:['sound_db','dB','v_int'],
    17:['sound_peak','','v_int'], 18:['prob_temp','\u2103','v_float'],
    19:['rssi','dBm','v_int'], 20:['snr','dB','v_float']
};
const data = msg.payload.data;
if (!data) return null;
let v = {};
for (const item of data) {
    const t = TAGS[item.tag_id];
    if (t) v[t[0]] = item[t[2]];
}
return [
    v.temperature !== undefined ? {payload: v.temperature} : null,
    v.humidity !== undefined ? {payload: v.humidity} : null,
    v.pressure !== undefined ? {payload: v.pressure} : null,
    v.accel_x !== undefined ? {payload: v.accel_x} : null,
    v.velocity !== undefined ? {payload: v.velocity} : null,
    v.sound_db !== undefined ? {payload: v.sound_db} : null,
    v.rssi !== undefined ? {payload: v.rssi} : null,
    v.snr !== undefined ? {payload: v.snr} : null,
    v.battery !== undefined ? {payload: v.battery} : null,
    {payload: v}
];'''

new_nodes = [
    # Tab
    {"id": "lora_tab", "type": "tab", "label": "LoRa Sensor Monitor", "disabled": False},

    # Dashboard config
    {"id": "ui_tab_lora", "type": "ui_tab", "name": "LoRa Monitor", "icon": "dashboard", "order": 1},
    {"id": "grp_env", "type": "ui_group", "name": "\ud658\uacbd \uc13c\uc11c", "tab": "ui_tab_lora", "order": 1, "disp": True, "width": "6"},
    {"id": "grp_vib", "type": "ui_group", "name": "\uc9c4\ub3d9 \uc13c\uc11c", "tab": "ui_tab_lora", "order": 2, "disp": True, "width": "6"},
    {"id": "grp_rf",  "type": "ui_group", "name": "RF \uc2e0\ud638",  "tab": "ui_tab_lora", "order": 3, "disp": True, "width": "6"},
    {"id": "grp_snd", "type": "ui_group", "name": "\uc18c\uc74c/\uae30\ud0c0", "tab": "ui_tab_lora", "order": 4, "disp": True, "width": "6"},

    # MQTT In
    {"id": "n_mqtt", "type": "mqtt in", "z": "lora_tab", "name": "MQTT plc/data",
     "topic": "plc/data", "qos": "0", "datatype": "json", "broker": broker_id,
     "x": 130, "y": 200, "wires": [["n_split"]]},

    # Splitter
    {"id": "n_split", "type": "function", "z": "lora_tab", "name": "\ud0dc\uadf8\ubcc4 \ubd84\ub9ac",
     "func": SPLIT_FUNC,
     "outputs": 10, "x": 340, "y": 200,
     "wires": [
         ["g_temp"], ["g_hum"], ["g_pres"],
         ["g_ax"], ["g_vel"],
         ["g_snd"],
         ["g_rssi"], ["g_snr"],
         ["g_bat"],
         ["d_all"]
     ],
     "outputLabels": ["\uc628\ub3c4","\uc2b5\ub3c4","\uae30\uc555","\uac00\uc18d\ub3c4X","\uc18d\ub3c4","\uc18c\uc74c","RSSI","SNR","\ubc30\ud130\ub9ac","\uc804\uccb4"]
    },

    # Gauges
    {"id":"g_temp","type":"ui_gauge","z":"lora_tab","name":"\uc628\ub3c4","group":"grp_env",
     "order":1,"width":"3","height":"3","gtype":"gage","title":"\uc628\ub3c4","label":"\u2103",
     "format":"{{value}}","min":"-20","max":"80",
     "colors":["#00b3d9","#00b500","#ca3838"],"seg1":"15","seg2":"35",
     "x":600,"y":80,"wires":[]},
    {"id":"g_hum","type":"ui_gauge","z":"lora_tab","name":"\uc2b5\ub3c4","group":"grp_env",
     "order":2,"width":"3","height":"3","gtype":"gage","title":"\uc2b5\ub3c4","label":"%",
     "format":"{{value}}","min":"0","max":"100",
     "colors":["#ca3838","#00b500","#00b3d9"],"seg1":"30","seg2":"70",
     "x":600,"y":120,"wires":[]},
    {"id":"g_pres","type":"ui_gauge","z":"lora_tab","name":"\uae30\uc555","group":"grp_env",
     "order":3,"width":"6","height":"2","gtype":"gage","title":"\uae30\uc555","label":"hPa",
     "format":"{{value}}","min":"900","max":"1100",
     "colors":["#00b3d9","#00b500","#ca3838"],"seg1":"980","seg2":"1040",
     "x":600,"y":160,"wires":[]},

    {"id":"g_ax","type":"ui_gauge","z":"lora_tab","name":"Accel X","group":"grp_vib",
     "order":1,"width":"3","height":"3","gtype":"gage","title":"Accel X","label":"g",
     "format":"{{value}}","min":"0","max":"30",
     "colors":["#00b500","#e6e600","#ca3838"],"seg1":"5","seg2":"15",
     "x":600,"y":220,"wires":[]},
    {"id":"g_vel","type":"ui_gauge","z":"lora_tab","name":"Velocity","group":"grp_vib",
     "order":2,"width":"3","height":"3","gtype":"gage","title":"Velocity","label":"mm/s",
     "format":"{{value}}","min":"0","max":"50",
     "colors":["#00b500","#e6e600","#ca3838"],"seg1":"4.5","seg2":"11.2",
     "x":600,"y":260,"wires":[]},

    {"id":"g_snd","type":"ui_gauge","z":"lora_tab","name":"\uc18c\uc74c","group":"grp_snd",
     "order":1,"width":"6","height":"3","gtype":"gage","title":"\uc18c\uc74c","label":"dB",
     "format":"{{value}}","min":"0","max":"120",
     "colors":["#00b500","#e6e600","#ca3838"],"seg1":"60","seg2":"85",
     "x":600,"y":300,"wires":[]},

    {"id":"g_rssi","type":"ui_gauge","z":"lora_tab","name":"RSSI","group":"grp_rf",
     "order":1,"width":"3","height":"3","gtype":"gage","title":"RSSI","label":"dBm",
     "format":"{{value}}","min":"-120","max":"0",
     "colors":["#ca3838","#e6e600","#00b500"],"seg1":"-100","seg2":"-60",
     "x":600,"y":340,"wires":[]},
    {"id":"g_snr","type":"ui_gauge","z":"lora_tab","name":"SNR","group":"grp_rf",
     "order":2,"width":"3","height":"3","gtype":"gage","title":"SNR","label":"dB",
     "format":"{{value}}","min":"-20","max":"15",
     "colors":["#ca3838","#e6e600","#00b500"],"seg1":"-5","seg2":"5",
     "x":600,"y":380,"wires":[]},

    {"id":"g_bat","type":"ui_gauge","z":"lora_tab","name":"\ubc30\ud130\ub9ac","group":"grp_snd",
     "order":2,"width":"6","height":"2","gtype":"gage","title":"\ubc30\ud130\ub9ac","label":"%",
     "format":"{{value}}","min":"0","max":"100",
     "colors":["#ca3838","#e6e600","#00b500"],"seg1":"20","seg2":"50",
     "x":600,"y":420,"wires":[]},

    # Debug
    {"id":"d_all","type":"debug","z":"lora_tab","name":"\uc804\uccb4 \ub370\uc774\ud130",
     "active":True,"tosidebar":True,"console":False,"tostatus":True,
     "complete":"payload","targetType":"msg",
     "statusVal":"payload.temperature","statusType":"msg",
     "x":600,"y":460,"wires":[]},

    # Comment
    {"id":"n_comment","type":"comment","z":"lora_tab","name":"LoRa Dashboard",
     "info":"MQTT: 192.168.1.2:1883 > plc/data\nDashboard: http://localhost:1880/ui\n9 gauges",
     "x":140,"y":60,"wires":[]},
]

# Add broker if needed
if not rpi_broker:
    new_nodes.append({
        "id": "mqtt_rpi_lora", "type": "mqtt-broker", "name": "RPi Mosquitto (LoRa)",
        "broker": "192.168.1.2", "port": "1883", "clientid": "nodered-lora",
        "autoConnect": True, "usetls": False, "protocolVersion": "4",
        "keepalive": "60", "cleansession": True
    })

all_flows = flows + new_nodes
print(f"Total: {len(all_flows)} nodes")

# 5. Deploy
data = json.dumps(all_flows).encode()
req = urllib.request.Request(
    f"{NODERED_URL}/flows",
    data=data,
    headers={"Content-Type": "application/json", "Node-RED-Deployment-Type": "full"},
    method="POST"
)
with urllib.request.urlopen(req) as resp:
    print(f"Deploy status: {resp.status}")

print("Done! Dashboard: http://localhost:1880/ui")
