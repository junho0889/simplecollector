-- ============================================================================
-- 기존 배포 환경 정리 스크립트
-- ============================================================================
-- 용도: 새 custom_init.sql 배포 전, 충돌/대체되는 기존 객체를 제거
-- 실행: publisher 중지 상태에서 DB에 직접 실행
--   psql -h <host> -U neuro0901 -d neurosense -f drop_legacy_objects.sql
--
-- 실행 순서:
--   1. publisher 중지 (docker stop)
--   2. 이 스크립트 실행
--   3. publisher 재시작 → 새 custom_init.sql 자동 적용
--   4. seed_data.sql 수동 실행 (초기 데이터)
--   5. backfill_statistics.sql 수동 실행 (과거 통계 복원)
-- ============================================================================


-- ============================================================================
-- 1. production_hourly 제거 (CA 또는 일반 테이블)
-- ============================================================================
-- production_hourly: 새 SQL에서 tb_prod_hourly로 대체
-- CA일 수도, 이전 마이그레이션으로 이미 일반 테이블일 수도 있음
DO $$
BEGIN
    -- hypertable/CA 정책 제거 시도 (없으면 스킵)
    BEGIN
        PERFORM remove_compression_policy('jem_jh02.production_hourly', if_not_exists => TRUE);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
    BEGIN
        PERFORM remove_retention_policy('jem_jh02.production_hourly', if_not_exists => TRUE);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;

    -- CA면 MATERIALIZED VIEW로, 아니면 TABLE로 DROP
    IF EXISTS (
        SELECT 1 FROM pg_matviews
        WHERE schemaname = 'jem_jh02' AND matviewname = 'production_hourly'
    ) THEN
        DROP MATERIALIZED VIEW jem_jh02.production_hourly CASCADE;
        RAISE NOTICE '  DROP MATERIALIZED VIEW: production_hourly';
    ELSE
        DROP TABLE IF EXISTS jem_jh02.production_hourly CASCADE;
        RAISE NOTICE '  DROP TABLE: production_hourly';
    END IF;
END $$;


-- ============================================================================
-- 2. 옛 이름 테이블 DROP (custom_init.sql이 새 이름으로 재생성)
-- ============================================================================
-- 옛 이름 → 새 이름 (custom_init.sql에서 CREATE TABLE IF NOT EXISTS)
--   shift_config             → tb_info_shift
--   production_target        → tb_info_target
--   target_history           → tb_hist_target
--   shift_history            → tb_hist_shift
--   production_shift_current → tb_prod_shift_current
--   production_shift_history → tb_prod_shift_history
--   production_daily         → tb_prod_daily
-- 옛 이름
DROP TABLE IF EXISTS jem_jh02.shift_config CASCADE;
DROP TABLE IF EXISTS jem_jh02.shift_config_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.production_target CASCADE;
DROP TABLE IF EXISTS jem_jh02.production_target_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.target_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.shift_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.company CASCADE;
DROP TABLE IF EXISTS jem_jh02.factory CASCADE;
DROP TABLE IF EXISTS jem_jh02.line CASCADE;
DROP TABLE IF EXISTS jem_jh02.custom_init_log CASCADE;
DROP TABLE IF EXISTS jem_jh02.production_shift_current CASCADE;
DROP TABLE IF EXISTS jem_jh02.production_shift_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.production_daily CASCADE;

-- 새 이름 (이미 생성되어 있으면 DROP 후 재생성)
DROP TABLE IF EXISTS jem_jh02.tb_info_shift CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_target CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_hist_target CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_hist_shift CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_shift_current CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_shift_history CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_hourly CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_daily CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_mode_change CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_alm_shift CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_prod_alm_daily CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_company CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_factory CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_line CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_sql_log CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_hist_cycle_time CASCADE;
DROP TABLE IF EXISTS jem_jh02.tb_info_cycle_time CASCADE;


-- ============================================================================
-- 3. 옛 뷰 DROP (새 이름 vw_* 로 재생성됨)
-- ============================================================================
-- 옛 {group}_latest_view → 새 vw_{group}_latest (이름 변경됨, 고아 뷰 제거)
DROP VIEW IF EXISTS jem_jh02.plc_data_latest_view CASCADE;
DROP VIEW IF EXISTS jem_jh02.alm_latest_view CASCADE;
DROP VIEW IF EXISTS jem_jh02.log_latest_view CASCADE;
DROP VIEW IF EXISTS jem_jh02.action_latest_view CASCADE;

-- 옛 v_* 뷰 → 새 vw_* 뷰 (custom_init.sql이 CREATE OR REPLACE로 재생성)
DROP VIEW IF EXISTS jem_jh02.v_production_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_production_monthly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_production_yearly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_production_shift_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_production_shift_monthly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_alarm_downtime CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_alarm_ranking_daily CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_alarm_ranking_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.v_alarm_ranking_monthly CASCADE;

-- 새 vw_* 뷰 DROP (컬럼 변경 시 CREATE OR REPLACE가 실패할 수 있으므로)
DROP VIEW IF EXISTS jem_jh02.vw_prod_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_prod_monthly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_prod_yearly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_prod_shift_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_prod_shift_monthly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_alarm_downtime CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_alarm_ranking_daily CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_alarm_ranking_weekly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_alarm_ranking_monthly CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_action_latest CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_alm_latest CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_log_latest CASCADE;
DROP VIEW IF EXISTS jem_jh02.vw_plc_data_latest CASCADE;


