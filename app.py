"""연저펀 대시보드 — 엑셀 포트폴리오 기록과 성과 차트만 관리합니다."""
from __future__ import annotations

from datetime import date
from io import BytesIO
import os
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from pension_dashboard import (
    ETF_NAMES,
    enrich_holdings,
    import_excel_bytes,
    portfolio_summary,
)

st.set_page_config(page_title="연저펀 대시보드", page_icon="📈", layout="wide")

st.markdown("""
<style>
    .block-container {padding-top: 1.35rem; padding-bottom: 2rem; max-width: 1600px;}
    [data-testid="stMetric"] {background: rgba(128,128,128,0.08); padding: 14px 16px; border-radius: 12px; border: 1px solid rgba(128,128,128,0.16); min-width: 0;}
    /* Large KRW KPI values can overflow their cards on smaller screens. */
    .st-key-pension_top_kpis [data-testid="stMetricValue"],
    .st-key-pension_top_kpis [data-testid="stMetricValue"] > div {
        font-size: clamp(1rem, 1.4vw, 1.35rem) !important;
        line-height: 1.2 !important;
        white-space: nowrap !important;
        overflow: visible !important;
    }
    .st-key-pension_top_kpis [data-testid="stMetricLabel"] {font-size: 0.9rem !important;}
    h1, h2, h3 {letter-spacing: -0.025em;}
    div[data-testid="stDataFrame"] {border-radius: 10px;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_quote(ticker: str) -> dict[str, Any]:
    """Try Yahoo intraday first, then the last daily close for Korean ETFs."""
    symbol = f"{ticker}.KS" if ticker.isdigit() else ticker
    errors: list[str] = []
    for period, interval, source in [("1d", "1m", "Yahoo Finance · 1분봉"), ("5d", "1d", "Yahoo Finance · 일봉")]:
        try:
            hist = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, timeout=12)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if closes.empty:
                continue
            stamp = closes.index[-1]
            try:
                asof = pd.Timestamp(stamp).strftime("%Y-%m-%d %H:%M" if interval == "1m" else "%Y-%m-%d")
            except Exception:
                asof = ""
            return {"price": float(closes.iloc[-1]), "asof": asof, "source": source}
        except Exception as exc:  # Provider/network issues fall through to the next source.
            errors.append(str(exc))
    return {"price": None, "asof": "", "source": "시세 조회 실패"}


def safe_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return os.getenv(name, "").strip()


def get_supabase_client():
    url = safe_secret("SUPABASE_URL")
    key = safe_secret("SUPABASE_ANON_KEY") or safe_secret("SUPABASE_KEY")
    if not url or not key:
        return None
    if "supabase_client" not in st.session_state:
        from supabase import create_client
        st.session_state.supabase_client = create_client(url, key)
    return st.session_state.supabase_client


def auth_panel(client) -> dict[str, Any] | None:
    if client is None:
        st.sidebar.warning("로컬 임시 모드 · Supabase 설정 전에는 데이터가 세션 종료 후 보존되지 않습니다.")
        return {"id": "local-session", "email": "local"}
    user = st.session_state.get("pension_user")
    if user:
        st.sidebar.caption(f"로그인: {user.get('email') or '사용자'}")
        if st.sidebar.button("로그아웃", use_container_width=True):
            try:
                client.auth.sign_out()
            except Exception:
                pass
            st.session_state.pop("pension_user", None)
            st.session_state.pop("pension_data_loaded", None)
            st.rerun()
        return user
    st.title("연저펀 대시보드 로그인")
    st.caption("V2의 Supabase 인증을 사용합니다. 새 연저펀 테이블은 기존 매매 분석 데이터와 분리되어 있습니다.")
    with st.form("pension_login"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
    if submitted:
        try:
            response = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
            logged_user = getattr(response, "user", None)
            if logged_user is None:
                st.error("로그인 응답에서 사용자 정보를 확인하지 못했습니다.")
            else:
                st.session_state.pension_user = {"id": str(logged_user.id), "email": getattr(logged_user, "email", email)}
                st.rerun()
        except Exception as exc:
            st.error(f"로그인 실패: {exc}")
    st.info("Supabase Secrets가 없으면 로컬 임시 모드로 실행됩니다. 배포 환경에서는 Secrets와 SQL 스키마 설정이 필요합니다.")
    return None


def _rows_to_records(df: pd.DataFrame, date_col: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if df is None or df.empty:
        return records
    for row in df.to_dict("records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                clean[key] = None
            elif isinstance(value, pd.Timestamp):
                clean[key] = value.date().isoformat()
            else:
                clean[key] = value.item() if hasattr(value, "item") else value
        if clean.get(date_col):
            records.append(clean)
    return records


def load_data(client, user_id: str) -> dict[str, list[dict[str, Any]]]:
    if client is None:
        return st.session_state.get("local_pension_data", {"holdings": [], "monthly": [], "weekly": []})
    try:
        holdings = client.table("pension_holdings").select("*").eq("user_id", user_id).order("sort_order").execute().data or []
        monthly = client.table("pension_monthly_history").select("*").eq("user_id", user_id).order("record_month").execute().data or []
        weekly = client.table("pension_weekly_returns").select("*").eq("user_id", user_id).order("record_date").execute().data or []
        st.session_state.pension_db_error = None
        return {"holdings": holdings, "monthly": monthly, "weekly": weekly}
    except Exception as exc:
        st.session_state.pension_db_error = str(exc)
        return st.session_state.get("local_pension_data", {"holdings": [], "monthly": [], "weekly": []})


def save_data(client, user_id: str, data: dict[str, list[dict[str, Any]]]) -> None:
    if client is None:
        st.session_state.local_pension_data = data
        return
    # Dedicated tables only; no old watchlist or trading-analysis tables are touched.
    mappings = [
        ("pension_holdings", "user_id,ticker", "holdings"),
        ("pension_monthly_history", "user_id,record_month", "monthly"),
        ("pension_weekly_returns", "user_id,record_date", "weekly"),
    ]
    for table, conflict, key in mappings:
        records = []
        for item in data.get(key, []):
            record = {k: v for k, v in item.items() if k not in {"id", "created_at", "updated_at"}}
            record["user_id"] = user_id
            records.append(record)
        if records:
            client.table(table).upsert(records, on_conflict=conflict).execute()


def prepare_data_for_save(parsed: dict[str, list[dict[str, Any]]], user_id: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for key, rows in parsed.items():
        result[key] = []
        for row in rows:
            clone = dict(row)
            if key == "weekly":
                for column in ["semi_return", "dow_return", "sp500_return", "nasdaq_return", "semi_change", "dow_change", "sp500_change", "nasdaq_change"]:
                    clone.setdefault(column, None)
            result[key].append(clone)
    return result


def display_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"₩{float(value):,.0f}"


def display_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:+.2f}%"


def monthly_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["record_month", "invested_amount", "total_balance", "profit", "return_pct", "return_change"])
    if "record_month" in df:
        df["record_month"] = pd.to_datetime(df["record_month"], errors="coerce")
    return df.sort_values("record_month").reset_index(drop=True)


def weekly_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["record_date", "semi_return", "dow_return", "sp500_return", "nasdaq_return", "semi_change", "dow_change", "sp500_change", "nasdaq_change"])
    if "record_date" in df:
        df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
    return df.sort_values("record_date").reset_index(drop=True)


def show_kpis(summary: dict[str, float], latest_month: dict[str, Any] | None = None) -> None:
    delta = None
    if latest_month and latest_month.get("return_change") is not None and pd.notna(latest_month.get("return_change")):
        delta = f"전월 대비 {float(latest_month['return_change']) * 100:+.2f}%p"
    # A named container lets CSS resize the two long KRW values without shrinking
    # percentages elsewhere in the app.
    with st.container(key="pension_top_kpis"):
        cols = st.columns(4)
        cols[0].metric("현재 평가금", display_number(summary["market_value"]))
        cols[1].metric("누적 투자금", display_number(summary["invested"]))
        cols[2].metric("평가손익", display_number(summary["profit"]), display_percent(summary["return_pct"]))
        cols[3].metric("최근 월말 수익률", display_percent(latest_month.get("return_pct") if latest_month else None), delta)


def render_overview(data: dict[str, list[dict[str, Any]]], holdings_df: pd.DataFrame) -> None:
    st.title("연저펀 포트폴리오")
    st.caption("기존 엑셀의 보유 현황과 월말 기록을 기반으로 만든 대시보드")
    history = monthly_df(data.get("monthly", []))
    latest = history.iloc[-1].to_dict() if not history.empty else None
    summary = portfolio_summary(holdings_df)
    show_kpis(summary, latest)
    st.subheader("현재 보유 현황")
    if not holdings_df.empty and holdings_df["현재가"].isna().any():
        missing = ", ".join(holdings_df.loc[holdings_df["현재가"].isna(), "종목명"].astype(str).tolist())
        st.warning(f"현재 시세와 엑셀 백업값을 모두 찾지 못한 종목이 있습니다: {missing}. 표시된 총 평가금은 해당 종목을 제외할 수 있습니다.")
    if holdings_df.empty:
        st.info("데이터 관리 메뉴에서 기존 엑셀 파일을 가져오세요.")
    else:
        show = holdings_df.copy()
        for col in ["목표비중", "현재비중", "수익률", "목표비중차이", "예상비중"]:
            show[col] = show[col].map(lambda x: (f"{float(x) * 100:.2f}%" if col in ["목표비중", "현재비중", "예상비중"] else display_percent(x)) if x is not None and not pd.isna(x) else "—")
        for col in ["월투자금", "매수단가", "현재가", "투자금", "평가금", "평가손익", "리밸런싱금액", "예상평가액"]:
            show[col] = show[col].map(display_number)
        show["월매수량"] = show["월매수량"].map(lambda x: f"{x:,.2f}" if x is not None and not pd.isna(x) else "—")
        show["추가주문수"] = show["추가주문수"].map(lambda x: f"{x:+,.0f}" if x is not None and not pd.isna(x) else "—")
        show = show.rename(columns={"티커": "티커", "시세기준": "시세 기준", "시세출처": "가격 출처", "리밸런싱금액": "목표비중 조정금액", "추가주문수": "목표수량 조정", "예상평가액": "조정 후 평가액", "예상비중": "조정 후 비중"})
        wanted = ["자산군", "종목명", "티커", "수량", "매수단가", "현재가", "투자금", "평가금", "평가손익", "수익률", "목표비중", "현재비중", "목표비중 조정금액", "월투자금", "월매수량", "목표수량 조정", "조정 후 평가액", "조정 후 비중", "시세 기준", "가격 출처"]
        st.dataframe(show[[c for c in wanted if c in show.columns]], use_container_width=True, hide_index=True)
        with st.expander("계산 방식 및 시세 안내"):
            st.write("현재가는 Yahoo Finance의 국내 ETF 코드(예: 390390.KS)에서 조회합니다. 장중 1분봉을 우선 시도하고, 실패하면 최근 일봉 종가를 사용합니다. 모두 실패하면 엑셀에 저장된 마지막 시세를 표시하며 출처에 이를 구분합니다.")
            st.write("리밸런싱금액은 (목표비중 − 현재비중) × 현재 평가금으로 계산합니다. 목표수량 조정은 기존 엑셀 방식에 맞춰 누적 투자금 × 목표비중을 현재가로 나눈 목표 수량에서 현재 수량을 차감합니다. 투자 판단이나 주문 전송은 하지 않습니다.")
    st.subheader("월말 자산 추이")
    if history.empty:
        st.info("월말 기록이 없습니다.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=history["record_month"], y=history["invested_amount"], mode="lines", name="누적 투자금"))
        fig.add_trace(go.Scatter(x=history["record_month"], y=history["total_balance"], mode="lines", name="총잔고"))
        fig.update_layout(template="plotly_dark", height=360, margin=dict(l=10, r=10, t=25, b=10), legend=dict(orientation="h", y=1.08))
        fig.update_yaxes(title="원화 (KRW)", tickformat=",.0f")
        st.plotly_chart(fig, use_container_width=True)

        # Combo chart inspired by the spreadsheet: grouped monthly bars plus a
        # cumulative-return line on a secondary percentage axis.
        st.subheader("월별 투자금·평가금과 누적 수익률")
        combo = history.copy()
        combo["month_label"] = combo["record_month"].dt.strftime("%Y-%m")
        invested_values = pd.to_numeric(combo["invested_amount"], errors="coerce")
        balance_values = pd.to_numeric(combo["total_balance"], errors="coerce")
        profit_values = pd.to_numeric(
            combo.get("profit", pd.Series(index=combo.index, dtype="float64")), errors="coerce"
        ).fillna(balance_values - invested_values)
        return_values = pd.to_numeric(
            combo.get("return_pct", pd.Series(index=combo.index, dtype="float64")), errors="coerce"
        )
        months = combo["month_label"].fillna("").tolist()

        combo_fig = go.Figure()
        combo_fig.add_trace(go.Bar(
            x=months, y=balance_values, name="총잔고(평가금)",
            marker_color="#f01414",
            hovertemplate="%{x}<br>총잔고: ₩%{y:,.0f}<extra></extra>",
        ))
        combo_fig.add_trace(go.Bar(
            x=months, y=invested_values, name="누적 투자금",
            marker_color="#1769d2",
            hovertemplate="%{x}<br>누적 투자금: ₩%{y:,.0f}<extra></extra>",
        ))
        combo_fig.add_trace(go.Bar(
            x=months, y=profit_values, name="수익금",
            marker_color="#10a35a",
            hovertemplate="%{x}<br>수익금: ₩%{y:,.0f}<extra></extra>",
        ))
        if return_values.notna().any():
            return_labels = [f"{v:.1%}" if pd.notna(v) else "" for v in return_values]
            max_return = max(0.0, float(return_values.max()))
            right_axis_max = max(0.8, (int(max_return / 0.2) + 1) * 0.2)
            combo_fig.add_trace(go.Scatter(
                x=months, y=return_values, name="누적 수익률",
                mode="lines+markers+text", text=return_labels,
                textposition="top center", cliponaxis=False,
                textfont=dict(size=9, color="#222222"),
                line=dict(color="#222222", width=2.5),
                marker=dict(color="#222222", size=5),
                yaxis="y2",
                hovertemplate="%{x}<br>누적 수익률: %{y:.1%}<extra></extra>",
            ))
        else:
            right_axis_max = 0.8
        combo_fig.update_layout(
            template="plotly_white", barmode="group", height=460,
            margin=dict(l=12, r=22, t=28, b=78),
            bargap=0.22, bargroupgap=0.04,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            xaxis=dict(
                title=None, tickangle=-45, tickfont=dict(size=9),
                categoryorder="array", categoryarray=months,
            ),
            yaxis=dict(
                title="금액 (원)", tickformat=",.0f",
                gridcolor="#dedede", zerolinecolor="#a8a8a8",
            ),
            yaxis2=dict(
                title="누적 수익률", overlaying="y", side="right",
                tickformat=".0%", dtick=0.2, range=[0, right_axis_max],
                showgrid=False, zeroline=False,
            ),
        )
        st.plotly_chart(combo_fig, use_container_width=True)

    if not holdings_df.empty:
        st.subheader("목표 비중 vs 현재 비중")
        alloc = holdings_df[["종목명", "목표비중", "현재비중"]].melt(id_vars="종목명", var_name="구분", value_name="비중")
        alloc["구분"] = alloc["구분"].map({"목표비중": "목표", "현재비중": "현재"})
        fig_alloc = px.bar(alloc, x="종목명", y="비중", color="구분", barmode="group", template="plotly_dark")
        fig_alloc.update_layout(height=330, margin=dict(l=10, r=10, t=15, b=10), yaxis_tickformat=".0%")
        st.plotly_chart(fig_alloc, use_container_width=True)


def render_monthly_history(data: dict[str, list[dict[str, Any]]], client, user_id: str) -> None:
    if st.session_state.pop("_reset_monthly_history_editor", False):
        st.session_state.pop("monthly_history_editor", None)
    st.title("월말 기록")
    st.caption("투자금 · 총잔고 · 수익금 · 누적 수익률 기록을 관리합니다.")
    df = monthly_df(data.get("monthly", []))
    if df.empty:
        st.info("데이터 관리 메뉴에서 엑셀을 가져오면 월말 기록이 표시됩니다.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["record_month"], y=df["return_pct"] * 100, mode="lines+markers", name="누적 수익률(%)"))
        fig.update_layout(template="plotly_dark", height=320, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="수익률 (%)")
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        st.plotly_chart(fig, use_container_width=True)
        bar = go.Figure()
        bar.add_trace(go.Bar(x=df["record_month"], y=df["profit"], name="수익금"))
        bar.update_layout(template="plotly_dark", height=280, margin=dict(l=10, r=10, t=15, b=10), yaxis_title="수익금 (원)")
        st.plotly_chart(bar, use_container_width=True)
        change_chart = go.Figure()
        change_chart.add_trace(go.Bar(x=df["record_month"], y=df["return_change"] * 100, name="수익률 증감"))
        change_chart.add_hline(y=0, line_dash="dot", line_color="gray")
        change_chart.update_layout(template="plotly_dark", height=260, margin=dict(l=10, r=10, t=15, b=10), yaxis_title="전월 대비 수익률 변화 (%p)")
        st.plotly_chart(change_chart, use_container_width=True)
        display = df.copy()
        display["record_month"] = display["record_month"].dt.strftime("%Y-%m")
        display["return_pct"] = display["return_pct"] * 100
        display["return_change"] = display["return_change"] * 100
        display = display.rename(columns={"record_month": "월", "invested_amount": "투자금", "total_balance": "총잔고", "profit": "수익금", "return_pct": "누적 수익률", "return_change": "수익률 증감"})
        edited_monthly = st.data_editor(
            display, use_container_width=True, hide_index=True, num_rows="fixed", key="monthly_history_editor",
            disabled=["월", "수익금", "수익률 증감"],
            column_config={
                "투자금": st.column_config.NumberColumn(format="₩%.0f", min_value=0, step=100000),
                "총잔고": st.column_config.NumberColumn(format="₩%.0f", min_value=0, step=100000),
                "수익금": st.column_config.NumberColumn(format="₩%.0f"),
                "누적 수익률": st.column_config.NumberColumn(format="%.2f%%", help="수익률(%)을 입력하세요."),
                "수익률 증감": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
        if st.button("월말 기록 수정 저장", type="primary", key="save_monthly_history"):
            edited_monthly = edited_monthly.sort_values("월")
            new_records = []
            previous_return = None
            for _, row in edited_monthly.iterrows():
                month_value = str(row["월"]).strip()
                month_dt = pd.to_datetime(month_value + "-01" if len(month_value) == 7 else month_value, errors="coerce")
                if pd.isna(month_dt):
                    continue
                invested_value = float(row["투자금"]) if pd.notna(row["투자금"]) else 0.0
                balance_value = float(row["총잔고"]) if pd.notna(row["총잔고"]) else 0.0
                return_value = float(row["누적 수익률"]) / 100 if pd.notna(row["누적 수익률"]) else (balance_value / invested_value - 1 if invested_value else None)
                change_value = return_value - previous_return if return_value is not None and previous_return is not None else None
                new_records.append({"record_month": month_dt.strftime("%Y-%m-01"), "invested_amount": invested_value, "total_balance": balance_value, "profit": balance_value-invested_value, "return_pct": return_value, "return_change": change_value})
                previous_return = return_value
            new_data = dict(data)
            new_data["monthly"] = new_records
            try:
                save_data(client, user_id, new_data)
                st.session_state["_reset_monthly_history_editor"] = True
                st.success("월말 기록을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"저장에 실패했습니다. Supabase 스키마 설정을 확인하세요: {exc}")
    st.divider()
    with st.expander("월말 기록 추가", expanded=False):
        with st.form("add_monthly_record"):
            month = st.date_input("기록 월", value=pd.Timestamp.now(tz="Asia/Seoul").date().replace(day=1), help="해당 월의 1일을 선택하세요.")
            invested = st.number_input("누적 투자금 (원)", min_value=0, step=100000, value=0)
            balance = st.number_input("총잔고 (원)", min_value=0, step=100000, value=0)
            submitted = st.form_submit_button("월말 기록 저장", type="primary")
        if submitted:
            month_str = month.replace(day=1).isoformat()
            ret = balance / invested - 1 if invested else None
            previous = df.iloc[-1]["return_pct"] if not df.empty else None
            change = ret - previous if ret is not None and previous is not None else None
            record = {"record_month": month_str, "invested_amount": float(invested), "total_balance": float(balance), "profit": float(balance-invested), "return_pct": ret, "return_change": change}
            new_data = dict(data)
            new_data["monthly"] = [r for r in data.get("monthly", []) if str(r.get("record_month", ""))[:7] != month_str[:7]] + [record]
            new_data["monthly"] = sorted(new_data["monthly"], key=lambda r: str(r.get("record_month", "")))
            previous_return = None
            for saved in new_data["monthly"]:
                saved["profit"] = float(saved.get("total_balance") or 0) - float(saved.get("invested_amount") or 0)
                current_return = saved.get("return_pct")
                saved["return_change"] = current_return - previous_return if current_return is not None and previous_return is not None else None
                if current_return is not None:
                    previous_return = current_return
            try:
                save_data(client, user_id, new_data)
                st.success("월말 기록을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"저장에 실패했습니다. Supabase 스키마 설정을 확인하세요: {exc}")


def render_weekly(data: dict[str, list[dict[str, Any]]], client, user_id: str) -> None:
    if st.session_state.pop("_reset_weekly_returns_editor", False):
        st.session_state.pop("weekly_returns_editor", None)
    st.title("ETF 주간 성과")
    st.caption("엑셀의 날짜별 ETF 누적 수익률을 비교합니다.")
    df = weekly_df(data.get("weekly", []))
    series = {
        "semi_return": "KODEX 미국반도체",
        "dow_return": "ACE 미국다우존스",
        "sp500_return": "TIGER S&P500",
        "nasdaq_return": "ACE 미국나스닥100",
    }
    if df.empty:
        st.info("데이터 관리 메뉴에서 엑셀을 가져오면 주간 성과가 표시됩니다.")
    else:
        fig = go.Figure()
        for column, name in series.items():
            if column in df and df[column].notna().any():
                fig.add_trace(go.Scatter(x=df["record_date"], y=df[column] * 100, mode="lines+markers", name=name))
        fig.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="누적 수익률 (%)", legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("누적 수익률은 원본 시트의 B:E열, 주간 증감률은 H:K열 값을 사용합니다.")
        latest = df.iloc[-1]
        previous = df.iloc[-2] if len(df) > 1 else None
        cols = st.columns(4)
        for i, (column, name) in enumerate(series.items()):
            current = latest.get(column)
            delta = (latest.get(column) - previous.get(column)) * 100 if previous is not None and pd.notna(latest.get(column)) and pd.notna(previous.get(column)) else None
            cols[i].metric(name, display_percent(current), f"최근 기록 변화 {delta:+.2f}%p" if delta is not None else None)

        change_series = {
            "semi_change": "KODEX 미국반도체 주간 증감",
            "dow_change": "ACE 미국다우존스 주간 증감",
            "sp500_change": "TIGER S&P500 주간 증감",
            "nasdaq_change": "ACE 미국나스닥100 주간 증감",
        }
        if any(column in df and df[column].notna().any() for column in change_series):
            change_fig = go.Figure()
            for column, name in change_series.items():
                if column in df and df[column].notna().any():
                    change_fig.add_trace(go.Bar(x=df["record_date"], y=df[column] * 100, name=name))
            change_fig.update_layout(template="plotly_dark", barmode="group", height=320, margin=dict(l=10, r=10, t=15, b=10), yaxis_title="주간 증감률 (%p)", legend=dict(orientation="h", y=1.12))
            change_fig.add_hline(y=0, line_dash="dot", line_color="gray")
            st.plotly_chart(change_fig, use_container_width=True)

        table = df.copy()
        table["record_date"] = table["record_date"].dt.strftime("%Y-%m-%d")
        for column in [*series.keys(), *change_series.keys()]:
            if column in table:
                table[column] = table[column] * 100
        table = table.rename(columns={"record_date": "날짜", **series, **change_series})
        change_labels = list(change_series.values())
        edited_weekly = st.data_editor(
            table, use_container_width=True, hide_index=True, num_rows="fixed", key="weekly_returns_editor",
            disabled=["날짜", *change_labels],
            column_config={
                "KODEX 미국반도체": st.column_config.NumberColumn("KODEX 미국반도체 누적", format="%.2f%%"),
                "ACE 미국다우존스": st.column_config.NumberColumn("ACE 미국다우존스 누적", format="%.2f%%"),
                "TIGER S&P500": st.column_config.NumberColumn("TIGER S&P500 누적", format="%.2f%%"),
                "ACE 미국나스닥100": st.column_config.NumberColumn("ACE 미국나스닥100 누적", format="%.2f%%"),
                **{label: st.column_config.NumberColumn(label, format="%.2f%%") for label in change_labels},
            },
        )
        if st.button("주간 성과 수정 저장", type="primary", key="save_weekly_returns"):
            new_records = []
            for _, row in edited_weekly.iterrows():
                date_value = pd.to_datetime(str(row["날짜"]), errors="coerce")
                if pd.isna(date_value):
                    continue
                new_records.append({
                    "record_date": date_value.date().isoformat(),
                    "semi_return": float(row["KODEX 미국반도체"]) / 100 if pd.notna(row["KODEX 미국반도체"]) else None,
                    "dow_return": float(row["ACE 미국다우존스"]) / 100 if pd.notna(row["ACE 미국다우존스"]) else None,
                    "sp500_return": float(row["TIGER S&P500"]) / 100 if pd.notna(row["TIGER S&P500"]) else None,
                    "nasdaq_return": float(row["ACE 미국나스닥100"]) / 100 if pd.notna(row["ACE 미국나스닥100"]) else None,
                })
            new_records.sort(key=lambda r: r["record_date"])
            for i, record in enumerate(new_records):
                next_record = new_records[i + 1] if i + 1 < len(new_records) else None
                for return_key, change_key in [("semi_return", "semi_change"), ("dow_return", "dow_change"), ("sp500_return", "sp500_change"), ("nasdaq_return", "nasdaq_change")]:
                    current_value = record.get(return_key)
                    next_value = next_record.get(return_key) if next_record else None
                    record[change_key] = next_value - current_value if next_value is not None and current_value is not None else None
            new_data = dict(data)
            new_data["weekly"] = new_records
            try:
                save_data(client, user_id, new_data)
                st.session_state["_reset_weekly_returns_editor"] = True
                st.success("ETF 주간 성과를 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"저장에 실패했습니다. Supabase 스키마 설정을 확인하세요: {exc}")
    st.divider()
    with st.expander("주간 기록 추가", expanded=False):
        with st.form("add_weekly_record"):
            record_date = st.date_input("기록 날짜", value=pd.Timestamp.now(tz="Asia/Seoul").date())
            c1, c2 = st.columns(2)
            semi = c1.number_input("KODEX 미국반도체 누적 수익률 (%)", value=0.0, step=1.0) / 100
            dow = c2.number_input("ACE 미국다우존스 누적 수익률 (%)", value=0.0, step=1.0) / 100
            c3, c4 = st.columns(2)
            sp500 = c3.number_input("TIGER S&P500 누적 수익률 (%)", value=0.0, step=1.0) / 100
            nasdaq = c4.number_input("ACE 미국나스닥100 누적 수익률 (%)", value=0.0, step=1.0) / 100
            submitted = st.form_submit_button("주간 기록 저장", type="primary")
        if submitted:
            iso = record_date.isoformat()
            record = {"record_date": iso, "semi_return": semi, "dow_return": dow, "sp500_return": sp500, "nasdaq_return": nasdaq}
            new_data = dict(data)
            new_records = sorted([r for r in data.get("weekly", []) if str(r.get("record_date", ""))[:10] != iso] + [record], key=lambda r: str(r.get("record_date", "")))
            for i, saved in enumerate(new_records):
                next_record = new_records[i + 1] if i + 1 < len(new_records) else None
                for return_key, change_key in [("semi_return", "semi_change"), ("dow_return", "dow_change"), ("sp500_return", "sp500_change"), ("nasdaq_return", "nasdaq_change")]:
                    current_value = saved.get(return_key)
                    next_value = next_record.get(return_key) if next_record else None
                    saved[change_key] = next_value - current_value if next_value is not None and current_value is not None else None
            new_data["weekly"] = new_records
            try:
                save_data(client, user_id, new_data)
                st.success("주간 기록을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"저장에 실패했습니다. Supabase 스키마 설정을 확인하세요: {exc}")


def render_data_management(data: dict[str, list[dict[str, Any]]], holdings_df: pd.DataFrame, client, user_id: str) -> None:
    st.title("데이터 관리")
    st.caption("처음에는 원본 엑셀을 한 번 가져오고, 이후 대시보드에서 수정하거나 새 기록을 추가하면 됩니다.")
    db_error = st.session_state.get("pension_db_error")
    if client is not None and db_error:
        st.warning("Supabase에서 데이터를 읽지 못했습니다. `sql/pension_dashboard_schema.sql`을 Supabase SQL Editor에서 실행했는지 확인하세요.")
        with st.expander("DB 오류 세부정보"):
            st.code(db_error)
    uploaded = st.file_uploader("기존 엑셀 파일 가져오기 (.xlsx)", type=["xlsx"], help="'연저펀'과 '연저펀주간수익률' 시트를 읽고 계좌번호는 가져오지 않습니다.")
    if uploaded is not None:
        col1, col2 = st.columns([3, 1])
        col1.write("파일을 읽으면 보유 종목 4개, 월말 기록, ETF별 주간 성과를 가져옵니다. 현재가는 엑셀 저장값을 백업으로 보관하고, 실제 화면에서는 Yahoo Finance 조회를 우선합니다.")
        if col2.button("엑셀 데이터 가져오기", type="primary", use_container_width=True):
            try:
                parsed = prepare_data_for_save(import_excel_bytes(uploaded.getvalue()), user_id)
                save_data(client, user_id, parsed)
                st.session_state.pension_db_error = None
                st.success(f"가져오기 완료: 보유 {len(parsed['holdings'])}개, 월말 {len(parsed['monthly'])}개, 주간 {len(parsed['weekly'])}개")
                st.rerun()
            except Exception as exc:
                st.error(f"가져오기에 실패했습니다: {exc}")
    st.divider()
    st.subheader("보유 종목 수정")
    if holdings_df.empty:
        st.info("먼저 엑셀 파일을 가져오세요.")
    else:
        editable = pd.DataFrame(data.get("holdings", []))
        if not editable.empty:
            cols = ["ticker", "asset_group", "name", "target_weight", "monthly_investment", "avg_cost", "quantity", "last_known_price", "sort_order"]
            editable = editable[[c for c in cols if c in editable.columns]].copy()
            editable["target_weight"] = pd.to_numeric(editable["target_weight"], errors="coerce") * 100
            editable = editable.rename(columns={"ticker": "티커", "asset_group": "자산군", "name": "종목명", "target_weight": "목표비중", "monthly_investment": "월투자금", "avg_cost": "매수단가", "quantity": "수량", "last_known_price": "시세 백업값", "sort_order": "정렬순서"})
            edited = st.data_editor(editable, use_container_width=True, hide_index=True, num_rows="fixed", disabled=["티커", "종목명", "자산군", "정렬순서"], column_config={
                "목표비중": st.column_config.NumberColumn(format="%.1f%%", help="저장값은 0~1 비율입니다."),
                "월투자금": st.column_config.NumberColumn(format="₩%.0f", min_value=0, step=10000),
                "매수단가": st.column_config.NumberColumn(format="₩%.0f", min_value=0, step=100),
                "수량": st.column_config.NumberColumn(min_value=0, step=1),
                "시세 백업값": st.column_config.NumberColumn(format="₩%.0f", min_value=0, step=10),
            })
            if st.button("보유 종목 수정 저장", type="primary"):
                edited = edited.rename(columns={"티커": "ticker", "자산군": "asset_group", "종목명": "name", "목표비중": "target_weight", "월투자금": "monthly_investment", "매수단가": "avg_cost", "수량": "quantity", "시세 백업값": "last_known_price", "정렬순서": "sort_order"})
                # The editable display uses percentage points; store as a 0–1 ratio.
                edited["target_weight"] = pd.to_numeric(edited["target_weight"], errors="coerce").fillna(0) / 100
                edited["monthly_investment"] = pd.to_numeric(edited["monthly_investment"], errors="coerce").fillna(0)
                edited["avg_cost"] = pd.to_numeric(edited["avg_cost"], errors="coerce").fillna(0)
                edited["quantity"] = pd.to_numeric(edited["quantity"], errors="coerce").fillna(0).astype(int)
                # NumberColumn format does not scale values; target_weight values stay as decimals.
                records = edited.to_dict("records")
                new_data = dict(data)
                new_data["holdings"] = records
                try:
                    save_data(client, user_id, new_data)
                    st.success("보유 종목을 저장했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"저장에 실패했습니다. Supabase 스키마 설정을 확인하세요: {exc}")
    st.divider()
    st.subheader("데이터 현황")
    cols = st.columns(3)
    cols[0].metric("보유 종목", len(data.get("holdings", [])))
    cols[1].metric("월말 기록", len(data.get("monthly", [])))
    cols[2].metric("주간 기록", len(data.get("weekly", [])))
    st.caption("이 앱은 신규 pension_* 테이블만 사용합니다. 기존 모멘텀·피벗·매매일지 테이블을 삭제하거나 수정하지 않습니다.")


client = get_supabase_client()
user = auth_panel(client)
if user:
    user_id = user["id"]
    data = load_data(client, user_id)
    if not data.get("holdings") and not data.get("monthly") and not data.get("weekly"):
        st.title("연저펀 대시보드")
        st.write("기존 엑셀 기록을 가져오면 이 화면에 포트폴리오와 차트가 구성됩니다.")
        st.info("왼쪽 메뉴의 **데이터 관리**에서 기존 `사본.xlsx` 파일을 업로드하세요.")
    prices: dict[str, dict[str, Any]] = {}
    if data.get("holdings"):
        if st.sidebar.button("시세 새로고침", use_container_width=True):
            fetch_quote.clear()
            st.rerun()
        with st.spinner("ETF 시세를 조회하는 중…"):
            for item in data["holdings"]:
                ticker = str(item.get("ticker", "")).strip().zfill(6)
                prices[ticker] = fetch_quote(ticker)
    holdings_df = enrich_holdings(data.get("holdings", []), prices)
    page = st.sidebar.radio("메뉴", ["대시보드", "월말 기록", "ETF 주간 성과", "데이터 관리"], label_visibility="collapsed")
    if client is not None:
        st.sidebar.caption("데이터 저장: Supabase")
    else:
        st.sidebar.caption("데이터 저장: 세션 임시 저장")
    if page == "대시보드":
        render_overview(data, holdings_df)
    elif page == "월말 기록":
        render_monthly_history(data, client, user_id)
    elif page == "ETF 주간 성과":
        render_weekly(data, client, user_id)
    else:
        render_data_management(data, holdings_df, client, user_id)
