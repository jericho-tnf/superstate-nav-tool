# Superstate NAV Query

Look up Superstate USTB NAV per share at a specific UTC instant, from three sources, and
reconcile them:

| Source | What it is | Evidential weight |
|---|---|---|
| **Off-chain API** | `api.superstate.com` continuous price — defined at every second | Issuer-reported, documented |
| **On-chain oracle** | Computed on Ethereum by `calculateRealtimeNavs` from daily checkpoints | Reproducible, but **derived** |
| **Official daily NAV/S** | `nav-daily` — one calculated value per business day, 17:00 ET | **Attestable** — a stored on-chain fact |

Only the third is a value anyone actually calculated. The first two are the same
straight-line accrual model, computed in two places; they agree bit-for-bit, which is an
integrity check on the on-chain publication, not independent corroboration of the NAV.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py                       # UI
python compare_nav.py 2026-07-27                     # CLI reconciliation
python compare_nav.py 2026-07-27 --worksheet         # + Etherscan re-performance steps
```

## Dependencies

`requirements.in` is the source of truth. `requirements.txt` is generated from it and
holds the **fully pinned tree**, because Streamlit Cloud installs from
`requirements.txt` and nothing else. Edit the `.in`, then regenerate:

```bash
python -m uv pip compile requirements.in \
  --python-version 3.14 --python-platform linux -o requirements.txt
