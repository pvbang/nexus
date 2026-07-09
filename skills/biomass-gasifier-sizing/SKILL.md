---
name: biomass-gasifier-sizing
description: Quantitative sizing of a downdraft biomass gasifier for small-scale power (rice husk / wood chips, ~5-50 kWe). Use for reactor throat/hearth diameters, hearth & throat load, air/fuel & equivalence ratio (ER), syngas LHV, cold-gas efficiency (CGE), fuel-rate ↔ electric-power chain, and sanity ranges. Triggers on "gasifier design", "khí hóa trấu/sinh khối", "throat diameter", "hearth load", "syngas LHV/ER/CGE", "biomass to electricity sizing".
license: MIT
---

# Biomass Downdraft Gasifier Sizing (small-scale power)

Reference correlations for sizing an Imbert/downdraft gasifier and its power
chain. Use these to DERIVE numbers and to SANITY-CHECK any figure before trusting
it. Sources: Reed & Das, *Handbook of Biomass Downdraft Gasifier Engine Systems*
(SERI/1988); FAO Forestry Paper 72 (1986). All numbers below are order-of-magnitude
design ranges — cite the correlation you use and show the arithmetic.

## Power chain (work BACKWARD from the electric target)

```
P_electric = m_fuel · LHV_fuel · η_gasif(CGE) · η_engine · η_generator
```
Typical small-scale efficiencies (use, and state, your chosen values):
- CGE (cold-gas efficiency) η_gasif : 0.60–0.80  (rice husk trends LOW: high ash/silica)
- η_engine (syngas ICE, shaft)      : 0.25–0.32
- η_generator                       : 0.90–0.95
- Overall biomass→electric          : ~0.12–0.20 (so ~20 kWe needs ~100–170 kW_th fuel)

Rice husk LHV_fuel (dry) ≈ 13–15 MJ/kg (HHV ~15–16); as-received lower with moisture.
=> Fuel rate for 20 kWe is typically ~20–35 kg/h rice husk. If your model says far
   outside this, RE-CHECK before proceeding.

## Syngas properties (downdraft air-blown) — sanity ranges
- LHV_syngas : 4.5–6.0 MJ/Nm³   (below 4.5 = too much N2/incomplete; above 6 = suspect)
- ER (equivalence ratio, actual/stoichiometric air) : 0.25–0.35 (design ~0.3)
- Dry syngas vol%: CO 18–22, H2 12–18, CH4 1–3, CO2 10–14, N2 45–55
- Specific gas production ~2.0–2.5 Nm³ syngas per kg dry biomass

## Reactor sizing — the two loads that set the two diameters

**THROAT (the narrowest section) is sized by HEARTH LOAD B_h**, defined as the
gas throughput per unit throat area:
```
B_h = Q_syngas / A_throat      [Nm³ syngas / (h · cm²) ]   typical 0.3–0.9 (design ~0.7)
A_throat = Q_syngas / B_h  ;  d_throat = sqrt(4·A_throat/π)
```
For a 20–30 kWe unit this yields **d_throat ≈ 80–120 mm** (NOT hundreds of mm).
A throat of ~250–500 mm implies a 150–250 kWe machine — an order-of-magnitude error.

**The REACTOR BODY / fuel bed is sized by the Specific Gasification Rate SGR**,
the FUEL mass throughput per unit body cross-section:
```
SGR = m_fuel / A_body           [ kg biomass / (h · m²) ]  typical 100–300
A_body = m_fuel / SGR  ;  d_body = sqrt(4·A_body/π)
```
This gives the larger shell diameter (a few hundred mm).

⚠️ **The #1 mistake**: mixing these up — using SGR (a body correlation) to size the
throat, or taking `max(d_throat_bySGR, d_throat_byBh)`. They are DIFFERENT
quantities for DIFFERENT sections. Throat ← B_h. Body ← SGR. Keep them separate;
never `max()` them into one "throat".

Hearth/throat geometry (Imbert): hearth (widest of throat zone) d_hearth ≈ 1.5–2.5×
d_throat; nozzle plane above throat; N air nozzles (typically 5) of Ø ~8–12 mm sized
so nozzle exit velocity ≈ 30–35 m/s at design air flow.

## Materials & temperature (Vietnam-manufacturable)
- Oxidation/throat zone hits ~1000–1100 °C → liner in SS310S / SS309 (or castable
  refractory); shell CT3/Q235 with ceramic-fibre insulation. Rice-husk ash fusion
  ~1000 °C → keep throat T below ash-fusion to avoid slagging.

## Self-check checklist (run these before reporting a design)
1. Does the power chain close? P_elec back-calculated = target ±5%?
2. d_throat in 80–120 mm for ~20 kWe? If not, the throat correlation is wrong.
3. Throat sized by B_h, body by SGR — not swapped, not max()'d?
4. LHV 4.5–6, ER 0.25–0.35, CGE 0.6–0.8 — all in range and mutually consistent?
5. Every number appears with ONE value across all files (design basis = CAD = report)?
6. Energy balance uses an INDEPENDENT forward computation, not a rearrangement that
   is algebraically forced to close (tautology).