-- ============================================================================
-- 4. 옛 함수/트리거 DROP (새 설계로 대체)
-- ============================================================================

-- 4-1. 기존 불필요 객체 (이전 버전에서 이미 대체됨)
DROP TABLE IF EXISTS jem_jh02.alm_shift_summary CASCADE;
DROP FUNCTION IF EXISTS jem_jh02.fn_compute_alm_shift_summary(TIMESTAMPTZ, TIMESTAMPTZ, VARCHAR, DATE);
DROP FUNCTION IF EXISTS jem_jh02.fn_daily_alm_check();
DROP TABLE IF EXISTS jem_jh02.alm_duration_log CASCADE;
DROP PROCEDURE IF EXISTS jem_jh02.sp_calc_alm_duration(TIMESTAMPTZ, TIMESTAMPTZ);
DROP TABLE IF EXISTS jem_jh02.operating_rate_hourly CASCADE;
DROP PROCEDURE IF EXISTS jem_jh02.sp_calc_operating_rate(TIMESTAMPTZ, TIMESTAMPTZ);

-- 4-2. PLC 리셋 감지 (fn_production_shift_tracker에 통합됨)
DROP TRIGGER IF EXISTS trg_plc_data_production_reset ON jem_jh02.plc_data_latest;
DROP FUNCTION IF EXISTS jem_jh02.fn_production_reset_check();
DROP TABLE IF EXISTS jem_jh02.plc_data_reset_log CASCADE;
DROP TABLE IF EXISTS jem_jh02.plc_data_reset_snapshot CASCADE;

-- 4-3. 트리거 먼저 DROP (함수 의존성 해제)
DROP TRIGGER IF EXISTS trg_production_shift ON jem_jh02.plc_data_latest;
DROP TRIGGER IF EXISTS trg_downtime_alm ON jem_jh02.alm_latest;
DROP TRIGGER IF EXISTS trg_downtime_action ON jem_jh02.action_latest;
DROP TRIGGER IF EXISTS trg_hourly_snapshot ON jem_jh02.production_shift_current;
DROP TRIGGER IF EXISTS trg_hourly_snapshot ON jem_jh02.tb_prod_shift_current;
DROP TRIGGER IF EXISTS trg_daily_aggregate ON jem_jh02.production_shift_history;
DROP TRIGGER IF EXISTS trg_daily_aggregate ON jem_jh02.tb_prod_shift_history;
DROP TRIGGER IF EXISTS trg_prod_mode_change ON jem_jh02.plc_data_latest;
DROP TRIGGER IF EXISTS trg_prod_alm_tracker ON jem_jh02.alm_history;
DROP TRIGGER IF EXISTS trg_tb_hist_shift ON jem_jh02.shift_config;
DROP TRIGGER IF EXISTS trg_tb_hist_shift ON jem_jh02.tb_info_shift;
DROP TRIGGER IF EXISTS trg_tb_hist_target ON jem_jh02.production_target;
DROP TRIGGER IF EXISTS trg_tb_hist_target ON jem_jh02.tb_info_target;
DROP TRIGGER IF EXISTS trg_tb_hist_cycle_time ON jem_jh02.tb_info_cycle_time;

-- 4-4. 함수 DROP (트리거 제거 후 안전하게 삭제)
DROP FUNCTION IF EXISTS jem_jh02.fn_get_current_shift();
DROP FUNCTION IF EXISTS jem_jh02.fn_get_current_shift(INTEGER);
DROP FUNCTION IF EXISTS jem_jh02.fn_production_shift_tracker();
DROP FUNCTION IF EXISTS jem_jh02.fn_downtime_tracker();
DROP FUNCTION IF EXISTS jem_jh02.fn_hourly_snapshot();
DROP FUNCTION IF EXISTS jem_jh02.fn_daily_aggregate();
DROP FUNCTION IF EXISTS jem_jh02.fn_backfill_daily_statistics();
DROP FUNCTION IF EXISTS jem_jh02.fn_tb_hist_shift();
DROP FUNCTION IF EXISTS jem_jh02.fn_tb_hist_target();
DROP FUNCTION IF EXISTS jem_jh02.fn_log_init(TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS jem_jh02.fn_prod_mode_change();
DROP FUNCTION IF EXISTS jem_jh02.fn_prod_alm_tracker();
DROP FUNCTION IF EXISTS jem_jh02.fn_tb_hist_cycle_time();


-- ============================================================================
-- 5. pg_cron 스케줄 제거 (있을 경우)
-- ============================================================================
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
-- 완료
-- ============================================================================
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '=== 정리 스크립트 실행 완료 ===';
    RAISE NOTICE '';
    RAISE NOTICE '다음 단계:';
    RAISE NOTICE '  1. publisher 재시작 → 새 custom_init.sql 자동 적용';
    RAISE NOTICE '  2. psql -f seed_data.sql → 초기 데이터 투입';
    RAISE NOTICE '  3. psql -f backfill_statistics.sql → 과거 통계 복원';
END $$;
