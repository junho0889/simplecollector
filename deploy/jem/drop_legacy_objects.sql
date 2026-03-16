-- ============================================================================
-- 기존 배포 환경 정리 스크립트
-- ============================================================================
-- 용도: 새 custom_init.sql 배포 전, 충돌/대체되는 기존 객체를 제거
-- 실행: publisher 중지 상태에서 DB에 직접 실행
--   psql -h <host> -U <user> -d neurosense -f drop_legacy_objects.sql
--
-- 주의: {schema}를 실제 스키마명으로 치환하고 실행할 것 (예: jem_jh02)
-- ============================================================================


-- ============================================================================
-- 1. 반드시 DROP (새 SQL과 충돌)
-- ============================================================================

-- production_hourly: 기존 Continuous Aggregate → 새 SQL에서 일반 테이블로 생성
-- CA는 DROP MATERIALIZED VIEW로 삭제해야 함
DO $$
BEGIN
    -- CA 정책 먼저 제거
    PERFORM remove_continuous_aggregate_policy('{schema}.production_hourly', if_not_exists => TRUE);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'production_hourly CA policy 제거 스킵: %', SQLERRM;
END $$;

DO $$
BEGIN
    PERFORM remove_compression_policy('{schema}.production_hourly', if_not_exists => TRUE);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'production_hourly compression policy 제거 스킵: %', SQLERRM;
END $$;

DO $$
BEGIN
    PERFORM remove_retention_policy('{schema}.production_hourly', if_not_exists => TRUE);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'production_hourly retention policy 제거 스킵: %', SQLERRM;
END $$;

DROP MATERIALIZED VIEW IF EXISTS {schema}.production_hourly CASCADE;


-- ============================================================================
-- 2. DROP 권장 (새 설계로 대체됨)
-- ============================================================================

-- 알람 교대별 통계 → v_alarm_ranking_daily/weekly/monthly로 대체
DROP TABLE IF EXISTS {schema}.alm_shift_summary CASCADE;
DROP FUNCTION IF EXISTS {schema}.fn_compute_alm_shift_summary(TIMESTAMPTZ, TIMESTAMPTZ, VARCHAR, DATE);
DROP FUNCTION IF EXISTS {schema}.fn_daily_alm_check();

-- 알람 지속시간 기록 → v_alarm_downtime 뷰로 대체
DROP TABLE IF EXISTS {schema}.alm_duration_log CASCADE;
DROP PROCEDURE IF EXISTS {schema}.sp_calc_alm_duration(TIMESTAMPTZ, TIMESTAMPTZ);

-- 시간별 가동율 → production_shift_current.operating_rate (트리거 실시간)로 대체
DROP TABLE IF EXISTS {schema}.operating_rate_hourly CASCADE;
DROP PROCEDURE IF EXISTS {schema}.sp_calc_operating_rate(TIMESTAMPTZ, TIMESTAMPTZ);


-- ============================================================================
-- 3. pg_cron 스케줄 제거 (있을 경우)
-- ============================================================================
-- 기존 스케줄이 등록되어 있으면 삭제 (없으면 에러 없이 스킵)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.unschedule('calc-alm-duration');
        PERFORM cron.unschedule('calc-operating-rate');
        PERFORM cron.unschedule('daily-alm-check');
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_cron 스케줄 제거 스킵: %', SQLERRM;
END $$;


-- ============================================================================
-- 4. 유지 항목 (참고용, DROP 하지 않음)
-- ============================================================================
-- plc_data_reset_log          — PLC 카운터 리셋 감지 (교대 전환과 다른 개념, 유지)
-- plc_data_reset_snapshot     — 리셋 시점 주요 지표 캡처 (유지)
-- fn_production_reset_check() — 리셋 감지 트리거 (유지)
-- {group}_latest_view         — 새 SQL에 포함됨 (OR REPLACE로 갱신)
-- {group}_master_updated_at   — 새 SQL에 포함됨 (OR REPLACE로 갱신)


-- 완료 확인
DO $$
BEGIN
    RAISE NOTICE '=== DROP 스크립트 실행 완료 ===';
    RAISE NOTICE '다음 단계: publisher 재시작하여 새 custom_init.sql 적용';
END $$;
