# Collector Module Development Guide

This document defines the interfaces, rules, and data flow required to develop a new protocol collector module for simpleCollector.

## Architecture Overview

```
[Device] ←Protocol→ [Collector] → [Processor] → [Buffer] → [Publisher] → [RabbitMQ]
                         ↑              ↑                        ↑
                    _do_collect    _parse_raw_data          _do_publish
                   (you implement)  (you implement)       (already done)
```

The framework handles scheduling, retries, reconnection, buffering, and publishing. You only implement **device communication** and **raw data parsing**.

---

## File Structure

Create your module under `src/collectors/`:

```
src/collectors/my_protocol/
├── __init__.py          # Export collector and processor classes
├── collector.py         # BaseCollector subclass — device communication
└── processor.py         # BaseProcessor subclass — raw data parsing
```

### `__init__.py`

```python
from .collector import MyProtocolCollector
from .processor import MyProtocolProcessor
```

Class naming convention:
- Collector class name **must end with `Collector`** (e.g., `MyProtocolCollector`)
- Processor class name **must end with `Processor`** (e.g., `MyProtocolProcessor`)

The registry discovers classes by scanning for these suffixes.

---

## Collector Implementation

### Required: Subclass `BaseCollector`

```python
from src.collectors.base import BaseCollector
from src.core.interfaces import CollectedData, TagDefinition, ConnectionState
from src.core.config import CollectorConfig
from src.core.events import EventBus
from datetime import datetime
from typing import Optional

class MyProtocolCollector(BaseCollector):

    def __init__(self, plc_id: int, name: str, config: CollectorConfig,
                 event_bus: Optional[EventBus] = None):
        super().__init__(plc_id, name, config, event_bus)
        # Access protocol-specific settings
        extra = config.protocol.extra if config.protocol else {}
        self._my_param = extra.get('my_param', 'default_value')

    async def _do_connect(self) -> bool:
        """Establish connection to the device.

        Available config:
            self._protocol_config.host      # str
            self._protocol_config.port      # int
            self._protocol_config.timeout_ms  # int (milliseconds)

        Returns:
            True if connected successfully, False otherwise.
        """
        host = self._protocol_config.host
        port = self._protocol_config.port
        timeout = self._protocol_config.timeout_ms / 1000.0
        # ... open TCP/Serial connection ...
        return True

    async def _do_disconnect(self) -> None:
        """Close the connection. Called on stop or before reconnect."""
        # ... close connection ...
        pass

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """Read data from the device for the given collection group.

        Available data:
            self._tags[group]  # List[TagDefinition] — tags to read
            self._plc_id       # int

        Returns:
            CollectedData with parsed values in metadata, or None to skip.
        """
        tags = self._tags.get(group, [])
        if not tags:
            return None

        # ... read from device ...
        values = {}
        for tag in tags:
            raw_value = self._read_tag(tag)  # your implementation
            values[tag.tag_id] = raw_value

        return CollectedData(
            source_time=datetime.now(),
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=b"",
            collection_group=group,
            metadata={"values": values},  # {tag_id: raw_value}
        )
```

### What BaseCollector Handles Automatically

| Feature | Detail |
|---------|--------|
| Collection loop | Creates `asyncio.Task` per group, calls `_do_collect()` at `interval_ms` |
| Retry on failure | `retry_count` retries with `retry_delay_ms` per collection |
| Timeout | `asyncio.wait_for(timeout_ms)` wraps each `_do_collect()` call |
| Reconnection | Exponential backoff (1s→5s cap), calls `_do_disconnect()` then `_do_connect()` |
| Failure data | On collect failure, generates `quality_code=0` data for all tags |
| on_change mode | Injects mode/deadband info into metadata |

### Optional Override

```python
async def _do_health_check(self) -> bool:
    """Default: checks self._state == CONNECTED. Override for ping/heartbeat."""
    return self._state == ConnectionState.CONNECTED
```

---

## Processor Implementation

### Required: Subclass `BaseProcessor`

