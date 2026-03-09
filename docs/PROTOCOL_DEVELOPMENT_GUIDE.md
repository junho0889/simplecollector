# Protocol Development Guide

simpleCollector에 새로운 PLC 프로토콜을 추가하기 위한 개발 가이드입니다.
MC Protocol, Modbus 구현을 기준으로 작성되었습니다.

---

## 1. 아키텍처 개요

```
[PLC] ← 프로토콜 → [Collector] → CollectedData → [Processor] → ProcessedData → [Buffer] → [Publisher] → [RabbitMQ]
                      ↑ BaseCollector 상속           ↑ BaseProcessor 상속
                      ↑ _do_connect/collect/disconnect  ↑ _parse_raw_data
```

### 데이터 흐름
1. **Collector**: PLC에 연결 → raw 데이터 수집 → `CollectedData.metadata`에 담아 전달
2. **Processor**: `metadata`에서 값 추출 → `(TagDefinition, value)` 튜플 리스트 반환
3. **BaseProcessor**: 자동 스케일링 적용 → `ProcessedData` 생성 → 버퍼 저장
4. **Publisher**: 버퍼에서 배치 추출 → RabbitMQ 발행 (이미 구현됨, 수정 불필요)

---

## 2. 디렉토리 구조

```
src/collectors/my_protocol/
├── __init__.py          # 클래스 등록 (Export)
├── collector.py         # MyProtocolCollector (BaseCollector 상속)
└── processor.py         # MyProtocolProcessor (BaseProcessor 상속)
```

---

## 3. 핵심 데이터 타입

### 3.1 TagDefinition (태그 정의)

`src/core/interfaces.py`에 정의. CSV에서 파싱됩니다.

```python
@dataclass
class TagDefinition:
    tag_id: int              # 고유 태그 ID
    tag_name: str            # 태그 이름
    address: str             # 프로토콜별 주소 (예: "D100", "C100", "HR100")
    data_type: DataType      # 데이터 타입 (UINT16, INT32, FLOAT32 등)
    scale: float = 1.0       # 스케일 (value × scale)
    offset: float = 0.0      # 오프셋 (value × scale + offset)
    decimals: Optional[int]  # 고정소수점 변환 (정수: ÷10^decimals, float: round)
    collection_group: str    # 수집 그룹 (plc_data, alm, log)
    memory: str              # 디바이스/메모리 영역 (D, M, I 등)
    string_length: Optional[int]  # STRING 길이
    word_length: Optional[int]    # 멀티 워드 수
    format: str              # 출력 포맷 오버라이드
```

### 3.2 DataType (지원 타입)

```python
class DataType(Enum):
    BOOL = "bool"
    INT8, INT16, INT32, INT64    # 부호 있는 정수
    UINT8, UINT16, UINT32, UINT64  # 부호 없는 정수
    FLOAT32, FLOAT64             # 부동소수점
    BYTE, WORD, DWORD, LWORD     # 고정 크기 정수
    STRING                       # 문자열
```

### 3.3 CollectedData (Collector → Processor)

```python
@dataclass
class CollectedData:
    source_time: datetime        # PLC 타임스탬프
    collection_time: datetime    # 수집 시각
    plc_id: int
    raw_data: bytes              # 프로토콜 raw 바이트 (비워도 됨)
    collection_group: str        # 수집 그룹명
    metadata: Dict[str, Any]     # ★ 프로토콜 데이터를 여기에 담는다
```

**metadata 패턴 예시:**
```python
# MC Protocol
metadata = {'devices': {'D': {0: 100, 100: 200}, 'M': {10: 1}}}

# Modbus
metadata = {'registers': {'holding': {0: 100}, 'coil': {0: 1}}}

# 새 프로토콜
metadata = {'your_key': {address: value, ...}}
```

### 3.4 ProcessedData (Processor → Publisher)

```python
@dataclass
class ProcessedData:
    source_time: datetime
    server_time: datetime
    plc_id: int
    tag_id: int
    data_type: DataType
    v_bool: Optional[bool]       # bool 값
    v_int: Optional[int]         # INT16/UINT16 범위
    v_bigint: Optional[int]      # INT64/UINT64 범위
    v_float: Optional[float]     # 스케일링된 실수
    v_text: Optional[str]        # STRING
    quality_code: int = 1        # 1=정상, 0=수집실패
    collection_group: str
```

