# Portfolio Dashboard V2 기능 명세서

- **버전:** 0.12 초안
- **작성 기준일:** 2026-10-19
- **변경 이력:** v0.12 — 사용자 최종 승인: 추적 손절 활성 조건(+1R), 고점 기준(진입 이후 최고 High), ATR 기준(ATR(22)×3), 비하향 원칙, 완료 일봉 종가 기준 공식 청산 신호 확정. v0.11의 패턴/Entry Quality/PP/Initial Risk 결정을 계승.
- **v0.11 이력:** 패턴 기준 초안을 V2 초기 기본값으로 채택; Entry Quality GOOD/CAUTION/WAIT 경계 설정; PP의 고정 MA 거리 필터 제거; Initial Risk ATR(14)×1.5 확정; 초기 손절과 상승형 ATR 추적 손절 동시 지원
- **v0.10 이력:** VCP와 클래식 베이스 패턴이 동시에 유효하면 VCP를 단일 기본 표시/활성 Candidate Pivot으로 우선 선택; 두 라벨과 두 피봇을 동시에 표시하지 않음
- **목적:** 기존 Streamlit/Supabase 포트폴리오 대시보드의 투자 규칙과 사용자 기능을 보존하면서, 계산 로직·데이터·UI를 분리한 V2를 병렬 구축한다.
- **상태 표기:**
  - **[확정]** 사용자와 합의한 원칙
  - **[초안]** V2 기본 설계안. 구현 전에 검토 가능
  - **[확인 필요]** 기존 코드/DB/실사용 동작을 더 확인해야 하는 항목
  - **[후순위]** 핵심 기능 안정화 후 구현할 항목

---

## 1. 제품 목표와 설계 원칙

### 1.1 목표

1. 주도주·모멘텀 중심의 투자 판단을 지원한다.
2. 패턴, 후보 피봇, 실제 돌파 피봇, 진입 단계, 위험도를 서로 혼동하지 않도록 구분한다.
3. 동일 종목·동일 기준 시점·동일 데이터에는 어느 화면에서든 같은 계산 결과를 제공한다.
4. 과거 시점 계산에서 미래 데이터를 사용하지 않는다.
5. 매매 이력과 과거 돌파 이벤트를 보존하고, 새로운 패턴 계산이 과거 기록을 덮어쓰지 않도록 한다.
6. 새 기능을 추가할 때 기존 계산을 복제하지 않고 공통 엔진을 사용한다.
7. Supabase/실제 거래 데이터를 보호하며, V2는 기존 앱과 병렬 검증 후 전환한다.

### 1.2 필수 아키텍처 원칙

- **[확정] 단일 계산 출처:** 패턴·피봇·Stage·Risk 계산은 공통 엔진에서 한 번 계산하고, UI는 결과를 표시한다.
- **[확정] UI와 도메인 로직 분리:** Streamlit 화면 함수 안에 핵심 매매 판정 로직을 중복 구현하지 않는다.
- **[확정] 데이터 기준일 명시:** 모든 계산 결과는 `as_of_date` 및 데이터의 마지막 완료 거래일을 함께 가진다.
- **[확정] 과거 시점 재현 가능:** 날짜 T 결과는 T일까지의 정보만 사용한다.
- **[확정] 과거 이벤트와 현재 후보 분리:** `Breakout Pivot`은 과거 실제 돌파 이벤트의 기준이며, `Candidate Pivot`은 현재 구조에서 계산한 후보이다.
- **[초안] 계산과 저장 분리:** 순수 계산 함수는 DB를 직접 수정하지 않는다. 읽기/계산/기록은 별도 서비스로 둔다.
- **[확인 필요]** 현재 Supabase 테이블·뷰·RPC 및 외부 API 의존성을 전체 조사해 V2 데이터 명세를 확정한다.

---

## 2. 사용자와 투자 철학

### 2.1 핵심 투자 방향

- 주도주와 강한 모멘텀 종목을 우선한다.
- 주가 모멘텀, 순위 모멘텀, 외국인 수급 점수 등 프로젝트에 이미 존재하는 리더십 지표를 활용하되, 실제 구현과 데이터 소스를 확인해 명세한다.
- Piotroski F-score 등 펀더멘털 필터는 모멘텀 후보를 보완하는 용도로 사용한다.
- 모든 관심종목을 무조건 장기 보유 대상으로 보지 않는다. 장기 핵심 보유(Core)와 위성(Satellite)/전술 포지션을 구분할 수 있어야 한다.
- 매수 타이밍뿐 아니라 추가매수·축소·청산 위험도 함께 관리한다.
- 레짐이 약하거나 시장 폭이 나쁠 때는 진입 신호를 더 보수적으로 해석한다.

### 2.2 의사결정 표시 원칙

- 모니터의 `GOOD`은 셋업/진입 품질 판정이지, 무조건 시장가 매수 명령이 아니다.
- Stage, Live State, Live Entry Quality는 서로 다른 질문에 답하므로 하나의 상태로 합치지 않는다.
- `—`와 `DATA INCOMPLETE`는 의미가 다르다. 값이 존재하지 않는 경우와 데이터가 부족해 계산할 수 없는 경우를 구분한다.
- 사용자가 이해할 수 있도록 핵심 판정 사유와 데이터 기준을 제공한다.

---

## 3. 애플리케이션 화면/기능 인벤토리

현재 코드에서 확인되는 주요 메뉴를 기준으로 작성했다. 2026-10-19 사용자 결정에 따라 Daily Direction과 History만 V2 기본 UI에서 제거하고, 나머지 기존 UI는 우선 유지한다.

| 기능 ID | 화면 | 역할 | 상태 |
|---|---|---|---|
| UI-01 | Overview | 실제 보유 종목 기반 포트폴리오 개요 | [확인 필요] 상세 위젯 인벤토리 필요 |
| UI-02 | Holdings | 실제 보유·비중·손익 조회/관리 | [확인 필요] |
| UI-03 | Market Regime | 시장 국면, 시장 폭, 방어/공격 환경 판단 | [확정 방향] |
| UI-04 | Momentum Monitor | 종목별 패턴·피봇·Stage·Live Quality 종합 모니터 | [핵심] |
| UI-05 | App Monitor | 데이터 갱신·상태·캐시·실행 성능 진단 | [확정 방향] 유지 |
| UI-06 | Quality Recovery | 품질/회복 관련 종목 필터 | [확정 방향] 유지 |
| UI-07 | Pattern Scanner | 패턴 탐색 상세 화면 | [V2 우선 유지 확정] 상세 구성 인벤토리 필요 |
| UI-08 | ADD Monitor | 보유 종목의 추가매수 후보/상태 감시 | [핵심] |
| UI-09 | PP Journal | Pocket Pivot 신호, 실제 거래, 성과 통계 | [핵심] |
| UI-10 | Daily Direction | 보유 종목의 최근 일봉 방향 | [V2에서 제거 확정] |
| UI-11 | Performance | 기간별 포트폴리오 수익률 및 QQQ/SPY 비교 | [확인 필요] |
| UI-12 | Allocation | 포트폴리오 배분 및 보유 비중 | [확정 방향] 유지 |
| UI-13 | History | 기록/스냅샷 이력 | [V2에서 제거 확정] |
| UI-14 | Chart | 종목 차트/레벨 확인 | [확인 필요] |
| UI-15 | Leaders | 주도주·섹터 리더십 맵 | [V2 우선 유지 확정] 상세 구성 인벤토리 필요 |

**[확정: 2026-10-19] UI 범위:** V2 기본 UI에서 `Daily Direction`과 `History`를 제거한다. 이 두 항목 외에는 현재 기능 인벤토리에 포함된 화면을 우선 유지한다. 따라서 `Pattern Scanner`, `Chart`, `Leaders`, `App Monitor`, `Quality Recovery`, `Allocation`, `PP Journal`, `ADD Monitor` 등을 지금 단계에서 임의로 삭제하거나 합치지 않는다. 화면 통합/정리는 기능 명세와 화면별 위젯 인벤토리를 검토한 뒤 별도 결정한다. `Notes`는 현재 인벤토리에 별도 화면으로 정의되어 있지 않으므로, 현행 앱의 실제 메뉴 여부는 UI 인벤토리 단계에서 확인하되 이번 결정만으로 새로운 Notes 화면을 추가하지 않는다.

---

## 4. 데이터 기준과 시간 규칙

### DATA-01. 완료된 일봉

- **[확정]** EOD 패턴·피봇 계산에는 가장 최근의 완료된 일봉을 포함한다.
- 미완성 당일 봉을 EOD 데이터처럼 사용하지 않는다.
- 이미 미완성 봉을 제거한 데이터에 대해 하위 함수가 마지막 행을 다시 무조건 제거하지 않도록 한다.
- 30분/5분 등 장중 데이터는 장중 모니터에서 별도로 사용하며, 완료 일봉과 혼합하지 않는다.

### DATA-02. 과거 날짜 계산

- **[확정]** 날짜 T의 신호를 재생성할 때 T 이후의 일봉/뉴스/상태를 사용하지 않는다.
- 모든 일봉은 거래소 현지 날짜를 보존하고, 표시 시간대(예: ET)와 내부 기준일을 구분한다.
- 기준 날짜·마지막 완료 봉·가격 소스가 UI에서 추적 가능해야 한다.

### DATA-03. 데이터 누락

- 가격 데이터 부족, 외부 API 실패, 캐시 오래됨, 패턴 미검출을 서로 다른 상태로 표시한다.
- 계산 실패를 정상적인 `BASE`로 조용히 치환하지 않는다.
- 모니터의 각 주요 행은 계산에 사용한 `as_of_date`를 알 수 있어야 한다.

---

## 5. 패턴 및 피봇 엔진

### 5.1 패턴 엔진의 출력 계약

모든 종목/기준일에 대해 엔진은 최소한 다음 필드를 반환한다.

- `pattern_name` / `pattern_status`
- `pattern_start_date` 또는 패턴 기준 구간
- `pattern_upper`
- `candidate_pivot`
- `candidate_pivot_source` (`PATTERN`, `20D`, `REBASE_FORMING` 등 명시적 enum)
- `twenty_day_reference`
- `breakout_pivot`
- `breakout_pivot_source`
- `next_pivot` 및 `next_pivot_source`
- `rebase_started_at`, `rebase_low`, `rebase_low_date` (재베이스 상태인 경우)
- `as_of_date`, `data_quality`, `explanation/reason_codes`

필드 이름과 화면 표시 문구는 최종 스키마 설계 단계에서 일관되게 정한다.

### 5.2 지원 패턴

현재 코드 및 과거 합의 기준으로 검토할 패턴 목록:

- Double Bottom
- Cup & Handle
- Cup Without Handle
- Flat Base
- Ascending Base
- Saucer Base
- High Tight Flag
- Base-on-Base
- VCP (Volatility Contraction Pattern): 완성형 베이스 패턴과 구별되는 독립 수축 셋업; §5.8의 수축·거래량·pivot 규칙 적용
- BASE FORMING (유효한 패턴이 완성되지 않은 상태)

**[확인 필요]** 실제 코드가 감지하는 전체 패턴 목록, 각 패턴의 데이터 길이/변동률/수축 조건, 패턴별 피봇 정의를 코드에서 추출해 표준화한다. IPO Base는 사용자가 원하지 않으므로 추가하지 않는다.

### 5.3 Candidate Pivot

- **[확정]** 현재 구조를 기준으로 계산하는 후보 피봇이다.
- 패턴이 유효하면 패턴 구조에서 계산한다.
- 패턴이 없고 재베이스 중이면 Candidate Pivot을 임의 생성하지 않는다. `—`로 표시하고 `20D Reference`는 참고선으로 별도 제공한다.
- Candidate Pivot이 과거 피봇 값과 같다고 가정하지 않는다. 현재 패턴이 바뀌면 Candidate Pivot은 바뀔 수 있다.
- 기존 패턴의 구조가 가격 하락으로 무효화된 경우 이전 Candidate Pivot을 현재 매수 후보로 계속 표시하지 않는다.

### 5.4 Breakout Pivot

- **[확정]** 실제 돌파 이벤트 시점에 캡처한 기준 가격이다.
- 이벤트가 기록된 후, 해당 돌파 사이클이 종료되기 전까지 후속 패턴의 `Pattern Upper`나 새 Candidate Pivot으로 덮어쓰지 않는다.
- 돌파 실패 또는 공식 사이클 종료 시 역사적 값은 보존하되, 현재 활성 Breakout Pivot인지/과거 이력인지 명확히 구분한다.
- Breakout Pivot의 원래 가격과 이벤트 날짜를 저장한다. UI의 현재 후보 피봇과 분리해 표시한다.

