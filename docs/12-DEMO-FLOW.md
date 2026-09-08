# 12. Demo Flow

**Audience:** Sales engineer, PM, hackathon presenter, investor-pitch driver
**Duration:** 12 minutes for the full 17 steps · 6 minutes for the short version
**Prerequisite:** `make demo-reset` — restores the seeded demo pack ([16-SAMPLE-DATA.md](16-SAMPLE-DATA.md))
to a known state in ~20 seconds.

---

## 12.1 The narrative

The demo tells one story, not a feature tour:

> *Ram Bahadur Tamang wants NPR 32 lakh to buy an electric taxi. Under today's process, a bank looks
> at his salary slip and his CIB score and guesses. We are going to look at the road he will actually
> drive, the economics of the vehicle on that road, and his ability to pay — score all three,
> structure the loan properly, and then watch the vehicle every night so that when things go wrong we
> know in week two, not month three.*

Three acts: **Act I — the route nobody scores.** **Act II — the decision that explains itself.**
**Act III — the failure we see coming.**

---

## 12.2 Setup

| Item | Value |
|------|-------|
| URL | `https://demo.evrca.local` (or `http://localhost:3000`) |
| Reset | `make demo-reset` — truncates transactional data, reseeds, sets "today" so that alert conditions are ripe |
| Screen | 1440×900, browser zoom 100%, notifications off, a second tab pre-opened at the alert queue |
| Accounts | `sabina.karki@bank.com.np` (Risk Manager) · `ramesh.adhikari@bank.com.np` (Credit Officer) · `kiran.shrestha@bank.com.np` (Portfolio Manager) — password `Demo@2026!Ev` |
| Fallback | If anything fails, every screen has a seeded equivalent already populated; switch to `LA-2026-000101` which is pre-assessed |

**Pre-demo checklist**
- [ ] `make demo-reset` run within the last hour
- [ ] `/health/ready` returns ok with all three active configurations present
- [ ] Loan `LN-2026-000087` shows a RED alert in the queue
- [ ] The route `Kathmandu–Dhulikhel` exists and is **not** yet assessed today (so step 4 is live)
- [ ] Applicant `Ram Bahadur Tamang` exists with financials but **no** application
- [ ] Browser logged out

---

## Act I — The route nobody scores (steps 1–5, ~3 min)

### Step 1 — Login

**Do:** log in as **Ramesh Adhikari (Credit Officer)**.
**Say:** *"This is a credit officer at a branch. Six roles, strict permissions, everything audited."*
**Show:** the split-screen login, then the role-appropriate sidebar.
**Watch for:** the sidebar has no Admin item — permissions are visibly real.

### Step 2 — Dashboard

**Do:** land on `/dashboard`. Pause on the KPI row.
**Say:** *"Fifteen applications, ten active loans, NPR 2.1 crore outstanding — and two red alerts we'll
come back to."*
**Show:** the ten KPI cards, the portfolio-by-grade donut, the "Attention Required" table.
**Watch for:** the "as of" caption — we are honest about data freshness.

### Step 3 — Open the route

**Do:** Routes → note the list shows ten corridors with scores and classes → open **Kathmandu–Dhulikhel**.
**Say:** *"Here is the thing no lender scores today. This is a corridor, not a customer. We assess it
once, and every application that runs on it reuses the assessment."*
**Show:** the route detail — 30 km, pitched road, 6 charging stations of which 3 are DC fast, 8 trips
a day, 1,200 daily passengers, 6 monsoon disruption days.
**Contrast (optional, 15s):** open **Kathmandu–Jiri** in the list — 187 km, 1 charger, gravel,
45 monsoon days, Class C. *"Same vehicle, same borrower, completely different loan."*

### Step 4 — Run the assessment

**Do:** click **Assess** → the modal shows the completeness checklist all green → select the
reference vehicle **BYD e6 (380 km)** → **Run Assessment**.
**Say:** *"Five weighted components. Every weight is set by the Risk Manager, not by an engineer."*
**Show:** the progress modal ticking through Charging → Road → Demand → Revenue → Risk (~1.5s).

### Step 5 — The route score (**hero moment 1**)