```python
from src.processors.base import BaseProcessor
from src.core.interfaces import CollectedData, TagDefinition
from typing import List, Tuple, Any

class MyProtocolProcessor(BaseProcessor):

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """Extract raw values from CollectedData for each tag.

        Args:
            data: CollectedData from collector (access data.metadata)
            tags: Tags for this collection group

        Returns:
            List of (tag, raw_value) tuples.
            raw_value=None means communication failure (quality_code=0).
        """
        values = data.metadata.get("values", {})
        return [(tag, values.get(tag.tag_id)) for tag in tags]
```

### What BaseProcessor Handles Automatically

| Feature | Detail |
|---------|--------|
| Scaling | `tag.apply_scaling(raw_value)` — applies `(raw * scale) + offset + decimals` |
| Type routing | Routes scaled value to correct DB column (`v_bool/v_int/v_bigint/v_float/v_text`) |
| on_change filter | Deadband-based change detection for `on_change` mode groups |
| Cache reset | Clears value cache on reconnect (prevents stale on_change data) |
| NaN/Inf check | Returns `quality_code=0` for NaN/Inf values |

### Alternative metadata Formats

Instead of `{"values": {tag_id: raw_value}}`, you can use protocol-specific formats if you write a custom processor:

```python
# MC Protocol style — organized by memory device
metadata = {
    "devices": {
        "D": {200: 3061, 201: 500},   # {address(int): word_value(int)}
        "M": {100: 0, 101: 1},
    }
}

# Modbus style — organized by register type
metadata = {
    "registers": {
        "holding":  {0: 100, 1: 200},  # {address(int): register_value}
        "input":    {0: 50},
        "coil":     {0: True},
        "discrete": {0: False},
    }
}
```

If you use `{"values": {tag_id: raw_value}}`, you can skip writing a custom processor and use `GenericProcessor` instead.

---

## Registration (2 files to modify)

### 1. `src/core/registry.py`

Add entry to `PROTOCOLS` dict:

```python
PROTOCOLS: Dict[str, Tuple[str, List[str]]] = {
    # ... existing entries ...
    'my_protocol': ('src.collectors.my_protocol', []),       # no dependencies
    'my_alias':    ('src.collectors.my_protocol', []),       # optional alias
    # With external dependency:
    'some_proto':  ('src.collectors.some_proto', ['some_package']),
}
```

The third element is a list of required pip packages. The registry checks they are importable before loading.

### 2. `src/main.py` — `ComponentFactory`

Add branches in `create_collector()` and `create_processor()`:

```python
# In ComponentFactory.create_collector():
elif protocol_type in ("my_protocol", "my_alias"):
    from src.collectors.my_protocol import MyProtocolCollector
    return MyProtocolCollector(plc_id, name, config, event_bus)

# In ComponentFactory.create_processor():
elif protocol_type in ("my_protocol", "my_alias"):
    from src.collectors.my_protocol import MyProtocolProcessor
    return MyProtocolProcessor(name)
```

---

## Configuration

### YAML Format

```yaml
collector:
  plc_id: 1                          # Unique PLC ID (1~100)
  name: "MyDevice"                   # Display name
  enabled: true
  tags_file: "config/tags.csv"       # Path to tags CSV

  protocol:
    type: "my_protocol"              # Must match registry key
    host: "192.168.0.100"
    port: 5000
    timeout_ms: 3000                 # Per-request timeout
    reconnect_interval_ms: 5000
    extra:                           # Protocol-specific params (free-form dict)
      my_param: "value"
      another_param: 42

  collection_groups:
    - name: "plc_data"               # Group name (matches tags CSV)
      interval_ms: 1000              # Collection interval
      mode: "polling"                # "polling" or "on_change"
    - name: "alm"
      interval_ms: 1000
      mode: "on_change"
      deadband: 0                    # 0 = any change triggers publish
      deadband_type: "absolute"      # "absolute" or "percent"
    - name: "log"
      interval_ms: 5000
      mode: "polling"

publisher:
  rabbitmq:
    enabled: true
    host: "localhost"
    port: 5672
    username: "admin"
    password: "admin"
    exchange_name: "plc.data"
    exchange_type: "topic"
    compression: "zlib"              # "zlib", "gzip", "none"
    encryption_enabled: false
  publish_interval_ms: 500
  max_retries: 3
  retry_delay_ms: 1000

buffer:
  max_size: 100000
  persist_path: ""                   # Empty = no persistence

logging:
  level: "INFO"
  file_path: "logs/collector.log"
```