## 5.5 Next Pivot

- **[사용자 확정 방향]** 활성 Breakout Pivot이 있는 사이클에서, 기존 돌파 이벤트 이후 새롭게 형성된 **유효 패턴**이 Candidate Pivot을 산출하면 그 Candidate Pivot을 즉시 `Next Pivot`으로 표시한다. 임의의 추가 안정성 대기일은 두지 않는다.
- 여기서 “유효 패턴”은 패턴별 구조 조건과 최소 데이터 조건을 이미 충족해 Pattern Engine이 정식 패턴으로 인정한 상태를 뜻한다. 단순한 임시 고점/후보 가격은 Next Pivot으로 승격하지 않는다.
- Next Pivot은 이후 패턴이 유효하게 재계산되는 동안 업데이트될 수 있다. 단, 과거 Breakout Pivot은 변경하지 않는다.
- 현재 활성 Breakout Pivot을 유지하는 동안 가격이 Next Pivot을 실제로 돌파하면 그 날짜에 새 돌파 이벤트를 기록하고, 새 Next Pivot 가격을 새 활성 Breakout Pivot으로 캡처한다. 이전 Breakout Pivot은 과거 이벤트로 보존한다.
- 새 패턴이 확인된 시점에 현재 종가가 이미 Candidate Pivot 위에 있다면 과거에 돌파한 것으로 소급 기록하지 않는다. 이후 데이터에서 가격이 피봇 아래/같은 수준에서 위로 실제 교차하는 새로운 이벤트가 확인될 때까지 기다린다.
- Next Pivot은 활성 Breakout 사이클을 이어갈 새 구조를 위한 값이다. 활성 Breakout Pivot이 없는 일반 BASE 상태에서는 Next Pivot을 억지로 만들지 않고 Candidate Pivot을 사용한다.
- **[사용자 확정 방향]** 후보가 정식 유효 패턴으로 인정되면 Next Pivot을 바로 지정하되, 실제 돌파 이벤트는 가격 교차가 발생한 날에만 기록한다. 임의의 3일/5일 안정성 대기는 사용하지 않는다.

## 5.6 피봇 거리 표시

- **[확정]** 기존의 단일 `돌파 Pivot 거리` 열은 다음 두 열로 분리한다.
  1. `Breakout Pivot 거리`: 현재가와 실제 돌파 피봇의 거리. 기록된 Breakout Pivot이 없으면 `—`.
  2. `Candidate Pivot / 20D Reference 거리`: Candidate Pivot이 있으면 해당 값과의 거리, 없고 20D Reference만 있으면 `20D REF`를 붙여 참고 거리로 표시.
- 두 거리의 기준을 섞지 않는다. Reference는 실제 패턴 피봇이나 실제 돌파 피봇으로 부르지 않는다.
- 거리 공식: `(현재가 / 기준가격 - 1) × 100%`.

### 5.7 20D Reference 정의

- **[확정 방향]** `20D Reference`는 재베이스 시작일을 기준으로 고정하는 가격이 아니라, 평가 기준일을 끝으로 하는 **최근 20개 완료 일봉의 최고 High**를 뜻한다.
- EOD 계산 시에는 평가일의 완료 일봉을 포함해 최근 20개 완료 일봉 중 `High` 최댓값을 사용한다. 장중 계산에서는 미완성 당일 봉을 포함하지 않고 가장 최근 완료 일봉까지의 20개 일봉을 사용한다.
- 20D Reference는 rolling reference이므로 하루가 지나면 구간도 한 거래일씩 이동한다. 재베이스 저점, Candidate Pivot, Breakout Pivot과는 별도 값이다.
- 유효한 패턴이 없거나 `REBASE_FORMING` 상태일 때만 모니터의 대체 참고선으로 표시한다. 이 값을 Candidate Pivot으로 승격하거나 `PIVOT APPROACH` 판정에 사용하지 않는다.

### 5.8 패턴별 최소 데이터/구조 조건 — IBD/O’Neil 중심 V2 기본 제안

**기본 방법론:** 대중적으로 사용되는 William O’Neil / Investor’s Business Daily(IBD)의 성장주 베이스 패턴 기준을 우선한다. 아래에 적힌 패턴 기간·깊이 중 IBD 자료에서 직접 제시한 값과, 자동 탐지를 위해 V2에서 보충한 수치는 구분한다. 패턴 차트는 본질적으로 일부 주관성이 있으므로 아래 수치는 V2의 초기 규칙이며, 코딩 전에 예시 차트 및 과거 사례로 확인한다. 패턴 기간은 거래일 기준으로 계산하며 1주는 약 5거래일로 환산한다.

**공통 규칙**
- 패턴 인정에는 최소 기간과 구조 조건을 모두 충족해야 한다. 날짜 수만 충족하거나 이름이 비슷한 모양만으로는 인정하지 않는다.
- 일반적인 continuation base는 선행 상승 추세 이후에 형성되는 것을 선호한다. Double Bottom은 별도의 reversal-base 유형으로 처리한다.
- Candidate Pivot은 각 패턴의 저항선 정의로 산출한다. 패턴의 피봇을 현재가가 이미 넘어섰다고 해서 과거 돌파 이벤트를 소급 생성하지 않는다.
- 실제 `BREAKOUT CONFIRMED`는 사용자 정의 이벤트 규칙을 그대로 쓴다: 완료 일봉 종가가 피봇을 상향 교차해 위에서 마감하고 EOD RVOL ≥ 1.30×인 당일에만 확정한다. IBD의 일부 자료가 안내하는 더 높은 거래량 배수(예: 50일 평균 대비 40–50% 증가)는 참고 원칙으로만 취급하며, V2의 기존 1.30× 이벤트 조건을 자동으로 바꾸지 않는다.
- 재베이스가 시작되면 기존 패턴 결과를 새 패턴으로 그대로 이어 쓰지 않는다. 새 구조를 다시 계산한다. 패턴 특성상 과거 구간이 필요한 경우(예: High Tight Flag의 선행 Pole, Base-on-Base의 첫 베이스)는 해당 패턴 정의에 필요한 선행 구간을 명시적으로 검증하고, 이전 Candidate Pivot만 단순 재사용하지 않는다.
- 패턴/셋업이 동시에 유효할 때 UI에는 하나의 주요 라벨과 하나의 활성 Candidate Pivot만 표시한다. **VCP가 유효하고 하나 이상의 클래식 패턴도 동시에 유효하면 VCP를 우선 선택**한다. 이 경우 Pattern 라벨은 `VCP`, Candidate Pivot Source는 `VCP`로 표시하며, 클래식 패턴 라벨과 별도 VCP Pivot을 동시에 노출하지 않는다. VCP가 유효하지 않을 때는 기존 클래식 패턴 우선순위를 적용한다: High Tight Flag → Base-on-Base(복합 라벨; 2차 베이스 유형도 내부 메타데이터로 저장) → Cup & Handle → Double Bottom → Flat Base → Cup Without Handle → Saucer Base. 어떤 경우에도 선택된 Candidate/Next Pivot은 과거 Breakout Pivot을 덮어쓰지 않는다.

| 패턴 | 최소 기간 / 핵심 수치 | 구조 규칙 및 자동화 판정 | Candidate Pivot | 근거 / 상태 |
|---|---|---|---|---|
| **Double Bottom** | 전체 ≥35 거래일(약 7주); 두 저점 사이 ≥20 거래일(약 4주); 저점 간 차이 ≤3% | 두 개의 뚜렷한 저점과 중앙 반등 고점이 있는 W 구조. 첫 저점에서 중앙 반등 고점까지 약 10–20% 상승하는 것을 기본 조건으로 둔다. 두 번째 저점은 첫 저점의 ±3% 범위로 본다. 깊이는 IBD의 전형적 예시를 따라 베이스 시작 고점에서 저점까지 40% 이내를 초기 상한으로 둔다. 두 번째 저점 뒤 상승이 이어지고 저점 사이의 중앙 고점이 저항 역할을 해야 한다. | 두 저점 사이 중앙 반등 구간의 최고 High | 기간·최대 깊이는 IBD; trough tolerance / middle-rally checks use StockCharts general guidance. 초기 자동화 규칙 |
| **Cup & Handle** | 전체 ≥35 거래일(약 7주); Handle ≥5 거래일 | 컵의 깊이는 기본적으로 15–30% 범위. 둥근 U자형이며 과도하게 급격한 V자 회복은 제외한다. 오른쪽 림이 왼쪽 림에 근접해야 한다(자동화 초기 허용 오차 ≤5%). 핸들은 컵 상단 절반에서 형성되어야 하며, 핸들 하락폭은 초기 기본 상한 15%로 제한한다. 핸들이 컵의 중간점 아래로 떨어지면 무효. | Handle 구간의 최고 High | IBD/O’Neil 기간·깊이·handle-upper-half 원칙; 5% 및 15% 경계는 V2 자동화용 초기 제안 |
| **Cup Without Handle** | ≥30 거래일(약 6주) | 부드러운 U자형 컵과 우측 회복. 핸들로 구분할 수 있는 별도 횡보/하락 구간이 없어야 한다. 깊이는 기본적으로 15–33% 범위, 오른쪽 림은 왼쪽 시작 고점의 5% 이내까지 회복하는 것을 초기 기준으로 한다. 날카로운 V자 반등은 제외한다. | 컵의 림/상단 저항 | IBD는 최소 약 6주, 약 15–33% 깊이를 안내; 림 허용 오차는 자동화용 제안 |
| **Flat Base** | ≥25 거래일(약 5주); 깊이 ≤15% | 베이스 구간 최고 High부터 최저 Low까지의 하락폭이 15% 이내이고, 가격 움직임이 비교적 좁은 횡보 구간이어야 한다. 선행 상승은 기본적으로 이전 수개월의 의미 있는 상승(권장 +20% 이상)을 요구하며, 엄격한 IBD 품질 조건에서는 +30% 이상을 선호한다. | 베이스 구간 최고 High | IBD의 5주·15% 핵심 기준; 선행 상승의 +20% 자동 게이트는 V2 기본 제안이며 +30%는 엄격 조건 |
| **Saucer Base** | ≥40 거래일(약 8주) 기본 제안 | 장기간에 걸친 완만하고 둥근 저점과 회복. V자 반전이나 몇 개의 급등 봉만으로는 인정하지 않는다. Cup Without Handle과 겹치면 더 뚜렷한 컵 림·깊이 구조가 있는 경우 Cup Without Handle을 우선하고, 장기간·완만·상대적으로 얕은 구조일 때 Saucer로 분류한다. 깊이 ≤25%는 초기 기본값 제안. | Saucer 상단 림/저항 | Saucer는 IBD/StockCharts에서 장기적·완만한 rounding pattern으로 설명되지만 보편적인 고정 일수·깊이는 없음; 기간·깊이는 V2 제안 |
| **Ascending Base** | ≥45 거래일; 전형적 45–80 거래일(약 9–16주) | 최소 3번의 구별되는 조정/후퇴가 필요하며 각 조정의 고점과 저점이 이전보다 높아야 한다. 후퇴별 조정폭은 6–25% 범위의 초기 허용값으로 둔다. 세 번의 고점/저점이 식별되지 않으면 인정하지 않는다. | 세 번째/마지막 조정 이후의 유효 상단 저항 | IBD가 설명하는 3회의 higher-high/higher-low pullback, 통상 9–16주 기준; 각 6–25%는 문헌 범위를 반영한 자동화 기본값 |
| **High Tight Flag** | Pole은 40 거래일 이내; Flag ≥15 거래일(약 3주), 기본 최대 25 거래일(약 5주) | Pole 구간에서 약 8주 이내 100% 이상 상승이 있어야 하는 드문 패턴. 이후 Flag 조정은 보통 10–20%, 초기 최대 25% 이내로 제한한다. 선행 급등 조건을 충족하지 않으면 Flag만으로 High Tight Flag라고 표시하지 않는다. | Flag 상단의 최고 High | IBD의 강한 Pole + 얕고 짧은 Flag 원칙; 구체적인 날짜/깊이 상한은 자동화용 제안 |
| **Base-on-Base** | 두 번째 베이스 자체가 해당 패턴의 최소 기간·구조 조건을 충족 | 첫 번째 베이스의 돌파 후 주가가 약 +20% 상승하기 전에 두 번째 베이스가 형성되며, 두 번째 베이스가 대부분 첫 번째 베이스 상단 부근/위에서 형성되어야 한다. V2 자동화 기준으로 두 번째 베이스 일봉의 과반이 첫 피봇 위에 있어야 한다. 두 번째 베이스 유형(Flat Base, Cup 등)을 별도 메타데이터로 저장한다. | 두 번째 베이스의 패턴별 피봇 | IBD Base-on-Base의 핵심 구조(20% 미만 상승, 두 번째 베이스가 주로 첫 베이스 위)에 기반; 과반 조건은 자동화용 제안 |

