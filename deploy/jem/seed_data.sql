-- ============================================================================
-- JEM 초기 데이터 (수동 실행)
-- ============================================================================
-- 용도: 사이트 계층, 교대 설정, 목표수량 등 초기 마스터 데이터 투입
-- 실행: psql -h <host> -U neuro0901 -d neurosense -f seed_data.sql
-- 주의: 모든 INSERT는 ON CONFLICT DO NOTHING → 이미 있으면 스킵 (멱등)
-- ============================================================================

-- 1. 사이트 계층: 회사 → 공장 → 라인
INSERT INTO jem_jh02.tb_info_company (company_id, company_name)
VALUES (1, '진영전기')
ON CONFLICT (company_id) DO NOTHING;

INSERT INTO jem_jh02.tb_info_factory (factory_id, company_id, factory_name)
VALUES (1, 1, '마산자유무역지역')
ON CONFLICT (factory_id) DO NOTHING;

INSERT INTO jem_jh02.tb_info_line (line_id, factory_id, line_name)
VALUES (1, 1, 'JH02')
ON CONFLICT (line_id) DO NOTHING;

-- 2. PLC → 라인 매핑
UPDATE jem_jh02.plc_master SET line_id = 1 WHERE line_id IS NULL;

-- 3. 교대 설정 (2교대, 라인별)
INSERT INTO jem_jh02.tb_info_shift (line_id, shift_type, shift_order, start_time, end_time, description) VALUES
    (1, 'day',   1, '08:30', '20:30', '주간'),
    (1, 'night', 2, '20:30', '08:30', '야간')
ON CONFLICT (line_id, shift_type) DO NOTHING;

-- 4. 교대 초기 이력 (이력 없는 교대만 INSERT)
INSERT INTO jem_jh02.tb_hist_shift (line_id, shift_type, start_time, end_time, changed_at)
SELECT sc.line_id, sc.shift_type, sc.start_time, sc.end_time, '2026-01-01 00:00:00+09'::timestamptz
FROM jem_jh02.tb_info_shift sc
WHERE NOT EXISTS (
    SELECT 1 FROM jem_jh02.tb_hist_shift h
    WHERE h.line_id = sc.line_id AND h.shift_type = sc.shift_type
);

-- 5. 목표 생산수량 (라인별)
INSERT INTO jem_jh02.tb_info_target (line_id, target_type, target_qty) VALUES
    (1, 'day',   20000),
    (1, 'night', 0)
ON CONFLICT (line_id, target_type) DO NOTHING;

-- 6. 사이클타임 (라인별)
INSERT INTO jem_jh02.tb_info_cycle_time (line_id, cycle_time_sec) VALUES
    (1, 2.2)
ON CONFLICT (line_id) DO NOTHING;