**Show, in this order:**
1. The gauge: **90.12 / 100 · Class A · Low Risk**.
2. The recommendation card: Eligible · Charging **Adequate** · Revenue potential **High** ·
   estimated monthly profit **NPR 1,36,935**.
3. The composition bars — then **expand Charging Infrastructure** to reveal the three sub-factors:
   density 2.0 stations per 10 km, maximum gap 12 km against 140 km of usable range (8.6%), 50% fast
   chargers.
4. The charging strip at the bottom, with the stations plotted along the corridor and the widest gap
   bracketed.

**Say:** *"Ninety-point-one-two, and here is exactly why. Not a black box — every component, every
sub-factor, every input, and a written explanation an officer can read to a credit committee. The
weights are configuration; I'll show you an administrator changing them in a moment."*

**Watch for:** the audience realising the number is *auditable*. That is the whole pitch.

---

## Act II — The decision that explains itself (steps 6–12, ~5 min)

### Step 6 — Select the applicant

**Do:** Customers → search "Ram" → open **Ram Bahadur Tamang**.
**Say:** *"Owner-driver, 34, six years of commercial driving, business revenue of NPR 1.45 lakh a
month, one existing loan."*
**Show:** the profile summary rail — total income, disposable income, existing EMI, DTI.

### Step 7 — Financial information

**Do:** open the **Financial Information** tab.
**Say:** *"Everything is versioned. If we amend his income next month, the decision we make today
still points at the version we actually used."*
**Show:** the live calculation panel: disposable income **NPR 72,600**, and the line "A proposed EMI
up to NPR 67,350 keeps FOIR within the 55% policy limit." — *plant this number; it pays off in step 11.*

### Step 8 — Create the application

**Do:** **New Application** → step 1 Ram (pre-selected) → step 2 confirm financials → step 3 vehicle
**BYD e6**, on-road NPR 46 lakh → step 4 route **Kathmandu–Dhulikhel** (shows *Class A, assessed
today*) → step 5 requested **NPR 32,00,000**, 60 months, 13%, down payment **NPR 14,00,000** (30.4%),
240 km/day, 26 days → step 6 review.
**Say:** *"Notice the vehicle economics appearing the moment we pick the model — energy cost per
kilometre of NPR 2.45 against roughly NPR 8 for a diesel equivalent."*
**Time-saver:** the wizard is pre-filled from the seed if you use **[Load demo application]**.

### Step 9 — Run the full assessment

**Do:** **Run Full Assessment**.
**Show:** the pipeline modal: Knock-out checks ✓ → Route assessment ✓ (reused) → Customer scoring ✓
→ Vehicle economics ✓ → Combined risk ✓ → Structuring ✓.
**Say:** *"Knock-outs run first. If he were blacklisted or in default, we would stop right here and
tell him why — no scoring, no ambiguity."*

### Step 10 — The customer score

**Do:** on the result screen, click **[View credit assessment]**.
**Show:** bureau score **742**, the 24-month repayment strip (mostly green, one amber), the knock-out
checklist all passing, then the six-component breakdown: **75.17 / Grade B**.
**Say:** *"Strong credit history at 85.8, strong vehicle economics at 90.96 — but existing debt scores
only 61.7. Hold that thought."*
**Do:** navigate back to the result.

### Step 11 — The combined decision (**hero moment 2**)

**Show, in this order:**
1. The banner: **MANUAL REVIEW · Final Risk Score 84.5 / 100 · Grade B**.
2. The three contribution cards: Route 90.12 × 40% = 36.05 · Customer 75.17 × 40% = 30.07 ·
   Vehicle economics 90.96 × 20% = 18.19.
3. **The reasons list** — negatives first: *post-loan FOIR of 58.8% exceeds the 50% approval
   threshold*; *disposable income barely covers the requested EMI*. Then the positives: *the vehicle
   generates 2.31× the EMI*; *Class A route with adequate charging*; *clean bureau record*;
   *30.4% down payment*.
4. The requested-vs-recommended table: requested NPR 32,00,000 → **recommended NPR 29,60,000**,
   EMI NPR 72,810 → **NPR 67,349**, LTV 69.57% → 64.35%, DSCR 2.31 → 2.49.

**Say:** *"This is the sentence that matters: a Class A route and a very strong asset do **not**
silently override a stretched borrower. The system says manual review, names the single gate that
failed, and tells you what would fix it. Eighty-four out of a hundred, and it still won't
auto-approve — because the FOIR gate is 50% and he is at 58.77%."*

