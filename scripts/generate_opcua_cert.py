#!/usr/bin/env python3
"""
OPC UA Certificate Generator
=============================

OPC UA 클라이언트용 자체 서명 인증서를 생성합니다.

Usage:
    python scripts/generate_opcua_cert.py --output certs/
    python scripts/generate_opcua_cert.py --output certs/ --cn "My Client" --org "MyCompany"

Options:
    --output      출력 디렉토리 (기본: ./certs)
    --cn          Common Name (기본: SimpleCollector OPC UA Client)
    --org         Organization (기본: NEUROSENSE)
    --country     Country Code (기본: KR)
    --validity    유효 기간 (일, 기본: 1825 = 5년)
    --uri         Application URI (기본: urn:NEUROSENSE:SimpleCollector:client)
    --key-size    RSA 키 크기 (기본: 2048)
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


def generate_certificate(
    output_dir: str,
    common_name: str = "SimpleCollector OPC UA Client",
    organization: str = "NEUROSENSE",
    country: str = "KR",
    state: str = "Seoul",
    locality: str = "Seoul",
    validity_days: int = 1825,
    application_uri: str = "urn:NEUROSENSE:SimpleCollector:client",
    key_size: int = 2048,
) -> tuple:
    """
    OPC UA 클라이언트용 자체 서명 인증서 생성.

    Args:
        output_dir: 출력 디렉토리
        common_name: 인증서 CN
        organization: 조직명
        country: 국가 코드
        state: 주/도
        locality: 도시
        validity_days: 유효 기간 (일)
        application_uri: OPC UA Application URI
        key_size: RSA 키 크기

    Returns:
        (인증서 경로, 개인키 경로, DER 경로) 튜플
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        print("Error: cryptography 라이브러리가 필요합니다.")
        print("설치: pip install cryptography")
        sys.exit(1)

    print("OPC UA Certificate Generator")
    print("=" * 40)

    # 출력 디렉토리 생성
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # RSA 키 쌍 생성
    print(f"Generating RSA {key_size}-bit private key...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend(),
    )

    # 인증서 생성
    print("Creating self-signed certificate...")

    # Subject/Issuer 정보
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, country),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, state),
        x509.NameAttribute(NameOID.LOCALITY_NAME, locality),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Industrial IoT"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    # 유효 기간
    not_before = datetime.utcnow()
    not_after = not_before + timedelta(days=validity_days)

    # 인증서 빌더
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )

    # 확장 필드 추가
    # Basic Constraints
    builder = builder.add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    )

    # Key Usage
    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=True,
            data_encipherment=True,
            key_agreement=False,
            content_commitment=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )

    # Extended Key Usage (Client Authentication)
    builder = builder.add_extension(
        x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.CLIENT_AUTH,
        ]),
        critical=False,
    )

    # Subject Alternative Name (OPC UA 필수)
    builder = builder.add_extension(
        x509.SubjectAlternativeName([
            x509.UniformResourceIdentifier(application_uri),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    )

    # Subject Key Identifier
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
        critical=False,
    )

    # 서명
    cert = builder.sign(private_key, hashes.SHA256(), default_backend())

    # 파일 저장
    cert_path = output_path / "client_cert.pem"
    key_path = output_path / "client_key.pem"
    der_path = output_path / "client_cert.der"

    # PEM 형식 인증서
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    # PEM 형식 개인키 (암호화 없음)
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # DER 형식 인증서 (Siemens PLC 등에서 필요)
    with open(der_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.DER))

    # 결과 출력
    print()
    print("Certificate Details:")
    print(f"  Common Name  : {common_name}")
    print(f"  Organization : {organization}")
    print(f"  Country      : {country}")
    print(f"  Valid From   : {not_before.strftime('%Y-%m-%d')}")
    print(f"  Valid Until  : {not_after.strftime('%Y-%m-%d')}")
    print(f"  URI          : {application_uri}")
    print(f"  Key Size     : {key_size} bits")
    print()
    print("Files Created:")
    print(f"  Certificate  : {cert_path.absolute()}")
    print(f"  Private Key  : {key_path.absolute()}")
    print(f"  DER Format   : {der_path.absolute()}")
    print()
    print("✓ Certificate generated successfully!")

    return str(cert_path), str(key_path), str(der_path)


def verify_certificate(cert_path: str):
    """인증서 정보 출력."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        print("Error: cryptography 라이브러리가 필요합니다.")
        return

    with open(cert_path, "rb") as f:
        cert_data = f.read()

    # PEM 또는 DER 형식 자동 감지
    try:
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
    except ValueError:
        cert = x509.load_der_x509_certificate(cert_data, default_backend())

    print("Certificate Information:")
    print("=" * 40)
    print(f"Subject: {cert.subject.rfc4514_string()}")
    print(f"Issuer: {cert.issuer.rfc4514_string()}")
    print(f"Serial: {cert.serial_number}")
    print(f"Not Before: {cert.not_valid_before}")
    print(f"Not After: {cert.not_valid_after}")

    # 확장 필드
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        print("Subject Alternative Names:")
        for name in san.value:
            print(f"  - {name}")
    except x509.ExtensionNotFound:
        pass


def main():
    import ipaddress
    global ipaddress
    import ipaddress as ip_module
    ipaddress = ip_module

    parser = argparse.ArgumentParser(
        description="OPC UA 클라이언트용 자체 서명 인증서 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  # 기본 설정으로 생성
  python scripts/generate_opcua_cert.py --output certs/

  # 사용자 정의 설정
  python scripts/generate_opcua_cert.py \\
      --output /app/certs \\
      --cn "Production Client" \\
      --org "MyCompany" \\
      --country "KR" \\
      --validity 365 \\
      --uri "urn:MyCompany:ProductionClient"

  # 인증서 정보 확인
  python scripts/generate_opcua_cert.py --verify certs/client_cert.pem
""",
    )

    parser.add_argument(
        "--output", "-o",
        default="./certs",
        help="출력 디렉토리 (기본: ./certs)",
    )
    parser.add_argument(
        "--cn",
        default="SimpleCollector OPC UA Client",
        help="Common Name (기본: SimpleCollector OPC UA Client)",
    )
    parser.add_argument(
        "--org",
        default="NEUROSENSE",
        help="Organization (기본: NEUROSENSE)",
    )
    parser.add_argument(
        "--country",
        default="KR",
        help="Country Code (기본: KR)",
    )
    parser.add_argument(
        "--state",
        default="Seoul",
        help="State/Province (기본: Seoul)",
    )
    parser.add_argument(
        "--locality",
        default="Seoul",
        help="City (기본: Seoul)",
    )
    parser.add_argument(
        "--validity",
        type=int,
        default=1825,
        help="유효 기간 (일, 기본: 1825 = 5년)",
    )
    parser.add_argument(
        "--uri",
        default="urn:NEUROSENSE:SimpleCollector:client",
        help="Application URI (기본: urn:NEUROSENSE:SimpleCollector:client)",
    )
    parser.add_argument(
        "--key-size",
        type=int,
        default=2048,
        choices=[2048, 4096],
        help="RSA 키 크기 (기본: 2048)",
    )
    parser.add_argument(
        "--verify",
        metavar="CERT_FILE",
        help="인증서 파일 정보 확인",
    )

    args = parser.parse_args()

    if args.verify:
        verify_certificate(args.verify)
    else:
        generate_certificate(
            output_dir=args.output,
            common_name=args.cn,
            organization=args.org,
            country=args.country,
            state=args.state,
            locality=args.locality,
            validity_days=args.validity,
            application_uri=args.uri,
            key_size=args.key_size,
        )


if __name__ == "__main__":
    main()
