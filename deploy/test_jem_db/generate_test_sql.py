#!/usr/bin/env python3
"""
custom_init.sql → custom_init_test.sql 크로스 스키마 변환 스크립트

매핑 규칙:
  - {schema} → jem_test (기본)
  - 소스 테이블 → jem_jh02 (plc_master, plc_data_*, alm_*, action_latest)
  - 크로스 스키마 트리거 → _test 접미사 (운영 트리거와 충돌 방지)
  - {group} 섹션 제거 (publisher 전용, 테스트에선 불필요)
  - ALTER TABLE plc_master 제거 (운영에서 이미 추가됨)
"""

import re
import os
import sys

# Windows cp949 encoding issue
sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT  = os.path.join(SCRIPT_DIR, '..', 'jem', 'custom_init.sql')
OUTPUT = os.path.join(SCRIPT_DIR, 'custom_init_test.sql')

with open(INPUT, 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. 원본 헤더 + {group} 섹션 제거 ──────────────────────────────
# "-- 1. API 읽기 전용 계정" 직전까지 모두 제거
marker = '\n-- ============================================================================\n-- 1. API'
idx = content.find(marker)
if idx < 0:
    raise RuntimeError('Section 1 marker not found')
content = content[idx:]

# ── 2. {schema} → jem_test ────────────────────────────────────────
content = content.replace('{schema}', 'jem_test')

# ── 3. 소스 테이블 → jem_jh02 (긴 이름부터 치환) ─────────────────
source_tables = [
    'plc_data_latest', 'plc_data_master', 'plc_master',
    'alm_latest', 'alm_master', 'alm_history',
    'action_latest',
]
for tbl in source_tables:
    content = content.replace(f'jem_test.{tbl}', f'jem_jh02.{tbl}')

# ── 4. ALTER TABLE plc_master 제거 ────────────────────────────────
content = re.sub(
    r'-- 2-4\. plc_master에[^\n]*\n'
    r'-- 기존 plc_master[^\n]*\n'
    r'ALTER TABLE jem_jh02\.plc_master\n'
    r'[^\n]*line_id[^\n]*;\n',
    '-- 2-4. plc_master의 line_id는 운영(jem_jh02)에서 이미 추가됨 — 스킵\n',
    content
)

# ── 5. 크로스 스키마 트리거 이름에 _test 접미사 ───────────────────
# 이 트리거들은 jem_jh02 테이블에 등록되므로 운영과 충돌 방지 필요
cross_triggers = [
    'trg_production_shift',
    'trg_downtime_alm',
    'trg_downtime_action',
    'trg_prod_mode_change',
    'trg_prod_alm_tracker',
]
for trg in cross_triggers:
    content = re.sub(rf'\b{re.escape(trg)}\b', f'{trg}_test', content)

# ── 6. 새 헤더 추가 ──────────────────────────────────────────────
header = """\
-- ============================================================================
-- JEM 테스트 스키마 — 크로스 스키마 버전
-- ============================================================================
-- 원본: deploy/jem/custom_init.sql (자동 생성 — 직접 수정하지 마세요)
-- 생성: python generate_test_sql.py
--
-- 구조:
--   소스 (jem_jh02, READ-ONLY):
--     plc_master, plc_data_latest/master, alm_latest/master/history, action_latest
--     → Publisher가 실시간 데이터를 적재하는 테이블
--
--   테스트 (jem_test, READ-WRITE):
--     tb_info_* (설정), tb_prod_* (결과), tb_hist_* (이력), 모든 fn_*
--     → 독립된 설정으로 독립된 결과 생성
--
-- 동작:
--   jem_jh02 테이블에 _test 트리거 등록
--   → 운영 데이터 변경 시 jem_test 함수도 호출
--   → jem_test.tb_info_target 등 설정 독립 변경 가능
--
-- 실행:
--   psql -h <host> -U neuro0901 -d neurosense -f custom_init_test.sql
--   psql -h <host> -U neuro0901 -d neurosense -f seed_data_test.sql
--
-- 제거:
--   psql -h <host> -U neuro0901 -d neurosense -f cleanup_test.sql
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS jem_test;

"""

content = header + content

# ── 7. 출력 ───────────────────────────────────────────────────────
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(content)

lines = content.count('\n') + 1
print(f'Generated: {OUTPUT}')
print(f'  Size: {len(content):,} bytes, {lines:,} lines')
print()

# ── 검증 ──────────────────────────────────────────────────────────
print('=== 검증: 정상 패턴 ===')
checks = [
    ('jem_test.tb_info_target',            'test config table'),
    ('jem_test.tb_prod_shift_current',     'test result table'),
    ('jem_test.fn_production_shift_tracker', 'test function'),
    ('jem_jh02.plc_data_latest',           'source data table'),
    ('jem_jh02.plc_master',                'source master table'),
    ('jem_jh02.alm_history',               'source alarm history'),
    ('trg_production_shift_test',          'cross-schema trigger'),
    ('trg_hourly_snapshot',                'internal trigger (no _test)'),
    ('CREATE SCHEMA IF NOT EXISTS jem_test', 'schema creation'),
]
for pattern, desc in checks:
    count = content.count(pattern)
    status = '✓' if count > 0 else '✗'
    print(f'  {status} {desc}: {count}x')

print()
print('=== 검증: 문제 패턴 ===')
issues = [
    ('jem_test.plc_data_latest',  'should be jem_jh02'),
    ('jem_test.plc_data_master',  'should be jem_jh02'),
    ('jem_test.plc_master',       'should be jem_jh02'),
    ('jem_test.alm_history',      'should be jem_jh02'),
    ('jem_test.alm_latest',       'should be jem_jh02'),
    ('jem_test.alm_master',       'should be jem_jh02'),
    ('jem_test.action_latest',    'should be jem_jh02'),
    ('{schema}',                  'unreplaced placeholder'),
    ('{group}',                   'unreplaced group placeholder'),
]
has_issues = False
for pattern, desc in issues:
    count = content.count(pattern)
    if count > 0:
        has_issues = True
        print(f'  ⚠ {pattern}: {count}x — {desc}')
        # Show context
        for m in re.finditer(re.escape(pattern), content):
            start = max(0, m.start() - 40)
            end = min(len(content), m.end() + 40)
            ctx = content[start:end].replace('\n', '↵')
            print(f'    → ...{ctx}...')

if not has_issues:
    print('  ✓ 문제 없음')


# ======================================================================
# backfill_statistics.sql → backfill_statistics_test.sql
# ======================================================================
# 원본은 jem_jh02 하드코딩. 크로스 스키마 매핑:
#   소스 (jem_jh02 유지): plc_data_integrated, plc_data_master, plc_master, alm_history
#   설정 (→ jem_test):    tb_info_shift, tb_info_target, tb_info_cycle_time
#   결과 (→ jem_test):    tb_prod_shift_history, tb_prod_hourly, tb_prod_daily,
#                          tb_prod_mode_change, fn_backfill_daily_statistics

BACKFILL_INPUT  = os.path.join(SCRIPT_DIR, '..', 'jem', 'backfill_statistics.sql')
BACKFILL_OUTPUT = os.path.join(SCRIPT_DIR, 'backfill_statistics_test.sql')

if os.path.exists(BACKFILL_INPUT):
    with open(BACKFILL_INPUT, 'r', encoding='utf-8') as f:
        bf = f.read()

    # 설정 테이블 → jem_test
    for tbl in ['tb_info_shift', 'tb_info_target', 'tb_info_cycle_time']:
        bf = bf.replace(f'jem_jh02.{tbl}', f'jem_test.{tbl}')

    # 결과 테이블 → jem_test
    for tbl in ['tb_prod_shift_history', 'tb_prod_hourly', 'tb_prod_daily', 'tb_prod_mode_change']:
        bf = bf.replace(f'jem_jh02.{tbl}', f'jem_test.{tbl}')

    # 함수 → jem_test
    bf = bf.replace('jem_jh02.fn_backfill_daily_statistics', 'jem_test.fn_backfill_daily_statistics')

    # 헤더 교체
    bf = bf.replace(
        '-- 스키마: jem_jh02',
        '-- 스키마: jem_test (소스: jem_jh02)'
    )

    with open(BACKFILL_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(bf)

    bf_lines = bf.count('\n') + 1
    print()
    print(f'Generated: {BACKFILL_OUTPUT}')
    print(f'  Size: {len(bf):,} bytes, {bf_lines:,} lines')

    # 검증
    print()
    print('=== backfill 검증 ===')
    bf_checks = [
        ('jem_jh02.plc_data_integrated', 'source data (should stay jem_jh02)'),
        ('jem_jh02.plc_data_master',     'source master (should stay jem_jh02)'),
        ('jem_jh02.plc_master',          'source plc_master (should stay jem_jh02)'),
        ('jem_jh02.alm_history',         'source alarm (should stay jem_jh02)'),
        ('jem_test.tb_info_shift',       'test config'),
        ('jem_test.tb_info_target',      'test config'),
        ('jem_test.tb_prod_shift_history', 'test result'),
        ('jem_test.tb_prod_hourly',      'test result'),
        ('jem_test.tb_prod_daily',       'test result'),
        ('jem_test.tb_prod_mode_change', 'test result'),
        ('jem_test.fn_backfill',         'test function'),
    ]
    for pattern, desc in bf_checks:
        count = bf.count(pattern)
        status = '✓' if count > 0 else '✗'
        print(f'  {status} {desc}: {count}x')

    # 문제 패턴
    bf_issues = [
        ('jem_jh02.tb_info_shift',        'should be jem_test'),
        ('jem_jh02.tb_info_target',       'should be jem_test'),
        ('jem_jh02.tb_info_cycle_time',   'should be jem_test'),
        ('jem_jh02.tb_prod_shift_history','should be jem_test'),
        ('jem_jh02.tb_prod_hourly',       'should be jem_test'),
        ('jem_jh02.tb_prod_daily',        'should be jem_test'),
        ('jem_jh02.tb_prod_mode_change',  'should be jem_test'),
        ('jem_jh02.fn_backfill',          'should be jem_test'),
    ]
    bf_has_issues = False
    for pattern, desc in bf_issues:
        count = bf.count(pattern)
        if count > 0:
            bf_has_issues = True
            print(f'  ⚠ {pattern}: {count}x — {desc}')
    if not bf_has_issues:
        print('  ✓ 문제 없음')
else:
    print(f'\n⚠ backfill 원본 없음: {BACKFILL_INPUT}')
