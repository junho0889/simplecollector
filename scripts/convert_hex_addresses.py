#!/usr/bin/env python3
"""
CSV 파일에서 X/Y 디바이스의 16진수 주소를 10진수로 변환.

Usage:
    python convert_hex_addresses.py input.csv output.csv
"""

import csv
import sys
import re
from pathlib import Path


def is_hex_string(s: str) -> bool:
    """문자열이 16진수인지 확인."""
    if not s:
        return False
    # 순수 숫자면 10진수로 간주
    if s.isdigit():
        return False
    # A-F 포함하면 16진수
    try:
        int(s, 16)
        return any(c in s.upper() for c in 'ABCDEF')
    except ValueError:
        return False


def convert_address(memory: str, address: str) -> tuple:
    """
    주소 변환.

    Args:
        memory: 메모리 영역 (D, M, L, X, Y)
        address: 주소 문자열

    Returns:
        (변환된 주소, 변환 여부)
    """
    if not address:
        return (0, False)

    # X/Y 디바이스이고 16진수 문자열인 경우
    if memory.upper() in ('X', 'Y') and is_hex_string(address):
        try:
            decimal = int(address, 16)
            return (decimal, True)
        except ValueError:
            pass

    # 일반 숫자
    try:
        return (int(address), False)
    except ValueError:
        return (0, False)


def convert_csv(input_path: str, output_path: str) -> dict:
    """
    CSV 파일의 주소를 변환.

    Returns:
        변환 통계
    """
    stats = {
        'total_rows': 0,
        'converted': 0,
        'conversions': []  # (tag_name, memory, old_addr, new_addr)
    }

    rows = []

    with open(input_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames

        for row in reader:
            stats['total_rows'] += 1

            # 주석 행 건너뛰기
            tag_id = row.get('tag_id', '')
            if not tag_id or tag_id.startswith('#'):
                rows.append(row)
                continue

            memory = row.get('memory', '')
            address = row.get('address', '')

            new_addr, converted = convert_address(memory, address)

            if converted:
                stats['converted'] += 1
                stats['conversions'].append((
                    row.get('tag_name', ''),
                    memory,
                    address,
                    new_addr
                ))
                row['address'] = str(new_addr)

            rows.append(row)

    # 출력 파일 작성
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python convert_hex_addresses.py input.csv [output.csv]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace('.csv', '_converted.csv')

    if not Path(input_path).exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    print(f"Converting: {input_path} -> {output_path}")
    print("-" * 60)

    stats = convert_csv(input_path, output_path)

    print(f"Total rows: {stats['total_rows']}")
    print(f"Converted: {stats['converted']}")
    print()

    if stats['conversions']:
        print("Conversion details (first 30):")
        print("-" * 60)
        print(f"{'Tag Name':<20} {'Memory':<6} {'Hex':<8} {'Decimal':<8}")
        print("-" * 60)

        for tag_name, memory, old_addr, new_addr in stats['conversions'][:30]:
            print(f"{tag_name:<20} {memory:<6} {old_addr:<8} {new_addr:<8}")

        if len(stats['conversions']) > 30:
            print(f"... and {len(stats['conversions']) - 30} more")

    print()
    print(f"Output saved to: {output_path}")


if __name__ == '__main__':
    main()
