-- ============================================================================
-- 테스트 스키마 정리 스크립트
-- ============================================================================
-- 용도: jem_test 스키마 및 jem_jh02에 등록된 _test 트리거 제거
-- 실행: psql -h <host> -U neuro0901 -d neurosense -f cleanup_test.sql
-- ============================================================================

-- 1. jem_jh02 테이블의 _test 트리거 제거
DROP TRIGGER IF EXISTS trg_production_shift_test ON jem_jh02.plc_data_latest;
DROP TRIGGER IF EXISTS trg_downtime_alm_test ON jem_jh02.alm_latest;
DROP TRIGGER IF EXISTS trg_downtime_action_test ON jem_jh02.action_latest;
DROP TRIGGER IF EXISTS trg_prod_mode_change_test ON jem_jh02.plc_data_latest;
DROP TRIGGER IF EXISTS trg_prod_alm_tracker_test ON jem_jh02.alm_history;

-- 2. jem_test 스키마 전체 삭제
DROP SCHEMA IF EXISTS jem_test CASCADE;