**VCP (Volatility Contraction Pattern) — 독립 수축 셋업**

VCP는 Flat Base, Cup & Handle, Double Bottom 같은 완성형 베이스 패턴과 동일한 종류의 라벨로 취급하지 않는다. Mark Minervini식의 선행 상승 추세 이후 수축 폭이 단계적으로 줄어드는 셋업으로 별도 감지·표시한다. VCP는 Breakout Readiness 점수의 다른 이름이 아니며, 별도의 구조 검증을 통과해야 한다.

- **선행 상승 추세:** 초기 자동화 기준으로 베이스 시작점부터 첫 수축 전 고점까지 최소 +30% 상승을 요구한다. 이 +30%는 V2의 기계적 필터 제안이며, 원전에서 모든 경우에 보장되는 절대 경계로 표현하지 않는다.
- **수축 개수:** 2~6회, 일반적으로 3~5회를 선호한다. 두 번뿐인 경우에는 마지막 수축이 특히 좁고 거래량이 마르는지 더 엄격히 확인한다.
- **수축 폭:** 각 수축의 고점에서 저점까지의 하락률 `T1, T2, ... Tn`을 계산한다. 뒤의 수축은 앞선 수축보다 작아야 한다. 자동화 초기 기준으로 `T(n) <= 0.75 × T(n-1)`를 기본 통과 조건으로 사용하되, 이 계수는 실제 사례 검증으로 조정 가능한 파라미터다.
- **첫 수축:** 기본 허용 구간 10~35%. 35%를 크게 초과하면 정상 VCP보다 손상된/넓은 베이스일 가능성이 높으므로 기본적으로 거부한다.
- **마지막 수축:** 최종 수축 폭은 8% 이하를 기본으로 한다. 6% 이하는 높은 품질의 tight coil 조건으로 추가 점수를 줄 수 있지만, 6%는 별도의 필수 통과 경계가 아니다.
- **저점 구조:** 수축 저점이 전반적으로 높아지는 구조를 선호한다. 마지막 수축이 이전 핵심 저점을 하향 이탈하면 해당 VCP 셋업은 무효화한다.
- **거래량:** 각 수축 구간의 평균 거래량이 전체적으로 감소해야 한다. 자동화 초기 기준으로 마지막 수축 평균 거래량은 첫 수축 평균의 75% 이하를 요구한다. 거래량 데이터가 없으면 VCP를 완전 검증된 상태로 인정하지 않고 `DATA INCOMPLETE`/`VCP UNCONFIRMED` 사유를 기록한다.
- **기간:** 모든 VCP에 대해 보편적으로 합의된 고정 최소 일수는 없으므로, V2 기본 자동화 기준은 최소 20개의 완료 일봉과 식별 가능한 2회 이상의 수축이다. 이 기간은 원전의 절대 규칙이 아니라 일봉 스캐너용 초기 필터다.
- **피봇:** 최종/가장 좁은 수축 구간의 최고 `High`를 VCP Pivot으로 산출한다. 실제 돌파 이벤트는 기존 공통 규칙(완료 일봉 종가가 피봇을 실제 상향 교차, EOD RVOL ≥1.30×이면 그 날 `BREAKOUT CONFIRMED`)을 따른다. 가격이 패턴 인식 시점에 이미 피봇 위에 있으면 소급 돌파를 기록하지 않는다.
- **시장/추세 문맥:** 장기 하락 추세의 급락 후 단순 반등만으로 VCP를 인정하지 않는다. 최소 선행 상승과 수축 구조를 충족해야 하며, 추세 템플릿/상대강도는 VCP 신호 자체와 구분된 품질 확인 항목으로 제공한다.
- **UI/엔진 분리:** Pattern Engine은 내부적으로 VCP와 클래식 패턴을 각각 평가할 수 있지만, UI에는 하나의 주요 셋업만 표시한다. VCP와 클래식 패턴이 동시에 유효하면 VCP가 우선이며 Pattern 라벨은 `VCP`, Candidate Pivot Source는 `VCP`로 한다. VCP가 유효하지 않으면 클래식 패턴을 표시한다. 별도의 `VCP Pivot` 열이나 두 번째 패턴 라벨은 표시하지 않는다. 엔진 내부 진단을 위한 보조 검출 정보는 UI에 노출할 필요가 없다. Pivot Engine은 선택된 단일 후보의 source와 선택 사유를 반환하며, Breakout Pivot은 별도로 보존한다.

VCP 정의의 핵심(선행 상승 추세, 연속적으로 좁아지는 2~6회 수축, 거래량 dry-up, 최종 수축 상단의 피봇)은 Minervini식 설명을 따른다. +30%, 0.75배, 8%, 75%, 20거래일 등의 숫자는 자동 스캐너의 초기 설정값이며, V2의 과거 차트 표본과 대조해 검증한다. 참고: Mark Minervini 관련 저서 발췌 및 VCP 구현 자료(§22 참조).

**재베이스 이후 패턴 인정**
- 재베이스가 발동한 즉시 기존 Candidate Pattern/Pivot은 현재 유효 셋업에서 제외된다. 새 패턴 탐색은 바로 시작하되, 해당 패턴의 최소 기간과 구조 조건이 모두 충족되기 전에는 `BASE FORMING`을 유지한다.
- Double Bottom은 재베이스 사이클에서 두 저점과 중간 반등 구조가 새로 형성되어야 한다. 단지 과거 패턴이 조회 데이터 안에 존재한다는 이유로 재사용하지 않는다.
- Cup/Flat/Saucer는 해당 패턴의 주요 base 구조가 현재 재베이스 이후에 새롭게 형성됐는지 검증한다. 선행 추세나 시작 림이 필요한 경우에는 그 역사적 구간을 설명 필드에 명시하며, 이전 Candidate Pivot 결과만 이어 쓰지 않는다.
- High Tight Flag는 선행 Pole이 패턴 정의의 일부이므로 재베이스 날짜 이후에 Pole 전체를 새로 만들도록 강제하지 않는다. 단, Pole과 Flag의 일자/수익률/깊이 기준을 명시적으로 재검증한다.
- 패턴 구조가 형성되지 않으면 아무 패턴도 억지로 부여하지 않는다. `BASE FORMING`과 20D Reference를 사용한다.

### 5.9 Next Pivot — 새 패턴 확인 즉시 지정

- **[사용자 확정 방향]** 활성 Breakout Pivot이 존재하는 동안, 새롭게 형성된 유효 패턴이 Candidate Pivot을 산출하면 별도의 안정성 대기 기간 없이 즉시 그 값을 `Next Pivot`으로 지정한다.
- “유효 패턴”은 해당 패턴별 최소 형성 기간과 구조 조건을 모두 통과한 경우를 뜻한다. 임시 고점이나 완성되지 않은 구조는 Next Pivot으로 지정하지 않는다.
- Next Pivot 지정은 새로운 돌파 이벤트가 발생했다는 뜻이 아니다. 가격이 실제로 Next Pivot을 상향 교차하고 완료 일봉이 그 위에서 마감한 날짜에만 새 돌파 이벤트를 기록한다.
- 패턴이 확인된 시점에 종가가 이미 Next Pivot 위라면 소급 돌파 이벤트를 생성하지 않는다. 이후 실제 가격 교차가 관측될 때까지 기다린다.
- 새 Next Pivot을 실제 돌파하면 그 가격을 새 활성 Breakout Pivot으로 캡처하고, 이전 Breakout Pivot의 가격·일자·이벤트는 과거 이력으로 보존한다.

## 6. 재베이스(Rebase) 엔진 — 핵심 우선순위

### 6.1 재베이스를 시작하는 트리거

재베이스는 **현재 유효한 셋업의 무효화/종료와 새 구조 탐색의 시작**이다. 아래 트리거는 같은 뜻이 아니므로 각 사유를 별도로 기록한다.

| ID | 트리거 | 기준 | 동작 | 상태 |
|---|---|---|---|---|
| RB-01 | 최근 20거래일 고점 대비 -10% | 현재 평가일 종가가 최근 20개 완료 일봉의 최고 `High` × 0.90 이하(평가일 완료 일봉을 포함해 계산) | 현재 Candidate Pattern/Pivot을 즉시 비활성화하고 새 구조 탐색 시작 | [확정 방향] |
| RB-02 | Candidate Pivot 대비 -10% | 현재 종가가 **전일까지 활성화되어 있던** Candidate Pivot의 90% 이하 | 해당 후보 패턴을 즉시 무효화하고 새 구조 탐색 시작 | [확정 방향] |
| RB-03 | 공식 Breakout Failure | 활성 Breakout 사이클이 있고, 완료 일봉 종가가 고정된 Breakout Pivot 아래로 마감 | 활성 돌파 사이클 종료, 재베이스 시작; 역사적 Breakout Pivot 이벤트는 보존 | [확정] |

- RB-01은 재베이스 트리거에 사용한 가격 기준점을 함께 기록해야 한다. 같은 고점/하락 에피소드가 지속되는 동안 매일 새 사이클을 만들거나 시작일을 앞으로 옮기지 않는다.
- RB-02는 당일 새로 계산된 패턴 상단으로 자기 자신을 비교하지 않는다. 트리거 계산에는 직전까지 유효했던 Candidate Pivot을 사용해 순환 참조를 피한다.
- RB-03은 **활성 돌파 사이클**에만 적용한다. 과거 이력에 남은 Breakout Pivot만 존재하고 현재 활성 돌파가 끝났다면, 그 과거 가격 아래에 있다는 이유만으로 매일 새 재베이스를 발동하지 않는다.
- 여러 트리거가 같은 날 발생하면 `BREAKOUT_FAILURE`를 우선 원인으로 기록하고, 나머지 조건도 진단용 보조 사유로 보존할 수 있다.
- PP 성과 분석에서 사용하는 Pivot 대비 -2% 기준은 Stage의 공식 Breakout Failure 기준과 별도 통계 규칙이다. 두 기준을 합치지 않는다.

### 6.2 재베이스 진입 즉시 동작

어느 트리거로든 새 재베이스 사이클이 시작되면 즉시 다음을 적용한다.

1. 과거 Candidate Pivot/Pattern을 현재 유효 매수 셋업으로 사용하지 않는다.
2. 새 패턴이 확인되기 전까지 `Pattern = BASE FORMING`, `Candidate Pivot = —`, `Candidate Source = REBASE_FORMING`으로 표시한다.
3. `20D Reference`는 참고 저항선으로만 제공하며 Candidate Pivot 또는 Breakout Pivot으로 부르지 않는다.
4. 기존 돌파 이벤트와 Breakout Pivot 원본 값/날짜는 기록으로 보존한다. 재베이스가 역사적 Breakout Pivot을 삭제하거나 새 Candidate Pivot으로 덮어쓰지 않는다.
5. 재베이스 탐색 시작일은 `rebase_started_at`으로 기록하고, 같은 사이클 동안 고정한다.
6. `rebase_low`와 `rebase_low_date`는 재베이스 시작일 이후 완료 일봉을 기준으로 추적한다. 더 낮은 저점이 생기면 최저가와 날짜만 갱신하고 시작일은 바꾸지 않는다.

### 6.3 재베이스 상태의 유지와 재발동 방지

- 재베이스 상태는 `BASE FORMING` 표시가 연속으로 저장되거나 동등한 상태 이력이 유지되는 동안 하나의 사이클로 취급한다.
- RB-01 조건이 여러 날 연속 참이어도 시작일은 처음 조건을 충족한 날로 유지한다. 동일한 20D 기준 고점에서 비롯된 하락 조건은 매일 새 트리거로 취급하지 않는다.
- `rebase_started_at`은 단순히 현재 가격이 -10% 기준선 위로 잠깐 반등했다는 이유만으로 초기화하지 않는다.
- 새로운 패턴이 검증되어 재베이스 사이클이 정상 종료되면, 이후의 새 가격/피봇 구조에서 발생한 새로운 무효화 사건은 별도 사이클을 시작할 수 있다.
- 재베이스 이력을 저장할 필드가 현재 Supabase 스키마에 없으면, 초기 단계에서는 현행 Snapshot 구조에서 복원할 수 있는지 검토한다. 기존 스키마 변경은 인벤토리와 백업/마이그레이션 계획 없이 수행하지 않는다.

