"""
Streamlit dashboard for ICT AI BOT PRO MAX.

Run with:  streamlit run src/ictbot/ui/app.py
"""

import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from ictbot.orchestrator.analyzer import analyze_pair
from ictbot.runtime import kill_switch
from ictbot.settings import BIAS_ENGINE, STRATEGY_MODE, UI_PAIRS, settings

# -----------------------------------------------------------------------------
# Page + auto-refresh
# -----------------------------------------------------------------------------
st.set_page_config(page_title="ICT AI BOT PRO MAX", layout="wide")
st_autorefresh(interval=5000, key="refresh")

st.markdown(
    """
    <style>
    .stApp { background-color:#050816; color:white; }
    section[data-testid="stSidebar"] { background-color:#111827; }
    div[data-testid="stMetric"] {
        background:#111827; border-radius:15px; padding:10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
st.sidebar.title("ICT AI BOT PRO MAX")
selected_pair = st.sidebar.selectbox("SELECT PAIR", UI_PAIRS)

# Show active config so the user knows what mode the analyzer is in.
mode_color = "#16a34a" if STRATEGY_MODE == "fade" else "#f59e0b"
st.sidebar.markdown(
    f"""
    <div style="background:#0f172a;padding:10px;border-radius:10px;
                border:1px solid #334155;margin-top:14px;">
        <div style="font-size:12px;color:#94a3b8;">ACTIVE CONFIG</div>
        <div style="font-size:14px;color:white;">
            BIAS_ENGINE: <b>{BIAS_ENGINE}</b><br/>
            STRATEGY_MODE: <b style="color:{mode_color};">{STRATEGY_MODE}</b>
        </div>
        <div style="font-size:11px;color:#64748b;margin-top:6px;">
            Edit ictbot/settings.py to switch.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Live-trading status (C3) — red banner + kill switch
# -----------------------------------------------------------------------------
_live_on = settings.enable_live_trading and not kill_switch.is_engaged()
_banner_bg = "#7f1d1d" if _live_on else "#14532d"
_banner_text = "🔴 LIVE TRADING ENABLED" if _live_on else "🟢 LIVE TRADING DISABLED"
st.sidebar.markdown(
    f"""
    <div style="background:{_banner_bg};padding:10px;border-radius:10px;
                border:1px solid #334155;margin-top:14px;text-align:center;">
        <div style="font-weight:700;color:white;">{_banner_text}</div>
        <div style="font-size:11px;color:#cbd5e1;margin-top:4px;">
            ENABLE_LIVE_TRADING={settings.enable_live_trading}<br/>
            kill_switch.is_engaged={kill_switch.is_engaged()}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
if _live_on:
    if st.sidebar.button("🛑 ENGAGE KILL SWITCH", type="primary"):
        kill_switch.engage(reason="dashboard-click")
        st.sidebar.warning("Kill switch engaged. ENABLE_LIVE_TRADING flipped to false.")
        st.rerun()
elif kill_switch.is_engaged():
    if st.sidebar.button("Release kill switch"):
        kill_switch.release()
        st.sidebar.info("Kill switch released. Set ENABLE_LIVE_TRADING=true in .env to resume.")
        st.rerun()

# Don't spam Telegram from the dashboard — let scanner.py own that.
data = analyze_pair(selected_pair, notify=False)

# -----------------------------------------------------------------------------
# Header
# -----------------------------------------------------------------------------
st.title("🚀 ICT AI BOT PRO MAX")

st.markdown(
    f"""
    <div style="background:#111827;padding:20px;border-radius:15px;
                border:1px solid #334155;">
        <h2 style="color:#00ff99;">ACTIVE PAIR : {data["pair"]}</h2>
        <h3>CURRENT PRICE : {round(data["price"], 2)}</h3>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Session cards
# -----------------------------------------------------------------------------
st.markdown("## 🌍 MARKET SESSIONS")


def _session_card(flag_title: str, time_str: str, status: str, open_color: str) -> str:
    color = open_color if status == "OPEN" else "#374151"
    return f"""
    <div style="background:{color};padding:20px;border-radius:20px;
                text-align:center;color:white;">
        <h2>{flag_title}</h2>
        <h1>{time_str}</h1>
        <h3>{status}</h3>
    </div>
    """


s1, s2, s3 = st.columns(3)
with s1:
    st.markdown(
        _session_card("🇯🇵 TOKYO", data["tokyo_time"], data["tokyo_status"], "#16a34a"),
        unsafe_allow_html=True,
    )
with s2:
    st.markdown(
        _session_card("🇬🇧 LONDON", data["london_time"], data["london_status"], "#2563eb"),
        unsafe_allow_html=True,
    )
with s3:
    st.markdown(
        _session_card("🇺🇸 NEW YORK", data["newyork_time"], data["newyork_status"], "#dc2626"),
        unsafe_allow_html=True,
    )

# -----------------------------------------------------------------------------
# India clock
# -----------------------------------------------------------------------------
st.markdown("## 🇮🇳 INDIA CLOCK")
st.markdown(
    f"""
    <div style="background:linear-gradient(135deg,#0f172a,#1e3a8a);
                padding:30px;border-radius:20px;text-align:center;
                border:2px solid #00ff99;">
        <h2>INDIAN STANDARD TIME</h2>
        <h1 style="font-size:60px;color:#00ff99;">{data["india_time"]}</h1>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# ICT flow cards
# -----------------------------------------------------------------------------
st.markdown("## 🧠 ICT FLOW")


def _flow_card(title: str, value: str, color: str) -> str:
    return f"""
    <div style="background:{color};padding:18px;border-radius:18px;
                text-align:center;color:white;">
        <h3>{title}</h3>
        <h2>{value}</h2>
    </div>
    """


def _bias_color(b: str) -> str:
    return "#16a34a" if b == "BULLISH" else "#dc2626"


def _signal_color(s: str) -> str:
    if "BULLISH" in s:
        return "#16a34a"
    if "BEARISH" in s:
        return "#dc2626"
    return "#374151"


c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.markdown(
        _flow_card("HTF BIAS", data["htf_bias"], _bias_color(data["htf_bias"])),
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        _flow_card("LTF BIAS", data["ltf_bias"], _bias_color(data["ltf_bias"])),
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(_flow_card("LTF POI", str(data["ltf_poi"]), "#f59e0b"), unsafe_allow_html=True)
with c4:
    poi_color = "#16a34a" if data["poi_tap"] == "POI TAPPED" else "#374151"
    st.markdown(_flow_card("POI TAP", data["poi_tap"], poi_color), unsafe_allow_html=True)
with c5:
    st.markdown(
        _flow_card("LTF MSS", data["ltf_mss"], _signal_color(data["ltf_mss"])),
        unsafe_allow_html=True,
    )
with c6:
    st.markdown(
        _flow_card("MICRO FVG", data["fvg"], _signal_color(data["fvg"])), unsafe_allow_html=True
    )

# -----------------------------------------------------------------------------
# Entry signal
# -----------------------------------------------------------------------------
st.markdown("## 🎯 ENTRY SIGNAL")
entry_color = {"BUY": "#16a34a", "SELL": "#dc2626"}.get(data["entry"], "#374151")
st.markdown(
    f"""
    <div style="background:{entry_color};padding:25px;border-radius:20px;
                text-align:center;color:white;">
        <h1>{data["entry"]}</h1>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Trade details
# -----------------------------------------------------------------------------
st.markdown("## 💰 TRADE DETAILS")
t1, t2, t3, t4 = st.columns(4)
with t1:
    st.metric("ENTRY PRICE", round(data["last_close"], 2))
with t2:
    st.metric("STOP LOSS", data["sl"])
with t3:
    st.metric("TAKE PROFIT", data["tp"])
with t4:
    st.metric("RR", f"1:{data['rr']}")

# -----------------------------------------------------------------------------
# Confidence
# -----------------------------------------------------------------------------
st.markdown("## 🤖 AI CONFIDENCE")
confidence = data["confidence"]
if confidence >= 80:
    st.success(f"HIGH CONFIDENCE : {confidence}%")
elif confidence >= 60:
    st.warning(f"MEDIUM CONFIDENCE : {confidence}%")
else:
    st.error(f"LOW CONFIDENCE : {confidence}%")

# -----------------------------------------------------------------------------
# Diagnostics — why no entry?
# -----------------------------------------------------------------------------
diag = data.get("diagnostics", {})
if diag and data["entry"] == "NO ENTRY":
    closest = diag["closest_direction"]
    blockers = diag["blockers"]
    near = " (near miss — 5/6 conditions met!)" if diag["near_miss"] else ""
    st.markdown(f"### 🩺 What's blocking a **{closest}**?{near}")
    for b in blockers:
        st.markdown(f"- {b}")

# -----------------------------------------------------------------------------
# Telegram status
# -----------------------------------------------------------------------------
from ictbot.settings import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

st.markdown("## 📲 TELEGRAM STATUS")
if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
    st.markdown(
        """
        <div style="background:#16a34a;padding:20px;border-radius:15px;
                    text-align:center;color:white;">
            <h2>🟢 TELEGRAM BOT CONNECTED</h2>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div style="background:#dc2626;padding:20px;border-radius:15px;
                    text-align:center;color:white;">
            <h2>🔴 TELEGRAM NOT CONFIGURED — set TELEGRAM_TOKEN / TELEGRAM_CHAT_ID in .env</h2>
        </div>
        """,
        unsafe_allow_html=True,
    )

# -----------------------------------------------------------------------------
# Live analysis summary
# -----------------------------------------------------------------------------
st.markdown("## 📊 LIVE MARKET ANALYSIS")
st.markdown(
    f"""
    <div style="background:#111827;padding:25px;border-radius:20px;
                color:white;border:1px solid #334155;">
        <h3>PAIR : {data["pair"]}</h3>
        <h3>ACTIVE SESSION : {data["active_session"]}</h3>
        <h3>HTF BIAS : {data["htf_bias"]}</h3>
        <h3>LTF BIAS : {data["ltf_bias"]}</h3>
        <h3>POI : {data["ltf_poi"]}</h3>
        <h3>POI TAP : {data["poi_tap"]}</h3>
        <h3>MSS : {data["ltf_mss"]}</h3>
        <h3>MICRO FVG : {data["fvg"]}</h3>
        <h3>ENTRY : {data["entry"]}</h3>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Backtest equity curve (from bt_curve.py)
# -----------------------------------------------------------------------------
import json

import pandas as pd

from ictbot.portfolio.journal import read_journal, score_journal
from ictbot.settings import CURVE_FILE

st.markdown("## 📊 BACKTEST EQUITY CURVE")
if CURVE_FILE.exists():
    try:
        with open(CURVE_FILE) as f:
            curve_payload = json.load(f)
        curve = curve_payload.get("curve", [])
        if curve:
            times = [pd.to_datetime(p["time"]) for p in curve]
            cum_R = [p["cum_R"] for p in curve]
            total_R = curve_payload.get("total_R", 0)
            n = curve_payload.get("n_closed", 0)
            pair_label = curve_payload.get("pair", "?")

            bt_fig = go.Figure()
            bt_fig.add_trace(
                go.Scatter(
                    x=times,
                    y=cum_R,
                    mode="lines+markers",
                    line=dict(color="#3b82f6", width=2),
                    marker=dict(
                        size=5,
                        color=["#16a34a" if p["outcome"] == "WIN" else "#dc2626" for p in curve],
                    ),
                    name="cumulative net R (backtest)",
                )
            )
            bt_fig.add_hline(y=0, line_dash="dash", line_color="#475569")
            bt_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#050816",
                plot_bgcolor="#050816",
                height=320,
                xaxis_rangeslider_visible=False,
                title=(
                    f"Backtest equity — {pair_label}   net {total_R:+.2f}R across {n} closed trades"
                ),
            )
            st.plotly_chart(bt_fig, use_container_width=True)
        else:
            st.info("backtest_curve.json has no closed trades yet")
    except Exception as e:
        st.warning(f"Couldn't load backtest curve: {e}")
else:
    st.info(
        "No backtest curve yet — run `python -m ictbot.engine.bt_curve BTC/USDT:USDT` to generate one."
    )

st.markdown("## 📈 EQUITY CURVE (from journal)")

all_signals = read_journal()
closed_signals = [s for s in all_signals if s["outcome"] in ("WIN", "LOSS")]
stats = score_journal(all_signals)

if closed_signals:
    # Build cumulative R-multiples: WIN adds +rr, LOSS adds -1
    closed_signals.sort(key=lambda s: s["closed_ts"] or s["ts"])
    times, cum_R = [], []
    running = 0.0
    for s in closed_signals:
        running += s["rr"] if s["outcome"] == "WIN" else -1
        times.append(pd.to_datetime(s["closed_ts"] or s["ts"]).tz_localize(None))
        cum_R.append(running)

    eq_fig = go.Figure()
    eq_fig.add_trace(
        go.Scatter(
            x=times,
            y=cum_R,
            mode="lines+markers",
            line=dict(color="#00ff99", width=2),
            marker=dict(size=6, color="#00ff99"),
            name="cumulative R",
        )
    )
    eq_fig.add_hline(y=0, line_dash="dash", line_color="#475569")
    eq_fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#050816",
        plot_bgcolor="#050816",
        height=320,
        xaxis_rangeslider_visible=False,
        title=(
            f"Equity curve — total: {running:+.1f}R   "
            f"trades: {stats['wins']}W/{stats['losses']}L   "
            f"win-rate: {stats['win_rate']:.1f}%"
        ),
    )
    st.plotly_chart(eq_fig, use_container_width=True)
else:
    st.info(
        f"📒 {len(all_signals)} signals in journal, {stats['open']} OPEN, "
        "0 closed yet. Run `make scan` to populate."
    )

# -----------------------------------------------------------------------------
# Live chart with journaled signal overlay
# -----------------------------------------------------------------------------

st.markdown("## 📈 LIVE MARKET CHART")
ltf_df = data["ltf_df"]

fig = go.Figure()
fig.add_trace(
    go.Candlestick(
        x=ltf_df["time"],
        open=ltf_df["open"],
        high=ltf_df["high"],
        low=ltf_df["low"],
        close=ltf_df["close"],
        name="price",
    )
)

# Overlay journal signals for this pair that fall in the chart window
journal_signals = read_journal(pair=data["pair"])
if journal_signals and len(ltf_df) > 0:
    chart_start = ltf_df["time"].iloc[0]
    chart_end = ltf_df["time"].iloc[-1]

    def _in_window(s):
        try:
            ts = pd.to_datetime(s["ts"]).tz_localize(None)
        except (TypeError, ValueError):
            return False
        return chart_start <= ts <= chart_end

    visible = [s for s in journal_signals if _in_window(s)]

    by_outcome = {"WIN": [], "LOSS": [], "OPEN": []}
    for s in visible:
        ts = pd.to_datetime(s["ts"]).tz_localize(None)
        by_outcome[s["outcome"]].append((ts, s["price"], s["entry"]))

    style = {
        "WIN": {"color": "#00ff99", "symbol": "triangle-up", "name": "WIN"},
        "LOSS": {"color": "#dc2626", "symbol": "x", "name": "LOSS"},
        "OPEN": {"color": "#f59e0b", "symbol": "circle", "name": "OPEN"},
    }
    for outcome, points in by_outcome.items():
        if not points:
            continue
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                mode="markers",
                name=style[outcome]["name"],
                marker=dict(
                    color=style[outcome]["color"],
                    symbol=style[outcome]["symbol"],
                    size=14,
                    line=dict(color="white", width=1),
                ),
                hovertext=[f"{p[2]} @ {p[1]} → {outcome}" for p in points],
                hoverinfo="text+x",
            )
        )

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#050816",
    plot_bgcolor="#050816",
    height=700,
    xaxis_rangeslider_visible=False,
    title=f"{data['pair']} LIVE CHART  (▲ wins · ✕ losses · ● open)",
)
st.plotly_chart(fig, use_container_width=True)

# -----------------------------------------------------------------------------
# Footer
# -----------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    """
    <center>
        <h3 style='color:#00ff99;'>ICT AI BOT PRO MAX</h3>
        <p style='color:gray;'>
            HTF Bias → LTF Bias → POI → POI Tap → MSS → Micro FVG → Entry
        </p>
    </center>
    """,
    unsafe_allow_html=True,
)
