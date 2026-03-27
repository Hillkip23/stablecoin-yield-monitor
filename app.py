"""
Stablecoin Yield Monitor — Live DeFi Yield Dashboard with Risk Scoring
Tracks: Aave v3, Sky/Maker (SSR), Morpho, Curve, Ethena (sUSDe), Coinbase USDC
Author: H. Cheruiyot
"""

import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.set_page_config(
    page_title="Stablecoin Yield Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFILLAMA_URL = "https://yields.llama.fi/pools"
MIN_TVL = 5_000_000
CACHE_TTL = 300  # 5 minutes

# History CSV path (written by the cron job; optional for Streamlit Cloud)
HISTORY_CSV = Path("data/yield_history.csv")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Risk Framework
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROTOCOL_PROFILES = {
    "coinbase": {
        "label": "Coinbase",
        "protocol_risk": 1,
        "custody_risk": 3,
        "depeg_risk": 1,
        "custody_type": "Centralized (Coinbase)",
        "notes": "Regulated CeFi. USDC is Circle-issued, fully reserved. "
                 "Counterparty risk = Coinbase solvency. Requires Coinbase One for rewards.",
        "color": "#848456",
        "icon": "🏦",
    },
    "aave-v3": {
        "label": "Aave V3",
        "protocol_risk": 2,
        "custody_risk": 1,
        "depeg_risk": 2,
        "custody_type": "Self-custodial (smart contract)",
        "notes": "Battle-tested lending protocol. $25B+ TVL. Immutable core contracts. "
                 "Risk = utilization spikes temporarily locking withdrawals.",
        "color": "#20808D",
        "icon": "🔷",
    },
    "sky-lending": {
        "label": "Sky (MakerDAO)",
        "protocol_risk": 2,
        "custody_risk": 1,
        "depeg_risk": 2,
        "custody_type": "Self-custodial (smart contract)",
        "notes": "Formerly MakerDAO. SSR backed by overcollateralized vaults + RWA. "
                 "sUSDS is the largest yield-bearing stablecoin ($10B+ supply).",
        "color": "#A84B2F",
        "icon": "🌤️",
    },
    "morpho-v1": {
        "label": "Morpho V1",
        "protocol_risk": 2,
        "custody_risk": 1,
        "depeg_risk": 2,
        "custody_type": "Self-custodial (vault-curated)",
        "notes": "Morpho Blue = permissionless isolated markets. Vault curators "
                 "(Gauntlet, Steakhouse, Re7) manage strategy. Curator quality varies.",
        "color": "#1B474D",
        "icon": "🔬",
    },
    "ethena-usde": {
        "label": "Ethena (sUSDe)",
        "protocol_risk": 3,
        "custody_risk": 3,
        "depeg_risk": 3,
        "custody_type": "Hybrid (off-exchange settlement via Copper/Ceffu)",
        "notes": "sUSDe yield from ETH staking + perpetual funding rate basis trade. "
                 "If funding turns persistently negative, yield compresses or reserve depletes. "
                 "Insurance fund ~$50M.",
        "color": "#944454",
        "icon": "⚡",
    },
    "curve-dex": {
        "label": "Curve DEX",
        "protocol_risk": 2,
        "custody_risk": 1,
        "depeg_risk": 3,
        "custody_type": "Self-custodial (AMM LP positions)",
        "notes": "LP exposure to impermanent loss if paired stablecoin depegs. "
                 "CRV emission rewards can be volatile. Past exploit (Jul 2023 reentrancy).",
        "color": "#FFC553",
        "icon": "🔄",
    },
}


def compute_risk_score(profile: dict) -> float:
    return round(
        0.35 * profile["protocol_risk"]
        + 0.30 * profile["custody_risk"]
        + 0.35 * profile["depeg_risk"],
        2,
    )


def risk_tier(score: float) -> str:
    if score <= 1.5:
        return "LOW"
    elif score <= 2.5:
        return "MODERATE"
    elif score <= 3.5:
        return "ELEVATED"
    return "HIGH"


def tier_color(tier: str) -> str:
    return {
        "LOW": "#437A22",
        "MODERATE": "#20808D",
        "ELEVATED": "#DA7101",
        "HIGH": "#A13544",
    }.get(tier, "#7A7974")


def match_protocol(project: str) -> Optional[str]:
    p = project.lower()
    for key in PROTOCOL_PROFILES:
        if key in p:
            return key
    return None


# Global color map for Plotly charts (protocol label → hex color)
color_map = {v["label"]: v["color"] for v in PROTOCOL_PROFILES.values()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Fetching
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@st.cache_data(ttl=CACHE_TTL)
def fetch_yields() -> pd.DataFrame:
    """Fetch and annotate stablecoin yields from DeFiLlama."""
    resp = requests.get(DEFILLAMA_URL, timeout=30)
    resp.raise_for_status()
    all_pools = resp.json()["data"]

    target_protocols = [
        "aave-v3", "morpho-v1", "morpho-blue",
        "sky-lending", "ethena-usde", "curve-dex",
    ]
    stable_kw = [
        "usdc", "usdt", "dai", "usds", "usde", "susde",
        "crvusd", "pyusd", "frax", "lusd", "rlusd",
    ]

    rows = []
    for pool in all_pools:
        project = (pool.get("project") or "").lower()
        symbol = (pool.get("symbol") or "").lower()

        if not any(t in project for t in target_protocols):
            continue
        if not any(s in symbol for s in stable_kw):
            continue

        tvl = pool.get("tvlUsd") or 0
        if tvl < MIN_TVL:
            continue

        apy = pool.get("apy") or 0
        apy_base = pool.get("apyBase") or 0
        apy_reward = pool.get("apyReward") or 0

        proto_key = match_protocol(pool.get("project", ""))
        if not proto_key:
            continue

        profile = PROTOCOL_PROFILES[proto_key]
        score = compute_risk_score(profile)
        tier = risk_tier(score)
        yrr = round(apy / score, 3) if score > 0 else 0

        rows.append({
            "protocol": profile["label"],
            "protocol_key": proto_key,
            "pool": pool.get("symbol", ""),
            "chain": pool.get("chain", ""),
            "tvl": tvl,
            "apy": round(apy, 4),
            "apy_base": round(apy_base, 4),
            "apy_reward": round(apy_reward, 4),
            "risk_score": score,
            "risk_tier": tier,
            "yield_risk_ratio": yrr,
            "color": profile["color"],
        })

    # Add Coinbase static entry
    cb = PROTOCOL_PROFILES["coinbase"]
    cb_score = compute_risk_score(cb)
    rows.append({
        "protocol": "Coinbase",
        "protocol_key": "coinbase",
        "pool": "USDC Rewards",
        "chain": "Coinbase (CeFi)",
        "tvl": 0,
        "apy": 3.85,
        "apy_base": 3.85,
        "apy_reward": 0,
        "risk_score": cb_score,
        "risk_tier": risk_tier(cb_score),
        "yield_risk_ratio": round(3.85 / cb_score, 3),
        "color": cb["color"],
    })

    df = pd.DataFrame(rows)
    df = df.sort_values("tvl", ascending=False).reset_index(drop=True)
    return df


def load_history() -> Optional[pd.DataFrame]:
    """Load historical yield data if available."""
    if HISTORY_CSV.exists():
        try:
            df = pd.read_csv(HISTORY_CSV)
            if "timestamp" in df.columns:
                df["date"] = pd.to_datetime(df["timestamp"].str[:10])
            return df
        except Exception:
            return None
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Sidebar
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with st.sidebar:
    st.markdown("## 📊 Stablecoin Yield Monitor")
    st.caption("Live DeFi yields with risk scoring")
    st.divider()

    page = st.radio(
        "Navigation",
        ["Overview", "Protocols", "History"],
        label_visibility="collapsed",
    )

    st.divider()

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption(
        "**Data:** DeFiLlama API + Coinbase\n\n"
        "**Risk model:** protocol (35%), custody (30%), depeg (35%)\n\n"
        "**Cache:** 5 min TTL"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fetch Data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

try:
    df = fetch_yields()
    data_ok = True
except Exception as e:
    st.error(f"Failed to fetch yield data: {e}")
    df = pd.DataFrame()
    data_ok = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Page: Overview
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if page == "Overview" and data_ok:
    st.title("Yield Overview")
    st.caption("Live stablecoin yields with risk scoring")

    # ── KPI Row ──
    active = df[df["apy"] > 0.1]
    best_yrr = active.loc[active["yield_risk_ratio"].idxmax()] if len(active) > 0 else None

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Avg Yield", f"{active['apy'].mean():.2f}%")
    if best_yrr is not None:
        c2.metric("Best Risk-Adj", f"{best_yrr['pool']}", f"{best_yrr['apy']:.2f}% · Y/R {best_yrr['yield_risk_ratio']:.2f}")
    c3.metric("Total TVL", f"${df['tvl'].sum() / 1e9:.1f}B")
    c4.metric("Pools Tracked", f"{len(df)}")
    c5.metric("Last Updated", datetime.now(timezone.utc).strftime("%H:%M UTC"))

    st.divider()

    # ── Yield vs Risk Scatter ──
    st.subheader("Yield vs Risk")
    st.caption("Bubble size = TVL · Dashed lines = iso yield/risk ratios (Y/R = 1, 2, 3)")

    scatter_df = active.copy()
    scatter_df["tvl_display"] = scatter_df["tvl"].apply(
        lambda x: f"${x / 1e6:.0f}M" if x > 0 else "N/A"
    )
    scatter_df["bubble_size"] = np.clip(scatter_df["tvl"] / 1e6, 5, 500)

    fig_scatter = px.scatter(
        scatter_df,
        x="risk_score",
        y="apy",
        size="bubble_size",
        color="protocol",
        color_discrete_map=color_map,
        hover_data={
            "pool": True,
            "chain": True,
            "apy": ":.2f",
            "risk_score": ":.2f",
            "yield_risk_ratio": ":.2f",
            "tvl_display": True,
            "bubble_size": False,
        },
        labels={
            "risk_score": "Risk Score →",
            "apy": "APY %",
            "protocol": "Protocol",
            "tvl_display": "TVL",
        },
    )

    # Add iso Y/R lines
    x_range = np.linspace(0.3, 4.2, 100)
    for yrr_val, label in [(1, "Y/R=1"), (2, "Y/R=2"), (3, "Y/R=3")]:
        fig_scatter.add_trace(
            go.Scatter(
                x=x_range,
                y=yrr_val * x_range,
                mode="lines",
                line=dict(dash="dash", color="rgba(200,200,200,0.25)", width=1),
                name=label,
                showlegend=False,
                hoverinfo="skip",
            )
        )
        # Label at right end
        y_at_end = yrr_val * 4.0
        if y_at_end < scatter_df["apy"].max() * 1.4:
            fig_scatter.add_annotation(
                x=4.05, y=y_at_end,
                text=label, showarrow=False,
                font=dict(size=10, color="rgba(200,200,200,0.5)"),
            )

    fig_scatter.update_layout(
        template="plotly_dark",
        plot_bgcolor="#0E1117",
        paper_bgcolor="#0E1117",
        height=500,
        xaxis=dict(range=[0.3, 4.2], title_font_size=12, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(range=[0, min(scatter_df["apy"].max() * 1.3, 20)], title_font_size=12, gridcolor="rgba(255,255,255,0.05)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(l=40, r=40, t=30, b=40),
    )

    st.plotly_chart(fig_scatter, use_container_width=True)

    st.divider()

    # ── Pool Rankings Table ──
    st.subheader("Pool Rankings")

    # Protocol filter
    protos = ["All"] + sorted(active["protocol"].unique().tolist())
    selected_proto = st.selectbox("Filter by Protocol", protos, label_visibility="collapsed")

    table_df = active.copy()
    if selected_proto != "All":
        table_df = table_df[table_df["protocol"] == selected_proto]

    # Sort selector
    sort_col = st.selectbox(
        "Sort by",
        ["yield_risk_ratio", "apy", "risk_score", "tvl"],
        format_func=lambda x: {
            "yield_risk_ratio": "Yield/Risk Ratio (best first)",
            "apy": "APY (highest first)",
            "risk_score": "Risk Score (lowest first)",
            "tvl": "TVL (largest first)",
        }[x],
        label_visibility="collapsed",
    )

    ascending = sort_col == "risk_score"
    table_df = table_df.sort_values(sort_col, ascending=ascending)

    # Format for display
    display_df = table_df[[
        "protocol", "pool", "chain", "tvl", "apy", "apy_base",
        "apy_reward", "risk_score", "risk_tier", "yield_risk_ratio",
    ]].copy()
    display_df.columns = [
        "Protocol", "Pool", "Chain", "TVL", "APY %", "Base %",
        "Reward %", "Risk", "Tier", "Y/R",
    ]
    display_df["TVL"] = display_df["TVL"].apply(
        lambda x: f"${x / 1e9:.2f}B" if x >= 1e9 else f"${x / 1e6:.0f}M" if x > 0 else "N/A"
    )
    display_df["APY %"] = display_df["APY %"].apply(lambda x: f"{x:.2f}%")
    display_df["Base %"] = display_df["Base %"].apply(lambda x: f"{x:.2f}%")
    display_df["Reward %"] = display_df["Reward %"].apply(lambda x: f"{x:.2f}%")
    display_df["Risk"] = display_df["Risk"].apply(lambda x: f"{x:.2f}")
    display_df["Y/R"] = display_df["Y/R"].apply(lambda x: f"{x:.2f}")

    st.dataframe(
        display_df.reset_index(drop=True),
        use_container_width=True,
        height=500,
        column_config={
            "Tier": st.column_config.TextColumn(width="small"),
        },
    )

    st.caption(f"Showing {len(display_df)} pools · Sorted by {sort_col}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Page: Protocols
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

elif page == "Protocols" and data_ok:
    st.title("Protocol Deep Dive")
    st.caption("Risk profiles and yield performance for each protocol")

    active = df[df["apy"] > 0.1]

    # Two columns of protocol cards
    cols = st.columns(3)

    for i, (key, profile) in enumerate(PROTOCOL_PROFILES.items()):
        col = cols[i % 3]
        score = compute_risk_score(profile)
        tier = risk_tier(score)

        proto_df = active[active["protocol_key"] == key]
        best_apy = proto_df["apy"].max() if len(proto_df) > 0 else 0
        avg_apy = proto_df["apy"].mean() if len(proto_df) > 0 else 0
        total_tvl = proto_df["tvl"].sum()

        with col:
            with st.container(border=True):
                st.markdown(f"### {profile['icon']} {profile['label']}")

                # Risk tier badge
                badge_color = tier_color(tier)
                st.markdown(
                    f"<span style='background:{badge_color}; color:white; "
                    f"padding:2px 10px; border-radius:4px; font-size:0.8em;'>"
                    f"{tier}</span> &nbsp; Score: {score}",
                    unsafe_allow_html=True,
                )

                st.markdown("")

                # Risk bars
                r1, r2, r3 = st.columns(3)
                r1.metric("Protocol", f"{profile['protocol_risk']}/5")
                r2.metric("Custody", f"{profile['custody_risk']}/5")
                r3.metric("Depeg", f"{profile['depeg_risk']}/5")

                # Risk progress bars
                st.progress(profile["protocol_risk"] / 5, text="Protocol")
                st.progress(profile["custody_risk"] / 5, text="Custody")
                st.progress(profile["depeg_risk"] / 5, text="Depeg")

                st.caption(profile["notes"])

                st.divider()

                # Stats
                m1, m2, m3 = st.columns(3)
                m1.metric("Best APY", f"{best_apy:.2f}%")
                m2.metric("Avg APY", f"{avg_apy:.2f}%")
                tvl_str = f"${total_tvl / 1e9:.1f}B" if total_tvl >= 1e9 else f"${total_tvl / 1e6:.0f}M" if total_tvl > 0 else "N/A"
                m3.metric("Total TVL", tvl_str)

                # Top pools
                if len(proto_df) > 0:
                    st.markdown("**Top Pools**")
                    top5 = proto_df.nlargest(5, "apy")[["pool", "chain", "tvl", "apy"]]
                    for _, row in top5.iterrows():
                        tvl_s = f"${row['tvl'] / 1e6:.0f}M" if row["tvl"] > 0 else ""
                        st.markdown(
                            f"- **{row['pool']}** · {row['chain']} · {tvl_s} · {row['apy']:.2f}%"
                        )

    # ── Risk Comparison Chart ──
    st.divider()
    st.subheader("Risk Score Comparison")

    risk_data = []
    for key, profile in PROTOCOL_PROFILES.items():
        risk_data.append({
            "Protocol": profile["label"],
            "Protocol Risk": profile["protocol_risk"],
            "Custody Risk": profile["custody_risk"],
            "Depeg Risk": profile["depeg_risk"],
            "Composite": compute_risk_score(profile),
        })

    risk_df = pd.DataFrame(risk_data)
    risk_df = risk_df.sort_values("Composite")

    fig_risk = go.Figure()
    for risk_type, color in [
        ("Protocol Risk", "#20808D"),
        ("Custody Risk", "#A84B2F"),
        ("Depeg Risk", "#FFC553"),
    ]:
        fig_risk.add_trace(go.Bar(
            y=risk_df["Protocol"],
            x=risk_df[risk_type],
            name=risk_type,
            orientation="h",
            marker_color=color,
        ))

    fig_risk.update_layout(
        barmode="group",
        template="plotly_dark",
        plot_bgcolor="#0E1117",
        paper_bgcolor="#0E1117",
        height=350,
        xaxis_title="Score (1-5)",
        margin=dict(l=10, r=10, t=10, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    st.plotly_chart(fig_risk, use_container_width=True)

    # ── Yield vs TVL by Protocol ──
    st.subheader("Yield vs TVL by Protocol")

    proto_summary = []
    for key, profile in PROTOCOL_PROFILES.items():
        proto_df = active[active["protocol_key"] == key]
        if len(proto_df) > 0:
            proto_summary.append({
                "Protocol": profile["label"],
                "Best APY": proto_df["apy"].max(),
                "Avg APY": proto_df["apy"].mean(),
                "Total TVL ($B)": proto_df["tvl"].sum() / 1e9,
                "Pool Count": len(proto_df),
                "Composite Risk": compute_risk_score(profile),
            })

    summary_df = pd.DataFrame(proto_summary)

    fig_bubble = px.scatter(
        summary_df,
        x="Composite Risk",
        y="Best APY",
        size="Total TVL ($B)",
        color="Protocol",
        color_discrete_map=color_map,
        hover_data=["Avg APY", "Pool Count", "Total TVL ($B)"],
        text="Protocol",
    )
    fig_bubble.update_traces(textposition="top center", textfont_size=10)
    fig_bubble.update_layout(
        template="plotly_dark",
        plot_bgcolor="#0E1117",
        paper_bgcolor="#0E1117",
        height=400,
        showlegend=False,
        margin=dict(l=40, r=40, t=20, b=40),
    )
    st.plotly_chart(fig_bubble, use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Page: History
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

elif page == "History" and data_ok:
    st.title("Historical Trends")
    st.caption("Track APY and risk-adjusted yield over time")

    hist_df = load_history()

    if hist_df is None or len(hist_df) == 0:
        st.info(
            "No historical data yet. History accumulates daily via the cron job.\n\n"
            "**To enable history on Streamlit Cloud**, add a `data/yield_history.csv` file "
            "to your repo, or set up a scheduled GitHub Action that appends daily snapshots.\n\n"
            "For now, here is today's cross-sectional snapshot:"
        )

        # Show a snapshot bar chart instead
        active = df[df["apy"] > 0.1]

        # Best pool per protocol
        best_per_proto = active.loc[active.groupby("protocol")["yield_risk_ratio"].idxmax()]
        best_per_proto = best_per_proto.sort_values("yield_risk_ratio", ascending=True)

        fig_bar = px.bar(
            best_per_proto,
            y="protocol",
            x="apy",
            color="protocol",
            color_discrete_map=color_map,
            orientation="h",
            text=best_per_proto["apy"].apply(lambda x: f"{x:.2f}%"),
            hover_data=["pool", "chain", "risk_tier", "yield_risk_ratio"],
            labels={"apy": "APY %", "protocol": "Protocol"},
        )
        fig_bar.update_layout(
            template="plotly_dark",
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            showlegend=False,
            height=350,
            title="Best Pool APY by Protocol (Today)",
            margin=dict(l=10, r=10, t=40, b=40),
        )
        fig_bar.update_traces(textposition="outside")
        st.plotly_chart(fig_bar, use_container_width=True)

        # Yield/Risk ratio comparison
        fig_yrr = px.bar(
            best_per_proto,
            y="protocol",
            x="yield_risk_ratio",
            color="protocol",
            color_discrete_map=color_map,
            orientation="h",
            text=best_per_proto["yield_risk_ratio"].apply(lambda x: f"{x:.2f}"),
            labels={"yield_risk_ratio": "Yield / Risk Ratio", "protocol": "Protocol"},
        )
        fig_yrr.update_layout(
            template="plotly_dark",
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            showlegend=False,
            height=350,
            title="Risk-Adjusted Yield Ratio by Protocol (Today)",
            margin=dict(l=10, r=10, t=40, b=40),
        )
        fig_yrr.update_traces(textposition="outside")

        # Add "attractive" threshold line
        fig_yrr.add_vline(x=2.0, line_dash="dash", line_color="#20808D", opacity=0.5,
                          annotation_text="Attractive threshold", annotation_position="top right")

        st.plotly_chart(fig_yrr, use_container_width=True)

    else:
        # Full historical charts
        st.success(f"Loaded {len(hist_df)} historical records")

        # Protocol label mapping
        proto_label_map = {k: v["label"] for k, v in PROTOCOL_PROFILES.items()}

        # Best pool per protocol per day
        hist_active = hist_df[hist_df["apy_total"].astype(float) > 0.1].copy()
        hist_active["apy_total"] = hist_active["apy_total"].astype(float)
        hist_active["risk_score"] = hist_active["risk_score"].astype(float)
        hist_active["yield_risk_ratio"] = hist_active["yield_risk_ratio"].astype(float)

        # Map protocol names
        hist_active["protocol_label"] = hist_active["protocol"].map(proto_label_map).fillna(hist_active["protocol"])

        # Group: best apy per protocol per date
        best_daily = hist_active.loc[
            hist_active.groupby(["date", "protocol_label"])["apy_total"].idxmax()
        ]

        # APY over time
        st.subheader("Best Pool APY by Protocol")
        fig_hist_apy = px.line(
            best_daily,
            x="date",
            y="apy_total",
            color="protocol_label",
            color_discrete_map={v["label"]: v["color"] for v in PROTOCOL_PROFILES.values()},
            markers=True,
            labels={"apy_total": "APY %", "date": "Date", "protocol_label": "Protocol"},
        )
        fig_hist_apy.update_layout(
            template="plotly_dark",
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            height=400,
            margin=dict(l=40, r=40, t=20, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig_hist_apy, use_container_width=True)

        # Yield/Risk ratio over time
        st.subheader("Yield / Risk Ratio Over Time")
        st.caption("Higher ratio = better risk-adjusted return")
        fig_hist_yrr = px.line(
            best_daily,
            x="date",
            y="yield_risk_ratio",
            color="protocol_label",
            color_discrete_map={v["label"]: v["color"] for v in PROTOCOL_PROFILES.values()},
            markers=True,
            labels={"yield_risk_ratio": "Y/R Ratio", "date": "Date", "protocol_label": "Protocol"},
        )
        fig_hist_yrr.add_hline(y=2.0, line_dash="dash", line_color="#20808D", opacity=0.5,
                               annotation_text="Attractive threshold")
        fig_hist_yrr.update_layout(
            template="plotly_dark",
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            height=400,
            margin=dict(l=40, r=40, t=20, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig_hist_yrr, use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Footer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.divider()
st.caption(
    "Data: [DeFiLlama](https://defillama.com) API + Coinbase · "
    "Risk model: protocol (35%), custody (30%), depeg (35%) · "
    "Built by H. Cheruiyot · "
    "[Created with Perplexity Computer](https://www.perplexity.ai/computer)"
)