### 6.4 새 패턴 탐색과 재베이스 종료

- 패턴 탐색은 재베이스 시작 직후부터 수행할 수 있다. 다만 **탐색 시작**과 **유효 패턴 인정**은 다르다.
- 기존의 모든 패턴에 동일하게 적용되는 30거래일 대기 게이트는 V2에서 그대로 복사하지 않는다. 대신 각 패턴의 실제 구조 요건 및 필요한 데이터 길이를 명시한다. 패턴별 조건표는 기존 구현과 합의된 규칙을 조사한 뒤 확정한다.
- 재베이스 후 유효한 새 패턴으로 인정하려면 패턴의 기준 구조가 재베이스 시작 이후 데이터에서 형성되어야 한다. 재베이스 이전부터 있던 오래된 패턴이 계속 탐지된다는 사실만으로 새 패턴으로 인정하지 않는다.
- 패턴별 최소 데이터/구조 조건을 충족하지 못하거나 상단/피봇이 유효하지 않으면 계속 `BASE FORMING`을 표시한다.
- 검증된 새 패턴이 Candidate Pivot을 제공하면 재베이스 탐색 상태를 종료하고 Candidate Pattern/Pivot 상태로 전환한다. 과거 Breakout Pivot 이력은 독립적으로 보존한다.
- 새 Candidate Pivot이 다시 현재 종가 대비 -10% 이하로 무너지면 새 재베이스 사이클을 시작한다.
- 같은 날 후보 패턴을 처음 확인하면서 그 날 이미 상단을 넘었다는 이유만으로 과거에 돌파가 있었던 것처럼 소급 기록하지 않는다. 실제 돌파일은 일자별 데이터에서 확인 가능한 가격 교차 규칙으로 결정한다.

### 6.5 재베이스 UI 출력 계약

| 필드 | 재베이스 중 기대값 |
|---|---|
| Pattern | `⏳ BASE FORMING` |
| Pattern 상단 | `—` |
| Candidate Pivot | `—` |
| Candidate Source | `REBASE_FORMING` |
| 20D Reference | 참고용 값. 실제 패턴 피봇이 아님 |
| Breakout Pivot | 실제 돌파 이력/활성 여부를 구분해 표시. 역사적 값은 삭제하지 않음 |
| Rebase Started At | 같은 사이클의 최초 트리거 날짜 |
| Rebase Low / Date | 재베이스 시작 후 현재까지 완료 일봉의 최저가/날짜 |
| Rebase Reason | `20D_HIGH_DRAWDOWN`, `CANDIDATE_PIVOT_DRAWDOWN`, `BREAKOUT_FAILURE` 중 해당 사유 |


## 7. Entry Stage 및 Live Monitor

### 7.1 서로 다른 세 레이어

- `ENTRY STAGE`: 완료 일봉 기준의 공식 Stage/과거 상태 이력.
- `LIVE STATE`: 장중 가격과 장중 누적 거래량으로 계산한 임시 현재 상태. LIVE 계산으로 EOD 공식 이력을 덮어쓰지 않는다.
- `LIVE ENTRY QUALITY`: 현재 신규 진입 셋업의 질/진입 적합성. Stage와 별도 계산한다.

세 필드는 서로 다른 질문에 답한다. 동일 종목·동일 기준 데이터라면 어느 화면에서나 같은 엔진 결과를 사용한다. Stage가 높다고 `LIVE ENTRY QUALITY`가 자동으로 GOOD이 되는 것은 아니다.

## 7.2 공식 Stage 목록 및 의미

| Stage | V2 정의 | 다음 전이/주의 |
|---|---|---|
| `BASE` | 활성 돌파 사이클이 없고, 현재 유효한 진입 단계 조건을 충족하지 않는 상태. 재베이스 중 새 패턴이 아직 완성되지 않은 경우도 포함 | Pattern 필드는 `BASE FORMING`으로 이유를 구체적으로 표현할 수 있음 |
| `BASE + PP` | 베이스/유효한 베이스 문맥에서 Pocket Pivot 신호가 발생했지만 아직 PIVOT APPROACH/돌파 단계가 아닌 상태 | PP가 발생했다고 Candidate Pivot을 만들지 않음 |
| `PIVOT APPROACH` | 유효 Candidate Pivot 아래에 있고, Candidate Pivot 대비 거리 -5% 이상 및 0% 미만 | Candidate Pivot이 없거나 재베이스 중이면 부여하지 않음 |
| `BREAKOUT` | (1) 종가가 유효 Candidate Pivot을 실제로 상향 교차했지만 그날 EOD RVOL < 1.30×인 경우, 또는 (2) 돌파 이벤트 이후 종가가 활성 Breakout Pivot 위에 계속 유지되는 후속 날짜 | 후속 날짜에는 RVOL이 1.30× 이상이어도 `BREAKOUT CONFIRMED`로 반복 표시하지 않음. 새 피봇의 실제 교차는 별도 이벤트 |
| `BREAKOUT CONFIRMED` | 유효 Candidate/Next Pivot을 실제로 상향 교차한 바로 그 완료 일봉에서 종가가 피봇 위이고 EOD RVOL ≥ 1.30×인 경우에만 표시하는 **1일 이벤트 Stage** | 다음 완료 일봉부터 종가가 활성 Breakout Pivot 위에 유지되면 `BREAKOUT`; 이벤트 이력은 `confirmed` 여부와 날짜를 보존 |
| `BREAKOUT FAILURE` | 활성 돌파 사이클에서 완료 일봉 종가가 고정 Breakout Pivot 아래로 마감한 날의 공식 이벤트 | 실패일은 이력으로 기록. 이후 활성 돌파 사이클은 종료하고 재베이스. 과거 Breakout Pivot은 이력으로 유지 |

**[확정: 2026-10-19] `BREAKOUT HOLD`는 공식 ENTRY STAGE 목록에서 제거한다.** 돌파 이벤트가 확인된 후에도 종가가 활성 Breakout Pivot 위에 유지되는 후속 날짜의 Stage는 `BREAKOUT`이다. `BREAKOUT CONFIRMED`는 반복적인 상태 라벨이 아니라 실제 피봇 상향 교차가 거래량 조건까지 충족한 그날의 일회성 Stage 이벤트다.

## 7.3 공식 Stage 판정 순서

**A. 활성 Breakout Pivot이 있는 경우**

1. 완료 일봉 종가가 고정 Breakout Pivot 아래로 마감하면 당일 `BREAKOUT FAILURE` 이벤트를 기록하고 재베이스를 시작한다. 이 조건이 최우선이다.
2. 실제 유효 `Next Pivot` 상향 교차가 당일 발생하고 종가가 그 피봇 위에서 마감하면, 그 날의 RVOL ≥ 1.30×이면 `BREAKOUT CONFIRMED`, 그렇지 않으면 `BREAKOUT`을 표시한다. 실제 교차 날짜와 볼륨 확인 여부를 돌파 이벤트로 기록하고, 새 피봇을 활성 Breakout Pivot으로 캡처한다. 이전 Breakout Pivot 이벤트는 이력에 보존한다.
3. 당일 새로운 Next Pivot 돌파가 없지만 종가가 활성 Breakout Pivot 위에 있으면 `BREAKOUT`을 표시한다. RVOL이 높은 것만으로 `BREAKOUT CONFIRMED`를 반복 생성하지 않는다.
4. 장중 가격 이탈만으로 공식 Failure를 확정하지 않는다. 공식 Failure는 완료 일봉 종가 기준이다.

**B. 활성 Breakout Pivot이 없는 경우**

1. 재베이스가 진행 중이고 유효 새 패턴이 없으면 Pattern은 `BASE FORMING`, Candidate Pivot은 공란, Stage는 `BASE`로 둔다.
2. 유효 Candidate Pivot을 종가가 실제로 상향 교차했고 RVOL ≥ 1.30×이면 그 날만 `BREAKOUT CONFIRMED`; RVOL < 1.30×이면 `BREAKOUT`을 표시한다. 실제 교차일의 피봇을 활성 Breakout Pivot으로 캡처한다.
3. 새로운 패턴이 인정된 첫날 종가가 이미 Candidate Pivot 위라면 소급 돌파 이벤트를 기록하지 않는다. 실제 교차 이벤트가 확인될 때까지 기다린다.
4. 종가가 Candidate Pivot 아래이고 피봇 거리 -5% 이상, 0% 미만이면 `PIVOT APPROACH`.
5. 위 조건이 없고 유효한 베이스 문맥에서 PP 조건이 충족되면 `BASE + PP`.
6. 그 외는 `BASE`.

**Stage 전이의 핵심:** `BREAKOUT CONFIRMED`는 하루짜리 공식 이벤트 라벨이고, 다음 날부터 활성 Breakout Pivot 위에 있으면 `BREAKOUT`이다. `BREAKOUT HOLD`는 존재하지 않는다. 과거 돌파 이벤트 로그와 매일의 현재 Stage는 각각 따로 보존한다.

### 7.4 PP 조건 및 Pivot Approach

- PP는 일반 차트 패턴이나 Breakout Pivot과 별도인 조기 수급 신호다.
- 현재 합의된 PP 방향은 베이스/주요 이동평균선 근처의 셋업에서 최근 10거래일의 상승일 거래량이 최대 하락일 거래량보다 큰 상황을 탐지하는 것이다.
- 베이스/이동평균선 근접의 정확한 경계, 거래량 결측 처리, PP 신호의 유효 기간은 기존 코드를 조사해 구체화한다.
- `PIVOT APPROACH`의 기본 가격 거리 범위는 2026-10-19 사용자 결정에 따라 -5% ≤ Candidate Pivot 대비 거리 < 0%로 적용한다.
- 패턴이 유효하지 않거나 Candidate Pivot이 비어 있으면 `PIVOT APPROACH`를 부여하지 않는다.

### 7.5 거래량 확인 및 과도한 확장

- EOD `BREAKOUT CONFIRMED` 기준: 그날 실제 피봇 상향 교차가 발생하고 종가가 피봇 위에서 마감하며 RVOL ≥ 1.30×. 다음 날 이후 같은 피봇 위 유지 상태는 `BREAKOUT`으로 표시한다.
- 현행 액션 기준을 초기 V2 기준으로 옮긴다: 피봇 대비 상승폭이 +5% 초과면 추격 주의, +7% 이상이면 신규 진입/추가매수를 제한한다. 이를 Stage 전이 자체와 혼동하지 않고 Action/Quality 게이트로 분리한다.
- `BREAKOUT CONFIRMED`는 추가매수 후보 상태이지 무조건 ADD 명령이 아니다. 시장 레짐, Live Entry Quality, 보유 비중 및 과도한 확장 여부가 최종 행동을 제약할 수 있다. Initial Risk 숫자는 별도로 표시하지만 Entry Quality 점수/라벨에는 반영하지 않는다. 손절·청산 관리는 독립 Risk/Exit Engine에서 처리한다.
- Stage 하락은 새 주문을 자동 생성하지 않으며, 기존 보유 포지션의 축소/청산은 Risk/Exit 규칙을 통해 따로 판정한다.

### 7.6 LIVE STATE와 공식 EOD Stage의 경계

- LIVE STATE는 현재 장중 가격/RVOL로 재계산하고, EOD Stage 이력은 마지막 완료 일봉 기준으로 유지한다.
- Live Stage의 임계값·표시명은 EOD 공식 Stage와 혼동되지 않아야 한다. 장중 하향 이탈은 기존 `LIVE STATE`에 반영하되, 전용 `BREAKOUT FAILURE RISK` 경고는 추가하지 않는다.
- 공식 `BREAKOUT FAILURE`는 완료 일봉 종가가 고정 Breakout Pivot 아래로 마감했을 때만 확정/기록한다. 장중 가격이 Breakout Pivot보다 2% 이상 아래로 내려가더라도 공식 Stage/Failure 이력 또는 돌파 사이클을 종료하지 않는다.
- 장중 -2% 조건은 공식 Stage Failure 기준 및 PP 성과 통계의 Pivot 대비 -2% 기준과 혼합하지 않는다. LIVE STATE는 기존 실시간 판정 규칙대로 표시한다.

### 7.7 Entry Quality — V2 확정 설계

**[사용자 확정: 2026-10-19] Initial Risk는 Entry Quality에 반영하지 않고, 숫자 표기만 유지한다.**