> BaseProcessor가 자동 생성하므로 직접 만들 필요 없음.

---

## 4. Collector 구현

### 4.1 반드시 구현할 메서드

```python
from ..base import BaseCollector
from ...core.interfaces import CollectedData, ConnectionState

class MyProtocolCollector(BaseCollector):

    async def _do_connect(self) -> bool:
        """PLC에 연결. 성공 시 True 반환."""
        pass

    async def _do_disconnect(self) -> None:
        """연결 해제. 리소스 정리."""
        pass

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """그룹별 데이터 수집. 실패 시 None 반환."""
        pass

    async def _do_health_check(self) -> bool:
        """(선택) 연결 상태 확인. 기본: self._state == CONNECTED"""
        pass
```

### 4.2 생성자 패턴

```python
def __init__(self, plc_id, name, config, event_bus=None):
    super().__init__(plc_id, name, config, event_bus)

    # 프로토콜 설정 추출
    if self._protocol_config:
        self._host = self._protocol_config.host
        self._port = self._protocol_config.port
        self._timeout = self._protocol_config.timeout_ms / 1000.0

        # 프로토콜 고유 설정 (YAML의 extra 섹션)
        extra = self._protocol_config.extra or {}
        self._my_param = extra.get('my_param', 'default')

    # TCP 연결 핸들
    self._reader: Optional[asyncio.StreamReader] = None
    self._writer: Optional[asyncio.StreamWriter] = None
```

### 4.3 _do_connect() 구현 예시

```python
async def _do_connect(self) -> bool:
    try:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=self._timeout
        )
        logger.info(f"[{self._name}] Connected to {self._host}:{self._port}")
        return True

    except asyncio.TimeoutError:
        logger.error(f"[{self._name}] Connection timeout")
        return False
    except Exception as e:
        logger.error(f"[{self._name}] Connection error: {e}")
        return False
```

> 재연결은 BaseCollector가 자동으로 처리합니다 (지수 백오프 1→2→4→5초).

### 4.4 _do_collect() 구현 예시

```python
async def _do_collect(self, group: str) -> Optional[CollectedData]:
    try:
        # 1. 해당 그룹의 태그 목록 가져오기
        tags = self._tags_by_group.get(group, [])
        if not tags:
            return None

        # 2. 읽기 그룹 최적화 (연속 주소 묶기)
        read_groups = self._optimize_read_groups(tags)

        # 3. PLC에서 데이터 읽기
        all_data = {}
        for rg in read_groups:
            response = await self._read_from_plc(rg)
            all_data.update(self._parse_response(response, rg))

        # 4. CollectedData 반환 (metadata에 프로토콜 데이터 담기)
        return CollectedData(
            source_time=datetime.now(),
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=b'',           # raw 바이트 (선택)
            collection_group=group,
            metadata={'registers': all_data}  # ★ 핵심
        )

    except Exception as e:
        logger.error(f"[{self._name}] Collection error: {e}")
        return None  # BaseCollector가 quality_code=0 데이터 생성
```

### 4.5 읽기 그룹 최적화 패턴

MC Protocol, Modbus 모두 동일한 패턴을 사용합니다:

```python
def _optimize_read_groups(self, tags):
    """연속된 주소를 묶어 요청 수 최소화."""

    # 1. 태그를 메모리 영역별로 분류
    # 2. 같은 영역 내 주소 정렬
    # 3. max_gap 이내면 하나의 그룹으로 병합
    # 4. max_per_read 초과 시 분할

    # MC Protocol: max_read_points = 960 (3E프레임)
    # Modbus:      max_registers = 125 (FC03/04), max_coils = 2000 (FC01/02)
```

---

## 5. Processor 구현

### 5.1 반드시 구현할 메서드

```python
from ..base import BaseProcessor
from ...core.interfaces import TagDefinition, CollectedData

class MyProtocolProcessor(BaseProcessor):

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        metadata에서 값 추출 → (TagDefinition, 파싱된 값) 리스트 반환.

        반환값: [(tag, value), (tag, value), ...]
        value: int, float, bool, str, 또는 None
        """
        pass
```

### 5.2 구현 예시

