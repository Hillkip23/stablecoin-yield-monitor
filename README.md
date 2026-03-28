# Stablecoin Yield Monitor

Live DeFi stablecoin yield dashboard with composite risk scoring across Aave V3, Sky/Maker (SSR), Morpho, Curve, Ethena (sUSDe), and Coinbase USDC.

## Features

- **Live yields** from DeFiLlama API with 5-minute cache
- **Composite risk scoring** — protocol (35%), custody (30%), depeg (35%)
- **Yield vs Risk scatter plot** with iso-yield/risk lines
- **Sortable pool rankings** with protocol filtering
- **Protocol deep dive** — risk breakdowns, top pools, TVL
- **Historical trends** — APY and yield/risk ratio over time
- **Dark teal theme** optimized for readability

## Risk Framework

Each protocol is scored on three axes (1–5 scale):

| Protocol | Protocol Risk | Custody Risk | Depeg Risk | Composite | Tier |
|---|---|---|---|---|---|
| Coinbase | 1 | 3 | 1 | 1.60 | MODERATE |
| Aave V3 | 2 | 1 | 2 | 1.70 | MODERATE |
| Sky (SSR) | 2 | 1 | 2 | 1.70 | MODERATE |
| Morpho | 2 | 1 | 2 | 1.70 | MODERATE |
| Curve | 2 | 1 | 3 | 2.05 | MODERATE |
| Ethena | 3 | 3 | 3 | 3.00 | ELEVATED |

## Local Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud

### Step 1 — Create a GitHub repo

```bash
cd C:\Users\HillKip\Documents\
mkdir stablecoin-yield-monitor
cd stablecoin-yield-monitor
git init
```

Copy all files from this project into that folder:
- `app.py`
- `requirements.txt`
- `.streamlit/config.toml`
- `.gitignore`
- `README.md`

```bash
git add .
git commit -m "Stablecoin yield monitor - Streamlit app"
git remote add origin https://github.com/hillkip23/stablecoin-yield-monitor.git
git push -u origin main
```

### Step 2 — Deploy on Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Sign in with your GitHub account (`hillkip23`)
3. Click **"New app"**
4. Select:
   - **Repository:** `hillkip23/stablecoin-yield-monitor`
   - **Branch:** `main`
   - **Main file path:** `app.py`
5. Click **"Deploy!"**

Your app will be live at:
`https://hillkip23-stablecoin-yield-monitor-app-XXXXX.streamlit.app`

## Historical Data (Optional)

The History page shows time-series charts when a `data/yield_history.csv` file exists with columns:

```
timestamp, protocol, pool, chain, tvl_usd, apy_total, risk_score, risk_tier, yield_risk_ratio
```

### Option A — Manual upload
Export the CSV from your local cron job and commit it to `data/yield_history.csv`.

### Option B — GitHub Action (automated)
Add a `.github/workflows/collect.yml` that runs the DeFiLlama collection script nightly and commits the updated CSV. This gives you a fully autonomous history pipeline.

## Data Sources

- **DeFiLlama** — `https://yields.llama.fi/pools` (public API, no auth)
- **Coinbase** — static USDC rewards rate (3.85% as of last update)

## Author

H.K
