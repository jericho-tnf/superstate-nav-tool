# Superstate NAV Query

Look up Superstate USTB NAV per share at a specific UTC instant, from three independent
sources, and reconcile them:

| Source | What it is |
|---|---|
| **Off-chain API** | `api.superstate.com` continuous price — defined at every second |
| **On-chain oracle** | Computed on Ethereum by `calculateRealtimeNavs` from daily checkpoints |
| **Official daily NAV** | `nav-daily` — one struck value per business day, as of 17:00 ET |

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py          # UI
python compare_nav.py 2026-07-27        # CLI reconciliation
```

## Three things that make this non-obvious

### 1. The on-chain "continuous" price is derived, not stored

Only **one checkpoint per business day** is ever written on-chain — the 17:00 ET strike,
published the next business morning at ~13:07 UTC. Every intermediate value is computed
at read time by straight-line interpolation between two checkpoints.

So a price returned for 13:15 UTC is *computed* on-chain but is not *retrievable* data —
no block contains it. Worked example, reproduced exactly from two stored points:

```
bracket:  cp[404] 11154292 @ 1784754000  →  cp[405] 11155345 @ 1784840400
slope  =  (11155345 − 11154292) / 86400  =  0.0121875 units/sec
value  =  11155345 + 0.0121875 × (1784985300 − 1784840400)
       =  11157110.97 → Solidity truncates → 11157110 → $11.157110
```

### 2. `nav-daily` forward-fills, and its date field carries no provenance

Ask for a Saturday, a market holiday, or `2026-12-31` and the API stamps **your requested
date** into `net_asset_value_date` and returns the last struck value. Nothing in the row
marks it as carried forward.

`resolve_daily_nav()` therefore resolves the true as-of date against the on-chain
checkpoint series — a checkpoint exists **iff** a NAV was struck that date. This
distinguishes real strikes from weekends, market holidays, and business days whose strike
has not published yet, with no holiday calendar required:

```
2026-07-24 Fri  →  strike    as_of 2026-07-24   11.15641200
2026-07-25 Sat  →  weekend   as_of 2026-07-24   11.15641200   ← carried
2026-07-03 Fri  →  holiday   as_of 2026-07-02   11.13311100   ← carried
2026-08-03 Mon  →  pending   as_of 2026-07-31   11.16390300   ← not yet published
```

Verified 24/24 real strikes matching on-chain exactly over one month.

### 3. The strike is 17:00 ET, so its UTC hour moves with DST

| Period | Strike in UTC |
|---|---|
| EST (Nov–Mar) | 22:00 |
| EDT (Mar–Nov) | 21:00 |

Confirmed across all 412 checkpoints: 265 at 21:00 UTC, 147 at 22:00 UTC, nothing else.
A hardcoded UTC default bakes in a seasonally drifting error, so use the **Strike** preset
when comparing against the official daily NAV — it is the only instant where all three
figures are comparable. End-of-day `23:59:59` sits 2–3h later and always reads above.

## Two bracketing views

A strike is invisible to the oracle until published (lag 0.67–3.68 days), so what the
oracle *said* at a past instant differs from the best estimate *now*:

- `ORACLE_VIEW` — brackets by `effective_at`. Reproduces exactly what a smart contract
  would have read at that instant. The off-chain API replicates this bit-for-bit.
- `HINDSIGHT_VIEW` — brackets by `timestamp`, using every strike now known. At a strike
  instant this returns that strike's stored value exactly, so it reconciles to the
  official daily NAV to the integer.

They agree except inside publication gaps, where they diverge by up to **0.04 bps**.

## Verified against mainnet

Oracle `0xe4fa682f94610ccd170680cc3b045d77d9e528a8`:

| Check | Result |
|---|---|
| `checkpoints(uint256)` → `b8a24252` | keccak-verified |
| `calculateRealtimeNavs(uint128×5)` → `62955b9b` | keccak-verified |
| `decimals()` | `6` |
| `CHECKPOINT_EXPIRATION_PERIOD()` | `432000` (5 days) |
| `timestamp` / `effective_at` monotonic | strictly increasing, all 412 |
| `effective_at > timestamp` | always; lag 0.67–3.68 days |
| API vs on-chain (oracle view) | exact to 6 dp across 9 months |

## Caveats

- **Pre-inception timestamps** return `0.000000` from the API with your timestamp echoed
  back. Raised as `NavUnavailable` rather than displayed as a NAV of zero.
- **Future timestamps** are silently clamped to "now" by the API; flagged as
  `clamped_from_future`.
- **Staleness** is measured from the checkpoint's `timestamp`, not `effective_at` —
  measuring from publication time would be lenient by up to 3.68 days.
- Weekend and Friday end-of-day readings are the least reliable points in the series:
  they extrapolate off a slope that predates the most recent (unpublished) strike.
- The public RPC is rate-limited. `_eth_call` distinguishes a genuine contract revert
  (`code 3, "execution reverted"`) from transport failures and retries the latter —
  without that, a throttled response would truncate the checkpoint search and return a
  stale price with no error.

## Layout

```
nav_time.py                 UTC/ET handling, strike resolution, CLI instant parsing
superstate_nav.py           off-chain API: continuous price + daily NAV provenance
superstate_onchain_nav.py   on-chain oracle: checkpoints + interpolated price
compare_nav.py              CLI three-way reconciliation
streamlit_app.py            UI
```