```python
async def _parse_raw_data(self, data, tags):
    registers = data.metadata.get('registers', {})
    results = []

    for tag in tags:
        try:
            # 1. 주소 파싱 (프로토콜별 구현)
            reg_type, address = self._parse_address(tag.address)

            # 2. 해당 영역의 레지스터 맵 선택
            reg_map = registers.get(reg_type, {})

            # 3. 데이터 타입별 값 추출
            value = self._extract_value(tag.data_type, reg_map, address, tag)

            if value is not None:
                results.append((tag, value))

        except Exception as e:
            logger.warning(f"Tag {tag.tag_id} parse error: {e}")
            continue  # 실패한 태그 스킵

    return results
```

### 5.3 값 추출 패턴

```python
def _extract_value(self, data_type, registers, address, tag):
    """데이터 타입별 값 추출."""

    if data_type == DataType.BOOL:
        return bool(registers.get(address, 0))

    elif data_type in (DataType.UINT16, DataType.WORD):
        return registers.get(address)

    elif data_type == DataType.INT16:
        raw = registers.get(address)
        if raw is None:
            return None
        return struct.unpack('<h', struct.pack('<H', raw))[0]

    elif data_type == DataType.INT32:
        return self._extract_int32(registers, address)

    elif data_type == DataType.FLOAT32:
        return self._extract_float32(registers, address)

    elif data_type == DataType.STRING:
        return self._extract_string(registers, address, tag)

    # ... 기타 타입


def _extract_int32(self, registers, address):
    """2 워드 → 32비트 정수."""
    low = registers.get(address)
    high = registers.get(address + 1)
    if low is None or high is None:
        return None
    raw = (high << 16) | low  # Little-endian word order
    return struct.unpack('<i', struct.pack('<I', raw))[0]


def _extract_float32(self, registers, address):
    """2 워드 → 32비트 실수."""
    low = registers.get(address)
    high = registers.get(address + 1)
    if low is None or high is None:
        return None
    raw = struct.pack('<HH', low, high)
    return struct.unpack('<f', raw)[0]


def _extract_string(self, registers, address, tag):
    """N 워드 → ASCII 문자열."""
    word_count = tag.word_length or 1
    chars = []
    for i in range(word_count):
        val = registers.get(address + i)
        if val is None:
            break
        high_byte = (val >> 8) & 0xFF
        low_byte = val & 0xFF
        if high_byte:
            chars.append(chr(high_byte))
        if low_byte:
            chars.append(chr(low_byte))
    return ''.join(chars).rstrip('\x00')
```

> **중요**: `decimals` 스케일링은 BaseProcessor가 자동 처리합니다. Processor에서는 raw 값만 반환하세요.

---

## 6. 등록 (Registration)

### 6.1 `__init__.py` 작성

```python
# src/collectors/my_protocol/__init__.py
from .collector import MyProtocolCollector
from .processor import MyProtocolProcessor

__all__ = [
    "MyProtocolCollector",
    "MyProtocolProcessor",
]
```

### 6.2 ProtocolRegistry 등록

`src/core/registry.py`의 `PROTOCOLS` dict에 추가:

```python
PROTOCOLS: Dict[str, Tuple[str, List[str]]] = {
    'mc_protocol': ('src.collectors.mc_protocol', []),
    'modbus': ('src.collectors.modbus', []),
    'my_protocol': ('src.collectors.my_protocol', ['optional_dep']),  # ← 추가
}
```

- 첫 번째 값: 모듈 경로
- 두 번째 값: 필수 외부 패키지 리스트 (없으면 `[]`)

### 6.3 자동 검색 규칙

ProtocolRegistry는 introspection으로 클래스를 찾습니다:
- **Collector**: 클래스명이 `*Collector`로 끝나야 함 (예: `MyProtocolCollector`)
- **Processor**: 클래스명이 `*Processor`로 끝나야 함 (예: `MyProtocolProcessor`)

---

## 7. YAML 설정

```yaml
collector:
  plc_id: 1
  name: "PLC1_MyProtocol"
  tags_file: "config/tags_my_protocol.csv"

  protocol:
    type: "my_protocol"         # ← registry 키와 일치
    host: "192.168.1.100"
    port: 9000
    unit_id: 1
    timeout_ms: 5000
    reconnect_interval_ms: 3000

    extra:                       # ← 프로토콜 고유 설정
      my_param: "value"
      another_param: 100

  collection_groups:
    - name: "plc_data"
      interval_ms: 1000
      timeout_ms: 5000
      retry_count: 2
      retry_delay_ms: 500

    - name: "alm"
      interval_ms: 1000
      mode: "on_change"
      deadband: 0
```