### Tags CSV Format

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
1,Production_Count,D,200,uint16,plc_data,1,0,,,,,Production count
2,Temperature,D,300,float32,plc_data,1,0,1,,,℃,Device temperature
3,Total_Production,D,210,uint32,plc_data,1,0,,,,,Total production (32-bit)
4,Model_Name,D,900,string,plc_data,1,0,,10,,,Current model (10 words)
100,ALM_EMG_Stop,M,100,bool,alm,1,0,,,,,Emergency stop alarm
200,LOG_Pressure,D,500,float32,log,0.1,0,1,,,bar,Pressure sensor
```

**Column definitions:**

| Column | Required | Description |
|--------|----------|-------------|
| `tag_id` | Yes | Unique integer ID |
| `tag_name` | Yes | Tag name |
| `memory` | No | Memory area (D, M, X, Y, W, R, etc.) — protocol-specific |
| `address` | Yes | Address within memory area |
| `data_type` | Yes | See data types below |
| `collection_group` | Yes | Must match YAML group name |
| `scale` | No | Multiply raw value (default: 1.0) |
| `offset` | No | Add after scaling (default: 0.0) |
| `decimals` | No | Fixed-point conversion for integers: `÷ 10^decimals` |
| `word_length` | No | Word count for STRING type reads |
| `format` | No | Override output type (e.g., `float32`, `int32`) |
| `unit` | No | Engineering unit |
| `description` | No | Tag description |

**Supported `data_type` values:**

| CSV value | DataType | DB column | Notes |
|-----------|----------|-----------|-------|
| `bool`, `BIT` | BOOL | v_bool | |
| `uint16`, `UDEC` (16-bit) | UINT16 | v_int | |
| `uint32`, `UDEC` (32-bit) | UINT32 | v_bigint | Exceeds INT range |
| `int16`, `DEC` (16-bit) | INT16 | v_int | |
| `int32`, `DEC` (32-bit) | INT32 | v_int | |
| `int64` | INT64 | v_bigint | |
| `float32`, `FLOAT` | FLOAT32 | v_float | |
| `float64` | FLOAT64 | v_float | |
| `string`, `STRING` | STRING | v_text | Requires `word_length` |

**Tag address format:** `memory + address` columns are combined. The collector receives `tag.memory` and `tag.address` separately, plus `tag.tag_name` as the full address string.

For STRING tags, `word_length` specifies how many 16-bit words to read. The address becomes `{memory}{address}:{word_length}` (e.g., `D900:10` = 10 words = 20 bytes).

---

## Data Flow Detail

### 1. Collection (your code)

```
_do_collect(group="plc_data")
  → Read tags from device
  → Return CollectedData(metadata={"values": {tag_id: raw_value}})
```

### 2. Processing (framework)

```
BaseProcessor.process(collected_data)
  → _parse_raw_data(data, tags)          # Your code: extract raw values
  → For each (tag, raw_value):
      scaled = tag.apply_scaling(raw)    # (raw * scale) + offset ÷ 10^decimals
      output_type = determine_type(tag)  # → v_bool / v_int / v_bigint / v_float / v_text
      → ProcessedData(tag_id, v_float=scaled, quality_code=1)
  → If on_change mode: filter unchanged values
  → buffer.put_many(processed_list)
```

### 3. Publishing (framework)

```
RabbitMQPublisher._publish_loop()
  → buffer.get_batch()
  → serialize: List[ProcessedData] → JSON → zlib compress → bytes
  → publish to exchange "plc.data" with routing_key "plc.{plc_id}.data"
  → AMQP headers: {compression, plc_id, batch_count}
```

### RabbitMQ Message Format

```
Exchange: plc.data (topic)
Routing key: plc.{plc_id}.data
Headers:
  compression: "zlib"
  encrypted: "false"
  plc_id: 1
  batch_count: 262