- Initial Risk는 Momentum Monitor에서 현재처럼 숫자(위험률 %)로 표시한다. 계산값이 유효하면 예: `-6.4%` 같은 숫자형 표기를 유지한다. 계산 불가 시에는 숫자를 꾸며내지 말고 `—` 또는 별도의 데이터 상태를 표시한다.
- Initial Risk는 EOD/LIVE Entry Quality의 가중 점수에 포함하지 않는다. Initial Risk가 12%를 넘는다는 이유만으로 Entry Quality를 `WAIT`로 강제하지 않는다. Initial Risk 값이 누락되었다는 이유만으로 Entry Quality를 `DATA INCOMPLETE`로 바꾸지도 않는다.
- LIVE 가격이 stop 아래로 내려갔다는 조건 역시 Entry Quality 라벨을 직접 바꾸는 데 사용하지 않는다. 해당 상황이 실제 손절/청산 조건에 해당하면 독립 `Risk/Exit Engine`에서 별도로 판단한다.
- V2 Entry Quality는 셋업·모멘텀·상대강도·추세/이동평균·거래량/Breakout Readiness의 품질만 평가한다. 위험률 숫자는 사용자 판단을 위한 별도 정보다.
- 초기 점수의 구성요소와 합산 방식은 아래 원칙을 따른다. 현행 코드에서 위험 점수 20점을 제외한 나머지 구성요소의 상대적인 가중 관계는 유지하고, 실제 적용 가능한 요소의 가중치 합으로 나눠 0–100으로 정규화한다.
  - EOD 기본 구성요소: Pattern 20, Breakout Readiness 25(정의된 BREAKOUT 단계에서는 제외), RS 15, Momentum 10, MA Structure 10. Initial Risk는 없음.
  - LIVE 구성요소: Pattern 15, Setup/Breakout Quality 20, RS 15, Live Momentum 10, Live Volume Quality 10, MA Structure 10. Initial Risk는 없음.
  - EOD/LIVE의 유효 구성요소 점수를 합산한 뒤 해당 시점에 적용 가능한 최대 점수로 나누어 0–100점으로 정규화한다. 해당되지 않는 항목은 0점 처리하지 않고 분모에서도 제외한다. 핵심 셋업 데이터 자체가 부족한 경우에 한해 `DATA INCOMPLETE`를 사용할 수 있다.
- **[사용자 확정: 2026-10-19] 품질 라벨 경계:** 정규화 점수 75점 이상은 `GOOD`, 60점 이상 75점 미만은 `CAUTION`, 60점 미만은 `WAIT`로 시작한다.
- 별도 가격 확장 게이트는 점수와 구분한다: Candidate/Breakout Pivot 대비 +5% 초과면 최소 `CAUTION`, +7% 이상이면 신규 진입/추가매수 `WAIT`를 적용한다. 다른 Stage/신호 조건과 충돌할 경우 우선순위는 Quality Gate 명세에서 별도로 명시한다.
- Entry Quality 결과에는 설명용 reason codes를 제공하되, Initial Risk의 높고 낮음 또는 누락은 Entry Quality 사유 코드/점수/라벨에 영향을 주지 않는다.

### 7.8 Momentum Monitor 표시 및 지표 제거 — V2 확정 설계

- **유지:** `Initial Risk` 숫자 열. 현재 사용자가 익숙한 숫자형 표현을 유지한다. 이 열은 정보 표시 전용이며 Entry Quality 점수/라벨을 바꾸지 않는다.
- **완전 제거:** `Turtle 2N Risk`. V2 UI뿐 아니라 전용 계산, 스냅샷, 데이터 계약 및 점수 의존성에서도 제거한다.
- **완전 제거:** Bollinger Bands 관련 지표(`BB Width`, `bb_width`, `bb_width_delta`, `bb_trend` 등), 계산, 캐시 필드, 상세 표시 및 Breakout Readiness 기여 점수. Bollinger 기반 값은 어떠한 Entry Quality/Breakout Readiness 계산에도 남기지 않는다.
- **Breakout Readiness 배점 유지:** 기존 contraction 합계 25점의 총점은 유지하되, BB Width에 배정되었던 7점과 BB 수축 지속성 3점을 제거한다. 사용자 결정에 따라 총 25점을 가능한 한 균등하게 `가격/ATR 변동성 수축 9점`, `거래량 수축 8점`, `수축 지속성/일관성 8점`으로 배분한다. 지속성은 ATR/가격 변동성 및 거래량의 시간 흐름만으로 판단하며 BB를 참조하지 않는다.
- **[사용자 확정 방향]** Breakout Readiness의 세 점수축은 가격/ATR 수축 9점, 거래량 수축 8점, 수축 지속성/일관성 8점이다.
- **[초기 수치 제안] 가격/ATR 수축 9점:** ATR(5)/ATR(20) ≤0.70이면 9점, ≤0.80이면 7점, ≤0.90이면 4점, 그보다 높으면 0점으로 시작한다. 가격 범위 수축은 독립적인 검증/보정 변수로 기록하되 동일 축 안에서 ATR과 중복 보상하지 않도록 한다.
- **[초기 수치 제안] 거래량 수축 8점:** 최근 5일 평균 거래량 / 그 이전 20일 평균 거래량 ≤0.70이면 8점, ≤0.80이면 6점, ≤0.90이면 3점, 그보다 높으면 0점으로 시작한다. 비교 구간이 부족하거나 거래량 데이터가 없으면 점수를 만들어내지 않는다.
- **[초기 수치 제안] 지속성/일관성 8점:** 최근 여러 구간의 ATR/가격 범위와 거래량이 함께 줄어드는 일관성을 평가한다. 3개 수준(강/중/약)으로 나눠 8/4/0점을 부여하는 기본안으로 시작하되, 실제 구현 전에 세부 구간 길이와 수축 단계 정의를 계산 명세에 고정한다. 이 점수축은 BB Width 또는 BB 기반 필드를 참조하지 않는다.
- 점수 재배분으로 전체 Breakout Readiness의 최대 점수는 25점으로 유지한다. 전체 점수와 하위 점수의 합산이 정확히 일치하는 테스트를 둔다.
- `VFI`, `MA`, `RSI`, `MACD` 등 사용자가 제거를 요청하지 않은 기존 지표는 이번 결정만으로 삭제하지 않는다.

## 8. Risk Engine

### 8.1 Initial Risk

- 종목·기준일에 대해 초기 손절 기준과 위험률(%)을 계산하고 Momentum Monitor에서 위험률을 숫자로 표시한다.
- **[사용자 확정]** Initial Risk 숫자는 Entry Quality에 반영하지 않는다. 이 지표는 진입 품질 점수/라벨을 바꾸지 않는 별도 참고 정보다.
- **[사용자 확정: 2026-10-19] ATR을 Initial Risk 계산의 1순위 기준으로 사용하고, 초기 손절 거리에는 `1.5 × ATR(14)`를 적용한다.** ATR 계산에는 가장 최근 완료된 일봉까지만 사용한다. 장중 Live Initial Risk에서는 기준 가격으로 현재가를 사용할 수 있지만 당일 미완성 봉을 ATR(14) 계산에 포함하지 않는다.
- 초기 손절선은 진입 시점의 진입가격과 당시 ATR(14)을 기준으로 계산하고, `initial_stop_price`로 기록해 이후 재계산으로 과거 진입 위험을 덮어쓰지 않는다. Initial Risk는 진입 기준 가격 대비 손절선까지의 위험률(%)을 표시하는 정보이며 Entry Quality에는 반영하지 않는다.
- `Reference Price`는 EOD 표에서는 해당 평가일의 완료 종가, Live 표에서는 현재가를 사용한다. 위험률은 `((Stop − Reference Price) / Reference Price) × 100`으로 산출하여 기존 숫자형(음수 %) 표시를 유지한다.
- ATR(14) 데이터가 부족하거나 계산 불가능하면 대체 기준을 순서대로 적용한다: (1) 최근 10개 완료 일봉의 유효한 스윙 저점이 Reference Price 아래에 있는 경우 그 저점, (2) 20일 이동평균선 −2%가 Reference Price 아래에 있는 경우 그 값. 두 기준 모두 유효하지 않으면 `—`와 계산 불가 사유를 표시한다. 기본 ATR이 유효할 때는 다른 기준이 더 가깝다는 이유만으로 ATR 기준을 바꾸지 않는다.
- 손절 기준은 Reference Price 아래에 있어야 한다. 진입가보다 높은 손절선을 Initial Risk로 표시하지 않는다.
- 계산이 불가능하면 `—` 등 비수치 상태를 표시하고 원인을 별도로 식별할 수 있어야 한다. 단, 이것만으로 Entry Quality를 `DATA INCOMPLETE`로 바꾸지 않는다.
- ATR(14) 계산이 불가능할 때만 대체 기준을 순서대로 적용한다: (1) Reference Price 아래에 있는 최근 10개 완료 일봉의 유효한 스윙 저점, (2) Reference Price 아래에 있는 20일 이동평균선 −2%. 두 기준 모두 유효하지 않으면 `—`와 계산 불가 사유를 표시한다. ATR이 유효하면 다른 기준이 더 가까운 이유만으로 ATR을 대체하지 않는다.
- 손절 기준은 Reference Price 아래에 있어야 한다. 진입가보다 높은 손절선을 Initial Risk로 표시하지 않는다.
- 계산이 불가능하면 `—` 등 비수치 상태를 표시하고 원인을 식별한다. 단, 이것만으로 Entry Quality를 `DATA INCOMPLETE`로 바꾸지 않는다.

### 8.2 Position Risk/Exit — 초기 손절 + 추적 손절

- **[사용자 확정: 2026-10-19]** 신규 포지션에는 초기 손절과 추적 손절을 모두 구현한다. 초기 손절은 진입 시점에 ATR(14)×1.5로 계산하여 `initial_stop_price`에 저장한다. 과거 진입의 초기 손절/위험률은 이후 데이터 재계산으로 덮어쓰지 않는다.
- **[사용자 확정: 2026-10-19, v0.12] 추적 손절 활성 조건:** 완료 일봉 종가가 진입가 + 1R 이상에 도달하면 추적 손절을 활성화한다. 여기서 `1R = 진입가 − 해당 진입 건의 initial_stop_price`다. 장중 고가만 +1R을 터치하고 종가가 기준에 미달하면 활성화하지 않는다.
- **[사용자 확정: 2026-10-19, v0.12] 추적 손절 산식:** 활성화 이후 진입 시점부터 현재까지의 최고 일봉 `High`에서 최근 22개 완료 일봉 기준 `3 × ATR(22)`를 뺀 값을 사용한다: `raw_trailing_stop = highest_high_since_entry − 3 × ATR(22)`. ATR 및 고점은 완료 일봉까지만 계산한다.
- **[사용자 확정: 2026-10-19]** 추적 손절은 상향하거나 유지할 수 있지만 절대 낮추지 않는다. 계산값이 기존 저장값보다 낮으면 기존 `trailing_stop_price`를 유지한다. 분할·추가 진입은 각 진입 건의 진입 가격과 초기 위험을 별도로 기록하며, 포지션 집계 시 각 진입 건의 위험/손절 기준을 혼동하지 않는다.
- stop 필드는 별도로 관리한다: `initial_stop_price`, `trailing_stop_price`, `effective_stop_price`, `stop_reason`. 추적 손절이 활성화되기 전에는 초기 손절이 유효 기준이다. 활성화된 뒤에는 `effective_stop_price = max(initial_stop_price, trailing_stop_price)`로 계산한다.
- **[사용자 확정: 2026-10-19, v0.12] 공식 청산 신호:** 완료 일봉 종가가 `effective_stop_price` 아래에서 마감하면 stop 신호 이벤트를 기록한다. 장중 저가가 stop 아래로 내려갔더라도 종가가 stop 위에서 마감하면 이 규칙에 따른 공식 청산 신호를 기록하지 않는다. 초기 손절과 추적 손절의 개별 원인도 구분해 `stop_reason`에 남긴다.
- 공식 청산 신호 기록은 주문 실행 또는 거래 완료 기록과 분리한다. 신호만으로 주문을 자동 전송하거나 거래/포지션을 자동으로 청산 완료 처리하지 않는다. 실제 청산 여부와 체결가는 주문/거래 기록으로 별도 반영한다.
- 보유 포지션, 비중, 진입일·진입가, 현재가, 초기 위험, 초기 손절, 추적 손절, 유효 손절 및 청산 신호/사유를 표시한다. Breakout failure, initial stop, trailing stop, take profit, manual/other 청산을 구분한다.
- 부분청산 포지션은 잔여 비중이 있으면 열린 포지션으로 유지한다. 기존 거래 기록을 재계산으로 덮어쓰지 않는다.