---

## 8. CSV 태그 파일

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,unit,string_length,word_length,format,description
1,Tag001,HR,0,uint16,plc_data,1,0,,,,,,생산수량
2,Tag002,HR,10,int32,plc_data,1,0,2,,,,, 온도 (decimals=2 → ÷100)
3,Tag003,HR,20,float32,plc_data,1,0,1,,,,, 압력 (decimals=1 → round)
4,Tag004,C,0,bool,alm,1,0,,,,,, 알람비트
5,Tag005,HR,100,string,log,1,0,,,10,5,, 설비상태문자열
```

| 필드 | 설명 |
|------|------|
| `memory` | 메모리 영역 (D, M, HR, C 등 — 프로토콜별 정의) |
| `address` | 프로토콜별 주소 (숫자 또는 "D100" 형태) |
| `data_type` | DataType enum 값 (소문자) |
| `collection_group` | 수집 그룹 이름 |
| `scale` / `offset` | `(raw × scale) + offset` |
| `decimals` | 정수: ÷10^decimals, float: round |
| `word_length` | STRING, 64비트 등 멀티워드 읽기 시 워드 수 |

---

## 9. decimals 스케일링 규칙 (중요)

PLC/HMI 업계 표준 고정소수점 변환입니다. **BaseProcessor가 자동 처리합니다.**

```python
# 정수 타입 (UINT16, INT32 등): 나누기 변환
#   decimals=2, raw=3061 → 3061 / 100 = 30.61
#   decimals=1, raw=255  → 255 / 10 = 25.5

# float 타입 (FLOAT32, FLOAT64): 단순 반올림
#   decimals=1, raw=69.123 → round(69.123, 1) = 69.1
#   decimals=2, raw=3.1415 → round(3.1415, 2) = 3.14
```

> Processor에서는 raw 값만 반환하면 됩니다. 스케일링을 직접 적용하지 마세요.

---

## 10. 에러 처리

### BaseCollector가 제공하는 자동 처리
- **연결 실패**: 지수 백오프 재연결 (1→2→4→5초, 무한 재시도)
- **수집 실패**: `_do_collect()`가 None 반환 시 quality_code=0 데이터 자동 생성
- **타임아웃**: BaseCollector가 그룹별 타임아웃 관리

### 프로토콜 구현에서 해야 할 것
```python
# _do_connect(): 연결 실패 시 False 반환 (예외를 던지지 말 것)
# _do_collect(): 수집 실패 시 None 반환
# _parse_raw_data(): 개별 태그 파싱 실패 시 skip하고 continue
```

---

## 11. 로깅

```python
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()

# 항상 [collector_name] 접두사 포함
logger.debug(f"[{self._name}] 상세 정보")
logger.verbose(f"[{self._name}] 트레이스")   # VERBOSE (커스텀 레벨)
logger.info(f"[{self._name}] 마일스톤")
logger.warning(f"[{self._name}] 복구 가능 문제")
logger.error(f"[{self._name}] 심각한 문제: {e}")
```

---

## 12. 성능 최적화 패턴

### 읽기 요청 최소화
```python
# Bad: 태그마다 개별 요청
for tag in tags:
    await read_single(tag.address)

# Good: 연속 주소 묶어서 배치 요청
groups = self._optimize_read_groups(tags)
for group in groups:
    await read_batch(group.start, group.count)
```

### 캐시 활용
```python
# 생성자에서 한 번만 계산
self._address_cache = {tag.tag_id: parse_address(tag) for tag in tags}