**Then, the what-if:** drag the **amount** slider down to NPR 28,00,000.
**Show:** EMI, FOIR (falls to ~52%), DSCR (rises to ~2.6) and LTV all recompute live.
**Say:** *"The officer can find the structure that works before committing to anything."*

### Step 12 — Record the decision

**Do:** log out, log in as **Sabina Karki (Risk Manager)** → open the same application →
**[Approve…]** → set amount **NPR 29,60,000**, add the condition *"Comprehensive insurance assigned
to the bank for the full tenure"* → the justification box is required because this is an override →
type *"FOIR brought within policy by reducing exposure; DSCR 2.5 and Class A route support the
structure"* → **Confirm Decision**.
**Say:** *"Different role, different powers. And because she is departing from the system
recommendation, the justification is mandatory and permanently audited. Override rate is a KPI on
her own dashboard."*
**Show (5s):** Admin → Audit Logs, filtered to `DECISION_OVERRIDE`, showing the entry with
before/after.

**Do:** **[Create Loan]** → disbursement date, first EMI date → confirm.
**Show:** the generated 60-instalment amortisation schedule; scroll to the last row — closing balance
**0.00** exactly.

---

## Act III — The failure we see coming (steps 13–17, ~4 min)

### Step 13 — Portfolio dashboard

**Do:** log in as **Kiran Shrestha (Portfolio Manager)** → **Portfolio**.
**Say:** *"Ten active loans. Everything green except two."*
**Show:** the risk table sorted by DPD, with the usage-change column and its sparklines.

### Step 14 — The repayment story

**Do:** open loan **LN-2026-000087** (a *different*, seeded borrower — Manisha Bhandari, a Tata
Nexon EV Max on the Bhaktapur–Banepa corridor) → **Repayment Schedule**.
**Show:** instalments 1–3 paid on time, instalment 4 partial, instalments 5–6 overdue. DPD **47**.
**Say:** *"Under the old process, this is where the story starts — a bounced EMI in month five. Watch
what our data actually knew."*

### Step 15 — The signal that came first (**hero moment 3**)

**Do:** open the **Behaviour** tab.
**Show:** the combined timeline — the daily-kilometres line falling from a 150 km baseline to
around 90 km **starting three weeks before** the first missed payment, with the EMI event markers
plotted on the same axis. Then the **Monitoring** tab: charging sessions down 33%, active days down
to 19 of 30, route deviation rising.

**Say:** *"The vehicle told us in week two. Kilometres fell 38% against baseline, charging sessions
fell a third, and the vehicle stopped running the financed route on some days. Repayment-only
monitoring found out six weeks later. **That gap is the product.**"*

### Step 16 — The alert

**Do:** **Alerts** → the queue is sorted severity-first → open **AL-2026-000451** (RED,
`EMI_OVERDUE_30_PLUS`).
**Show:**
- The trigger evidence: *Days past due = 47 (threshold > 30)*, with the metric table.
- The supporting context charts — the same usage decline, right there in the alert.
- The **recommended action** playbook from the rule.
- The SLA countdown and the assignment control.

**Say:** *"Every alert carries the rule that fired, the value that fired it, the threshold it
breached, and what to do about it. And notice there is exactly one alert — the YELLOW that fired at
day 5 was automatically superseded when this crossed thirty days, with the link preserved."*

**Optional (20s), if the audience is technical:** Admin → Risk Rules → open `USAGE_DROP_MODERATE` →
show the condition builder rendering *"Alert when usage change is at most −20% AND greater than −35%
AND active days in 30 days is at least 5"* in plain language. *"A risk manager writes rules. Not an
engineer, and not a release."*

### Step 17 — Work it and resolve

**Do:** **Acknowledge** → add a `CALL_LOG` activity: *"Spoke to borrower. Vehicle off-road since 18
Aug for motor controller replacement; part now fitted. Promised full clearance by 15 Sep."* →
assign to a Field Officer → then **Resolve** with code `PAYMENT_RECEIVED` and notes.
**Show:** the alert closes, the activity timeline retains the whole history, the loan's risk status
returns to GREEN, and the portfolio KPI updates.
**Say:** *"Contacted in week two instead of month three. That is the difference between a cure and a
write-off — and every step of it is on the audit trail."*