Body: zlib(json([
  {
    "source_time": "2026-03-09T10:00:01.123456",
    "server_time": "2026-03-09T10:00:01.234567",
    "plc_id": 1,
    "tag_id": 1,
    "data_type": "uint16",
    "v_int": 3061,
    "quality_code": 1,
    "collection_group": "plc_data"
  },
  ...
]))
```

---

## Scaling Rules

```python
scaled = (raw_value * scale) + offset

# Decimals (integer types only — PLC/HMI industry standard):
if data_type in (UINT16, INT16, UINT32, INT32, ...):
    scaled = round(scaled / (10 ** decimals), decimals)
    # Example: uint16, decimals=2, raw=3061 → (3061*1+0)/100 = 30.61

# Decimals (float types):
if data_type in (FLOAT32, FLOAT64):
    scaled = round(scaled, decimals)
    # Example: float32, decimals=1, raw=69.123 → 69.1
```

**Output type override:** If `scale != 1.0` or `decimals` is set, the output type becomes `v_float` regardless of the original data type. The `format` column can override this.

---

## Collection Modes

### `polling` (default)
- Reads all tags every `interval_ms`
- All values are published regardless of change

### `on_change`
- Reads all tags every `interval_ms`
- Only publishes values that **changed** since last collection
- `deadband=0`: any change triggers publish
- `deadband>0, absolute`: publish if `|new - old| > deadband`
- `deadband>0, percent`: publish if `|new - old| / |old| * 100 > deadband`
- First collection always publishes all values (no cache yet)
- Reconnection clears cache → next collection publishes all

---

## Error Handling Patterns

| Scenario | Framework behavior |
|----------|-------------------|
| `_do_connect()` returns False | Retries with exponential backoff (1s→5s) |
| `_do_collect()` raises exception | Retries `retry_count` times, then generates `quality_code=0` data |
| `_do_collect()` timeout | Same as exception |
| `_do_collect()` returns None | Skips this cycle (no data published) |
| `raw_value` is None | `quality_code=0`, all `v_*` columns are None |
| `raw_value` is NaN/Inf | `quality_code=0`, treated as invalid |
| Connection lost mid-operation | `_reconnect_loop` triggers, value cache cleared |
| Publisher fails | Data returned to buffer front (`put_front`), no data loss |

### Quality Codes

| Code | Meaning |
|------|---------|
| 0 | BAD — communication failure |
| 1 | GOOD — normal (default) |
| 2 | UNCERTAIN |
| 3 | TIMEOUT |
| 4 | ERROR |

---

## Downstream: collector-publisher

After RabbitMQ, the `collector-publisher` service consumes messages and writes to TimescaleDB:

```
RabbitMQ
├── queue.db      → COPY batch insert → {group}_integrated (time-series)
├── queue.monitor → UPSERT            → {group}_latest (current value)
└── queue.mqtt    → MQTT forward       → external MQTT broker
```

### DB Tables Created Per Group

| Table | Purpose |
|-------|---------|
| `{group}_master` | Tag metadata (from CSV) |
| `{group}_latest` | Latest value per tag (UPSERT) |
| `{group}_integrated` | Full time-series (only for mode=all) |
| `{group}_history` | Value change history (extension) |
| `{group}_snapshot` | Signal capture on rising edge (extension) |

---

## Running

```bash
# Local
python -m src.main -c config/my_collector.yaml

# Docker (ARM64)
python build_deploy.py --collector
# Output: deploy/jem/neuro_collector_mc.tar
```

---

## Checklist for New Protocol

- [ ] Create `src/collectors/my_protocol/` directory
- [ ] Implement `MyProtocolCollector(BaseCollector)` with `_do_connect`, `_do_disconnect`, `_do_collect`
- [ ] Implement `MyProtocolProcessor(BaseProcessor)` with `_parse_raw_data`
- [ ] Export both in `__init__.py`
- [ ] Register in `src/core/registry.py` PROTOCOLS dict
- [ ] Add branch in `src/main.py` ComponentFactory
- [ ] Create YAML config with `protocol.type: "my_protocol"`
- [ ] Create tags CSV with appropriate memory/address/data_type
- [ ] Test: `python -m src.main -c config/my_config.yaml`
- [ ] Verify RabbitMQ messages arrive with correct format