# 수집 시 캐시 사용
reg_type, addr = self._address_cache[tag.tag_id]
```

---

## 13. CollectorHub 메타데이터 (필수)

새 프로토콜을 추가하면 **반드시** CollectorHub용 메타데이터 JSON을 작성해야 합니다.
웹 UI에서 설정 폼을 동적으로 생성하는 데 사용됩니다.

### 13.1 파일 위치

```
collectorhub/local-images/{protocol_name}.json
```

기존 참고: `mc_protocol.json`, `modbus.json`, `publisher.json`

### 13.2 설계 원칙: 필수 vs 옵션

사용자가 최소한의 입력으로 컬렉터를 생성할 수 있도록 필드를 분류합니다.

**필수 (required: true)** — 사용자가 반드시 입력해야 하는 항목:
- PLC ID, 이름
- 프로토콜 연결 정보 (IP, 포트)
- 프로토콜 핵심 설정 (시리즈, 모드 등)
- 최소 1개 수집 그룹 (이름 + 주기)

**옵션 (required: false)** — 기본값이 있어 입력 안 해도 동작:
- 타임아웃, 재연결 간격, 재시도 횟수
- 네트워크/고급 설정 (collapsed: true로 숨김)
- 버퍼/퍼블리셔 세부 설정
- 로깅 레벨

**공통 섹션 (모든 프로토콜 동일)** — MC/Modbus에서 복사:
- buffer, publisher, rabbitmq, logging 그룹
- container 환경변수, 볼륨 설정

### 13.3 JSON 최상위 구조

```json
{
  "name": "neuro_collector-{protocol}",
  "display_name": "표시 이름",
  "description": "설명",
  "version": "0.x.x",
  "docker_image": "collector-{protocol}",
  "service_type": "collector",
  "protocol": "{protocol_name}",
  "icon": "radio-tower",

  "config_schema": { ... },     // ★ 폼 필드 정의
  "container": { ... },         // Docker 컨테이너 설정
  "depends_on": ["rabbitmq"],
  "build_args": { ... }
}
```

### 13.4 config_schema 구조

```json
{
  "config_schema": {
    "groups": [
      {
        "key": "그룹키",
        "label": "그룹 제목",
        "description": "설명",
        "icon": "lucide-icon-name",
        "order": 1,
        "collapsed": false,           // true면 접힌 상태 (고급 설정용)
        "type": "array",              // 반복 그룹 (collection_groups용)
        "visible_when": { ... },      // 조건부 표시
        "fields": [ ... ]
      }
    ]
  }
}
```

### 13.5 필드 정의 스키마

```json
{
  "key": "collector.protocol.extra.my_field",   // YAML 경로 (dot notation)
  "label": "라벨",
  "type": "number",                              // number, text, ip, select, toggle, password, hex
  "default": 100,
  "required": true,
  "description": "사용자에게 보여줄 설명",
  "placeholder": "100",
  "suffix": "ms",                                // 단위 표시 (선택)
  "hint": "추가 안내 텍스트",                      // 선택
  "validation": {                                 // 선택
    "min": 0, "max": 1000, "step": 10,
    "pattern": "^[a-z]+$",
    "max_length": 50
  },
  "visible_when": {                              // 조건부 표시 (선택)
    "field": "다른필드키",
    "value": "특정값"                              // 또는 "values": ["값1", "값2"]
  },
  "options": [                                   // select 타입 전용
    { "value": "opt1", "label": "옵션1", "description": "설명" }
  ]
}
```

### 13.6 필드 타입 가이드

| type | 용도 | 예시 |
|------|------|------|
| `number` | 정수/실수 입력 | 포트, 타임아웃, 주기 |
| `text` | 문자열 입력 | PLC 이름, 파일 경로 |
| `ip` | IP 주소 | PLC IP |
| `select` | 드롭다운 선택 | PLC 시리즈, 프레임 타입 |
| `toggle` | 불리언 스위치 | 암호화 활성화 |
| `password` | 비밀번호 | RabbitMQ 비밀번호 |
| `hex` | 16진수 입력 | unit_io, pc_no |

### 13.7 그룹 분류 템플릿

새 프로토콜 메타데이터 작성 시 아래 순서로 그룹을 구성합니다:

| order | key | 내용 | collapsed |
|-------|-----|------|-----------|
| 1 | `plc_info` | PLC ID, 이름, 태그 파일 | false |
| 2 | `protocol` | **프로토콜 고유** 연결 설정 (필수 + 일부 옵션) | false |
| 3 | `protocol_*` | **프로토콜 고유** 고급/네트워크 설정 | **true** |
| 4 | `collection_group` | 수집 그룹 (array, mode/deadband 포함) | false |
| 5 | `buffer` | 버퍼 설정 (공통) | **true** |
| 6 | `publisher` | 퍼블리셔 설정 (공통) | **true** |
| 7 | `rabbitmq` | RabbitMQ 연결 (공통) | false |
| 8 | `logging` | 로깅 (공통) | **true** |

> order 1, 2, 4, 7만 펼쳐짐 → 사용자는 **4개 섹션만 보고** 바로 생성 가능

### 13.8 collection_group 필수 필드

모든 프로토콜의 collection_group에 아래 필드를 포함해야 합니다:

```json
{
  "key": "mode",
  "type": "select",
  "default": "polling",
  "options": [
    { "value": "polling", "label": "Polling" },
    { "value": "on_change", "label": "On Change" }
  ]
},
{
  "key": "deadband",
  "type": "number",
  "default": 0,
  "visible_when": { "field": "mode", "value": "on_change" }
}
```

### 13.9 프로토콜 고유 필드 (key 네이밍)

- 공통 설정: `collector.protocol.host`, `collector.protocol.port` 등
- 프로토콜 고유: `collector.protocol.extra.{field_name}`
- extra 하위는 YAML의 `protocol.extra` 섹션에 매핑됨

```yaml
# JSON key: collector.protocol.extra.plc_series → YAML:
protocol:
  extra:
    plc_series: "iq-r"