---

## 12.3 Closing (30 seconds)

Return to the main dashboard.

> *"Three modules, one decision. A route that gets scored instead of assumed. A borrower assessed
> against the economics of the asset on that route. And a portfolio that tells you it is failing
> while you can still do something about it. Every weight, every threshold and every alert rule is
> owned by the risk team, versioned, and auditable — which means this platform can be tuned to your
> credit policy in an afternoon rather than a release cycle."*

---

## 12.4 Short version (6 minutes)

Steps **1 → 3 → 4 → 5 → 8 (pre-filled) → 9 → 11 → 15 → 16**. Drop the login switches by pre-opening
three browser profiles, and drop the what-if sliders.

---

## 12.5 Audience-specific emphasis

| Audience | Spend time on | Skip |
|----------|---------------|------|
| **Investor / hackathon panel** | Steps 5, 11, 15 — the three hero moments and the "gap is the product" line | Configuration screens, audit detail |
| **CTO / architect** | The configuration versioning (step 12 audit), the rule builder (step 16 optional), `/api/v1/docs`, the health endpoint | The narrative framing |
| **Chief Risk Officer** | Step 11's reason list, the override justification, the audit entry, the FOIR gate holding against a high score | The wizard mechanics |
| **Credit officers (training)** | Steps 6–12 in full, slowly, with them driving | Act III |
| **Portfolio / collections** | Act III in full, especially step 15 | Act I |

---

## 12.6 Anticipated questions

| Question | Answer |
|----------|--------|
| "Where do the weights come from?" | Expert judgement, documented, and deliberately configurable. Every score component is persisted so the book can be statistically re-fitted once ~300 loans mature. We are explicit that this is an expert scorecard, not a fitted model — and we show the calibration governance in [07-SCORING-ENGINE.md](07-SCORING-ENGINE.md) §7.7. |
| "Is the CIB integration real?" | Not in this demo — it is a mock adapter behind the same interface the live provider will implement. Switching is one environment variable. The commercial contract is a business decision, not an engineering one. |
| "Where does telematics come from?" | An OEM cloud feed, an aftermarket OBD device, or a fleet app — the adapter takes daily aggregates. In the demo it is a mock generator. If a lender has no telematics, the platform still works; usage rules are disabled by configuration and the loan is flagged as degraded rather than silently scored as healthy. |
| "What if a borrower unplugs the tracker?" | Silence is itself an alert — `TELEMETRY_SILENT_2D` and `TELEMETRY_SILENT_5D`. We never read missing data as good news. |
| "Can an officer game the score?" | Inputs are audited and documents are attached. Overrides require a justification and are counted as a KPI. Segregation of duties prevents the creator from approving. |
| "Does this replace our core banking system?" | No. It recommends and monitors. Disbursement, the general ledger and payment rails stay where they are. Integration is a Phase 2 item. |
| "How long to deploy for us?" | Fourteen weeks for the MVP with a four-person team. Configuration to your credit policy is days, not weeks, because it is data. |
| "What happens with a Class C route?" | It is a knock-out by default, overridable only by a Risk Manager waiver with a justification. That is a configuration choice — a lender who wants to price Class C rather than decline it can turn the knock-out off and let the score and LTV caps do the work. |

---

## 12.7 Demo failure recovery

| Failure | Recovery |
|---------|----------|
| Assessment returns an error | Switch to the pre-assessed `LA-2026-000101`; say "we have one prepared" and continue |
| Slow response | Keep talking through the *why* — the pipeline modal is designed to make the wait informative |
| No alerts in the queue | Run `make demo-alerts` (forces a rule evaluation for the demo date) — 5 seconds |
| Login fails | Second browser profile is pre-authenticated as the Risk Manager |
| Total outage | A recorded 6-minute screen capture of the full flow is kept at `docs/demo/evrca-demo.mp4` |
| Someone asks for a feature that does not exist | "That is on the roadmap — it is item X in [13-FUTURE-ENHANCEMENTS.md](13-FUTURE-ENHANCEMENTS.md). The MVP scope was deliberately frozen so we could ship in fourteen weeks." |
