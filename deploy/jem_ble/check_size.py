import asyncio
import asyncpg


async def check():
    conn = await asyncpg.connect(
        host='192.168.0.148', port=5432,
        user='user', password='neuro0901',
        database='neurosense', timeout=60
    )

    def fmt(b):
        if b == 0:
            return '0 B'
        for u in ['B', 'KB', 'MB', 'GB', 'TB']:
            if abs(b) < 1024:
                return f'{b:.1f} {u}'
            b /= 1024
        return f'{b:.1f} PB'

    # 1. 비압축 chunk 평균 (=1일 raw)
    rows = await conn.fetch("""
        SELECT hypertable_name,
               pg_total_relation_size(chunk_schema || '.' || chunk_name) as bytes
        FROM timescaledb_information.chunks
        WHERE hypertable_schema = 'jem_jh02'
          AND NOT is_compressed AND range_end <= now()
    """)
    uncomp_avg = {}
    for r in rows:
        uncomp_avg.setdefault(r['hypertable_name'], []).append(r['bytes'])
    for k in uncomp_avg:
        uncomp_avg[k] = sum(uncomp_avg[k]) / len(uncomp_avg[k])

    # 2. 실제 압축 비율 (hypertable_compression_stats)
    main_hts = [
        'plc_data_integrated', 'plc_product_data_integrated',
        'ble_data_integrated', 'plc_setting_data_integrated',
        'log_snapshot', 'alm_history'
    ]
    comp_info = {}
    for ht in main_hts:
        try:
            row = await conn.fetchrow(
                "SELECT before_compression_total_bytes as bef, "
                "after_compression_total_bytes as aft "
                "FROM hypertable_compression_stats($1)",
                f'jem_jh02.{ht}'
            )
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_schema = 'jem_jh02' "
                "AND hypertable_name = $1 AND is_compressed", ht
            )
            if row and row['bef'] > 0 and cnt > 0:
                comp_info[ht] = {
                    'ratio': row['aft'] / row['bef'],
                    'daily_comp': row['aft'] / cnt,
                    'daily_raw_from_stats': row['bef'] / cnt,
                }
        except Exception:
            pass

    # 3. 출력
    print("=" * 90)
    print(f"  {'테이블':40s} {'raw/일':>10s} {'압축후/일':>10s} {'절감률':>8s}")
    print("=" * 90)

    total_raw = 0
    total_comp = 0
    per_table = {}

    for ht in main_hts:
        raw = uncomp_avg.get(ht, 0)
        if raw == 0 and ht in comp_info:
            raw = comp_info[ht]['daily_raw_from_stats']

        if ht in comp_info:
            daily_comp = comp_info[ht]['daily_comp']
            ratio_str = f"{(1 - comp_info[ht]['ratio']) * 100:.1f}%"
        else:
            daily_comp = raw
            ratio_str = "N/A"

        total_raw += raw
        total_comp += daily_comp
        per_table[ht] = {'raw': raw, 'comp': daily_comp}
        print(f"  {ht:40s} {fmt(raw):>10s} {fmt(daily_comp):>10s} {ratio_str:>8s}")

    print("-" * 90)
    print(f"  {'합계':40s} {fmt(total_raw):>10s} {fmt(total_comp):>10s}")

    # BLE 분리
    ble_raw = per_table.get('ble_data_integrated', {}).get('raw', 0)
    ble_comp = per_table.get('ble_data_integrated', {}).get('comp', 0)
    plc_raw = total_raw - ble_raw
    plc_comp = total_comp - ble_comp

    # 4. 장기 추정
    print()
    print("=" * 90)
    print("  용량 추정 (비압축 윈도우 + 압축 보관)")
    print("  PLC: compression_after=1일 / BLE: compression_after=7일")
    print("=" * 90)
    print(f"  {'기간':20s} {'비압축(활성)':>14s} {'압축(보관)':>14s} {'합계':>14s}")
    print("-" * 90)

    scenarios = [
        ("당일 (1일)", plc_raw*1 + ble_raw*1, 0),
        ("1주 (7일)", plc_raw*1 + ble_raw*7, plc_comp*6),
        ("1개월 (30일)", plc_raw*1 + ble_raw*7, plc_comp*29 + ble_comp*23),
        ("6개월 (180일)", plc_raw*1 + ble_raw*7, plc_comp*179 + ble_comp*173),
        ("1년 (365일)", plc_raw*1 + ble_raw*7, plc_comp*364 + ble_comp*358),
        ("2년 (730일)", plc_raw*1 + ble_raw*7, plc_comp*729 + ble_comp*723),
        ("3년 (1095일)", plc_raw*1 + ble_raw*7, plc_comp*1094 + ble_comp*1088),
    ]

    for label, uncomp_w, comp_s in scenarios:
        total = uncomp_w + comp_s
        print(f"  {label:20s} {fmt(uncomp_w):>14s} {fmt(comp_s):>14s} {fmt(total):>14s}")

    print()
    print("  --- 비교: 압축 없이 raw만 쌓을 경우 ---")
    print(f"  {'1년':20s} {fmt(total_raw * 365):>14s}")
    print(f"  {'3년':20s} {fmt(total_raw * 365 * 3):>14s}")
    print("=" * 90)

    await conn.close()


asyncio.run(check())