---

## 9. Pocket Pivot 및 PP Journal

### 9.1 Pocket Pivot Signal — O’Neil disciple / Morales–Kacher 기준의 기술 규칙

PP는 일반 베이스 패턴 및 Breakout Pivot과 별개인 조기 수급 신호다. 핵심은 **상승일 거래량이 직전 10거래일의 모든 하락일 거래량보다 큰 것**이며, 가격이 10일선 또는 50일선에서 지지를 받거나 이를 회복하는 문맥을 함께 요구한다. 이는 Gil Morales/Chris Kacher의 Pocket Pivot 설명과 일치한다(§22 참고).

**핵심 수치 규칙**
1. 신호일은 완료 일봉 기준 `Close[t] > Close[t-1]`인 상승일이어야 한다.
2. 신호일 거래량은 신호일을 제외한 직전 10개 완료 일봉 중 `Close[i] < Close[i-1]`인 하락일들의 최대 거래량보다 **엄격하게 커야 한다**. 당일 거래량은 비교 구간에 넣지 않는다.
3. 직전 10거래일에 유효한 하락일이 없어 비교 기준을 계산할 수 없다면, 단순히 조건을 자동 통과시키지 않고 `PP_VOLUME_REFERENCE_UNAVAILABLE`로 기록해 신호를 생성하지 않는다.
4. **[사용자 확정: 2026-10-19] 10일선/50일선 거리의 고정 퍼센트 제한(예: 1% 근접, 3% 확장 상한)을 적용하지 않는다.** 대신 가격이 10일선 또는 50일선을 지지·재탈환하는 행동과 전체 상승 추세/건설적 베이스 문맥을 함께 평가한다. 예를 들어 종가가 이동평균선을 상향 회복하거나, 당일 저가가 평균선을 테스트/일시 하회한 뒤 종가가 그 위에서 마감하는지 확인한다. 특정 1%/3% 이격 숫자를 충족하지 않는다는 이유만으로 PP를 자동 거부하지 않는다.
5. PP는 건설적인 베이스 또는 상승 추세 내에서 발생해야 한다. 장기간(약 5개월 이상)의 뚜렷한 하락 추세에서 발생한 단순 반등, 급락 후 V자 회복, 주요 이동평균선 아래에서 지지 구조가 없는 종목은 유효한 진입용 PP로 인정하지 않는다. 이 문맥은 신호일의 가격·거래량 조건과 분리된 `PP_CONTEXT_VALID` 품질 게이트로 기록한다.
6. PP는 10일선/50일선에서의 early-entry/continuation 신호다. Pocket Pivot 자체가 새로운 클래식 패턴이나 Breakout Pivot을 생성하지 않는다. 유효 Candidate Pivot이 존재하면 `PIVOT APPROACH`/돌파 Stage가 우선하고, 아직 피봇 접근/돌파가 아니며 베이스 문맥이 유효할 때만 `BASE + PP`를 표시한다.
7. PP가 탐지된 날짜, 종목, 당시 가격, 거래량, 직전 10일 최대 하락일 거래량, 참조 이동평균선, 패턴/Stage/시장 레짐 문맥, 품질 게이트 통과 여부와 이후 결과를 보존한다. 신호는 날짜별 이벤트로 기록하며 화면 재렌더링 때문에 같은 날 중복 기록하지 않는다.

**기원 자료와 구분:** 10일 최대 하락일 거래량 초과 및 10/50일선 주변 가격 행동은 널리 알려진 Pocket Pivot의 핵심 규칙이다. V2는 사용자 결정에 따라 고정 MA 이격률 제한을 적용하지 않으며, 가격 행동과 추세/베이스 문맥으로 판별한다.

### 9.2 Trade Journal

- PP Signal과 실제 매매는 별도 엔터티이며 필요할 경우 연결한다.
- 실제 최초 진입 Stage를 저장하고 나중의 현재 Stage 변경으로 덮어쓰지 않는다.
- Position 상태는 OPEN/PARTIAL/CLOSED를 지원한다.
- 90%를 청산하고 10%를 보유 중이라면 OPEN/PARTIAL 상태로 남긴다. 전량 청산만 CLOSED 처리한다.
- 추가매수별 진입 날짜/가격/비중/Stage를 기록한다.
- 부분청산과 전량청산을 각각 기록하고 청산 사유를 보존한다.

### 9.3 분석 지표

- PP → Pivot 도달률
- PP → Breakout 도달률
- Breakout Failure 비율
- PP 이후 +10% / +20% 도달률
- 평균 승리/손실, 승률, 손익비, 기대값, MFE/MAE, R multiple
- 부분청산 중 포지션과 완전 종료 포지션을 통계 목적에 따라 구분한다.

---

## 10. Market Regime, Momentum, Leadership

### 10.1 Market Regime

- 시장 추세, 시장 폭(Breadth), 주요 지수, 종목 리더십 등을 활용해 시장 환경을 표시한다.
- 레짐은 매수 신호를 단독 생성하기보다 신호의 위험도/공격성 조절에 사용한다.
- Regime 점수, 구간 이름, 액션 문구, 저장 주기, 데이터 소스는 현행 코드 인벤토리 후 명세한다.

### 10.2 Momentum Monitor

- 종목별 가격·상대강도/모멘텀·패턴·후보/돌파 피봇·거리·RVOL·Stage·Live Quality·공식 상태 이력·매수 이력을 통합 표시한다.
- 장중 가격과 장중 등락률은 EOD 패턴 기준 가격과 분리한다.
- 동일한 엔진 결과를 ADD Monitor와 다른 화면에서도 재사용한다.

### 10.3 Leadership Map

- 단순 상승률 히트맵이 아니라 신고가/강한 상대강도/랭크 상승/섹터 리더십 및 차기 리더 후보를 관찰한다.
- 종목이 리더십 맵에 들어온 이유와 진입 단계가 설명 가능해야 한다.
- 사용자 선호에 따라 강한 리더를 우선 관찰하되, 신호가 약한 시장에서의 위험은 별도로 표시한다.

---

## 11. Portfolio, Allocation, Performance

- 실제 보유 종목과 비중을 기반으로 평가액/손익/기여도/기간 수익률을 계산한다.
- QQQ/SPY와 비교하는 수익률 화면을 유지한다(현재 기능 유지 여부 최종 확인).
- Allocation은 현재 비중과 목표 비중을 구분한다.
- 관심종목(watchlist)과 실제 보유 포지션을 혼동하지 않는다.
- 관심종목에 Core/Satellite 또는 장기 관찰 목적을 표시할 수 있는지 검토한다.
- 입금/출금, 환율, 실현/미실현 손익 계산의 기존 정의를 명세하고 기존 기록과 대조한다.

---

## 12. Supabase와 영속 데이터

### 12.1 현황

현재 파일에서 직접 확인되는 주요 테이블 이름에는 `transactions`, `daily_directions`, `portfolio_values`가 있다. PP Signal/Trade/Exit/Watchlist/Snapshot 등의 데이터도 사용되지만 전체 테이블과 스키마는 추가 조사해 확정해야 한다.

### 12.2 마이그레이션 원칙

- **[확정]** 초기 V2 단계에서는 기존 DB 스키마와 사용자 거래 데이터를 무단 변경하지 않는다.
- 현재 데이터 테이블/필드/키/상태값/Unique constraint/RPC/캐시를 문서화한다.
- 기존 DB 읽기와 V2 계산 결과 비교를 먼저 한다.
- 새 필드가 꼭 필요한 경우 별도 migration 계획, 백업, 롤백 방법을 마련한다.
- 계산 캐시와 업무 기록을 구분한다. 캐시는 폐기/재계산할 수 있어야 하며 거래 기록은 임의로 지우지 않는다.

---

## 13. 성능·운영 요구사항

- 외부 가격 API 호출을 배치/캐시한다.
- 캐시는 키와 유효기간, 데이터 기준일, 강제 새로고침 동작이 명확해야 한다.
- 종목별 중복 계산을 줄이고 한 번 계산한 결과를 필요한 화면에서 재사용한다.
- App Monitor에서 핵심 함수 호출시간, 오류, 캐시 상태, 마지막 성공 갱신을 확인한다.
- 캐시 무효화로 인해 DB 기록이나 매매 이력이 변경되어서는 안 된다.
- 장중 데이터 미수신과 패턴 미검출을 구별한다.

---

## 14. V2 모듈 구조 제안

```text
app.py                       # Streamlit 진입점, 페이지 라우팅
ui/
  overview.py
  momentum_monitor.py
  add_monitor.py
  pp_journal.py
  risk.py
  market_regime.py
  allocation.py
  leadership.py
services/
  market_data_service.py     # OHLCV/장중 가격/외부 데이터/캐시
  portfolio_service.py       # holdings/transactions/performance
  snapshot_service.py        # EOD 스냅샷/공식 상태 이력
  trade_journal_service.py   # PP signals/trades/exits
engines/
  pattern_engine.py          # 패턴별 순수 계산
  pivot_engine.py            # candidate/breakout/next pivot 규칙
  rebase_engine.py           # rebase trigger/state/low tracking
  stage_engine.py            # official stage and transitions
  entry_quality_engine.py    # live quality and reason codes
  risk_engine.py             # initial risk/stops/exit calculations
  momentum_engine.py         # RS/price momentum/volume quality
  regime_engine.py           # regime/breadth calculations
repositories/
  supabase_repository.py     # DB 접근만 담당
models/
  schemas.py                 # typed input/output contracts/enums
config/
  settings.py                # thresholds/feature flags/cache settings
tests/
  fixtures/                  # synthetic OHLCV and known historical cases
  test_rebase_engine.py
  test_pivot_engine.py
  test_stage_engine.py
  test_risk_engine.py
```

이 구조는 초기안이다. 최종 구현 시 파일이 지나치게 작게 쪼개지지 않도록 하되, 계산 규칙·데이터 접근·UI는 분리한다.

---

## 15. 인수 기준 및 필수 검증 사례

V2는 아래 사례가 예상 결과와 맞기 전까지 기존 앱을 대체하지 않는다.

| TEST ID | 사례 | 기대 결과 |
|---|---|---|
| T-01 | 최신 완료 일봉 포함 | 데이터 조회에서 이미 완료 봉을 걸렀다면 하위 계산이 마지막 봉을 다시 제거하지 않음 |
| T-02 | 과거 날짜 상태 재생성 | 미래 데이터 누출 없음 |
| T-03 | Candidate Pivot이 변경됨 | Breakout Pivot은 이벤트 규칙에 따라 보존 |
| T-04 | 종가가 최근 20일 고점 대비 10% 이상 하락 | 기존 Candidate Pattern 즉시 비활성화, 재베이스 진입 |
| T-05 | 종가가 Candidate Pivot 대비 10% 이상 하락 | 기존 후보를 무효화하고 재베이스 추적 |
| T-06 | Breakout Failure 판정 | 현재 돌파 사이클 종료, 과거 Breakout Pivot 이벤트 보존 |
| T-07 | 재베이스 상태에서 추가 급락 | `rebase_started_at` 유지, `rebase_low/date` 갱신 |
| T-08 | 재베이스 후 아직 새 패턴 불충분 | `BASE FORMING`, Candidate Pivot `—`, Source `REBASE_FORMING`, 20D Reference 표시 |
| T-09 | 급락 후 오래된 Double Bottom만 탐지 | 이전 패턴을 새 유효 패턴으로 오인하지 않음 |
| T-10 | 새 패턴이 구조 요건 충족 | 새 Candidate Pivot 생성; 과거 Breakout Pivot은 독립 유지 |
| T-11 | Candidate/Breakout Pivot 거리 | 서로 다른 열과 명시된 기준으로 계산 |
| T-12 | Initial Risk의 최근 저점이 진입가보다 높음 | 유효한 손절선 선택/대체 계산 또는 명시적 실패 상태; 숫자 위험률을 표시하되 Entry Quality 점수/라벨은 Initial Risk 때문에 바뀌지 않음 |
| T-13 | 부분청산 후 잔여 비중 존재 | Position은 OPEN/PARTIAL로 유지 |
| T-14 | 동일 기준 데이터, 여러 화면 | Momentum/ADD/Pattern 화면이 같은 계산 결과 사용 |
| T-15 | 데이터 부족/API 실패 | `DATA INCOMPLETE` 등 명시적 상태, 임의의 정상 신호로 대체하지 않음 |

### 사용자 사례를 고정 테스트로 보존

