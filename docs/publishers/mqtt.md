# MQTT Publisher

MQTT Broker로 데이터를 발행하는 방법을 안내합니다.

---

## 개요

**MQTT Publisher**는 수집된 데이터를 MQTT Broker로 실시간 발행합니다.
IoT 플랫폼 연동 및 실시간 모니터링에 적합합니다.

!!! warning "베타 버전"
    MQTT Publisher는 현재 베타 버전입니다.
    프로덕션 환경에서는 충분한 테스트 후 사용하세요.

### 특징

- JSON 형식 메시지
- QoS 레벨 지원 (0, 1, 2)
- 태그별 개별 토픽
- TLS 지원 (예정)

---

## 설정

### 기본 설정

```yaml title="config/collector.yaml"
publisher:
  mqtt:
    enabled: true
    host: "localhost"
    port: 1883
    topic_prefix: "factory/plc"
```

### 전체 옵션

```yaml
publisher:
  mqtt:
    enabled: true                  # 활성화 여부
    host: "localhost"              # 브로커 호스트
    port: 1883                     # 브로커 포트
    username: "collector"          # 사용자 (선택)
    password: "${MQTT_PASSWORD}"   # 비밀번호 (선택)
    client_id: "collector_1"       # 클라이언트 ID
    topic_prefix: "factory/plc"    # 토픽 접두사
    qos: 1                         # QoS 레벨 (0, 1, 2)
    retain: false                  # 메시지 보존
    keepalive: 60                  # 연결 유지 (초)
```

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | Publisher 활성화 |
| `host` | string | localhost | 브로커 호스트 |
| `port` | int | 1883 | 브로커 포트 |
| `username` | string | - | 인증 사용자 |
| `password` | string | - | 인증 비밀번호 |
| `client_id` | string | auto | 클라이언트 ID |
| `topic_prefix` | string | - | 토픽 접두사 |
| `qos` | int | 1 | QoS 레벨 |
| `retain` | bool | false | 메시지 보존 |

---

## 토픽 구조

### 기본 구조

```
{topic_prefix}/{plc_id}/{tag_id}
```

**예시:**

```
factory/plc/1/1      # PLC 1, Tag 1
factory/plc/1/2      # PLC 1, Tag 2
factory/plc/2/101    # PLC 2, Tag 101
```

### 와일드카드 구독

```bash
# 모든 PLC의 모든 태그
factory/plc/#

# PLC 1의 모든 태그
factory/plc/1/#

# 모든 PLC의 Tag 1
factory/plc/+/1
```

---

## 메시지 형식

### JSON 구조

```json
{
  "time": "2026-02-05T10:00:01.123Z",
  "plc_id": 1,
  "tag_id": 1,
  "tag_name": "Temperature",
  "value": 25.5,
  "unit": "°C",
  "quality_code": 1
}
```

### 필드 설명

| 필드 | 타입 | 설명 |
|------|------|------|
| `time` | string | ISO 8601 타임스탬프 |
| `plc_id` | int | PLC 식별자 |
| `tag_id` | int | 태그 식별자 |
| `tag_name` | string | 태그 이름 |
| `value` | any | 값 (타입에 따라 다름) |
| `unit` | string | 단위 (설정된 경우) |
| `quality_code` | int | 품질 코드 |

### 배치 메시지 (선택)

여러 태그를 하나의 메시지로 발행:

```json
{
  "time": "2026-02-05T10:00:01.123Z",
  "plc_id": 1,
  "tags": [
    {"tag_id": 1, "value": 25.5, "quality_code": 1},
    {"tag_id": 2, "value": 1.23, "quality_code": 1},
    {"tag_id": 3, "value": true, "quality_code": 1}
  ]
}
```

---

## QoS 레벨

### QoS 0 (At most once)

- 최대 한 번 전송
- 확인 응답 없음
- 메시지 손실 가능
- 가장 빠름

### QoS 1 (At least once) - 권장

- 최소 한 번 전송
- 확인 응답 필요
- 중복 가능
- 균형잡힌 선택

### QoS 2 (Exactly once)

- 정확히 한 번 전송
- 4단계 핸드셰이크
- 가장 느림
- 중요 데이터용

```yaml
publisher:
  mqtt:
    qos: 1    # 권장
```

---

## MQTT Broker 설정

### Mosquitto (Docker)

```bash
docker run -d \
  --name mosquitto \
  -p 1883:1883 \
  -v mosquitto-config:/mosquitto/config \
  -v mosquitto-data:/mosquitto/data \
  eclipse-mosquitto:2
```

### 설정 파일

```conf title="mosquitto/config/mosquitto.conf"
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd

# 로깅
log_dest stdout
log_type all

# 지속성
persistence true
persistence_location /mosquitto/data/
```

### 사용자 추가

```bash
docker exec -it mosquitto \
  mosquitto_passwd -c /mosquitto/config/passwd collector
```

---

## 클라이언트 연동

### Python (paho-mqtt)

```python
import paho.mqtt.client as mqtt
import json

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    print(f"Tag {data['tag_id']}: {data['value']}")

client = mqtt.Client()
client.username_pw_set("user", "password")
client.on_message = on_message
client.connect("localhost", 1883)
client.subscribe("factory/plc/#")
client.loop_forever()
```

### Node.js

```javascript
const mqtt = require('mqtt');

const client = mqtt.connect('mqtt://localhost:1883', {
  username: 'user',
  password: 'password'
});

client.on('connect', () => {
  client.subscribe('factory/plc/#');
});

client.on('message', (topic, message) => {
  const data = JSON.parse(message.toString());
  console.log(`Tag ${data.tag_id}: ${data.value}`);
});
```

### MQTT Explorer

GUI 도구로 메시지 모니터링:

1. [MQTT Explorer](http://mqtt-explorer.com/) 다운로드
2. 연결 설정 (host, port, username, password)
3. 토픽 구독 및 메시지 확인

---

## 예제

### 실시간 모니터링 설정

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "Realtime_Monitor"

  protocol:
    type: modbus
    host: "192.168.1.100"
    port: 502

  collection:
    - group: realtime
      interval_ms: 500      # 0.5초 주기
      tags_file: "config/tags_realtime.csv"

publisher:
  database:
    enabled: false          # DB 비활성화

  mqtt:
    enabled: true
    host: "localhost"
    port: 1883
    topic_prefix: "monitor/line1"
    qos: 0                  # 실시간은 QoS 0
    retain: false
```

### Node-RED 연동

```json
[
  {
    "id": "mqtt-in",
    "type": "mqtt in",
    "topic": "factory/plc/1/+",
    "qos": "1",
    "broker": "mqtt-broker"
  },
  {
    "id": "function",
    "type": "function",
    "func": "msg.payload = JSON.parse(msg.payload);\nreturn msg;"
  },
  {
    "id": "dashboard",
    "type": "ui_gauge",
    "name": "Temperature"
  }
]
```

---

## 문제 해결

### 연결 실패

```
ERROR | MQTT connection failed: Connection refused
```

**확인:**

```bash
# 브로커 실행 확인
docker ps | grep mosquitto

# 연결 테스트
mosquitto_sub -h localhost -t test -v
mosquitto_pub -h localhost -t test -m "hello"
```

### 인증 오류

```
ERROR | MQTT connection failed: Not authorized
```

**확인:**

- username/password 설정
- 브로커의 인증 설정

### 메시지 손실

**확인:**

- QoS 레벨 증가 (0 → 1)
- 네트워크 상태
- 브로커 용량

### 지연 발생

**확인:**

- QoS 레벨 감소 (2 → 1 → 0)
- 배치 크기 조정
- 네트워크 대역폭

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
