# Blue Vector: How the Models Work and What the Results Mean

**A beginner's guide to the implemented methodology, equations, and results.**

Results snapshot: **November 22, 2026**, on `issue-5/integration`.

This document explains the executed implementation. Older assignment documents and flow-model
notes contain examples from earlier data vintages. Current scorecards support the numbers here.
No model was retrained or changed to write this guide.

> **Subsequent audit, 2026-11-22:** [section 15](#15-methodology-audit-and-hormuz-readiness) records a material Hormuz surrogate failure and price-feature limitation. The historical scores below remain valid descriptive results, but they do not certify readiness to use all three models as reliable crisis simulators.

## Contents

1. [Results in plain English](#1-results-in-plain-english)
2. [The questions and the models](#2-the-questions-and-the-models)
3. [Data and the shipping network](#3-data-and-the-shipping-network)
4. [Estimating demand](#4-estimating-demand)
5. [The flow model](#5-the-flow-model)
6. [Shipping costs and network indices](#6-shipping-costs-and-network-indices)
7. [The graph neural network](#7-the-graph-neural-network)
8. [Training and testing the GNN](#8-training-and-testing-the-gnn)
9. [The price model](#9-the-price-model)
10. [Errors, accuracy, and uncertainty](#10-errors-accuracy-and-uncertainty)
11. [Detailed results and comparisons](#11-detailed-results-and-comparisons)
12. [What can be concluded](#12-what-can-be-concluded)
13. [Reproduction and source files](#13-reproduction-and-source-files)
14. [Glossary](#14-glossary)
15. [Methodology audit and Hormuz readiness](#15-methodology-audit-and-hormuz-readiness)

## 1. Results in plain English

Blue Vector represents maritime shipping as a network. It estimates how disruptions redistribute
crude-oil traffic, calculates transport costs, and tests whether shipping information helps
forecast Brent oil prices.

- **Real-only GNN:** 12.18% average percentage error on the evaluated weekly tanker observations;
  67.95% accuracy in the direction of weekly changes.
- **Simulated-plus-real GNN:** 12.77% average percentage error; 67.73% directional accuracy.
- **Mixed training versus repeating last week's traffic:** RMSE improved by 6.5% on Ever Given
  and 18.7% on Panama, but deteriorated by 17.2% on Red Sea. Simulator pretraining did not help
  uniformly.
- **Separate scenario surrogate:** 17.10% weighted percentage error for tanker transits against
  the flow simulator on unseen compound disruptions. This measures simulator agreement, not
  real-world accuracy.
- **Observed-network price model:** 2.30% average percentage error and $2.40/barrel RMSE,
  versus 2.41% and $2.46/barrel for repeating last week's price. The 2.6% RMSE improvement is
  statistically uncertain.

The results support a reproducible research prototype with useful behavior on some historical
windows. They do not establish a reliable causal Brent price effect from closing a canal or
validate real-only GNN counterfactual flows on unobserved lanes.

## 2. The questions and the models

Imagine closing Suez. Some ships may go around Africa, increasing distance and avoiding canal
tolls. Elsewhere, a closure with no modeled alternative may prevent cargo from moving.
Predicting these consequences involves several distinct questions:

| Question | Task | Current model |
|---|---|---|
| Given a closure, how would crude move under our assumptions? | Conditional scenario simulation | Flow model |
| Can a learned model reproduce the simulator's answers? | Simulator imitation | Separate GNN scenario surrogate |
| Given past observations, what will next week's traffic be? | Temporal forecasting | Real-only and mixed-training GNNs |
| Given past prices and shipping information, what will next week's Brent price be? | Temporal forecasting | Ridge regression |
| How much of a price change is caused by the closure? | Causal inference | Not identified by this implementation |

A **scenario** assumes a particular disruption. A **forecast** uses information preceding its
target observation. A **causal effect** separates the disruption's influence from other events
happening at the same time. Accuracy on one task does not establish accuracy on another.

```text
Comtrade + PortWatch port totals --> Crude demand --> Flow simulator --> Shipping costs
                                                         |
                                                         v
                                                Simulated scenarios
                                                  |             |
                                                  v             v
                                           GNN surrogate   GNN pretraining
                                                                |
PortWatch past traffic -----------------------------------------> Mixed traffic GNN
          |
          +----------------------------------------------------> Real-only traffic GNN
          |
          +--> Observed Suez/Cape share --+
                                        +--> Weekly price regression
EIA past Brent prices -------------------+
Frozen GNN summaries, optionally --------+
```

The current price regression uses observed, lagged network information. It does not use fabricated
historical global freight costs or directly price every simulated barrel of stranded demand.

## 3. Data and the shipping network

### 3.1 Data sources

| Source | Used for | Qualification |
|---|---|---|
| IMF PortWatch | Daily chokepoint tanker counts and port import/export tonnage estimates | All tankers are not all crude carriers; carrying capacity is not actual loaded cargo. |
| UN Comtrade, HS 2709 | Country-to-country crude trade | Reporting is incomplete and importer/exporter records can disagree. |
| EIA | Daily Brent prices and upstream chokepoint oil-flow reference estimates | Some published flow totals include products or pipelines. |
| Tracked graph/configuration | Locations, distances, connections, tariff assumptions, parameters | These are reference/model inputs, not live ship positions. |

[DATA.md](DATA.md) documents source access and qualifications. No new source is adopted here.
PortWatch already aggregates AIS-derived maritime activity; we do not process every raw ship
position. AIS is a vessel-position reporting system.

### 3.2 The graph

A **graph** is a set of places, called nodes, joined by connections, called edges. Here it has
**142 nodes**: 100 ports and 42 sea junctions. Its **279 edges** consist of 10 chokepoints,
69 lanes, and 200 port connectors.

A connector lets a voyage begin or end at a port. The router forbids using an intermediate port
as a shortcut. The ten chokepoints are Suez, Bab-el-Mandeb, Hormuz, Malacca, Gibraltar, Panama,
Cape of Good Hope, Bosporus, Sunda, and Magellan.

Stored physical corridors combine traffic traveling in both directions. A CSV's `from_node`
and `to_node` ordering does not turn its total into a signed one-way flow.

### 3.3 Units and weekly observations

| Quantity | Unit |
|---|---|
| Oil demand/volume | Barrels per week, bbl/week |
| Tanker traffic | Transits per week |
| Distance | Nautical miles, nm |
| Speed | Knots: nautical miles/hour |
| Sailing time/delay | Days |
| Charter rate | US dollars per ship per day |
| Shipping cost or Brent price | US dollars per barrel |
| Utilization/share | Dimensionless fraction; multiply by 100 for percent |

A transit is one passage through a chokepoint. A ship passing several chokepoints can contribute
to several counts. These counts are not a count of unique ships across the whole network.

Weeks end on Friday. The real-data GNN requires all seven daily observations for a chokepoint-week.
Missing weeks remain missing rather than becoming zero traffic. Weekly Brent is the average of
available daily prices; holidays are excluded rather than assigned a zero price.

### 3.4 Provenance

`observed`, `modeled`, `simulated`, and `gnn` labels distinguish measurements, calculations, solver
training cases, and neural outputs. A row-level source label does not mean every derived column
was directly measured. In particular, only observed transit counts supervise real-data GNN loss.
Crude volumes, utilization, and delays require additional assumptions or calculations.

## 4. Estimating demand

The router needs an **origin-destination matrix**, or OD matrix. Entry $Q_{ij}$ is crude shipped
from origin port $i$ to destination port $j$.

### 4.1 Allocate country trade among ports

The country pipeline uses 2019–2022 trade reports, preferring importer reports when both sides
exist. It uses valid sea-mode reports where available, otherwise deducts valid pipeline quantities
from all-mode totals, otherwise retains an all-mode proxy. Method fields record those choices.
The result is therefore not a completely observed sea-only shipping manifest.

An initial port allocation is:

$$
Q_{ij}^{(0)}=F_{ab}s_i^{\mathrm{export}}s_j^{\mathrm{import}}
$$

$F_{ab}$ is annual crude trade from country $a$ to country $b$. Port $i$ belongs to $a$, port $j$
to $b$, and the $s$ terms are their shares of country port activity. Illustratively, a 25% export
share and 40% import share allocate $0.25\times0.40=10\%$ of the country pair's initial flow.

Port totals use a 2019-01-01–2023-10-31 reference window. The existing port-universe crude-share
assumption is **0.53**, with approximately **7.3 barrels per tonne**. The flow solver separately
uses **7.33** when converting carrying-capacity tonnes into its edge cargo-per-transit proxy.
These are approximate conversions, not direct measurements of loaded crude.

### 4.2 Match port totals with IPF

**Iterative proportional fitting (IPF)** alternates between adjusting exports and imports:

$$
Q_{ij}\leftarrow Q_{ij}\frac{R_i}{\sum_jQ_{ij}},\qquad
Q_{ij}\leftarrow Q_{ij}\frac{C_j}{\sum_iQ_{ij}}
$$

$R_i$ is port $i$'s export target; $C_j$ is port $j$'s import target. Fixing columns can disturb
rows, so the algorithm repeats until both match sufficiently well. Because selected ports also
trade outside the network, total exports and imports are first balanced to their mean.

Domestic pairs and self-shipping are excluded. The existing rules handle zero-target/unsupported
marginals. Unreported trade pairs are not filled with invented positive seed flows in this run
(`SEED_FILL = 0`). The fit converged in **75 iterations**, with maximum relative marginal error
**$9.4\times10^{-7}$**, producing **5,380 positive port pairs**.

Annual volume becomes weekly volume through:

$$
Q_{ij}^{\mathrm{week}}=Q_{ij}^{\mathrm{year}}/52.1429
$$

Current total demand is **144,826,807 barrels/week**, with zero baseline stranded demand.
Older repository totals belong to earlier input vintages.

## 5. The flow model

Implementation: [flow_solver.py](scripts/flow_model/flow_solver.py) and
[scenario_engine.py](scripts/flow_model/scenario_engine.py).

### 5.1 Sailing time, transits, and utilization

For edge $e$:

$$
f_e=\frac{L_e}{24v},\qquad T_e=\frac{V_e}{B_e},\qquad u_e=\frac{T_e}{C_e}
$$

$L_e$ is nautical miles, $v$ is speed in knots, $f_e$ is free sailing days, $V_e$ is weekly barrels,
$B_e$ is assumed barrels per tanker transit, $T_e$ is crude-equivalent transits, and $C_e$ is weekly
capacity on the same crude-transit basis. Configured speed is **13 knots**. An illustrative
3,120 nm leg therefore takes $3,120/(24\times13)=10$ days before congestion.

Cargo size uses estimated carrying-capacity information where available, otherwise the documented
fallbacks, including **1,000,000 bbl/transit**. Open capacity is a historical tanker-throughput
proxy multiplied by the corridor's crude share. A historical percentile is not a physical wall:
unthrottled edges can exceed it.

### 5.2 Congestion and routing cost

The delay used during loading is:

$$
d_e=s_e\alpha\min(u_e,5)^\beta+\gamma\max(0,u_e-1)
$$

$d_e$ is days of delay; $s_e$ is a one-day service base at chokepoints or free sailing days elsewhere.
The configured values are $\alpha=0.12$, $\beta=3$, and $\gamma=1$ day per excess utilization unit.
The power term makes delay grow with congestion; the second term adds queueing above capacity.
This is a model relationship, not an observed queue at every passage.

Generalized routing cost is:

$$
c_e=\frac{(f_e+d_e)R}{B_e}+\tau_e+w_e(1-q_e)
$$

$R$ is dollars per ship per day, $\tau_e$ is toll dollars/barrel, $w_e$ is the applicable war-risk
parameter, and $q_e$ is the fraction of baseline throughput retained. Every term is dollars/barrel.
Charter cost means the cost of hiring the ship. A longer untolled route can be cheaper than a
short congested canal.

### 5.3 Ten-pass assignment and disruption

Dijkstra's shortest-path algorithm finds lowest-cost routes. The solver loads one tenth of demand
in each of **ten passes**, updating costs between passes. This is incremental loading, not an
exact global constrained optimum; the finite step size and order can affect the approximation.

A requested $q_e=0$ closes a passage, $q_e=1$ leaves it open, and intermediate values throttle it.
For a throttled edge with positive baseline traffic:

$$
C_e(q_e)=q_eT_e^{\mathrm{baseline}}
$$

If baseline traffic is zero, the solver instead scales open capacity. The exported
`throttle_factor` is capacity/open-capacity, clipped to $[0,1]$, and can differ from requested $q_e$.
An already **98%-saturated throttled edge** is removed before subsequent loading passes. Since
this happens between increments, capacity is not an exact continuously enforced within-pass ceiling.

Demand with no remaining path is **stranded**. This means it cannot move in the modeled network
under the assumed disruption, not that the oil disappears or that every real alternative is absent.

### 5.4 Historical settings and calibration

| Scenario | Assumed restriction |
|---|---|
| Ever Given | Suez retains $1/7$ throughput for a representative blocked week |
| Panama drought | Panama retains $24/36$ throughput |
| Red Sea | Suez and Bab-el-Mandeb retain 0.85; war-risk parameter $1.25/bbl |

Existing Red Sea settings were calibrated on December 2023–February 2024. The objective combined
Suez/Cape percentage-change errors and share error:

$$
\mathcal L_{\mathrm{cal}}=
\frac{|p_S^{m}-p_S^{o}|+|p_C^{m}-p_C^{o}|}{100}+|s^m-s^o|
$$

$p$ is percentage change in percentage points; $s$ is Suez/(Suez+Cape) as a fraction. Superscripts
$m,o$ mean modeled and observed. March–June 2024 was reserved for assessment. Current rebuilding
preserved these settings rather than recalibrating them to the new demand matrix. They are a
later-vintage input when discussing 2021: they were not historically available then.

## 6. Shipping costs and network indices

Implementation: [shipping_cost.py](scripts/price/shipping_cost.py) and
[network_indices.py](scripts/price/network_indices.py).

### 6.1 Freight cost on an edge

The shipping-cost calculation isolates charter-and-toll cost:

$$
c_e^{\mathrm{freight}}(R)=\frac{(f_e+d_e)R}{B_e}+\tau_e
$$

It does not separately add the router's explicit war-risk preference penalty. The selected route
can still reflect that preference. Low/base/high charter rates are **$35k/$50k/$75k per day**;
these are assumptions, not a statistical prediction interval.

For active edges, cargo size is volume/transits. Idle edges use the configured fallback. Recovering
baseline tolls subtracts both sailing and delay costs:

$$
\tau_e=\max\left(0,c_e^{\mathrm{baseline}}-
\frac{(f_e+d_e^{\mathrm{baseline}})R_{\mathrm{base}}}{B_e}\right)
$$

Subtracting sailing alone would mistakenly classify baseline delay as toll and charge it twice.
Illustratively, two days plus one delay day at $50k/day and 1 million bbl/transit cost $0.15/bbl
before tolls. A $0.20/bbl toll makes total transport cost $0.35/bbl. These are teaching numbers.

### 6.2 Complete voyage cost and days

Each served barrel crosses a loading connector and an unloading connector. Thus served barrels are:

$$
D=\frac12\sum_{e\in\mathrm{connectors}}V_e
$$

Then complete-voyage averages and their scenario change are:

$$
\overline c(R)=\frac{\sum_eV_ec_e^{\mathrm{freight}}(R)}{D},\qquad
\Delta\overline c=\overline c_{\mathrm{scenario}}-\overline c_{\mathrm{baseline}}
$$

$$
\overline t=\frac{\sum_eV_e(f_e+d_e)}{D}
$$

Dividing by all edge traversals instead of $D$ produces an average leg, not a voyage. Splitting a
ten-day leg into two five-day legs must leave full voyage cost unchanged; this is checked.
When demand strands, the served-voyage population changes, which affects comparison with baseline.
Final-edge-state days can differ from the flow engine's accumulated loading-pass voyage summary.

### 6.3 Suez share and changed transport work

For observed weekly counts $N_{S,t}$ at Suez and $N_{C,t}$ at the Cape:

$$
s_t=\frac{N_{S,t}}{N_{S,t}+N_{C,t}}
$$

This is that two-passage routing split, not Suez's share of all global shipping. For crude-based
scenarios, apply relative solver changes to January–October 2023 observed all-tanker means first:

$$
\widetilde N_e=\overline N_e^{\mathrm{reference}}
\frac{T_e^{\mathrm{scenario}}}{T_e^{\mathrm{baseline}}}
$$

Form the scenario share from these implied counts. This assumes crude percentage responses can
proxy all-tanker responses; it does not equate their raw count levels.

Changed edge activity is summarized by:

$$
I_{\mathrm{reroute}}=\frac{\sum_e|V_e^{\mathrm{scenario}}-V_e^{\mathrm{baseline}}|L_e}{10^9}
$$

Units are billion barrel-nautical-miles. The absolute sum counts decreases and increases, so it is
not net additional voyage distance or a count of unique displaced barrels. The flow engine has
a separate OD-based extra-work calculation for comparable served pairs.

Historical global route days, freight costs, and rerouted work are not directly measured by these
chokepoint counts. Historical feature fields for them remain missing; the price regression does
not fit coefficients to invented zero-valued route histories.

## 7. The graph neural network

Implementation: [gnn_model.py](scripts/gnn/gnn_model.py) and
[train_gnn.py](scripts/gnn/train_gnn.py).

### 7.1 Learning from connected routes

A conventional model reads a row of numbers. A GNN also uses which places and routes connect.
Each node and edge receives a numerical **hidden state**, then repeatedly exchanges information
with neighbors. These internal vectors are learned representations, not individually named
physical quantities such as barrels or dollars.

An **MLP** is a small neural network of learned linear transformations and nonlinear functions.
The executed experiments use hidden vectors of length **32** and **three message-passing layers**.
The model class defaults to 64 hidden units; the trainer explicitly selects 32.

### 7.2 Inputs and normalization

Node inputs have six slots: port flag, junction flag, latitude/90, longitude/180, and two demand
slots. Both demand slots are zero in the executed trainer, so solver-derived OD demand does not
enter the real-only model.

Edge inputs have fourteen slots. The temporal trainer reuses the original dataset interface's
first eleven positions, but some meanings differ from simulator pretraining:

| Slots | Simulator-pretraining inputs | Temporal real-data inputs |
|---|---|---|
| 0–2 | Lane/chokepoint/connector flags | Same |
| 3 | Distance / 10,000 nm | Same |
| 4 | Sailing days / 30 at 13 knots | Same |
| 5 | Baseline model transits, floored at one, / 1,000 | Training observed mean, floored at one, / 1,000 |
| 6 | Toll dollars/barrel | Zero; current tariffs excluded |
| 7 | Reported scenario throttle factor | One; no scenario closure input |
| 8 | Baseline volume / training volume scale | Zero |
| 9 | Same baseline transit feature as slot 5 | Same observed mean feature as slot 5 |
| 10 | Baseline solver utilization | Zero |
| 11–13 | Ones, representing normalized baseline history | Normalized lag-one, four-week mean, seasonal count |

Temporal slot 5 is a scale feature, not an observed physical capacity. This table describes actual
training inputs, not every feature originally proposed in the assignment.

For chokepoint $e$, the scale uses only training weeks:

$$
\mu_e=\max\left(1,\operatorname{mean}_{t\in\mathrm{training}}N_{e,t}\right)
$$

The three lagged inputs for target week $t$ are:

$$
\frac{N_{e,t-1}}{\mu_e},\qquad
\frac{\frac14\sum_{j=1}^{4}N_{e,t-j}}{\mu_e},\qquad
\frac{N_{e,t-52}}{\mu_e}
$$

The seasonal input falls back to the expanding mean strictly before $t$ when the year-ago value
is unavailable. Missing lane observations are tensor placeholders and receive no real-data loss;
they do not become measured zero traffic.

### 7.3 Message-passing equations

Let $x_v,x_e$ be the node/edge inputs and $h_v,h_e$ their hidden states. First encode them:

$$
h_v^{(0)}=\operatorname{MLP}_{\mathrm{node}}(x_v),\qquad
h_e^{(0)}=\operatorname{MLP}_{\mathrm{edge}}(x_e)
$$

For each layer $\ell$, sum incident edge states and update nodes, then edges:

$$
a_v^{(\ell)}=\sum_{e\ni v}h_e^{(\ell)}
$$

$$
h_v^{(\ell+1)}=\operatorname{LayerNorm}
\left(h_v^{(\ell)}+\operatorname{ReLU}(W_\ell a_v^{(\ell)}+b_\ell)\right)
$$

$$
h_e^{(\ell+1)}=\operatorname{LayerNorm}\left(h_e^{(\ell)}+
\operatorname{MLP}_\ell([h_u^{(\ell+1)},h_v^{(\ell+1)},h_e^{(\ell)}])\right)
$$

$u,v$ are the edge endpoints; square brackets mean joining vectors. ReLU replaces negative values
with zero. Layer normalization rescales hidden vectors to stabilize learning. Adding the old
state is a residual connection, preserving useful information while learning updates. Messages
aggregate at both endpoints because physical corridors carry both travel directions.

### 7.4 Outputs and the eight-number graph summary

The edge head produces four scaled outputs: volume, transits, utilization, and delay. A Softplus
activation keeps them nonnegative:

$$
\operatorname{Softplus}(z)=\log(1+e^z)
$$

A mask sets volume and transits to exactly zero for closed edges. Real-data training supervises
only the transit output at observed chokepoints. Other real-only outputs are not validated
physical estimates.

Temporal transit predictions are multiplied by $\mu_e$ to recover transits/week. For example,
a training mean of 100 and normalized output 0.95 imply a forecast of 95. If the actual count is
100, absolute error is five transits and percentage error is 5%. This is illustrative arithmetic.

An **embedding** is a compact graph summary. The model concatenates the mean and elementwise
maximum of final edge states, then maps that vector to eight numbers:

$$
z=W_{\mathrm{emb}}[\operatorname{mean}_eh_e,\operatorname{max}_eh_e]+b_{\mathrm{emb}}
$$

The edge loss trains the underlying hidden states. The embedding projection has no separate
training loss and remains a fixed seeded projection in these runs. Its eight coordinates are
not eight identified economic causes.

## 8. Training and testing the GNN

### 8.1 Separate learning, selection, and assessment

**Training** adjusts weights. **Validation** selects a checkpoint and when to stop. **Testing**
assesses the selected model after those choices are frozen. Using target weeks to choose scales
or checkpoints would leak answers into the experiment.

| Event model | Last training Friday | Validation Fridays | Test Fridays | Test pairs |
|---|---|---|---|---:|
| Ever Given | 2020-12-25 | 2021-01-01–03-12 | 2021-03-19–04-09 | 40 |
| Panama | 2023-04-28 | 2023-05-05–07-28 | 2023-08-04–12-29 | 220 |
| Red Sea | 2023-07-28 | 2023-08-04–10-27 | 2024-03-01–06-28 | 180 |

A pair is one chokepoint in one week, not an independent disruption. Ten chokepoints across four
Ever Given weeks create 40 pairs. Earlier events can enter later-event training histories.
Red Sea H1 is excluded from temporal fine-tuning and checkpoint selection.

Within the test window, the last observed week can inform the next forecast while weights remain
frozen. This is repeated one-week forecasting, not predicting an entire crisis before it starts.

### 8.2 Real-only versus simulated-plus-real

**Real-only** starts from random weights and uses only observed PortWatch transit labels.
**Mixed training** first learns from the flow simulator, then fine-tunes the same architecture
on the same real training/validation weeks.

The simulator creates:

$$
1+79\times4+5\times4=337\text{ scenarios}
$$

These are a baseline, each of 79 lanes/chokepoints restricted at four levels, and five compound
combinations at four levels. Connector closures are excluded. With 279 edges each, there are
**94,023 rows**. The 317 baseline/single-edge cases become **254 training** and **63 validation**
cases via fixed-seed shuffling. All **20 compound cases** are held out from both. Three historical
scenario inputs are also exported for assessment rather than used in those splits.

Volume, utilization, and delay targets are divided by their training-data 95th-percentile scales,
floored at one. Transits are divided by the edge's baseline simulated count, floored at one.
Divide-only scaling preserves zero for closure masking. Temporal inputs/targets instead use
training observed means. Pretraining transfers weights across these input/target regimes; it
does not make crude-equivalent counts identical to observed all-tanker counts.

Mixed training includes a later-vintage OD matrix and a simulator calibrated using Red Sea data.
It is a **retrospective prior**, not a clean as-of-2021 information set. Graph selection is also
retrospective. Source publication delays and revisions are not reconstructed, so these are
observation-time backtests, not live release-time forecasts.

### 8.3 Loss and optimization

For normalized residual $r=\widehat y-y$, smooth L1/Huber loss uses:

$$
\ell(r)=\begin{cases}\tfrac12r^2,&|r|<1\\|r|-\tfrac12,&|r|\ge1\end{cases}
$$

Small errors are penalized quadratically, while large errors grow linearly to reduce domination
by a few extremes. With supervision mask $m_{e,k}$ for edge $e$ and target $k$:

$$
\mathcal L=\frac{\sum_{e,k}m_{e,k}\ell(\widehat y_{e,k}-y_{e,k})}{\sum_{e,k}m_{e,k}}
$$

Simulator training supervises four targets; real training supervises observed transits only.
There is no signed conservation-loss term: combined two-way edge labels cannot correctly be
treated as signed flows just from their arbitrary CSV orientation.

Training uses Adam, learning rate **0.001**, weight decay **0.0001**, batches of **32**, gradient-norm
clipping at **1**, and at most **150 pretraining/180 fine-tuning epochs**. An epoch is one pass
through training samples. After 25 epochs without sufficient validation improvement, training
stops and restores the best validation checkpoint.

Seeds **7, 19, 43** control initialization and shuffling. Predictions average all three:

$$
\widehat y_{\mathrm{ensemble}}=(\widehat y_7+\widehat y_{19}+\widehat y_{43})/3
$$

No seed is selected using test error. Seeds measure training variability, not three independent
real-world experiments.

### 8.4 The separate scenario-surrogate export

`gnn_edge_predictions.parquet` uses simulator-pretrained checkpoints **before temporal fine-tuning**.
It is separate from the two real weekly forecasting experiments. Its 20 compound and three
historical inputs produce **6,417 rows** in the 22-column contract.

Exports reconcile predicted transits with baseline cargo size and scenario capacity:

$$
\widehat V_e=\widehat T_eB_e^{\mathrm{baseline}},\qquad \widehat u_e=\widehat T_e/C_e
$$

Closed-edge delay is zeroed; costs/deltas are recomputed. Zero-capacity edges use zero utilization.
These exported quantities differ from raw independent output heads, so final synthetic scores
evaluate reconciled exports. Separate files retain per-seed raw-head diagnostics.

Surrogate stranded cargo is a nonnegative connector-volume residual against baseline served
demand, not a learned OD-feasibility guarantee. Its anomaly field is a zero placeholder. Exact
directional conservation and real-world route validity remain unproven; the flow solver remains
authoritative for scenario feasibility.

## 9. The price model

Implementation: [brent_model.py](scripts/price/brent_model.py) and
[backtest_price.py](scripts/price/backtest_price.py).

### 9.1 Forecast a correction to last week's price

Let $P_t$ be week $t$'s Brent average. The random-walk baseline predicts:

$$
\widehat P_t=P_{t-1}
$$

Prices often persist, so a useful model must improve on that benchmark. Ridge regression instead
learns a correction $\Delta P_t=P_t-P_{t-1}$ from four lagged inputs:

$$
x_t=\left[P_{t-1},\quad \tfrac14\sum_{j=1}^{4}P_{t-j}-P_{t-1},\quad
s_{t-1},\quad s_{t-1}-s_{t-2}\right]
$$

These are last price, its gap from the recent mean, last Suez share, and the latest known share
change. No target-week price/count enters those inputs. Standardization uses fitting weeks only:

$$
z_{t,j}=\frac{x_{t,j}-\overline x_{j,\mathrm{fit}}}{\max(\sigma_{j,\mathrm{fit}},10^{-8})}
$$

Prediction and coefficient fitting are:

$$
\widehat P_t=\max(0,P_{t-1}+\beta_0+z_t^\top\beta)
$$

$$
\min_{\beta_0,\beta}\sum_{t\in\mathrm{fit}}(\Delta P_t-\beta_0-z_t^\top\beta)^2
+10\sum_j\beta_j^2
$$

The penalty discourages large coefficients; the intercept is unpenalized. Strength 10 is fixed,
not chosen for the best test score. With an intercept column in $X$, the solution is:

$$
\widehat\beta=(X^\top X+10\operatorname{diag}(0,1,\ldots,1))^{-1}X^\top\Delta P
$$

Code solves this linear system rather than explicitly calculating the inverse.

### 9.2 Four models

| Model | Inputs beyond an intercept |
|---|---|
| Random walk | No fitted correction |
| AR(1) baseline | Lagged price only |
| Observed-network Ridge | Four inputs above |
| Network + GNN summaries | Those inputs plus eight embedding coordinates |

AR(1) means a first-order autoregression: price depends on its previous value. Regressing the
correction on lagged price is algebraically an AR(1) price equation before regularization/clipping.
The implementation uses the same Ridge framework for that baseline.

Optional summaries come from a separate real-only GNN trained before August 2022 and selected
using validation through December 2022. It is frozen before price calibration/testing. Its
weekly inputs are prior traffic observations; the GNN does not itself predict Brent.

### 9.3 Fit, calibration, test, and prediction bands

| Stage | Dates | Common weeks | Purpose |
|---|---|---:|---|
| Fit | Available common history through 2022-12-30 | 204 | Coefficients and scaling |
| Calibrate | 2023-01-06–2023-10-27 | 43 | Interval radius |
| Test | 2023-11-03–2024-06-28 | 35 | Frozen forecast assessment |

All models use identical weeks after joining embeddings. Calibration does not refit coefficients;
test outcomes do not select interval width. For calibration absolute errors $a_i=|P_i-\widehat P_i|$,
sort them and select:

$$
k=\min(n,\lceil0.90(n+1)\rceil),\qquad r=a_{(k)}
$$

$a_{(k)}$ is the $k$th smallest error. For $n=43$, $k=40$. Prediction bands are:

$$
[\max(0,\widehat P_t-r),\ \widehat P_t+r]
$$

Observed-network $r$ is about **$5.057/barrel**, giving **$10.115/barrel** mean test width. These
are calibrated residual bands, not the originally planned quantile-IRLS model. Serial dependence
and regime changes limit nominal coverage guarantees. A prediction interval concerns future
observations; a confidence interval for average improvement answers a different question.

To assess improvement uncertainty, the backtest resamples model and random-walk errors together
in circular **four-week blocks**, recomputes their RMSE difference, and repeats **2,000 times**
with a fixed seed. Circular blocks can wrap from last week to first. The 2.5th/97.5th percentiles
provide an approximate 95% interval, sensitive to the short sample and chosen block length.

### 9.4 Current-vintage scenario sensitivities

The separate scenario script fits through **2025-11-14**, reserving the latest 52 observed weeks,
**2025-11-21–2026-11-13**, for fresh residual calibration. Radius is **$12.671/barrel**. A new fit
does not reuse an old 2023 radius.

It anchors price to the latest observed weekly level and recent price-average gap. It sets share
to the scenario's implied share and share change to scenario minus baseline share, then reports:

$$
\Delta\widehat P_{\mathrm{scenario}}=\widehat P_{\mathrm{scenario}}-\widehat P_{\mathrm{baseline}}
$$

This is historical association extrapolated to assumed inputs, not a causal multi-week response.
Outside-historical-share flags identify some extrapolation. Freight/supply-risk decomposition
fields remain missing and `causal_decomposition_identified = False`. Negative associations are
retained rather than forced to expected positive signs. Even a Hormuz closure stranding crude
does not identify a Brent supply-risk premium. Residual bands do not cover uncertainty about
causation or unprecedented scenarios.

## 10. Errors, accuracy, and uncertainty

Let $y_i$ be actual value, $\widehat y_i$ the prediction, and $e_i=\widehat y_i-y_i$ the error.
Positive error means overprediction. There are $n$ scored observations.

### 10.1 MAE and RMSE

$$
\mathrm{MAE}=\frac1n\sum_i|e_i|,\qquad
\mathrm{RMSE}=\sqrt{\frac1n\sum_ie_i^2}
$$

MAE is the average mistake size; RMSE penalizes large misses more strongly. Units remain
transits/week or dollars/barrel. If two mistakes are 0 and 10, MAE is 5 while RMSE is about 7.07.

### 10.2 Percentage errors and informal closeness

$$
\mathrm{MAPE}=\frac{100}{|I|}\sum_{i\in I}\frac{|e_i|}{|y_i|}
$$

$I$ contains nonzero actuals. The GNN evaluator reports its denominator and excluded zero count;
the reported temporal event scores have no actual zeros. MAPE weights a 10% error on a small
corridor equally with a 10% error on a large one. Tiny actuals can inflate it. CSV MAPE fields
are fractions; this guide multiplies by 100 to display percentages.

For synthetic quantities with many zeros, weighted absolute percentage error is useful:

$$
\mathrm{WAPE}=100\frac{\sum_i|e_i|}{\sum_i|y_i|}
$$

It divides total absolute error by total actual magnitude. It is not the same weighting as MAPE.

An actual of 100 and prediction of 90 have a 10% absolute percentage error. The informal shorthand
"90% accurate" would mean:

$$
\mathrm{closeness}=100-\mathrm{MAPE}
$$

That is not classification accuracy, a probability of correctness, or a percentage of exact
predictions. It can become negative if MAPE exceeds 100%. Any closeness number here is explicitly
labeled and accompanied by percentage error.

### 10.3 Direction and baseline improvement

For forecasts, compare predicted/actual changes from the prior observation $y_i^{\mathrm{previous}}$:

$$
\mathrm{DA}=\frac{100}{n}\sum_i\mathbf1\{
\operatorname{sign}(\widehat y_i-y_i^{\mathrm{previous}})=
\operatorname{sign}(y_i-y_i^{\mathrm{previous}})\}
$$

The indicator is one for a sign match and zero otherwise. For traffic, the prior count belongs
to the same chokepoint. A repeat-last-week forecast predicts zero change, so it can have good
RMSE and almost zero directional accuracy when actuals usually change. That does not make it
a useless baseline.

The flow validator's event direction uses pre-event means and an additional small-change
tolerance. Its direction score is not the same task as the weekly forecast sign score.

$$
\mathrm{RMSE\ skill}=100\left(1-
\frac{\mathrm{RMSE}_{\mathrm{model}}}{\mathrm{RMSE}_{\mathrm{baseline}}}\right)
$$

Positive skill means lower error; negative means worse. Reducing RMSE from 25 to 20 is 20% skill,
not a statement that predictions are 20% or 80% accurate in absolute terms.

**Interval coverage** is the percentage of actuals inside the lower/upper bounds. **Width** is
average upper minus lower in dollars/barrel. Both matter: very wide bands can cover most actuals
without being informative. Coverage and mean-prediction accuracy measure different properties.

## 11. Detailed results and comparisons

### 11.1 GNN forecasts on real observations

These use three-seed averaged forecasts. MAE/RMSE units are tanker transits/week.

| Event | Model | MAE | RMSE | MAPE | Direction correct |
|---|---|---:|---:|---:|---:|
| Ever Given | Real-only GNN | 16.41 | 25.20 | 15.96% | 67.5% |
| Ever Given | Simulated + real GNN | 15.97 | 25.16 | 16.28% | 67.5% |
| Ever Given | Repeat last week | 20.85 | 26.90 | 18.55% | 0.0% |
| Ever Given | Year-ago/seasonal | 22.98 | 31.94 | 16.22% | 70.0% |
| Panama | Real-only GNN | 13.19 | 20.41 | 9.49% | 71.4% |
| Panama | Simulated + real GNN | 13.00 | 20.00 | 9.18% | 72.3% |
| Panama | Repeat last week | 15.84 | 24.60 | 11.51% | 1.4% |
| Panama | Year-ago/seasonal | 18.15 | 26.29 | 13.68% | 60.5% |
| Red Sea | Real-only GNN | 17.96 | 24.52 | 14.62% | 63.9% |
| Red Sea | Simulated + real GNN | 19.88 | 27.09 | 16.38% | 62.2% |
| Red Sea | Repeat last week | 16.02 | 23.11 | 12.82% | 2.2% |
| Red Sea | Year-ago/seasonal | 39.23 | 53.87 | 37.13% | 61.7% |

Mixed GNN RMSE skill over repeat-last-week is **+6.5%, +18.7%, −17.2%**, respectively.
Real-only skill is **+6.3%, +17.0%, −6.1%**.

Combining all **440 chokepoint-week pairs** gives:

| Mode | MAPE | Informal 100 − MAPE closeness | Direction correct |
|---|---:|---:|---:|
| Real-only | 12.18% | 87.82% | 67.95% |
| Simulated + real | 12.77% | 87.23% | 67.73% |

Panama contributes 220 pairs, Red Sea 180, and Ever Given 40. Aggregation therefore weights longer
windows more heavily; it is not an equal average of event scores or accuracy on all possible
crises. Mixed training helps selectively. Sustained regime change is a possible interpretation
of the Red Sea failure, not a demonstrated causal diagnosis.

### 11.2 GNN versus the flow model

The weekly scorecard includes a static `physics_conditional` estimate. It applies the solver's
scenario/baseline ratio to observed training-period means, then scores the same weekly targets:

| Event | Real-only GNN MAPE | Mixed GNN MAPE | Static conditional flow MAPE |
|---|---:|---:|---:|
| Ever Given | 15.96% | 16.28% | 34.14% |
| Panama | 9.49% | 9.18% | 18.19% |
| Red Sea | 14.62% | 16.38% | 19.09% |

The GNN has lower error here, but refreshes its inputs with last week's observations. The flow
estimate is static and conditional on a specified disruption. Windows include changing intensity
and pre/post-event weeks. This descriptive comparison does not prove superior GNN counterfactual
simulation.

The intended flow event-average validation uses:

$$
\widehat N_e^{\mathrm{crisis}}=N_e^{\mathrm{pre}}
\frac{T_e^{\mathrm{scenario}}}{T_e^{\mathrm{baseline}}}
$$

It compares implied versus observed crisis averages on all ten chokepoints:

| Event-average window | MAE, transits/week | RMSE | MAPE | Event direction match |
|---|---:|---:|---:|---:|
| Ever Given peak | 23.628 | 31.794 | 23.42% | 80% |
| Panama drought | 16.233 | 23.040 | 11.18% | 70% |
| Red Sea H2 | 22.160 | 30.115 | 17.72% | 80% |

These windows and averaged targets differ from individual weekly GNN observations. Averaging can
suppress noise. Comparing Panama's 11.18% event-average flow error directly with 9.18% weekly GNN
error would imply a comparability that the designs do not have.

### 11.3 GNN agreement with the simulator

There are **20 unseen compound scenarios × 279 edges = 5,580 pairs**. Targets are solver outputs.

| Reconciled exported target | MAE | RMSE | WAPE | RMSE skill over unchanged baseline |
|---|---:|---:|---:|---:|
| Oil volume, bbl/week | 500,036 | 2,113,152 | 17.87% | +10.3% |
| Crude-equivalent transits/week | 0.662 | 3.004 | 17.10% | +32.0% |
| Utilization ratio | 0.00886 | 0.10817 | 27.07% | −5.5% |
| Delay, days | 0.02022 | 0.26705 | 64.59% | +4.7% |

The unchanged baseline predicts no network effect. The surrogate improves transit imitation,
but utilization is worse and relative delay error remains high. Many tiny delays explain how
small delay MAE can coexist with large WAPE. Informal 82.9% transit closeness based on WAPE is
agreement with the simulator only, not 82.9% real-world accuracy.

### 11.4 Price results

All four models use the same 35 weeks. MAPE is calculated from saved held-out predictions.

| Model | MAE, $/bbl | RMSE, $/bbl | MAPE | Direction correct | RMSE skill vs random walk |
|---|---:|---:|---:|---:|---:|
| Random walk | 1.990 | 2.463 | 2.41% | 0.0% | 0.0% |
| AR(1) | 1.970 | 2.446 | 2.38% | 62.9% | +0.7% |
| Observed network | 1.895 | 2.400 | 2.30% | 60.0% | +2.6% |
| Network + GNN summaries | 1.907 | 2.425 | 2.32% | 60.0% | +1.6% |

Observed-network informal price-level closeness is **97.70%**; random walk already reaches
**97.59%**. Price persistence explains most of the apparent closeness. Direction is correct on
21/35 weeks. Nominal 90% bands cover 34/35 observations:

| Model | Interval coverage | Mean width, $/bbl |
|---|---:|---:|
| Random walk | 100.0% | 10.572 |
| AR(1) | 100.0% | 10.164 |
| Observed network | 97.1% | 10.115 |
| Network + GNN summaries | 97.1% | 9.803 |

One test observation changes coverage by about 2.86 percentage points. Above-90% coverage is
not by itself evidence of a better mean forecast.

The bootstrap interval for observed-network RMSE minus random-walk RMSE is
**[−$0.260, +$0.125]/barrel**. It includes zero: the measured advantage is not established as
statistically reliable. GNN summaries do not improve on the observed-network regression here.

### 11.5 Shipping-cost example

Fully closing Suez changes average modeled network voyage cost per served barrel by:

| Charter rate | Change |
|---|---:|
| $35,000/day | +$0.023/bbl |
| $50,000/day | +$0.046/bbl |
| $75,000/day | +$0.086/bbl |

These are global network averages. A particular Europe–Asia cargo can incur a larger detour
cost. They are not spot freight-rate forecasts, Brent effects, or the old corridor-cost example.

## 12. What can be concluded

Supported uses include explaining scenario routing under explicit assumptions, comparing the
two GNN training strategies on stated historical windows, measuring simulator approximation,
calculating transparent freight assumptions, and reporting a small uncertain price-backtest gain.

The important boundaries are:

1. **Limited observations:** ten chokepoints do not measure route volumes/delays everywhere.
   Held-out-chokepoint generalization and real-only counterfactual lane flows are not validated.
2. **Approximate routing:** static demand, statistical capacities, incomplete physical vessel
   restrictions/port queues, and coarse lanes limit realism.
3. **Simulator inheritance:** imitation can reproduce solver errors; mixed training has a
   retrospective prior rather than a clean historical information set.
4. **Few events:** only three historical windows, with correlated corridors/weeks; Red Sea
   remains a failure against persistence.
5. **Limited physical constraints:** zero flow and arithmetic consistency do not guarantee valid
   OD routing, directional conservation, or correct surrogate stranded demand.
6. **Small price advantage:** high level closeness largely reflects persistence; the 35-week
   RMSE gain is statistically uncertain.
7. **No causal identification:** inventories, global demand, supply decisions, conflict, and
   other forces can affect prices and shipping simultaneously. Freight pass-through and a
   closure's supply-risk premium are not isolated by this regression.
8. **Retrospective availability:** graph selection, revised observations, release lags, and later
   calibration limit live as-of-event forecasting claims.

A presentation-ready summary is:

> Blue Vector combines an interpretable crude-routing simulator with graph-based traffic
> prediction. Simulator pretraining improves weekly transit RMSE for Ever Given and Panama,
> but not Red Sea. The network price regression shows a small, statistically uncertain advantage
> over a random walk. Scenario freight costs are explicit model calculations; causal Brent
> impacts remain unestablished.

## 13. Reproduction and source files

### 13.1 Implementation map

| Purpose | Source |
|---|---|
| Data qualifications | [DATA.md](DATA.md) |
| Country reconciliation | [od_country_flows.py](scripts/od_matrix/od_country_flows.py) |
| Port totals and fitting | [od_port_shares.py](scripts/od_matrix/od_port_shares.py), [od_matrix.py](scripts/od_matrix/od_matrix.py) |
| Routing and scenarios | [flow_solver.py](scripts/flow_model/flow_solver.py), [scenario_engine.py](scripts/flow_model/scenario_engine.py) |
| Simulation generation | [gnn_sim_generator.py](scripts/flow_model/gnn_sim_generator.py) |
| GNN architecture/training | [gnn_model.py](scripts/gnn/gnn_model.py), [train_gnn.py](scripts/gnn/train_gnn.py) |
| GNN evaluation | [evaluate_gnn.py](scripts/gnn/evaluate_gnn.py) |
| Costs/indices | [shipping_cost.py](scripts/price/shipping_cost.py), [network_indices.py](scripts/price/network_indices.py) |
| Price model/backtest | [brent_model.py](scripts/price/brent_model.py), [backtest_price.py](scripts/price/backtest_price.py) |
| Dated integration details | [Deliverable 5 section 9](deliverables/DELIVERABLE5.md#9-integration-results--2026-11-22) |
| Environment and full run order | [Reproduction instructions](scripts/README.md#deliverable-5-reproduction) |

The full reproduction link specifies the tested Python/packages, ingestion order, demand rebuild,
scenario generation, training commands, and evaluation. The original `gnn_dataset.py` provides
a retrospective event-excluded bundle; chronological forecasting uses `train_gnn.py`.

### 13.2 Local audit artifacts

The following remain under ignored `data/processed/`:

| Artifact | Contents |
|---|---|
| `gnn_run_manifest.csv` | Dates/validation losses for 18 event-model fits |
| `gnn_temporal_predictions.parquet` | Per-seed forecasts and actuals |
| `gnn_ensemble_predictions.parquet` | Three-seed averaged forecasts |
| `gnn_evaluation_report.csv` | Event metrics and baselines |
| `gnn_evaluation_by_edge.csv`, `gnn_seed_evaluation.csv` | Corridor and seed detail |
| `gnn_synthetic_evaluation_report.csv` | Reconciled surrogate error on held-out compounds |
| `gnn_edge_predictions.parquet` | 22-column scenario export |
| `validation_report.csv` | Separate flow event-average validation |
| `scenario_shipping_costs.csv`, `scenario_price_features.parquet` | Scenario costs/features |
| `price_backtest_predictions.parquet`, `price_backtest_report.csv` | All test price predictions and scores |
| `price_interval_calibration.csv` | Historical backtest interval settings |
| `price_model_design.parquet` | Joined regression design, separate from source contracts |
| `scenario_price_calibration.csv`, `brent_scenario_impacts.parquet` | Current calibration and noncausal sensitivities |
| `issue5_run_provenance.json` | Package versions, seeds, input/output hashes |

Checkpoints/loss curves are retained locally. No data or credentials are committed. Revised
public sources can change rerun scores, so retain the input hashes and dates with a result.

### 13.3 Verification

Checks cover finite tensors, graph shapes, closed-edge zero flow, observed transit-label mapping,
annual/weekly units, OD marginal agreement, export identities, and common test pairs across
models/seeds. Future-observation perturbation checks confirm training/validation tensors and
scales do not change when future observations change. Price checks confirm target-week prices
and shares do not enter their own lagged features.

Cost checks cover route-segmentation invariance and consistent cost/index functions. Interval
checks cover nonnegative values and ordered lower/mean/upper bounds. These checks address
implementation errors; they do not eliminate the scientific limitations above.

## 14. Glossary

| Term | Meaning |
|---|---|
| Baseline | Reference network or simple forecast; specify which |
| Calibration | Setting parameters or uncertainty bands on designated data |
| Chokepoint | Narrow or strategically important shipping passage |
| Counterfactual | Assumed alternative world, such as a canal being closed |
| Embedding | Compact vector summarizing learned network states |
| Epoch | One pass through training data |
| Feature | Model input number |
| Fine-tuning | Further training pretrained weights on another dataset |
| Graph | Nodes and connecting edges |
| Holdout/test set | Observations reserved for frozen-model assessment |
| Label/target | Training answer or evaluation truth |
| Leakage | Using target/future information in learning or selection |
| Mask | Indicator selecting which labels count in loss |
| Normalization | Rescaling magnitudes for learning |
| Persistence/random walk | Predicting the previous observed value again |
| Pretraining | Initial learning before adaptation to the final task |
| Regression | Predicting numerical quantities rather than categories |
| Residual | Actual minus predicted value; error can use the opposite convention |
| Scenario surrogate | Learned approximation to a simulator |
| Stranded demand | Modeled cargo with no available route |
| Throughput | Quantity moving through a passage per time period |
| Validation | Data used to select checkpoints/settings before testing |


## 15. Methodology audit and Hormuz readiness

**Audit date: November 22, 2026. Decision: useful research test, but not ready to claim effective,
physically reliable closure results from all three model families.** No architecture, coefficients,
training data or published benchmark scorecards were changed by this audit. Checks against the
previous run-provenance hashes found no changes in its recorded input/output files.

### 15.1 What was checked and what passed

The audit checked the routing, scenario, GNN training/evaluation, cost/index, price and validation
implementations against the Markdown descriptions and saved outputs. It also inspected the existing
dashboard and source ingestion behavior. It did not independently re-verify every tariff, license,
team-permission claim, or historical dataset-vetting statement in the source register. Those dated
checks and unresolved `TBD` fields must retain their qualifications.

Verified properties include the stated graph size, current OD total, unit conversions, current
metric tables, chronological GNN splits, forecast lagging, residual-interval method, and cost
aggregation formulas. The raw downloaded chokepoint table contains **zero duplicate port-days and
zero missing tanker counts**. The tracked graph-check artifact has **32 PASS and one WARN**, not
33 unconditional passes. These checks establish implementation properties, not universal accuracy.

The weekly attribute builder generally accepts five or more observations and fills missing numeric
counts upstream. The temporal trainer and observed price-share path explicitly require seven-day
weeks. Thus seven-day completeness is a property of those modeling paths and this checked input,
not a guarantee that every upstream table has seven valid days.

### 15.2 Material methodology findings

| Finding | Evidence | Implication |
|---|---|---|
| Scenario-surrogate cargo feasibility fails on Hormuz | Zero masked Hormuz flow coexists with nearly unchanged predicted connector shipments | Do not use its stranded-demand or downstream flow outputs as physically valid closure results. |
| Closure zeros are enforced | `gnn_model.py` masks closed-edge volume/transits | Getting zero at Hormuz is an implementation constraint, not evidence the network learned the crisis. |
| Single-edge Hormuz closure is already seen | `sim_E003_th_000` is in all three saved pretraining sets | Running this input again is a stress/regression test, not unseen-scenario validation. |
| Price scenario lacks direct supply-shock variables | Current scenario regression uses lagged-price anchors and Suez-share features | Its small Hormuz sensitivity does not measure a market supply premium. Optional GNN-summary forecasting can contain indirect traffic information, but still does not identify causality. |
| Current scenario intervals include the 2026 crisis | Calibration spans 2025-11-21–2026-11-13 | Reusing these bands for a supposedly untouched 2026 test contaminates that assessment. |
| Flow settings/demand are inherited, static inputs | Existing OD/capacity/cargo parameters are reused | The full-closure result is conditional on this modeled network, not a 2026 world-supply forecast. |
| Physical bypass and market responses are missing | No scenario-dependent pipeline export reallocation, inventories, production substitution or demand response | A persistent real crisis cannot be represented solely by closing the sea edge. |
| Independent historical validation is overstated in older notes | Parameter-retention discussion considered H2/Ever Given results; the GFW-named checker has no GFW voyage observations | Treat historical comparisons as retrospective. The grid's H1 objective alone does not establish that all later research decisions ignored test results. |

Additional qualifications: the primary cargo proxy is estimated carrying-capacity tonnes × 7.33,
not observed loaded crude or full vessel-dimension enforcement. Throttled edges are removed between
loading passes, so a pass can overshoot the requested ceiling. The current baseline Panama
utilization is **3.2666** with **5.8731 modeled delay days**; that exposes assumptions, not an
observed real queue.

The corridor-delay checker constructs an undirected route graph without the main solver's
split-port restriction. It can therefore use paths the solver forbids. It is a published-benchmark
geometry diagnostic, not independent ship-level or assigned-OD voyage-delay validation. Earlier
corridor values were not freshly reproduced in this audit.

The EIA chokepoint ingestion script checks a web page but returns its embedded reference tables;
`--refresh` does not turn those constants into newly scraped 2026 observations. Use the adopted
PortWatch counts and EIA Brent series for a new crisis test, with their actual vintages.

### 15.3 Full-closure smoke test: what the existing models actually produce

The isolated probe called the existing scenario engine with `ormuz` retained throughput of
1.0, 0.75, 0.50, 0.25 and 0.0. It reconstructed the existing simulator GNN inputs and averaged
seeds 7/19/43. The price column applies the current scenario regression's own Suez-share mapping.
These are diagnostic outputs, **not a scored historical 2026 backtest**.

| Retained fraction | Flow stranded, million bbl/week | Flow stranded, % of modeled demand | Flow Hormuz transits/week | GNN Hormuz transits/week | GNN connector-residual stranded, million bbl/week | Conditional Brent change vs open |
|---|---:|---:|---:|---:|---:|---:|
| 1.00 | 0.000 | 0.00% | 87.170 | 86.475 | 0.204 | $0.000 |
| 0.75 | 6.149 | 4.25% | 69.736 | 66.224 | 0.278 | +$0.010 |
| 0.50 | 15.373 | 10.61% | 43.585 | 65.104 | 0.283 | +$0.028 |
| 0.25 | 21.522 | 14.86% | 26.151 | 64.037 | 0.288 | +$0.043 |
| 0.00 | 30.746 | 21.23% | 0.000 | 0.000 | 0.293 | +$0.070 |

The flow denominator is **144.827 million bbl/week of selected-network demand**. Full closure's
30.746 million bbl/week equals about **4.39 million bbl/day** within this network; 21.23% must
not be called a percentage of world oil supply. No sea alternative is represented for the Gulf
origins behind Hormuz; the model's fixed origin allocation strands their shipments.

At full closure the GNN still predicts approximately **144.533 million bbl/week of served connector
shipments**, almost the full 144.827 million demand, versus the flow model's roughly 114.081 million
served barrels. Its 0.293 million stranded residual is only about **0.20%** of network demand.
This is a severe failed feasibility test, including a case used in pretraining. At partial closure
its Hormuz predictions also respond too weakly. It is more informative than a global average
surrogate error dominated by unchanged or lightly affected edges.

The +$0.070 regression sensitivity is merely an indirect Suez-share association. It is **not a
credible estimate of the price impact** of removing a major export passage. A small number with
a wide residual interval is not evidence that the actual supply shock has little price effect.

Raw probe outputs remain ignored under `data/processed/hormuz_audit_smoke.csv` and
`hormuz_audit_smoke_flows.parquet`. No headline benchmark results were overwritten.

### 15.4 What is known about the 2026 event and local coverage

The UN's September 2026 assessment dates the conflict's onset to **February 28, 2026** and describes
severe Hormuz traffic restrictions, partial recovery and renewed disruption. It does not describe
one uniform zero-traffic state throughout the year. See the [UN September assessment, Box 1](https://desapublications.un.org/file/22656/download).

The IEA's [September 18 commentary](https://www.iea.org/commentaries/oil-markets-strain-to-plug-the-gap-left-by-middle-east-supply-shortfall)
describes bypass exports, inventories, non-Gulf output and demand adjustments. These responses
are absent from the static sea-routing model. The [EIA September outlook](https://www.eia.gov/outlooks/steo/archives/Sep26.pdf)
also treats export restrictions, production shut-ins and alternative routes separately. Those
reports supply event context here; they have not been adopted as new model-input datasets.

Local inputs already extend far enough for a retrospective study:

- Complete seven-day PortWatch modeling weeks: **2019-01-11–2026-11-13**.
- EIA weekly Brent table: **2019-01-04–2026-11-20**. Its last week does not create a matching
  PortWatch week; a joint evaluation must use the common complete observations.

Illustrative descriptions of the local Hormuz tanker series, **not validation metrics**, are:

| Observation bin | Full weeks | Mean tanker transits/week |
|---|---:|---:|
| January–February 2026, through February 27 | 9 | 257.33 |
| March–May 2026 | 13 | 15.38 |
| June–July 2026 | 9 | 34.44 |
| August–November 13, 2026 | 15 | 8.40 |

All-tanker counts are different from the all-ship counts and oil-volume estimates in external
reports. AIS coverage and later revisions also require caution; these are not exact crude exports.

### 15.5 A defensible next test, with decisions still to confirm

Two experiments should be reported separately:

1. **Conditional stress test:** fixed modeled demand under open/partial/full Hormuz restrictions.
   Report served/stranded barrels, affected origins, network routing/costs and feasibility failures.
   Label the absence of pipeline reallocations. Treat full closure as an endpoint scenario rather
   than equating it with every week of the observed crisis.
2. **Historical forecasting test:** freeze all training, normalization, checkpoint selection and
   price-interval calibration before the chosen crisis boundary. Test traffic and prices on common
   later observation weeks using only permitted lagged inputs. Compare persistence/seasonal traffic
   and random-walk/AR(1) prices, and report onset, partial recovery and sustained restriction
   separately. Do not derive a week's scenario throttle from that same week's target traffic and
   then claim an independent forecast of it.

A reasonable **proposed**, not yet adopted, boundary is February 28, with the first full affected
Friday-ending week March 6 and current joint endpoint November 13. A pre-event price fit/calibration
split must reserve only earlier observations; the existing current-vintage 52-week scenario
calibration cannot be reused. Existing pre-2023 temporal GNN checkpoints and the frozen historical
price benchmark can be assessed as frozen old models, or a pre-2026 refresh can be chosen before
scoring. Those are materially different protocols and should be confirmed before a headline test.

To claim all three model families work effectively on closure scenarios, additional work is needed:

- Repair or constrain surrogate cargo feasibility, validate per-scenario/affected-corridor errors,
  and rerun all benchmarks after any architectural change. Closed-edge masks alone are insufficient.
- If market supply-risk pricing is the objective, confirm an appropriate price design with direct
  observed Hormuz/supply indicators and pre-event-only fitting. More features can still fail; they
  do not automatically identify causality or guarantee accuracy.
- If real export adaptation is an objective, confirm the scope and data for pipeline/export-port
  reallocations. Do not silently interpret present static stranding as net world supply loss.

### 15.6 Readiness by model

| Model/use | Ready now? | Defensible output |
|---|---|---|
| Flow model, hypothetical closure | Yes, as a clearly limited conditional stress test | Network-specific routing, served/stranded demand and assumption-based costs |
| GNN, weekly observed traffic | Ready for a carefully frozen diagnostic evaluation, not a promised good score | Counts versus persistence/seasonal baselines on actual crisis weeks |
| GNN, closure scenario surrogate | No, not physically reliable in the tested Hormuz case | A documented failed stress test until feasibility is repaired |
| Price, frozen weekly forecasts | Can be evaluated with strictly pre-event calibration | Observed price errors, baseline skill, coverage and width |
| Price, causal Hormuz premium | No | Current model does not identify that quantity |
| Dashboard, integrated three-model demo | No | Current UI exposes flow outputs; GNN/price integration and parameter consistency are incomplete |

The evidence is sufficient to run an honest research evaluation and learn from failures. It is
not sufficient to promise decent 2026 errors or present all three models as a validated causal
crisis-analysis chain.
