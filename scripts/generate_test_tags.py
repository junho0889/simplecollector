"""
5000개 랜덤 태그 생성 스크립트
"""
import csv
import random

# 데이터 타입 및 가중치
DATA_TYPES = [
    ('uint16', 1, 30),     # (타입, 워드수, 가중치)
    ('int16', 1, 20),
    ('uint32', 2, 20),
    ('int32', 2, 10),
    ('float32', 2, 15),
    ('bool', 1, 5),
]

# 메모리 영역
MEMORIES = ['D', 'D', 'D', 'D', 'HR']  # D가 더 자주 사용

# 수집 그룹
GROUPS = ['fast', 'fast', 'fast', 'normal', 'slow']  # fast가 더 많이

def generate_tags(num_tags: int, output_file: str):
    """5000개 태그 생성"""

    tags = []
    current_address = 0

    # 가중치 기반 데이터 타입 리스트 생성
    weighted_types = []
    for dtype, words, weight in DATA_TYPES:
        weighted_types.extend([(dtype, words)] * weight)

    for tag_id in range(1, num_tags + 1):
        # 랜덤 데이터 타입 선택
        data_type, word_count = random.choice(weighted_types)

        # 메모리 영역
        memory = random.choice(MEMORIES)

        # 주소 (연속 배치)
        address = current_address
        current_address += word_count

        # 그룹
        group = random.choice(GROUPS)

        # 스케일, 오프셋
        if data_type in ('float32',):
            scale = 1.0
            offset = 0.0
            decimals = 2
        elif data_type in ('uint16', 'int16', 'uint32', 'int32'):
            scale = random.choice([1.0, 0.1, 0.01, 0.001])
            offset = 0.0
            decimals = 2 if scale != 1.0 else ''
        else:
            scale = 1.0
            offset = 0.0
            decimals = ''

        # 단위
        units = ['', '', '', '°C', 'bar', 'rpm', 'mm', '%', 'A', 'V', 'kW']
        unit = random.choice(units)

        tag = {
            'tag_id': tag_id,
            'tag_name': f'Tag_{tag_id:05d}',
            'memory': memory,
            'address': address,
            'data_type': data_type,
            'collection_group': group,
            'scale': scale,
            'offset': offset,
            'decimals': decimals,
            'word_length': '',
            'format': '',
            'unit': unit,
            'description': f'Test tag {tag_id}'
        }
        tags.append(tag)

    # CSV 저장
    fieldnames = ['tag_id', 'tag_name', 'memory', 'address', 'data_type',
                  'collection_group', 'scale', 'offset', 'decimals',
                  'word_length', 'format', 'unit', 'description']

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tags)

    # 통계 출력
    print(f"Generated {len(tags)} tags")
    print(f"Address range: 0 - {current_address}")

    # 그룹별 통계
    group_counts = {}
    type_counts = {}
    for tag in tags:
        g = tag['collection_group']
        t = tag['data_type']
        group_counts[g] = group_counts.get(g, 0) + 1
        type_counts[t] = type_counts.get(t, 0) + 1

    print("\nGroup distribution:")
    for g, c in sorted(group_counts.items()):
        print(f"  {g}: {c}")

    print("\nType distribution:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

if __name__ == '__main__':
    generate_tags(5000, 'config/tags_stress_test.csv')
