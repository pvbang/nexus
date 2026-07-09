"""Group B/C (audit f188a4c4): the number-integrity safeguards must recognize the
SI units this domain uses (meters, m/s, dB, Hz...) WITHOUT matching units inside
ordinary words ("3 models" must not yield 3 m)."""
from __future__ import annotations

from app.orchestration.artifact_reexecutor import extract_headline_numbers
from app.orchestration.numeric_integrity import extract_prose_facts


# ── re-exec headline extraction (artifact_reexecutor._HEADLINE_UNIT) ──────────

def test_meters_extracted():
    assert extract_headline_numbers("R_max = 421.26 m") == {421.26}
    assert extract_headline_numbers("range 1000 m at SNR") == {1000.0}


def test_engineering_units_extracted():
    assert 20.0 in extract_headline_numbers("speed 20 m/s cruise")
    assert 13.0 in extract_headline_numbers("SNR_min = 13 dB")
    assert 9.5 in extract_headline_numbers("f = 9.5 GHz X-band")
    assert 1.0 in extract_headline_numbers("B = 1 MHz bandwidth")
    assert 1.0 in extract_headline_numbers("baseline 1 km coverage")


def test_existing_units_still_work():
    assert extract_headline_numbers("efficiency 28%") == {28.0}
    assert 488.0 in extract_headline_numbers("488 trades executed")
    assert 26.24 in extract_headline_numbers("throughput 26.24 kg/h")


def test_bare_word_collisions_not_matched():
    # 'm' inside 'models' / 's' inside 'systems' must NOT be read as a unit.
    assert extract_headline_numbers("evaluated 3 models") == set()
    assert extract_headline_numbers("ran 5 systems") == set()
    assert extract_headline_numbers("7 meters_of_config") == set()  # m not at boundary


def test_mm_not_split_into_m():
    # '150 mm' must extract 150 once, not 150 (mm) plus a stray 'm' match.
    assert extract_headline_numbers("range resolution 150 mm") == {150.0}


def test_longest_unit_wins_m_per_s():
    # 'm/s' must be matched as a whole, not 'm' leaving '/s'.
    assert 14.575 in extract_headline_numbers("v_radial = 14.575 m/s")


def test_uppercase_million_version_not_matched_as_metric():
    # final-review Minor 1: case-sensitive metric symbols. Capital M (million),
    # S (version/plural) must NOT collide with metre/second.
    assert extract_headline_numbers("Plan 3M budget") == set()
    assert extract_headline_numbers("raised $5M") == set()
    assert extract_headline_numbers("version 2.0S") == set()
    assert extract_headline_numbers("the 90S decade") == set()
    # lowercase metric symbols still work (real seconds/metres)
    assert 3.0 in extract_headline_numbers("took 3 s")
    assert 421.0 in extract_headline_numbers("421 m range")


def test_distinctive_units_stay_case_insensitive():
    # dB / Hz / kg keep IGNORECASE matching for their lowercase/variant forms.
    assert 13.0 in extract_headline_numbers("snr 13 db")
    assert 9.5 in extract_headline_numbers("freq 9.5 ghz")
    assert 26.24 in extract_headline_numbers("rate 26.24 KG")


# ── numeric_integrity prose facts (numeric_integrity._UNIT) ───────────────────

def test_prose_meters_fact():
    facts = extract_prose_facts("R_max=338.50 m headline")
    vals = {(round(f.value, 2), f.unit) for f in facts if abs(f.value - 338.5) < 0.01}
    assert (338.5, "m") in vals


def test_prose_db_fact():
    facts = extract_prose_facts("SNR_min = 16.8 dB operative")
    assert any(abs(f.value - 16.8) < 0.01 and f.unit == "dB" for f in facts)


def test_prose_word_collision_no_unit():
    # '3 models' → value 3 may be captured (unitless), but NEVER with unit 'm'.
    facts = extract_prose_facts("evaluated 3 models")
    assert all(f.unit != "m" for f in facts)