- AMAT: 재베이스 이후에도 과거 Double Bottom이 살아 있는 후보로 표시되는 문제
- MU: 큰 하락 이후 후보 피봇이 과거 수준에 머무는 문제
- SOXX: 기존 패턴/피봇과 새 재베이스 상태가 충돌하는 문제
- META: 2026-09-21 부근의 실제 Breakout Pivot $718.45를 새 패턴 상단이 덮어쓰지 않는지
- GOOG: 과거/현재 Candidate Pivot이 바뀌더라도 과거 돌파 이력이 분리되는지
- STX: 현재 후보 피봇과 돌파 이력 구분
- AMAT/LRCX: Initial Risk가 합리적인 유효한 stop basis와 숫자 위험률로 계산되는지; Initial Risk 값은 Entry Quality에 영향이 없어야 함

위 가격/날짜 예시는 과거 모니터에서 제공된 사례이며, 최종 테스트 데이터는 원본 OHLCV와 공식 저장 이력을 확보해 고정한다.

---

## 16. 단계별 구축 계획

### Phase 0 — 명세 확정 및 현행 인벤토리

- [ ] 화면별 위젯/액션 인벤토리 작성
- [ ] 모든 Supabase 테이블·필드·RPC·쓰기 경로 목록화
- [ ] 패턴별 공식 조건과 피봇 계산 정의 확정
- [ ] Stage 전이표/Failure 기준 확정
- [ ] 재베이스 상태 전이표 확정
- [ ] Initial Risk/Exit Engine 조건표 확정
- [ ] 필수 사례에 대한 기준 출력 캡처

### Phase 1 — 핵심 계산 엔진

- [ ] 공통 OHLCV input/output schema
- [ ] Pattern/Pivot/Rebase/Stage/Risk 엔진을 순수 계산 함수로 분리
- [ ] 합성 가격 및 실제 고정 사례 테스트
- [ ] 과거 날짜 재현 테스트

### Phase 2 — 데이터/서비스 계층

- [ ] Market data/cache service
- [ ] Supabase repository(읽기 전용 우선)
- [ ] Snapshot/Journal 데이터 어댑터
- [ ] 기존 대시보드와 V2의 계산 결과 비교 로깅

### Phase 3 — Momentum Monitor를 첫 화면으로 이관

- [ ] 단일 계산 결과를 UI에서 표시
- [ ] Candidate/Breakout/20D Reference 및 거리 컬럼 분리
- [ ] 재베이스 상태 및 판정 사유 표시
- [ ] 실사용 사례 대조

### Phase 4 — 핵심 기능 순차 이관

- [ ] ADD Monitor
- [ ] PP Journal/Trade/Exit analytics
- [ ] Risk/Initial Risk/Exit Engine
- [ ] Market Regime/Leadership/Quality Recovery
- [ ] Portfolio/Allocation/Performance/Chart (History UI is removed; historical data storage remains if used by other functions)
- [ ] App Monitor/performance instrumentation

### Phase 5 — Recovery signals [후순위]

- [ ] Recovery Setup 정의 및 표시
- [ ] 20D SMA 상향 교차 50D SMA: Recovery Cross
- [ ] 50D SMA 상향 교차 200D SMA: Golden Cross
- [ ] 기존 Pattern/Pivot/Breakout 로직과 독립적으로 유지
- [ ] 신호 단독으로 Candidate Pivot을 만들거나 BREAKOUT 상태를 발행하지 않음

### Phase 6 — 운영 전환

- [ ] 기존 앱과 V2 병렬 검증
- [ ] 포트폴리오/거래 기록 읽기 비교
- [ ] V2의 모든 필수 인수 기준 통과
- [ ] 쓰기 기능을 작은 범위에서 검증
- [ ] 롤백 가능한 전환 계획 승인 후 기본 앱 전환

---

## 17. 남은 의사결정 목록

1. **Trailing Stop 파라미터는 사용자 승인 완료(v0.12):** 완료 일봉 종가가 +1R에 도달하면 활성화; `highest_high_since_entry − 3×ATR(22)` 적용; 추적 손절은 하향 금지; 완료 일봉 종가가 `effective_stop_price` 아래에서 마감하면 공식 청산 신호만 기록하고 주문 전송/거래 완료 처리는 별도로 한다.
2. **Core/Satellite 적용 단위:** Watchlist 종목 단위인지, 실제 포지션 단위인지 결정한다.
3. **가격 데이터 공급원:** 현재 사용하는 공급원/지연/조정가격(split/dividend adjusted) 정의를 확인한다.
4. **기존 DB 스키마:** 재베이스 시작일/저점과 Breakout Event를 기존 테이블에서 안전하게 복원/저장할 수 있는지 인벤토리 이후 결정한다.
5. **세부 점수/조건 검증:** 패턴별 수치 기준, Breakout Readiness 하위 점수 임계값, PP 문맥 판정은 V2 초기 기본값으로 구현한 뒤 고정 사례/실제 출력으로 검증하고 필요하면 변경관리 절차로 조정한다.


---
## 18. 2026-10-19 결정 로그

### 18.1 사용자 확정 규칙

- V2 기본 UI에서 `Daily Direction`과 `History` 화면은 제거한다. 그 외 현행 UI는 우선 유지하며, 추가 제거/통합은 별도 결정 전까지 하지 않는다.
- `PIVOT APPROACH`는 유효 Candidate Pivot 대비 -5.0% 이상, 0% 미만이다. Candidate Pivot이 없거나 `REBASE_FORMING` 상태이면 적용하지 않는다.
- **[사용자 확정: 2026-10-19] Initial Risk는 Momentum Monitor에서 기존과 같이 수치로 표시하되, EOD/LIVE Entry Quality 점수와 라벨에는 반영하지 않는다. 위험률 >12% 또는 Initial Risk 데이터 누락만으로 `WAIT`/`DATA INCOMPLETE`를 강제하지 않는다. 별도 손절/청산 처리는 Risk/Exit Engine의 책임이다.
- **[사용자 확정: 2026-10-19] Turtle 2N Risk는 V2에서 UI 및 계산 지표로 완전히 제거한다.
- **[사용자 확정: 2026-10-19] Bollinger Bands/BB Width는 V2에서 지표 계산, UI, 캐시/데이터 필드 및 Breakout Readiness 기여 점수까지 완전히 제거한다.
- **[사용자 확정: 2026-10-19]** BB 제거 후 Breakout Readiness contraction 점수 총 25점은 가능한 한 균등하게 가격/ATR 수축 9점, 거래량 수축 8점, 수축 지속성/일관성 8점으로 나눈다. 각 축의 점수 임계값은 구현 직전 별도 계산 명세에서 확정하고, 점수 축은 가능한 한 중복되지 않게 정의한다.
- 공식 `BREAKOUT FAILURE`는 활성 Breakout Pivot 아래에서 **완료 일봉 종가가 마감**했을 때만 확정한다. 장중 -2% 이탈 전용 경고는 추가하지 않으며 LIVE STATE는 기존 방식만 유지한다.
- `BREAKOUT CONFIRMED`는 실제 피봇 상향 교차와 당일 EOD RVOL ≥1.30× 조건이 모두 충족된 **돌파 당일에만** 표시한다. 이후 종가가 활성 Breakout Pivot 위에 유지되면 Stage는 `BREAKOUT`이다.
- `BREAKOUT HOLD`는 공식 ENTRY STAGE에서 삭제한다.
- 새 패턴이 검증된 시점의 종가가 Candidate Pivot 위에 이미 있더라도 돌파 이벤트를 과거 날짜로 소급하지 않는다. 실제 가격 교차가 확인되는 날에 이벤트를 기록한다.
- `20D Reference`는 최근 20개 완료 일봉의 최고 `High`를 사용하는 rolling reference다. 재베이스 시작일에 고정된 피봇이 아니며, Candidate Pivot이나 Breakout Pivot을 대체하지 않는다.
- 과거 Breakout Pivot과 그 돌파 이벤트는 새 Candidate Pivot/Next Pivot 계산으로 덮어쓰지 않는다.
- 재베이스 시작일은 같은 사이클에서 유지하며, 추가 하락으로 새로운 저점이 생기면 최저가와 날짜만 갱신한다.
- 재베이스는 기존 Candidate Pattern을 즉시 무효화하고 새 구조를 탐색한다. 새 패턴은 패턴별 구조·데이터 조건을 충족해야 인정한다.
- PP 성과 분석의 Pivot 대비 -2% 통계는 공식 EOD Breakout Failure 기준과 별개다.

- 패턴 기준은 대중적으로 알려진 O’Neil / IBD(CAN SLIM) 계열의 고전적 베이스 패턴 관행을 우선 참고한다.
- **[사용자 확정 방향: 2026-10-19]** VCP를 지원한다. VCP는 클래식 베이스 패턴과 구별되는 수축 셋업으로 인식하며, 선행 상승 추세·2~6회 연속 수축·수축 폭 감소·거래량 dry-up·최종 수축 피봇을 검사한다. 자동 탐지 수치는 §5.8에서 제안하고 대표 사례 검증 후 고정한다.
- **[사용자 확정: 2026-10-19]** VCP와 클래식 베이스 패턴이 동시에 유효하면 VCP를 우선한다. UI에는 두 패턴명/두 피봇을 동시에 표시하지 않고, 주요 Pattern 라벨을 `VCP`, Candidate Pivot Source를 `VCP`로 하는 단일 셋업만 표시한다. VCP가 유효하지 않을 때만 클래식 패턴 우선순위를 사용한다. 이 우선순위는 Candidate/Next Pivot 선택에만 적용되며 과거 Breakout Pivot을 변경하지 않는다.
- **[사용자 확정: 2026-10-19]** Pocket Pivot은 Gil Morales/Chris Kacher 계열의 널리 알려진 규칙을 출발점으로 삼는다: 상승일, 당일 거래량이 직전 10거래일 내 하락일 최대 거래량 초과, 10일선 또는 50일선의 지지/회복, 건설적인 베이스/상승 추세 문맥. **고정된 이동평균선 이격률 제한은 사용하지 않는다.**
- **[사용자 확정: 2026-10-19]** Initial Risk는 ATR(14)×1.5를 1순위/기본 손절 거리로 사용한다. ATR은 최근 완료 일봉을 기준으로 계산하며, ATR이 계산되지 않을 때만 최근 10일 저점 및 20일선 −2%를 차례대로 대체 기준으로 검토한다. 별도 Turtle 2N Risk 지표는 복원하지 않는다.
- **[사용자 확정: 2026-10-19]** Initial Risk는 숫자로만 표시하고 Entry Quality에 반영하지 않는다. GOOD은 점수 ≥75, CAUTION은 60–74.99, WAIT는 <60으로 시작한다.
- **[사용자 확정 방향: 2026-10-19]** 패턴별 수치 기준표(§5.8)를 V2의 초기 기본값으로 채택한다. 일반적으로 널리 알려진 O’Neil/IBD 및 Minervini식 패턴 기준을 기반으로 하되, 문헌의 관행과 자동화용으로 보충한 숫자를 구분하고, 이후 사례 검증으로 조정한다.
- **[사용자 확정: 2026-10-19]** BB 제거 후 Breakout Readiness는 총 25점으로 유지하고 가격/ATR 수축 9점, 거래량 수축 8점, 수축 지속성/일관성 8점으로 배분한다. GOOD/CAUTION/WAIT 라벨 임계값은 위와 같으며, 하위 세부 계산은 계산 명세에 둔다.
- **[사용자 확정: 2026-10-19]** 초기 손절과 추적 손절을 함께 구현한다. 세부 규칙은 v0.12 §8.2에 따른다: 완료 일봉 종가 +1R 활성화, 진입 이후 최고 High − 3×ATR(22), stop 비하향, 완료 일봉 종가가 유효 손절선 아래에서 마감할 때 공식 청산 신호 기록. 실제 주문 및 거래 완료 처리와 분리한다.
- 유효한 새 패턴이 검증되면 Next Pivot은 즉시 지정한다. 별도의 안정성 대기일은 두지 않으며, 실제 돌파 이벤트는 가격 교차일에만 기록한다.

### 18.2 패턴 최소 기간 — V2 초기 기본값 채택

