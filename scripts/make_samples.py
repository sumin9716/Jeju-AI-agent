"""합성 샘플 데이터 생성기.

실제 제주데이터허브 데이터를 내려받기 전에도 에이전트를 실행·시연·테스트할 수 있도록
레지스트리(jeju_agent/datahub/registry.yaml)의 컬럼 구조를 따르는 **가짜(합성) 데이터**를 만든다.
값은 계절성·요일·날씨 효과를 흉내 낸 난수이며 실제 통계가 아니다. 모든 파일은 data/samples/ 에 저장된다.

실행: python scripts/make_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jeju_agent.config import SAMPLES_DIR  # noqa: E402

SEED = 20261105
rng = np.random.default_rng(SEED)

# 읍면동: (시군구, 거주 기준인구, 근무 비율, 방문 기준, 관광 가중치, 가까운 관측소, 위도, 경도)
EMD = [
    ("노형동", "제주시", 55000, 0.55, 9000, 0.3, "제주", 33.486, 126.480),
    ("연동", "제주시", 43000, 0.70, 12000, 0.5, "제주", 33.489, 126.493),
    ("이도2동", "제주시", 50000, 0.60, 8000, 0.2, "제주", 33.497, 126.535),
    ("삼양동", "제주시", 24000, 0.35, 4000, 0.4, "제주", 33.523, 126.590),
    ("애월읍", "제주시", 38000, 0.40, 15000, 1.0, "고산", 33.463, 126.330),
    ("조천읍", "제주시", 26000, 0.35, 9000, 0.8, "제주", 33.535, 126.640),
    ("구좌읍", "제주시", 15000, 0.30, 8000, 0.9, "성산", 33.520, 126.850),
    ("한림읍", "제주시", 22000, 0.35, 7000, 0.8, "고산", 33.413, 126.270),
    ("중문동", "서귀포시", 13000, 0.45, 14000, 1.0, "서귀포", 33.250, 126.420),
    ("대정읍", "서귀포시", 23000, 0.35, 5000, 0.5, "고산", 33.222, 126.250),
    ("성산읍", "서귀포시", 15000, 0.30, 10000, 1.0, "성산", 33.450, 126.910),
    ("안덕면", "서귀포시", 12000, 0.30, 7000, 0.9, "서귀포", 33.260, 126.330),
    ("남원읍", "서귀포시", 19000, 0.30, 4000, 0.5, "서귀포", 33.280, 126.710),
    ("표선면", "서귀포시", 13000, 0.30, 5000, 0.7, "성산", 33.330, 126.830),
]
AGES = ["10대", "20대", "30대", "40대", "50대", "60대", "70대 이상"]
AGE_SHARE_RES = np.array([0.10, 0.13, 0.15, 0.18, 0.18, 0.14, 0.12])
AGE_SHARE_WORK = np.array([0.01, 0.17, 0.26, 0.26, 0.20, 0.08, 0.02])
AGE_SHARE_VISIT = np.array([0.06, 0.24, 0.26, 0.18, 0.14, 0.08, 0.04])
SEXES = ["남", "여"]
STATIONS = {"제주": (33.514, 126.530), "서귀포": (33.246, 126.565), "성산": (33.387, 126.880), "고산": (33.294, 126.163)}
DATES = pd.date_range("2024-01-01", "2024-12-31", freq="D")


def season_factor(d: pd.Timestamp, tourism: float) -> float:
    m = d.month
    base = {1: 0.85, 2: 0.85, 3: 0.95, 4: 1.05, 5: 1.1, 6: 1.0, 7: 1.15, 8: 1.3, 9: 1.05, 10: 1.15, 11: 1.0, 12: 0.9}[m]
    return 1 + (base - 1) * (0.4 + 0.6 * tourism)


def make_weather() -> pd.DataFrame:
    rows = []
    for name, (lat, lon) in STATIONS.items():
        offset = {"제주": 0.0, "서귀포": 1.2, "성산": -0.5, "고산": -0.3}[name]
        for d in DATES:
            doy = d.dayofyear
            mean_t = 16 + 10.5 * np.sin(2 * np.pi * (doy - 110) / 365) + offset + rng.normal(0, 1.6)
            rain_p = 0.22 + 0.25 * (d.month in (6, 7)) + 0.12 * (d.month in (8, 9))
            rain = 0.0
            if rng.random() < rain_p:
                rain = float(np.round(rng.gamma(1.4, 9.0), 1))
                if d.month in (7, 8, 9) and rng.random() < 0.06:
                    rain += float(np.round(rng.gamma(3, 30), 1))  # 집중호우/태풍
            wind = float(np.round(np.clip(rng.gamma(3.0, 1.1) + (1.5 if name == "고산" else 0), 0.5, 20), 1))
            rows.append(
                {
                    "날짜": d.strftime("%Y-%m-%d"),
                    "관측소명": name,
                    "평균기온": round(mean_t, 1),
                    "최고기온": round(mean_t + abs(rng.normal(4.5, 1.2)), 1),
                    "최저기온": round(mean_t - abs(rng.normal(4.0, 1.2)), 1),
                    "강수량": rain,
                    "평균풍속": wind,
                }
            )
    return pd.DataFrame(rows)


def make_floating(weather: pd.DataFrame) -> pd.DataFrame:
    rain = weather.set_index(["날짜", "관측소명"])["강수량"].to_dict()
    rows = []
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, _, _ in EMD:
        for d in DATES:
            ds = d.strftime("%Y-%m-%d")
            weekend = d.dayofweek >= 5
            holiday = ds in {"2024-01-01", "2024-02-09", "2024-02-10", "2024-02-11", "2024-02-12", "2024-03-01", "2024-05-05", "2024-05-06", "2024-05-15", "2024-06-06", "2024-08-15", "2024-09-16", "2024-09-17", "2024-09-18", "2024-10-03", "2024-10-09", "2024-12-25"}
            off = weekend or holiday
            r = rain.get((ds, stn), 0.0)
            rain_pen = 0.72 if r >= 20 else (0.88 if r >= 5 else 1.0)
            visit_mult = season_factor(d, tourism) * (1 + 0.45 * tourism if off else 1.0) * rain_pen * rng.normal(1, 0.06)
            work_mult = (0.35 if off else 1.0) * rng.normal(1, 0.03)
            res_mult = rng.normal(1, 0.015)
            for si, sex in enumerate(SEXES):
                sex_w = 0.5 + (0.01 if si == 1 else -0.01)
                for ai, age in enumerate(AGES):
                    resid = base_res * AGE_SHARE_RES[ai] * sex_w * res_mult
                    work = base_res * work_ratio * AGE_SHARE_WORK[ai] * sex_w * work_mult
                    visit = base_visit * AGE_SHARE_VISIT[ai] * sex_w * visit_mult
                    rows.append((ds, sgg, emd, sex, age, int(round(resid)), int(round(work)), int(round(visit))))
    return pd.DataFrame(rows, columns=["날짜", "시군구", "읍면동", "성별", "연령대", "거주인구", "근무인구", "방문자수"])


def make_bus(weather: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rain = weather.set_index(["날짜", "관측소명"])["강수량"].to_dict()
    stops, rows = [], []
    sid = 100
    names_pool = ["사거리", "입구", "주민센터", "초등학교", "농협", "보건소", "항", "해수욕장", "마을회관", "시장", "터미널", "공원"]
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, lat, lon in EMD:
        n_stops = 3 if base_res > 30000 else 2
        for k in range(n_stops):
            sid += 1
            stop_id = f"S{sid:04d}"
            stop_name = f"{emd} {names_pool[(sid + k) % len(names_pool)]}"
            s_lat = lat + rng.normal(0, 0.012)
            s_lon = lon + rng.normal(0, 0.012)
            stops.append({"정류소ID": stop_id, "정류소명": stop_name, "시군구": sgg, "읍면동": emd, "위도": round(s_lat, 5), "경도": round(s_lon, 5)})
            base_ride = (base_res * 0.012 + base_visit * 0.02 * tourism) / n_stops * rng.uniform(0.6, 1.4)
            for d in DATES:
                ds = d.strftime("%Y-%m-%d")
                off = d.dayofweek >= 5
                r = rain.get((ds, stn), 0.0)
                mult = season_factor(d, tourism) * (0.8 + 0.4 * tourism if off else 1.0) * (0.85 if r >= 10 else 1.0) * rng.normal(1, 0.12)
                ride = max(0, int(round(base_ride * mult)))
                alight = max(0, int(round(ride * rng.normal(0.95, 0.08))))
                rows.append((ds, stop_id, stop_name, emd, ride, alight))
    return pd.DataFrame(stops), pd.DataFrame(rows, columns=["날짜", "정류소ID", "정류소명", "읍면동", "승차인원", "하차인원"])


def make_card() -> pd.DataFrame:
    inds = {"식음료": 1.0, "소매": 0.8, "숙박": 0.6, "문화레저": 0.5, "교통": 0.4, "쇼핑": 0.7, "유흥": 0.2}
    months = pd.period_range("2017-01", "2018-12", freq="M")
    ages = ["20대", "30대", "40대", "50대", "60대 이상"]
    age_w = np.array([0.22, 0.28, 0.22, 0.17, 0.11])
    rows = []
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, _, _ in EMD:
        for p in months:
            mf = season_factor(p.to_timestamp(), tourism)
            growth = 1 + 0.08 * ((p.year - 2017) + p.month / 12)
            for ind, w in inds.items():
                for si, sex in enumerate(SEXES):
                    for ai, age in enumerate(ages):
                        cnt = base_visit * 0.9 * w * age_w[ai] * 0.5 * mf * growth * rng.normal(1, 0.1)
                        cnt = max(0, int(round(cnt)))
                        amt = int(round(cnt * rng.normal({"식음료": 28000, "소매": 19000, "숙박": 110000, "문화레저": 24000, "교통": 15000, "쇼핑": 42000, "유흥": 38000}[ind], 0.08)))
                        rows.append((str(p), emd, ind, sex, age, cnt, amt))
    return pd.DataFrame(rows, columns=["연월", "읍면동", "업종", "성별", "연령대", "이용건수", "이용금액"])


def make_restaurants() -> pd.DataFrame:
    types = ["한식", "카페", "분식", "일식", "중식", "양식", "호프/통닭", "기타"]
    type_w = np.array([0.34, 0.22, 0.08, 0.08, 0.06, 0.08, 0.09, 0.05])
    words1 = ["바다", "돌담", "감귤", "한라", "오름", "해녀", "별빛", "노을", "소나무", "파도", "유채", "동백"]
    words2 = {"한식": ["식당", "밥상", "국수", "흑돼지"], "카페": ["카페", "커피", "베이커리", "로스터스"], "분식": ["분식", "김밥"], "일식": ["스시", "초밥"], "중식": ["반점", "짬뽕"], "양식": ["파스타", "키친"], "호프/통닭": ["치킨", "호프"], "기타": ["푸드", "식당"]}
    rows = []
    for i in range(700):
        emd, sgg, base_res, work_ratio, base_visit, tourism, *_ = EMD[int(rng.integers(0, len(EMD)))]
        t = str(rng.choice(types, p=type_w))
        name = f"{rng.choice(words1)} {rng.choice(words2[t])}"
        # 최근 개업이 많도록 치우친 분포
        year = int(np.clip(2025 - rng.exponential(7), 1995, 2025))
        start = pd.Timestamp(year=year, month=int(rng.integers(1, 13)), day=int(rng.integers(1, 28)))
        closed = rng.random() < (0.42 if t == "카페" else 0.33)
        close_date = ""
        status = "영업"
        if closed:
            life_days = int(rng.gamma(2.0, 600))
            cd = start + pd.Timedelta(days=life_days)
            if cd < pd.Timestamp("2025-08-31"):
                close_date = cd.strftime("%Y-%m-%d")
                status = "폐업"
        rows.append(
            {
                "사업장명": name,
                "업태구분": t,
                "시군구": sgg,
                "읍면동": emd,
                "도로명주소": f"제주특별자치도 {sgg} {emd} 샘플로 {int(rng.integers(1, 300))}",
                "인허가일자": start.strftime("%Y-%m-%d"),
                "영업상태": status,
                "폐업일자": close_date,
            }
        )
    return pd.DataFrame(rows)


def make_visitors() -> pd.DataFrame:
    months = pd.period_range("2018-01", "2025-07", freq="M")
    purposes = {"관광": 0.78, "사업": 0.08, "친지방문": 0.07, "교육": 0.03, "기타": 0.04}
    rows = []
    for p in months:
        ts = p.to_timestamp()
        mf = season_factor(ts, 1.0)
        covid_dom = 1.0
        covid_for = 1.0
        if pd.Timestamp("2020-02-01") <= ts <= pd.Timestamp("2020-05-31"):
            covid_dom = 0.5
        elif pd.Timestamp("2020-06-01") <= ts <= pd.Timestamp("2022-12-31"):
            covid_dom = 1.15  # 해외여행 대체 수요
        if pd.Timestamp("2020-03-01") <= ts <= pd.Timestamp("2022-09-30"):
            covid_for = 0.03
        elif pd.Timestamp("2022-10-01") <= ts <= pd.Timestamp("2023-12-31"):
            covid_for = 0.45
        for grp, base, cf in (("내국인", 1_050_000, covid_dom), ("외국인", 120_000, covid_for)):
            for purpose, share in purposes.items():
                val = base * share * mf * cf * rng.normal(1, 0.05)
                rows.append((str(p), grp, purpose, int(round(val))))
    return pd.DataFrame(rows, columns=["연월", "구분", "방문목적", "입도객수"])


def make_spots() -> pd.DataFrame:
    spots = [
        ("성산일출봉", "서귀포시", 180000), ("만장굴", "제주시", 60000), ("천지연폭포", "서귀포시", 70000), ("주상절리", "서귀포시", 65000),
        ("한라산 어리목", "제주시", 30000), ("비자림", "제주시", 45000), ("섭지코지", "서귀포시", 50000), ("산방산", "서귀포시", 40000),
        ("용머리해안", "서귀포시", 35000), ("정방폭포", "서귀포시", 30000), ("천제연폭포", "서귀포시", 28000), ("제주민속촌", "서귀포시", 25000),
        ("에코랜드", "제주시", 40000), ("아쿠아플라넷", "서귀포시", 55000), ("카멜리아힐", "서귀포시", 30000), ("휴애리", "서귀포시", 20000),
        ("새별오름", "제주시", 25000), ("우도", "제주시", 90000), ("협재해수욕장", "제주시", 60000), ("함덕해수욕장", "제주시", 55000),
    ]
    months = pd.period_range("2019-01", "2024-12", freq="M")
    rows = []
    for name, sgg, base in spots:
        beach = "해수욕장" in name
        for p in months:
            ts = p.to_timestamp()
            mf = season_factor(ts, 1.0)
            if beach:
                mf *= 2.2 if ts.month in (7, 8) else 0.55
            covid = 0.45 if pd.Timestamp("2020-02-01") <= ts <= pd.Timestamp("2020-06-30") else 1.0
            rows.append((str(p), name, sgg, int(round(base * mf * covid * rng.normal(1, 0.08)))))
    return pd.DataFrame(rows, columns=["연월", "관광지명", "시군구", "유입자수"])


def make_agri() -> pd.DataFrame:
    # (품목, 품종, 출하 월, 기준가 원/kg, 변동)
    items = [
        ("감귤", "노지온주", [10, 11, 12, 1], 1800, 0.25), ("감귤", "하우스온주", [5, 6, 7, 8, 9], 5200, 0.2), ("감귤", "천혜향", [1, 2, 3], 4300, 0.2),
        ("당근", "일반", [11, 12, 1, 2, 3, 4], 1300, 0.3), ("양배추", "일반", [11, 12, 1, 2, 3, 4, 5], 700, 0.35), ("무", "월동무", [11, 12, 1, 2, 3, 4], 600, 0.35),
        ("브로콜리", "일반", [11, 12, 1, 2, 3, 4], 2600, 0.25), ("마늘", "남도", [5, 6, 7], 3900, 0.2), ("감자", "봄감자", [4, 5, 6], 1500, 0.25),
    ]
    rows = []
    for item, variety, months, base, vol in items:
        level = base * rng.normal(1, 0.1)
        for d in DATES:
            if d.month not in months or d.dayofweek == 6:
                continue
            level = level * (1 + rng.normal(0, 0.02)) * (1 + 0.003 * (base - level) / base)
            for grade, gm in (("특", 1.25), ("상", 1.0), ("보통", 0.78)):
                price = int(round(level * gm * rng.normal(1, 0.03) / 10) * 10)
                qty = int(round(rng.gamma(4, 1500) * gm))
                rows.append((d.strftime("%Y-%m-%d"), item, variety, grade, price, qty))
    return pd.DataFrame(rows, columns=["날짜", "품목", "품종", "등급", "평균가격", "거래량"])


def _emd_lookup() -> dict:
    return {e[0]: e for e in EMD}


def make_wifi() -> pd.DataFrame:
    places = ["버스정류장", "관광지", "공공시설", "해수욕장", "시장", "공원", "항만", "주민센터"]
    rows = []
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, lat, lon in EMD:
        n = int(10 + base_res / 4000 + base_visit / 1500 * tourism)
        for k in range(n):
            place = str(rng.choice(places, p=[0.3, 0.2 * tourism / 1.0 if tourism else 0.05, 0.15, 0.1, 0.05, 0.1, 0.05, 0.05] / np.sum([0.3, 0.2 * tourism / 1.0 if tourism else 0.05, 0.15, 0.1, 0.05, 0.1, 0.05, 0.05])))
            rows.append(
                {
                    "AP명": f"{emd}_{place}_{k + 1:02d}",
                    "설치장소": place,
                    "시군구": sgg,
                    "읍면동": emd,
                    "위도": round(lat + rng.normal(0, 0.015), 5),
                    "경도": round(lon + rng.normal(0, 0.015), 5),
                }
            )
    return pd.DataFrame(rows)


def make_ev() -> pd.DataFrame:
    rows = []
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, lat, lon in EMD:
        n = int(6 + base_res / 5000 + base_visit / 3000)
        for k in range(n):
            fast = rng.random() < 0.55
            rows.append(
                {
                    "충전소명": f"{emd} 충전소 {k + 1}",
                    "시군구": sgg,
                    "읍면동": emd,
                    "주소": f"제주특별자치도 {sgg} {emd} 샘플로 {int(rng.integers(1, 400))}",
                    "충전기유형": "급속" if fast else "완속",
                    "충전기수": int(rng.integers(1, 5)) if fast else int(rng.integers(2, 12)),
                    "위도": round(lat + rng.normal(0, 0.015), 5),
                    "경도": round(lon + rng.normal(0, 0.015), 5),
                }
            )
    return pd.DataFrame(rows)


def make_accommodations() -> pd.DataFrame:
    types = ["호텔", "콘도미니엄", "휴양펜션", "일반숙박", "호스텔"]
    rows = []
    for emd, sgg, base_res, work_ratio, base_visit, tourism, stn, lat, lon in EMD:
        n = int(4 + base_visit / 900 * tourism + base_res / 20000)
        for k in range(n):
            t = str(rng.choice(types, p=[0.25, 0.1, 0.35, 0.22, 0.08]))
            rooms = {"호텔": int(rng.integers(30, 300)), "콘도미니엄": int(rng.integers(40, 250)), "휴양펜션": int(rng.integers(3, 20)), "일반숙박": int(rng.integers(8, 40)), "호스텔": int(rng.integers(10, 60))}[t]
            rows.append(
                {
                    "업소명": f"{emd} {t} {k + 1}",
                    "업종": t,
                    "시군구": sgg,
                    "읍면동": emd,
                    "객실수": rooms,
                    "영업상태": "영업" if rng.random() < 0.9 else "휴업",
                    "위도": round(lat + rng.normal(0, 0.02), 5),
                    "경도": round(lon + rng.normal(0, 0.02), 5),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    weather = make_weather()
    outputs = {
        "daily_weather_sample.csv": weather,
        "floating_population_emd_sample.csv": make_floating(weather),
        "card_domestic_tourist_sample.csv": make_card(),
        "restaurants_sample.csv": make_restaurants(),
        "visitors_by_purpose_sample.csv": make_visitors(),
        "tourist_spots_domestic_sample.csv": make_spots(),
        "agri_price_daily_sample.csv": make_agri(),
    }
    stops, bus = make_bus(weather)
    outputs["bus_stops_sample.csv"] = stops
    outputs["bus_stop_daily_users_sample.csv"] = bus
    # 위치 데이터 3종은 기존 샘플의 난수열을 바꾸지 않도록 마지막에 생성한다.
    outputs["public_wifi_ap_sample.csv"] = make_wifi()
    outputs["ev_charging_stations_sample.csv"] = make_ev()
    outputs["tourist_accommodations_sample.csv"] = make_accommodations()
    for name, df in outputs.items():
        path = SAMPLES_DIR / name
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"{name:40s} {len(df):>8,} rows  {path.stat().st_size / 1024:8.0f} KB")
    (SAMPLES_DIR / "README.md").write_text(
        "# 합성 샘플 데이터 (실제 데이터 아님)\n\n"
        "이 폴더의 CSV 는 `scripts/make_samples.py` 가 난수로 만든 **가짜 데이터**입니다. "
        "제주데이터허브 실제 데이터의 컬럼 구조(레지스트리 기준)를 흉내 내어 에이전트의 동작을 시연·테스트하기 위한 것이며, "
        "값은 실제 통계가 아닙니다. 실제 분석은 `.env` 에 프로젝트 키를 넣어 Open API 로 수집하거나 "
        "허브에서 내려받은 파일을 `data/raw/` 에 넣어 수행하세요.\n\n"
        f"- 생성 시드: {SEED}\n- 기간: 2024년(일 단위), 카드 2017~2018(월), 입도객 2018~2025(월), 관광지 2019~2024(월)\n"
        "- 위치 데이터(와이파이 AP·전기차 충전소·관광 숙박업)는 읍면동 중심 좌표 주변에 난수로 배치한 가짜 위치입니다.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