```

Resolve for the deployment target — Streamlit Cloud runs Linux on Python 3.14 — not for
whatever machine you are on, or the pins can reference wheels that do not exist there. The
current lock is verified installable on both that target and Windows/3.12 for local work.

This exists because an unpinned build broke a previously working deploy with no repo
change: starlette 1.4.0 shipped on 2026-08-05 and made `thread_minimum_size` a required
argument of `GZipResponder.__init__`, which Streamlit's subclass does not pass, so every
request raised `TypeError` and the server answered 500 to health checks. For a tool whose
output goes into an engagement file, a figure regenerated in six months should come from
the same code path — hence the full pin rather than just patching that one package.

## Sources of record

| What | Where |
|---|---|
| API spec (OpenAPI) | `https://api.superstate.com/api-docs/openapi.json`, rendered at [`/swagger-ui/`](https://api.superstate.com/swagger-ui/) |
| `real-time-price` | spec tag `Prices`, operationId `real_time_price_handler`, `POST /v1/funds/{id}/real-time-price`, unauthenticated |
| `nav-daily` | spec tag `funds_runtime_data_handler`, plus [docs.superstate.com/investors/api](https://docs.superstate.com/investors/api) |
| Contract addresses | [docs.superstate.com/investors/smart-contracts](https://docs.superstate.com/investors/smart-contracts) |
| Oracle contract | `0xe4fa682f94610ccd170680cc3b045d77d9e528a8` — "Superstate USTB Continuous Price Oracle" |
| Chainlink USTB feed | `0x289B5036cd942e619E1Ee48670F98d214E745AAC` — independent daily NAV/S, **not yet used by this tool** |
| Oracle source | [`superstateinc/onchain-redemptions`](https://github.com/superstateinc/onchain-redemptions) → `src/oracle/SuperstateOracle.sol` |

## Three things that make this non-obvious

### 1. The on-chain "continuous" price is derived, not stored

Only **one checkpoint per business day** is ever written on-chain — the 17:00 ET NAV/S
checkpoint, published the next business morning at ~13:07 UTC. Every intermediate value is computed
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
date** into `net_asset_value_date` and returns the last calculated value. Nothing in the
row marks it as carried forward.

`resolve_daily_nav()` therefore resolves the true as-of date against the on-chain
checkpoint series — a checkpoint exists **iff** a NAV/S was calculated that date. This
distinguishes real checkpoints from weekends, market holidays, and business days whose
checkpoint has not published yet, with no holiday calendar required:

```
2026-07-24 Fri  →  checkpoint  as_of 2026-07-24   11.15641200
2026-07-25 Sat  →  weekend     as_of 2026-07-24   11.15641200   ← carried
2026-07-03 Fri  →  holiday     as_of 2026-07-02   11.13311100   ← carried
2026-08-03 Mon  →  pending     as_of 2026-07-31   11.16390300   ← not yet published
```

Verified 24/24 real checkpoints matching on-chain exactly over one month.

### 3. The NAV/S checkpoint is 17:00 ET, so its UTC hour moves with DST

| Period | Checkpoint in UTC |
|---|---|
| EST (Nov–Mar) | 22:00 |
| EDT (Mar–Nov) | 21:00 |

Confirmed across all 412 checkpoints: 265 at 21:00 UTC, 147 at 22:00 UTC, nothing else.
A hardcoded UTC default bakes in a seasonally drifting error, so use the **NAV/S
checkpoint** preset when comparing against the official daily NAV/S — it is the only
instant where all three figures are comparable. End-of-day `23:59:59` sits 2–3h later and always reads above.

## Bracketing: the UI uses one view, deliberately

A checkpoint is invisible to the oracle until published (lag 0.67–3.68 days), so which two
checkpoints you bracket against matters.

`ORACLE_VIEW` brackets by `effective_at` — when a checkpoint becomes *usable*, not when its
NAV/S was calculated. That is what the contract itself does; `effective_at` exists in the
struct for precisely that purpose. It reproduces exactly what a smart contract would have
read at that instant, and the off-chain API replicates it bit-for-bit. **The UI uses this
and nothing else.**

`HINDSIGHT_VIEW` brackets by `timestamp` instead, using every checkpoint now known. The
module still exposes it for analytical use from the CLI, but **the oracle never does this**,
so its output is not an on-chain figure. It was removed from the UI because showing it
beside the official NAV/S invited recording a value the oracle never reported — at
2026-07-31 23:59:59 the oracle returned `11.164048` while hindsight gives `11.164035`.

The two differ only inside publication gaps, by up to **0.04 bps**.

And if what you want is the official NAV/S, neither is the route — read the checkpoint
directly. At a checkpoint instant the hindsight figure is arithmetically identical to
`checkpoints(N).navs` (the interpolation term is multiplied by zero), so it is a roundabout
path to a number the third card already reads straight from storage.

## Re-performing a figure by hand

Every on-chain number is reproducible on a block explorer, which is the point of tagging
it `derived` rather than trusting it. The provenance panel emits the exact
`calculateRealtimeNavs` inputs with each one traced to `checkpoints(N).navs` or
`.timestamp`, plus the bracket rationale; `--worksheet` prints the same thing for both
views. Worked example for 2026-07-31 23:59:00 UTC:

```
checkpoints(409) -> navs=11161771  timestamp=1785358800
checkpoints(410) -> navs=11162843  timestamp=1785445200

calculateRealtimeNavs(1785542340, 11161771, 1785358800, 11162843, 1785445200)
  -> 11164048  ->  $11.164048
```

What this tests is exactly what the code decides — the target timestamp and the choice of
two checkpoints. The interpolation belongs to the contract and is not under test.

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
  back, as HTTP `200` rather than the `400` the spec lists. Raised as `NavUnavailable`
  rather than displayed as a NAV of zero.
- **Future timestamps** are silently clamped to "now" by the API; flagged as
  `clamped_from_future`. Neither this nor the `0.000000` behaviour appears in the OpenAPI
  spec, which is why both guards are hand-rolled rather than driven off documented errors.
- **Staleness** is measured from the checkpoint's `timestamp`, not `effective_at` —
  measuring from publication time would be lenient by up to 3.68 days.
- Weekend and Friday end-of-day readings are the least reliable points in the series:
  they extrapolate off a slope that predates the most recent (unpublished) checkpoint.
- The public RPC is rate-limited. `_eth_call` distinguishes a genuine contract revert
  (`code 3, "execution reverted"`) from transport failures and retries the latter —
  without that, a throttled response would truncate the checkpoint search and return a
  stale price with no error.
- The public RPC also **refuses archive requests** (`"Archive requests require a personal
  token"`), so historical state and log queries are unavailable. Anything needing them
  requires an archive-capable endpoint.

## Known unresolved

**Share count does not reconcile to on-chain supply.** `outstanding_shares` from
`nav-daily` materially exceeds the summed token supply across every chain Superstate
documents:

```
Ethereum totalSupply()          67,695,561.610335
+ Plume                            226,637.757499
+ Solana                           210,704.984207
                                ─────────────────
  total on-chain                 68,132,904.352041
  outstanding_shares (08/03)      83,348,590.578575
  gap                            −15,215,686  (−18.3%)
```

The gap **grew** as shares were issued (79.65M on 07/31 → 83.35M on 08/03), so it is
structural rather than a timing artefact — roughly 15M shares appear not to be tokenised
on any chain. Whether that is book-entry holdings, another share class, or something else
is unverified.

**Consequence:** on-chain token supply cannot currently be used as an independent check on
the NAV denominator, and `AUM ÷ outstanding_shares` reconciles only within the API's own
figures (exactly, to 6e-14). Confirming this needs an archive RPC to read historical
supply, or an answer from Superstate.

## Not yet done

- **Chainlink USTB feed** (`0x289B5036…`) is documented and live but unused. It is the one
  genuinely *independent* source available — a separate contract, separate operator —
  and its answer matched `checkpoints(411).navs` exactly (`11163903`), published ~90
  seconds after Superstate's own checkpoint became effective. Adding it would give real
  corroboration rather than two views of the same data.
- **Holdings** (`GET /v2/funds/1/holdings`) returns the actual T-bill portfolio with
  maturities and costs. Pricing those independently is the only route to verifying the NAV
  rather than confirming it was published consistently.
- **`GET /v1/funds/{id}/share-distribution`** may explain the share-count gap above.

## Layout

```
nav_time.py                 UTC/ET handling, checkpoint resolution, CLI instant parsing
superstate_nav.py           off-chain API: continuous price + daily NAV provenance
superstate_onchain_nav.py   on-chain oracle: checkpoints + interpolated price
compare_nav.py              CLI three-way reconciliation
streamlit_app.py            UI
```
