"""Update Node-RED MQTT broker for LoRa to use SSH tunnel."""
import json
import urllib.request

NODERED_URL = "http://localhost:1880"

# Get flows
req = urllib.request.Request(f"{NODERED_URL}/flows")
with urllib.request.urlopen(req) as resp:
    flows = json.loads(resp.read())

# Find and update LoRa broker
updated = False
for f in flows:
    if f.get("id") == "mqtt_rpi_lora":
        old = f"{f.get('broker')}:{f.get('port')}"
        f["broker"] = "host.docker.internal"
        f["port"] = "1884"
        f["name"] = "RPi LoRa (via tunnel)"
        print(f"Updated broker: {old} -> host.docker.internal:1884")
        updated = True
        break

if not updated:
    print("ERROR: mqtt_rpi_lora broker not found")
    exit(1)

# Deploy
data = json.dumps(flows).encode()
req = urllib.request.Request(
    f"{NODERED_URL}/flows",
    data=data,
    headers={"Content-Type": "application/json", "Node-RED-Deployment-Type": "full"},
    method="POST"
)
with urllib.request.urlopen(req) as resp:
    print(f"Deploy: {resp.status}")
print("Done!")
