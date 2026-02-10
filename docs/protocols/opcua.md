# OPC UA Client

OPC UA(Open Platform Communications Unified Architecture) 프로토콜 가이드입니다.

---

## 개요

OPC UA는 산업 자동화를 위한 플랫폼 독립적인 표준 통신 프로토콜입니다.
IEC 62541 국제 표준으로 제정되어 있으며, 강력한 보안과 정보 모델링을 제공합니다.

### 주요 특징

| 특징 | 설명 |
|------|------|
| **표준** | IEC 62541 국제 표준 |
| **보안** | X.509 인증서, 암호화, 서명 |
| **확장성** | 다양한 정보 모델 지원 |
| **포트** | TCP 4840 (기본) |
| **전송** | Binary (UA TCP), XML (SOAP) |

### 지원 장비

- Siemens S7-1500 (내장 OPC UA 서버)
- Rockwell FactoryTalk Gateway
- KEPServerEX
- Ignition Gateway
- Prosys OPC UA Simulation Server
- 대부분의 최신 산업용 장비

---

## Quick Start Guide

### 1단계: 테스트 서버 실행 (Prosys Simulation Server)

개발/테스트를 위해 무료 OPC UA 시뮬레이션 서버를 사용합니다:

1. [Prosys OPC UA Simulation Server](https://prosysopc.com/products/opc-ua-simulation-server/) 다운로드
2. 설치 후 실행
3. 기본 설정으로 서버 시작 (포트: 53530)

### 2단계: 보안 없이 연결 테스트

```yaml title="config/collector_opcua_test.yaml"
collector:
  plc_id: 1
  name: "OPC_UA_Test"

  protocol:
    type: opcua
    host: "localhost"
    port: 53530
    timeout_ms: 5000

    extra:
      security_policy: "None"
      security_mode: "None"
      authentication: "Anonymous"

  collection_groups:
    - name: fast
      interval_ms: 1000
```

```csv title="config/tags_opcua_test.csv"
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset
1,Counter,OPC,ns=3;i=1001,int32,fast,1.0,0.0
2,Random,OPC,ns=3;i=1002,float64,fast,1.0,0.0
3,Sinusoid,OPC,ns=3;i=1003,float64,fast,1.0,0.0
```

### 3단계: 실행 및 결과 확인

```bash
python -m src.main --config config/collector_opcua_test.yaml
```

**예상 출력:**

```log
2026-02-06 10:00:00 [INFO] collector.system: Configuration loaded
2026-02-06 10:00:00 [INFO] collector.system: Protocol: opcua
2026-02-06 10:00:00 [INFO] collector.collection: [OPC_UA_Test] No security policy configured
2026-02-06 10:00:00 [INFO] collector.collection: [OPC_UA_Test] Using anonymous authentication
2026-02-06 10:00:01 [INFO] collector.collection: [OPC_UA_Test] Connected to OPC UA server: opc.tcp://localhost:53530
2026-02-06 10:00:01 [INFO] collector.collection: [OPC_UA_Test] Server status: Running
2026-02-06 10:00:02 [INFO] collector.collection: Collected 3 tags in 45ms
```

**데이터베이스 결과:**

```sql
SELECT * FROM modbus.plc_data WHERE plc_id = 1 ORDER BY time DESC LIMIT 5;
```

| time | plc_id | tag_id | v_int32 | v_float | quality_code |
|------|--------|--------|---------|---------|--------------|
| 2026-02-06 10:00:02 | 1 | 1 | 12345 | NULL | 192 |
| 2026-02-06 10:00:02 | 1 | 2 | NULL | 0.7234 | 192 |
| 2026-02-06 10:00:02 | 1 | 3 | NULL | 0.8660 | 192 |

### 4단계: 보안 연결 (인증서 사용)

```bash
# 인증서 생성 (아래 상세 가이드 참조)
python scripts/generate_opcua_cert.py --output certs/

# 보안 설정으로 실행
python -m src.main --config config/collector_opcua_secure.yaml
```

**보안 연결 예상 출력:**

```log
2026-02-06 10:00:00 [INFO] collector.collection: Security configured: policy=Basic256Sha256, mode=SignAndEncrypt
2026-02-06 10:00:00 [INFO] collector.collection: Using username authentication: admin
2026-02-06 10:00:01 [INFO] collector.collection: [OPC_UA_Secure] Connected to OPC UA server
2026-02-06 10:00:01 [INFO] collector.collection: [OPC_UA_Secure] Secure channel established
```

---

## 인증서 기초 (X.509 Study)

### X.509 인증서란?

X.509는 공개키 인증서의 국제 표준 형식입니다. OPC UA에서 보안 통신을 위해 사용됩니다.

```
┌─────────────────────────────────────────────────────────────┐
│                    X.509 인증서 구조                         │
├─────────────────────────────────────────────────────────────┤
│  Version              : v3                                   │
│  Serial Number        : 고유 일련번호                        │
│  Signature Algorithm  : sha256WithRSAEncryption              │
│  Issuer               : 발급자 정보 (CN, O, C)               │
│  Validity             : 유효 기간 (Not Before ~ Not After)   │
│  Subject              : 소유자 정보 (CN, O, C)               │
│  Public Key           : 공개키 (RSA 2048/4096)               │
│  Extensions           : 확장 필드                            │
│    - Subject Alt Name : URI, DNS, IP 등                      │
│    - Key Usage        : 디지털 서명, 키 암호화               │
│    - Basic Constraints: CA 여부                              │
│  Signature            : 발급자의 서명                        │
└─────────────────────────────────────────────────────────────┘
```

### 인증서 종류

| 종류 | 용도 | 발급 주체 | 신뢰성 |
|------|------|-----------|--------|
| **자체 서명 (Self-Signed)** | 개발/테스트 | 자기 자신 | 낮음 |
| **CA 서명 (CA-Signed)** | 운영 환경 | 인증 기관 | 높음 |
| **사설 CA (Private CA)** | 기업 내부 | 내부 CA | 중간 |

### OPC UA 인증서 요구사항

OPC UA 표준에서 요구하는 인증서 조건:

| 항목 | 요구사항 | 설명 |
|------|----------|------|
| **Key Size** | 최소 2048비트 | RSA 2048 이상 권장 |
| **Signature** | SHA-256 이상 | SHA-1은 더 이상 권장되지 않음 |
| **Subject Alt Name** | URI 필수 | Application URI와 일치해야 함 |
| **Key Usage** | Digital Signature, Key Encipherment | 필수 확장 필드 |
| **Extended Key Usage** | Client Auth, Server Auth | 용도에 따라 설정 |
| **Validity** | 1~5년 권장 | 너무 길면 보안 위험 |

### 인증서 체인

```
┌─────────────────────────────────────────────────────────────┐
│                      인증서 체인 구조                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   [Root CA Certificate]     ← 최상위 인증 기관 (자체 서명)   │
│            │                                                 │
│            ▼                                                 │
│   [Intermediate CA]         ← 중간 인증 기관 (선택적)        │
│            │                                                 │
│            ▼                                                 │
│   [End Entity Certificate]  ← 클라이언트/서버 인증서         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 인증서 생성 가이드

### 인증서 형식 비교

| 형식 | 확장자 | 인코딩 | 용도 |
|------|--------|--------|------|
| **PEM** | .pem, .crt | Base64 (텍스트) | Linux/Unix 표준, Simple Collector **기본** |
| **DER** | .der, .cer | Binary | Windows, Siemens PLC |
| **PKCS#12** | .p12, .pfx | Binary | 인증서+개인키 번들 |

!!! info "Simple Collector 기본 형식"
    Simple Collector는 **PEM 형식**을 기본으로 사용합니다.
    Windows 환경에서도 PEM 형식을 권장하며, 필요시 DER로 변환할 수 있습니다.

---

### Ubuntu/Linux에서 인증서 생성

#### 방법 1: OpenSSL 사용 (권장)

```bash title="Ubuntu/Linux 인증서 생성"
# 1. 디렉토리 생성
mkdir -p ~/opcua_certs
cd ~/opcua_certs

# 2. 개인키 생성 (RSA 2048비트)
openssl genrsa -out client_key.pem 2048

# 3. 설정 파일 생성
cat > client_cert.conf << 'EOF'
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C = KR
ST = Seoul
L = Seoul
O = NEUROSENSE
OU = Industrial IoT
CN = SimpleCollector OPC UA Client

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
URI.1 = urn:NEUROSENSE:SimpleCollector:client
DNS.1 = localhost
IP.1 = 127.0.0.1
EOF

# 4. 인증서 서명 요청 (CSR) 생성
openssl req -new -key client_key.pem -out client_csr.pem -config client_cert.conf

# 5. 자체 서명 인증서 생성 (5년 유효)
openssl x509 -req -days 1825 \
    -in client_csr.pem \
    -signkey client_key.pem \
    -out client_cert.pem \
    -extensions v3_req \
    -extfile client_cert.conf

# 6. 인증서 확인
openssl x509 -in client_cert.pem -text -noout
```

**예상 출력 (인증서 확인):**

```
Certificate:
    Data:
        Version: 3 (0x2)
        Serial Number: ...
        Signature Algorithm: sha256WithRSAEncryption
        Issuer: C=KR, ST=Seoul, L=Seoul, O=NEUROSENSE, OU=Industrial IoT, CN=SimpleCollector OPC UA Client
        Validity
            Not Before: Feb  6 00:00:00 2026 GMT
            Not After : Feb  5 00:00:00 2031 GMT
        Subject: C=KR, ST=Seoul, L=Seoul, O=NEUROSENSE, OU=Industrial IoT, CN=SimpleCollector OPC UA Client
        X509v3 extensions:
            X509v3 Basic Constraints:
                CA:FALSE
            X509v3 Key Usage:
                Digital Signature, Key Encipherment, Data Encipherment
            X509v3 Extended Key Usage:
                TLS Web Client Authentication
            X509v3 Subject Alternative Name:
                URI:urn:NEUROSENSE:SimpleCollector:client, DNS:localhost, IP Address:127.0.0.1
```

#### 방법 2: Simple Collector 내장 스크립트

```bash
# 인증서 생성 스크립트 실행
python scripts/generate_opcua_cert.py \
    --output ~/opcua_certs \
    --cn "SimpleCollector Client" \
    --org "NEUROSENSE" \
    --country "KR" \
    --validity 1825 \
    --uri "urn:NEUROSENSE:SimpleCollector:client"
```

**예상 출력:**

```
OPC UA Certificate Generator
============================
Generating RSA 2048-bit private key...
Creating self-signed certificate...

Certificate Details:
  Common Name  : SimpleCollector Client
  Organization : NEUROSENSE
  Country      : KR
  Valid Until  : 2031-02-05
  URI          : urn:NEUROSENSE:SimpleCollector:client

Files Created:
  Certificate  : /home/user/opcua_certs/client_cert.pem
  Private Key  : /home/user/opcua_certs/client_key.pem
  DER Format   : /home/user/opcua_certs/client_cert.der

✓ Certificate generated successfully!
```

---

### Windows에서 인증서 생성

#### 방법 1: OpenSSL (Git Bash 또는 WSL)

Git for Windows 설치 시 포함된 OpenSSL을 사용합니다:

```powershell title="PowerShell에서 Git Bash OpenSSL 사용"
# Git Bash OpenSSL 경로 확인
& "C:\Program Files\Git\usr\bin\openssl.exe" version
```

```bash title="Git Bash에서 실행"
# Git Bash 열기: 시작 → Git Bash

# 디렉토리 생성
mkdir -p /c/opcua_certs
cd /c/opcua_certs

# 개인키 생성
openssl genrsa -out client_key.pem 2048

# 인증서 생성 (단순 버전)
openssl req -new -x509 -days 1825 \
    -key client_key.pem \
    -out client_cert.pem \
    -subj "/C=KR/O=NEUROSENSE/CN=SimpleCollector Client" \
    -addext "subjectAltName=URI:urn:NEUROSENSE:SimpleCollector:client"

# DER 형식으로 변환 (Siemens PLC용)
openssl x509 -in client_cert.pem -outform der -out client_cert.der
```

#### 방법 2: PowerShell (Windows 기본)

```powershell title="PowerShell 인증서 생성"
# 관리자 권한으로 PowerShell 실행

# 인증서 저장 경로
$certPath = "C:\opcua_certs"
New-Item -ItemType Directory -Force -Path $certPath

# 자체 서명 인증서 생성
$cert = New-SelfSignedCertificate `
    -Subject "CN=SimpleCollector Client, O=NEUROSENSE, C=KR" `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears(5) `
    -KeyUsage DigitalSignature, KeyEncipherment `
    -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.2") `
    -FriendlyName "SimpleCollector OPC UA Client"

# 인증서 내보내기 (DER 형식)
Export-Certificate -Cert $cert -FilePath "$certPath\client_cert.der" -Type CERT

# PFX로 내보내기 (개인키 포함)
$password = ConvertTo-SecureString -String "password123" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath "$certPath\client_cert.pfx" -Password $password

Write-Host "Certificate created: $certPath\client_cert.der"
Write-Host "PFX created: $certPath\client_cert.pfx"
```

**PFX를 PEM으로 변환 (OpenSSL 필요):**

```bash
# 인증서 추출
openssl pkcs12 -in client_cert.pfx -clcerts -nokeys -out client_cert.pem

# 개인키 추출
openssl pkcs12 -in client_cert.pfx -nocerts -nodes -out client_key.pem
```

#### 방법 3: Simple Collector 내장 스크립트 (Windows)

```powershell title="PowerShell"
# Python 스크립트 실행
python scripts\generate_opcua_cert.py `
    --output C:\opcua_certs `
    --cn "SimpleCollector Client" `
    --org "NEUROSENSE" `
    --country "KR"
```

---

### 인증서 형식 변환

```bash title="형식 변환 명령어"
# PEM → DER (Siemens, KEPServer용)
openssl x509 -in client_cert.pem -outform der -out client_cert.der

# DER → PEM
openssl x509 -in client_cert.der -inform der -out client_cert.pem

# PEM → PKCS#12/PFX (인증서+개인키 번들)
openssl pkcs12 -export \
    -in client_cert.pem \
    -inkey client_key.pem \
    -out client_cert.pfx \
    -passout pass:password123

# PKCS#12/PFX → PEM
openssl pkcs12 -in client_cert.pfx -out client_bundle.pem -nodes
```

---

## 인증서 등록 가이드

### Siemens S7-1500

1. **TIA Portal에서 인증서 가져오기**
   ```
   프로젝트 → PLC → 속성 → OPC UA → 서버 → 신뢰할 수 있는 클라이언트
   → 가져오기 → client_cert.der 선택
   ```

2. **PUT/GET 통신 허용**
   ```
   프로젝트 → PLC → 속성 → 보호 → 연결 메커니즘
   → "PUT/GET 통신의 원격 파트너에서 액세스 허용" 체크
   ```

3. **프로젝트 다운로드**

### KEPServerEX

1. **OPC UA Configuration Manager 실행**
2. **신뢰할 수 있는 클라이언트 탭**
3. **가져오기 → client_cert.der 선택**
4. **서버 재시작**

### Ignition Gateway

1. **Config → OPC UA → Server Settings**
2. **Security → Trusted Certificates**
3. **Upload → client_cert.pem 선택**

### Prosys Simulation Server

1. **설정 → Security → Trusted Clients**
2. **Add → client_cert.pem 선택**
3. **또는**: 처음 연결 시 자동으로 "Rejected" 폴더에 저장됨
   - 해당 인증서를 "Trusted" 폴더로 이동

---

## 보안 아키텍처

### 보안 계층

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
├─────────────────────────────────────────────────────────────┤
│               Session Layer (User Authentication)            │
│       Anonymous │ Username/Password │ X.509 Certificate      │
├─────────────────────────────────────────────────────────────┤
│           Secure Channel Layer (Security Policy)             │
│           Encryption │ Signing │ Key Exchange                │
├─────────────────────────────────────────────────────────────┤
│               Transport Layer (UA TCP/HTTPS)                 │
└─────────────────────────────────────────────────────────────┘
```

### 보안 정책 (Security Policy)

| 정책 | 암호화 | 서명 | 키 교환 | 권장 |
|------|--------|------|---------|------|
| **None** | 없음 | 없음 | 없음 | 개발/테스트용 |
| **Basic256** | AES-256-CBC | RSA-SHA1 | RSA-OAEP | 레거시 (비권장) |
| **Basic256Sha256** | AES-256-CBC | RSA-SHA256 | RSA-OAEP | **권장 (기본값)** |
| **Aes128Sha256RsaOaep** | AES-128-CBC | RSA-SHA256 | RSA-OAEP | 최신 |
| **Aes256Sha256RsaPss** | AES-256-CBC | RSA-PSS | RSA-OAEP | 최신 (최고 보안) |

!!! tip "Simple Collector 기본값"
    보안 정책을 지정하지 않으면 `None`이 사용됩니다.
    운영 환경에서는 반드시 `Basic256Sha256` 이상을 사용하세요.

### 보안 모드 (Security Mode)

| 모드 | 메시지 서명 | 메시지 암호화 | 사용 사례 |
|------|------------|--------------|-----------|
| **None** | ✗ | ✗ | 테스트/개발 환경 |
| **Sign** | ✓ | ✗ | 무결성만 필요한 경우 |
| **SignAndEncrypt** | ✓ | ✓ | **운영 환경 권장** |

### 사용자 인증 (User Authentication)

| 방식 | 보안 수준 | 설정 | 사용 사례 |
|------|----------|------|-----------|
| **Anonymous** | 낮음 | `authentication: Anonymous` | 테스트/읽기 전용 |
| **Username/Password** | 중간 | `authentication: Username` | 일반 운영 환경 |
| **X.509 Certificate** | 높음 | `authentication: Certificate` | 고보안 환경 |

---

## 연결 설정

### 기본 설정 (보안 없음 - 개발용)

```yaml title="config/collector_opcua_dev.yaml"
collector:
  plc_id: 1
  name: "OPC_UA_Dev"

  protocol:
    type: opcua
    host: "localhost"
    port: 4840
    timeout_ms: 5000
    reconnect_interval_ms: 5000

    extra:
      security_policy: "None"
      security_mode: "None"
      authentication: "Anonymous"
```

### 운영 설정 (Username 인증)

```yaml title="config/collector_opcua_prod.yaml"
collector:
  plc_id: 1
  name: "OPC_UA_Production"

  protocol:
    type: opcua
    host: "192.168.1.100"
    port: 4840
    timeout_ms: 5000

    extra:
      # 보안 정책 (Basic256Sha256 권장)
      security_policy: "Basic256Sha256"
      security_mode: "SignAndEncrypt"

      # 사용자 인증
      authentication: "Username"
      username: "collector"
      password: "${OPC_UA_PASSWORD}"    # 환경변수 참조

      # 클라이언트 인증서 (Secure Channel용)
      certificate_path: "/app/certs/client_cert.pem"
      private_key_path: "/app/certs/client_key.pem"

      # Application URI (인증서의 Subject Alt Name과 일치해야 함)
      application_uri: "urn:NEUROSENSE:SimpleCollector:client"
```

### 고보안 설정 (Certificate 인증)

```yaml title="config/collector_opcua_highsec.yaml"
collector:
  plc_id: 1
  name: "OPC_UA_HighSecurity"

  protocol:
    type: opcua
    host: "192.168.1.100"
    port: 4840
    timeout_ms: 5000

    extra:
      # 최고 보안 정책
      security_policy: "Aes256Sha256RsaPss"
      security_mode: "SignAndEncrypt"

      # 인증서 기반 인증 (가장 안전)
      authentication: "Certificate"

      # 클라이언트 인증서
      certificate_path: "/app/certs/client_cert.pem"
      private_key_path: "/app/certs/client_key.pem"

      # 서버 인증서 (상호 인증)
      server_certificate_path: "/app/certs/server_cert.pem"

      # 고급 설정
      application_uri: "urn:NEUROSENSE:SimpleCollector:client"
      session_timeout_ms: 60000
```

---

## 노드 주소 형식

### NodeId 형식

```
┌─────────────────────────────────────────────────────────────┐
│                     NodeId 형식                              │
├─────────────────────────────────────────────────────────────┤
│  ns={namespace};{identifier_type}={identifier}               │
├─────────────────────────────────────────────────────────────┤
│  식별자 타입:                                                │
│    s = String  (문자열)     예: ns=2;s=Temperature           │
│    i = Numeric (정수)       예: ns=2;i=1001                  │
│    g = GUID                 예: ns=2;g=550e8400-e29b-...     │
│    b = ByteString (Base64)  예: ns=2;b=TWFu                  │
└─────────────────────────────────────────────────────────────┘
```

### 태그 설정 예시

```csv title="config/tags_opcua.csv"
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,unit,description
1,Temperature,OPC,ns=2;s=Temperature,float32,fast,1.0,0.0,°C,온도 센서
2,Pressure,OPC,ns=2;s=Pressure,float32,fast,1.0,0.0,bar,압력 센서
3,MotorSpeed,OPC,ns=2;i=1001,uint16,fast,0.1,0.0,rpm,모터 속도
4,ProductCount,OPC,ns=2;i=1002,uint32,slow,1.0,0.0,EA,생산 수량
5,AlarmStatus,OPC,ns=2;s=Alarm.Active,bool,fast,1.0,0.0,,알람 상태
```

---

## 에러 처리

### 인증서 오류

!!! error "BadCertificateUntrusted"
    ```
    ERROR | OPC UA status error: BadCertificateUntrusted
    ```

    **원인:** 클라이언트 인증서가 서버의 신뢰 목록에 없음

    **해결:**

    1. 클라이언트 인증서를 서버의 Trusted 폴더에 복사
    2. 또는 서버 관리 도구에서 인증서 가져오기
    3. 인증서의 Application URI가 설정과 일치하는지 확인

!!! error "BadCertificateTimeInvalid"
    ```
    ERROR | OPC UA status error: BadCertificateTimeInvalid
    ```

    **원인:** 인증서 유효 기간 만료 또는 시스템 시간 불일치

    **해결:**

    1. 인증서 유효 기간 확인: `openssl x509 -in cert.pem -dates -noout`
    2. 시스템 시간 동기화 확인
    3. 필요시 인증서 재발급

!!! error "BadSecurityModeRejected"
    ```
    ERROR | OPC UA status error: BadSecurityModeRejected
    ```

    **원인:** 서버가 요청한 보안 모드를 지원하지 않음

    **해결:**

    1. 서버 엔드포인트 조회로 지원 정책 확인
    2. `security_policy`와 `security_mode` 조정

### 연결 오류

!!! failure "Connection refused"
    ```
    ERROR | Connection to opc.tcp://192.168.1.100:4840 failed
    ```

    **확인 사항:**

    1. 서버 실행 중인지 확인
    2. IP 주소 및 포트 확인
    3. 방화벽 설정 확인 (TCP 4840)

---

## 성능 최적화

### 권장 설정

| 항목 | 개발 환경 | 운영 환경 | 설명 |
|------|----------|----------|------|
| 보안 정책 | None | Basic256Sha256 | 운영 시 반드시 보안 적용 |
| 수집 주기 | 1000ms | 100ms~5s | 서버 부하 고려 |
| 세션 타임아웃 | 30000ms | 60000ms | 연결 유지 |
| 일괄 읽기 | 10개 | 50~100개 | 네트워크 효율화 |

---

## 참고 자료

### 공식 문서

- [OPC Foundation 공식 문서](https://opcfoundation.org/)
- [asyncua 라이브러리 문서](https://opcua-asyncio.readthedocs.io/)
- [IEC 62541 표준](https://webstore.iec.ch/publication/61109)

### 인증서 관련

- [OpenSSL 공식 문서](https://www.openssl.org/docs/)
- [X.509 인증서 표준 (RFC 5280)](https://tools.ietf.org/html/rfc5280)
- [OPC UA Security Model](https://opcfoundation.org/developer-tools/specifications-unified-architecture)

### 테스트 서버

- [Prosys OPC UA Simulation Server](https://prosysopc.com/products/opc-ua-simulation-server/) (무료)
- [Unified Automation UaExpert](https://www.unified-automation.com/products/development-tools/uaexpert.html) (무료 클라이언트)

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