- **[사용자 확정 방향]** 일반적으로 널리 사용되는 O’Neil / IBD(CAN SLIM) 계열의 고전적 베이스 패턴 기준을 우선 참고한다.
- **[사용자 확정 방향]** 유효한 새 패턴이 검증되면 Candidate Pivot을 즉시 Next Pivot으로 설정하며, 별도의 3일/5일 대기 기간은 두지 않는다. 실제 가격 교차일에만 새 돌파 이벤트를 기록한다.
- **[사용자 결정: 2026-10-19]** §5.8의 패턴별 최소 기간/구조 기준 표를 V2의 초기 기본값으로 채택한다. 기간은 패턴별 조건이며 모든 패턴에 공통으로 적용하는 재베이스 대기 기간이 아니다. 자동화용 제안 숫자는 실제 예시 차트 검증 후 변경관리 절차로 조정할 수 있다.
- **[초기 기본값]** 구체적 구조 패턴을 일반적인 BASE FORMING/Saucer 분류보다 먼저 평가한다. VCP가 유효하면 가장 먼저 선택한다. 패턴 중복 사례 검증 후 실제 분류 순서는 변경관리 절차로 조정할 수 있다.


### 18.3 추가 결정 — V2 구현 기준

- **[사용자 확정: 2026-10-19]** §5.8의 패턴별 기간/구조 수치 초안을 V2의 초기 기본값으로 채택한다. 대중적인 O’Neil/IBD 및 Minervini식 기준을 출발점으로 하며, 자동화용 수치는 검증 후 조정할 수 있다.
- **[사용자 확정: 2026-10-19]** Entry Quality 라벨은 정규화 점수 ≥75 `GOOD`, ≥60 및 <75 `CAUTION`, <60 `WAIT`로 시작한다.
- **[사용자 확정: 2026-10-19]** Breakout Readiness 수축 점수는 25점 총점을 유지하며 가격/ATR 수축 9점, 거래량 수축 8점, 수축 지속성/일관성 8점으로 배분한다. 세부 점수 임계값은 계산 명세의 초기 제안값을 사용하되, 계층 간 중복 채점을 방지한다.
- **[사용자 확정: 2026-10-19]** Pocket Pivot의 고정 이동평균선 거리 제한을 제거한다. 당일 상승과 직전 10일 최대 하락일 거래량 초과, 10일선 또는 50일선 지지/회복, 건설적인 상승 추세/베이스 문맥은 유지한다.
- **[사용자 확정: 2026-10-19]** Initial Risk는 ATR(14)×1.5 우선으로 계산하고, ATR을 사용할 수 없을 때만 유효한 최근 10일 스윙 저점, 그다음 20일선−2%를 대체 기준으로 사용한다. Initial Risk 숫자는 Entry Quality에 반영하지 않는다.
- **[사용자 확정: 2026-10-19, v0.12]** 실제 포지션에는 초기 손절과 추적 손절을 함께 제공한다. 초기 손절은 진입 시 저장한다. 추적 손절은 완료 일봉 종가 +1R 도달 시 활성화하고, `최고 High since entry − 3×ATR(22)`로 산출한다. 손절선은 절대 낮추지 않으며 완료 일봉 종가가 유효 손절선 아래에서 마감할 때 공식 청산 신호 이벤트만 기록한다. 주문 전송과 거래 완료 처리는 별도로 수행한다.

---

## 19. 변경 관리 규칙

- 투자 규칙은 코드 수정 전에 이 명세에 먼저 반영한다.
- 규칙 변경 시 기존 동작/새 동작/영향 화면/테스트 사례를 함께 기록한다.
- 한 번에 하나의 주요 엔진 또는 기능군을 변경한다.
- 기존 Breakout Pivot, 실제 거래 기록, 과거 신호 이력은 무단 삭제/덮어쓰기를 금지한다.
- 새 기능은 Feature Flag 또는 별도 개발 화면에서 검증한 후 기본 화면에 연결한다.
- 기능 명세서와 코드는 같은 버전 번호를 기록한다.

---

## 20. 패턴 기준 참고 자료

이 자료들은 패턴 형태/고전적인 기간을 참고하기 위한 것이며, 자동 스캐너에 사용할 숫자 조건을 직접 확정해 주는 것은 아니다. V2의 수치 조건은 §5.8의 [검토] 상태를 사용자와 최종 합의한 뒤 고정한다.

- Investor's Business Daily, “How To Invest: Base Patterns To Learn And Master For Buying And Selling Stocks” (2025-03-11). 이 자료는 전형적인 기준으로 Flat Base 약 5주, Cup Without Handle 약 6주, 많은 다른 베이스 약 7주를 제시하고, 베이스 깊이와 거래량의 중요성을 강조한다: https://www.investors.com/how-to-invest/investors-corner/how-to-invest-stock-chart-analysis/
- Moneycontrol, “Technical Classroom: How to analyze a stock's cup-with-handle” (2019-06-01). Cup & Handle의 7주 최소 기간, 핸들 최소 5거래일, 핸들이 컵 상단 절반에 있어야 한다는 조건 등을 설명한다: https://www.moneycontrol.com/news/business/markets/technical-classroom-how-to-analyze-a-stocks-cup-with-handle-4054131.html
- Investor's Business Daily, “How To Buy Growth Stocks: Why 3 Mild Retreats May Yield The Bullish Ascending Base” (2024-05-28). Ascending Base의 3회 연속 higher high/higher low 후퇴와 약 9–16주 기간을 설명한다: https://www.investors.com/how-to-invest/investors-corner/chart-reading-202-how-3-retreats-may-yield-an-ascending-base/
- Investor's Business Daily, “Stock Market Winners Often Take Flight In The Base-on-Base Pattern” (2026-01-09). 첫 돌파 뒤 20% 미만의 상승 후 두 번째 베이스가 주로 첫 베이스 위에서 형성되는 구조를 설명한다: https://www.investors.com/how-to-invest/investors-corner/stock-market-winners-take-flight-base-on-base-pattern/


## 21. 기존 앱의 코드 의존성 조사 — 현행 코드에 대한 역사적 참고

아래는 구버전 `app.py`의 현재 동작을 기록한 내용이며, 이는 V2의 확정 규칙이 아니다. V2에서는 §7.7–§7.8 및 §18.1의 최신 사용자 결정을 우선한다.

- 현행 `app.py`의 `v47_entry_quality()`에서 Initial Risk는 Entry Quality 가중치 20으로 사용되며, 위험률 >12%는 WAIT 조건이다. 필수 위험 데이터가 없으면 DATA INCOMPLETE가 된다.
- 현행 `v49_live_entry_quality()` 역시 Initial Risk를 가중치 20으로 사용하며, 위험률 >12% 또는 현재가가 stop 이하인 경우 WAIT 처리한다. **V2에서는 이 의존성을 제거한다.**
- Turtle 2N Risk는 현행 Entry Quality 점수에는 포함되지 않고 비교용 수치로 표시된다. **V2에서는 해당 지표와 계산을 제거한다.**
- 현행 `v22_technical_confirmation()`은 BB Width를 표시용으로 계산한다. 현행 `v257_breakout_readiness()`는 contraction 점수에서 BB Width percentile에 최대 7점, BB Width 지속성에 최대 3점을 제공한다. **V2에서는 BB Width 계산과 해당 10점 기여를 모두 제거하고, 가격/ATR 수축 및 거래량 수축 관련 점수로 재배분한다.**

이 섹션은 기존 코드의 동작을 설명할 뿐이며 V2에서 유지하라는 요구사항이 아니다. 구현자는 새 코드에 현행 의존성을 그대로 복사하지 않아야 한다.

## 22. 참고자료 — 패턴 규칙

- Investor’s Business Daily, “How To Read Stock Charts” infographic: Cup-with-Handle / Double Bottom 최소 7주, Flat Base 최소 5주, 대표적인 base depth와 pivot 설명. https://get.investors.com/wp-content/uploads/2023/05/MS-How-to-Read-Stock-Charts.pdf
- Investor’s Business Daily, “Seeking Bullish Stock Charts? Cup Without Handle Amazingly Easy To Spot” (2024): Cup Without Handle 최소 6주, 약 15–33% 깊이, U자형 및 prior high buy point. https://www.investors.com/how-to-invest/investors-corner/stock-charts-cup-without-handle-amazingly-easy-to-spot/
- StockCharts ChartSchool, “Double Bottom Reversal”: 두 trough 간 약 3% 이내, 첫 trough에서 central peak까지 약 10–20% 반등, 두 trough 간 약 4주 이상 선호. https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/double-bottom-reversal
- StockCharts ChartSchool, “Cup With Handle”: U자형 컵, handle, base depth, 기간, 거래량의 고전적 설명. https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/cup-with-handle
- Investor’s Business Daily, “How To Buy Growth Stocks: Why 3 Mild Retreats May Yield The Bullish Ascending Base” (2024): 세 번의 higher-high/higher-low pullbacks, 9–16주, 조정폭의 일반적 범위. https://www.investors.com/how-to-invest/investors-corner/chart-reading-202-how-3-retreats-may-yield-an-ascending-base/
- Investor’s Business Daily, “Stock Market Winners Often Take Flight In The Base-on-Base Pattern” (2026): 두 번째 base가 첫 base 돌파 이후 +20% 전에 주로 첫 base 상단 근처/위에 형성되는 개념. https://www.investors.com/how-to-invest/investors-corner/stock-market-winners-take-flight-base-on-base-pattern/
- Investor’s Business Daily, “After A 102% Rip Higher In 2025, Is Micron Technology Stock Delivering A New Buy Point Soon?” (2025): High Tight Flag의 4–8주 Pole에서 최소 약 100% 상승, 이어지는 Flag가 10–25% 조정하는 드문 패턴임을 설명한다. https://www.investors.com/research/micron-technology-stock-mu-ai-new-buy-point-markets-investing/
- StockCharts ChartSchool, “Rounding Bottom”: Saucer/Rounding Bottom은 장기적이고 완만한 반전 패턴이며, 흔히 주봉 차트에서 더 적합하다고 설명한다. V2의 ≥40거래일/≤25% 기준은 이 설명을 자동 탐지용으로 구체화한 제안이지, StockCharts의 고정 수치 규칙이 아니다. https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/rounding-bottom



## 23. 추가 참고 자료 — VCP, Pocket Pivot, ATR Risk

- Gil Morales / Chris Kacher, *In The Trading Cockpit with the O’Neil Disciples*: Pocket Pivot의 핵심 거래량 규칙(당일 거래량이 직전 10일의 어떤 하락일 거래량보다 큼) 및 10일/50일 이동평균선 주변의 지지/회복 문맥을 설명한다. https://www.everand.com/book/343295426/In-The-Trading-Cockpit-with-the-O-Neil-Disciples-Strategies-that-Made-Us-18-000-in-the-Stock-Market
- New Trader U, “Ten Rules for Trading Pocket Pivots”: 건설적인 베이스/상승 추세, 10일 최대 하락일 거래량 비교, 장기 하락 추세 및 주요 이동평균선 아래의 약한 설정을 피하는 문맥을 요약한다. https://www.newtraderu.com/2012/08/21/ten-rules-for-trading-pocket-pivots-2/
- Mark Minervini, *Trade Like a Stock Market Wizard* (available excerpt): 연속 수축은 대체로 이전 수축의 약 절반 정도로 줄어드는 경향, 보통 2~4회(때로 5~6회) 수축을 설명한다. https://studylib.net/doc/28040183/mark-minervini---think-and-trade-like-a-stock-market-wizard-how-to-achieve-super-per
- The Desk, “Minervini VCP rules”: 자동 탐지 관점에서 2~6회 수축, 첫 수축 10~35%, 최종 수축 8% 이하, 거래량 감소 등을 정량화한 예시다. 해당 사이트의 모든 규칙이 원전의 엄격한 공식 조건이라는 뜻은 아니며, V2의 수치 후보를 비교하는 보조 자료로만 사용한다. https://deanjones.com/scanner/learn
- Fidelity, “Average True Range (ATR)”: ATR은 보통 14기간으로 계산하며, stop 설정에 ATR 배수를 쓰는 방식을 설명하고 예로 1.5× ATR을 언급한다. 이는 보편적으로 강제된 유일한 배수가 아니라 전략별로 선택하는 파라미터다. https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr
- Fidelity/StockCharts 설명에서 ATR은 갭을 포함한 가격 변동성을 측정하므로 고정 퍼센트 stop보다 변동성에 적응할 수 있다. V2의 초기 stop은 사용자가 ATR(14) × 1.5로 확정했다. https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/atr-trailing-stops
- StockCharts ChartSchool, “Chandelier Exit”: 일반적인 long trailing stop 예시는 최근 22기간 최고 High − 3×ATR(22)이며 22, 3.0을 기본 설정으로 설명한다. V2 추적 손절의 후보 파라미터로만 참고하며 아직 사용자 승인 전이다. https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit
