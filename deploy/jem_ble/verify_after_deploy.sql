-- ============================================================================
-- JEM+BLE 배포 후 검증 스크립트
-- ============================================================================
-- 사용법: psql -h 10.8.0.4 -p 15432 -U user -d neurosense -f verify_after_deploy.sql
-- ============================================================================

\echo '================================================================'
\echo '  1. VIEW 검증 (13개)'
\echo '================================================================'

SELECT v.viewname,
       CASE WHEN c.relname IS NOT NULL THEN 'OK' ELSE 'MISSING' END as status
FROM (VALUES
  ('vw_plc_data_latest'), ('vw_alm_latest'), ('vw_log_latest'), ('vw_action_latest'),
  ('vw_alarm_downtime'), ('vw_alarm_ranking_daily'), ('vw_alarm_ranking_weekly'),
  ('vw_alarm_ranking_monthly'), ('vw_prod_weekly'), ('vw_prod_monthly'),
  ('vw_prod_yearly'), ('vw_prod_shift_weekly'), ('vw_prod_shift_monthly')
) v(viewname)
LEFT JOIN pg_class c ON c.relname = v.viewname AND c.relnamespace = 'jem_jh02'::regnamespace
ORDER BY v.viewname;

-- VIEW 실제 조회 가능 여부
\echo ''
\echo '  VIEW 조회 테스트:'
SELECT 'vw_plc_data_latest' as view_name, COUNT(*) as rows FROM jem_jh02.vw_plc_data_latest;
SELECT 'vw_alm_latest' as view_name, COUNT(*) as rows FROM jem_jh02.vw_alm_latest;
SELECT 'vw_log_latest' as view_name, COUNT(*) as rows FROM jem_jh02.vw_log_latest;
SELECT 'vw_action_latest' as view_name, COUNT(*) as rows FROM jem_jh02.vw_action_latest;

\echo ''
\echo '================================================================'
\echo '  2. FUNCTION 검증 (26개)'
\echo '================================================================'

SELECT f.func_name,
       CASE WHEN r.routine_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END as status
FROM (VALUES
  ('action_master_updated_at'), ('alm_master_updated_at'),
  ('fn_alm_history_on_insert'), ('fn_alm_history_on_update'),
  ('fn_backfill_daily_statistics'), ('fn_daily_aggregate'),
  ('fn_downtime_tracker'), ('fn_get_current_shift'),
  ('fn_home_alm'), ('fn_home_process'), ('fn_home_process_detail'),
  ('fn_home_production_hourly_chart'), ('fn_home_production_status'),
  ('fn_hourly_snapshot'), ('fn_log_init'),
  ('fn_log_snapshot_on_update'), ('fn_prod_alm_tracker'),
  ('fn_prod_mode_change'), ('fn_production_shift_tracker'),
  ('fn_production_target_history'), ('fn_shift_config_history'),
  ('fn_tb_hist_cycle_time'), ('fn_tb_hist_shift'), ('fn_tb_hist_target'),
  ('log_master_updated_at'), ('plc_data_master_updated_at')
) f(func_name)
LEFT JOIN information_schema.routines r
  ON r.routine_schema = 'jem_jh02' AND r.routine_name = f.func_name
ORDER BY f.func_name;

\echo ''
\echo '================================================================'
\echo '  3. TRIGGER 검증 (20개)'
\echo '================================================================'

SELECT t.trg_name, t.tbl_name,
       CASE WHEN it.trigger_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END as status
FROM (VALUES
  ('trg_downtime_action', 'action_latest'),
  ('trg_action_master_updated_at', 'action_master'),
  ('trg_prod_alm_tracker', 'alm_history'),
  ('trg_alm_history_insert', 'alm_latest'),
  ('trg_alm_history_update', 'alm_latest'),
  ('trg_downtime_alm', 'alm_latest'),
  ('trg_alm_master_updated_at', 'alm_master'),
  ('trg_log_snapshot_update', 'log_latest'),
  ('trg_log_master_updated_at', 'log_master'),
  ('trg_prod_mode_change', 'plc_data_latest'),
  ('trg_production_shift', 'plc_data_latest'),
  ('trg_plc_data_master_updated_at', 'plc_data_master'),
  ('trg_tb_hist_cycle_time', 'tb_info_cycle_time'),
  ('trg_tb_hist_shift', 'tb_info_shift'),
  ('trg_tb_hist_target', 'tb_info_target'),
  ('trg_hourly_snapshot', 'tb_prod_shift_current'),
  ('trg_daily_aggregate', 'tb_prod_shift_history')
) t(trg_name, tbl_name)
LEFT JOIN information_schema.triggers it
  ON it.trigger_schema = 'jem_jh02' AND it.trigger_name = t.trg_name AND it.event_object_table = t.tbl_name
ORDER BY t.tbl_name, t.trg_name;

\echo ''
\echo '================================================================'
\echo '  4. BLE 테이블 생성 확인 (신규)'
\echo '================================================================'

SELECT t.tbl_name,
       CASE WHEN c.relname IS NOT NULL THEN 'OK' ELSE 'NOT CREATED' END as status
FROM (VALUES
  ('ble_master'), ('ble_data_master'), ('ble_data_latest'), ('ble_data_integrated')
) t(tbl_name)
LEFT JOIN pg_class c ON c.relname = t.tbl_name AND c.relnamespace = 'jem_jh02'::regnamespace
ORDER BY t.tbl_name;

\echo ''
\echo '================================================================'
\echo '  5. Master Sync 확인'
\echo '================================================================'

SELECT 'plc_master' as tbl, COUNT(*) as rows FROM jem_jh02.plc_master
UNION ALL SELECT 'plc_data_master', COUNT(*) FROM jem_jh02.plc_data_master
UNION ALL SELECT 'alm_master', COUNT(*) FROM jem_jh02.alm_master
UNION ALL SELECT 'log_master', COUNT(*) FROM jem_jh02.log_master
UNION ALL SELECT 'action_master', COUNT(*) FROM jem_jh02.action_master
UNION ALL SELECT 'ble_data_master', COUNT(*) FROM jem_jh02.ble_data_master
ORDER BY 1;

\echo ''
\echo '================================================================'
\echo '  6. 데이터 흐름 확인'
\echo '================================================================'

SELECT 'plc_data_integrated' as tbl, COUNT(*) as rows, MAX(timestamp) as latest, now()-MAX(timestamp) as lag
FROM jem_jh02.plc_data_integrated
UNION ALL
SELECT 'alm_latest', COUNT(*), MAX(timestamp), now()-MAX(timestamp)
FROM jem_jh02.alm_latest
UNION ALL
SELECT 'plc_data_latest', COUNT(*), MAX(timestamp), now()-MAX(timestamp)
FROM jem_jh02.plc_data_latest;

\echo ''
\echo '================================================================'
\echo '  7. 기존 데이터 보존 확인'
\echo '================================================================'

SELECT MIN(timestamp) as earliest_data,
       MAX(timestamp) as latest_data,
       COUNT(*) as total_rows,
       pg_size_pretty(pg_total_relation_size('jem_jh02.plc_data_integrated')) as size
FROM jem_jh02.plc_data_integrated;

\echo ''
\echo '  완료'
\echo '================================================================'