```

---

## 14. 검증 체크리스트

### Collector/Processor
- [ ] `__init__.py`에서 Collector, Processor 클래스 export
- [ ] `registry.py`에 프로토콜 등록
- [ ] `python -m py_compile src/collectors/my_protocol/collector.py` 성공
- [ ] `python -m py_compile src/collectors/my_protocol/processor.py` 성공
- [ ] `_do_connect()` → True/False 반환 확인
- [ ] `_do_collect()` → CollectedData 또는 None 반환 확인
- [ ] `_parse_raw_data()` → `[(tag, value), ...]` 반환 확인
- [ ] 연결 실패 시 재연결 동작 확인 (BaseCollector 자동)
- [ ] 수집 실패 시 quality_code=0 데이터 생성 확인
- [ ] BOOL, UINT16, INT32, FLOAT32, STRING 타입 파싱 확인
- [ ] decimals 스케일링 결과 확인 (BaseProcessor 자동)
- [ ] on_change 모드 동작 확인 (alm 그룹)

### CollectorHub 메타데이터
- [ ] `collectorhub/local-images/{protocol}.json` 작성
- [ ] 필수 필드(required:true) 최소화 (IP, 포트, 프로토콜 핵심만)
- [ ] 고급 설정 그룹에 `collapsed: true` 적용
- [ ] collection_group에 mode/deadband 필드 포함
- [ ] 공통 섹션(buffer, publisher, rabbitmq, logging) mc_protocol.json에서 복사
- [ ] container 섹션(환경변수, 볼륨, 네트워크) 설정
- [ ] JSON syntax 검증 (`python -m json.tool < file.json`)

---

## 14. 참고 파일 위치

```
핵심 인터페이스:
├─ src/core/interfaces.py      # DataType, TagDefinition, CollectedData, ProcessedData
├─ src/core/config.py          # ProtocolConfig, CollectorConfig, ConfigLoader
├─ src/core/registry.py        # ProtocolRegistry (프로토콜 등록)
├─ src/collectors/base.py      # BaseCollector (상속 대상)
└─ src/processors/base.py      # BaseProcessor (상속 대상)

참고 구현 (MC Protocol):
├─ src/collectors/mc_protocol/collector.py   # 프레임 빌드, 디바이스 코드, 읽기 최적화
├─ src/collectors/mc_protocol/processor.py   # 디바이스별 값 추출, 비트/워드 파싱
└─ src/collectors/mc_protocol/__init__.py    # 등록 패턴

참고 구현 (Modbus):
├─ src/collectors/modbus/collector.py        # FC01-04, CRC-16, TCP/RTU 전송
├─ src/collectors/modbus/processor.py        # 레지스터 타입별 파싱
└─ src/collectors/modbus/__init__.py         # 등록 패턴

파이프라인:
└─ src/pipeline/manager.py     # Collector+Processor+Publisher 조합

CollectorHub 메타데이터:
├─ collectorhub/local-images/mc_protocol.json   # MC Protocol 메타데이터 (참고)
├─ collectorhub/local-images/modbus.json         # Modbus 메타데이터 (참고)
└─ collectorhub/local-images/publisher.json      # Publisher 메타데이터 (참고)
```
