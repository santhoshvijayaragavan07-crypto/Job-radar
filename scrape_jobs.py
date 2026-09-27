"""
Job Radar - daily job collector and ranker for ECE freshers.
Runs once a day via GitHub Actions, or manually with: python scrape_jobs.py
Outputs: index.html (dashboard), jobs.csv, data/jobs.json
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta

import pandas as pd
from jobspy import scrape_jobs

# ------------------- EDIT YOUR SEARCH HERE -------------------
# The roles you want. Add or remove terms freely.
ROLE_TERMS = [
    "embedded engineer",
    "VLSI engineer",
    "electronics engineer",
    "hardware engineer fresher",
    "CAE engineer",
]

# The cities you can work in.
LOCATIONS = ["Chennai", "Bengaluru", "Coimbatore"]

# Job sites to search. "indeed" and "linkedin" work best.
# You can add "naukri", but it sometimes blocks automated searches.
SITES = ["indeed", "linkedin"]

# How many results to pull per search (higher = slower, more jobs)
RESULTS_WANTED = 15

# Only keep jobs posted in the last N hours (168 = 1 week)
HOURS_OLD = 168
# --------------------------------------------------------------

# Core ECE words. One of these must be in the job TITLE for a top score.
CORE_ECE = {
    "embedded": 22, "firmware": 22, "vlsi": 22, "asic": 22, "fpga": 20,
    "rtl": 20, "physical design": 20, "dft": 16, "soc": 14,
    "electronics": 18, "semiconductor": 18, "analog": 16, "pcb": 16,
    "cae": 20, "fea": 20, "ansys": 18, "hypermesh": 18,
    "iot": 14, "circuit": 12, "hardware": 14, "layout": 14,
    "verification": 14, "validation": 10,
}

# Supporting words. Nice to see, but not enough on their own.
SUPPORT = {
    "design engineer": 8, "test engineer": 8, "ece": 10, "electrical": 8,
    "instrumentation": 10, "automation": 8, "robotics": 10,
    "trainee engineer": 10,
}

# Points added when the job looks fresher-friendly
FRESHER_KEYWORDS = {
    "fresher": 30, "freshers": 30, "entry level": 25, "entry-level": 25,
    "trainee": 25, "graduate": 20, "graduates": 20, "junior": 15,
    "intern": 8, "internship": 8, "campus": 15, "0-1": 15, "0-2": 15,
    "0 - 1": 15, "0 - 2": 15, "apprentice": 15,
}

# Points removed when the job looks too senior
SENIOR_KEYWORDS = {
    "senior": -40, "sr.": -40, "sr ": -40, "lead": -40, "principal": -50,
    "manager": -50, "staff engineer": -30, "staff -": -30, "staff ": -30,
    "architect": -25, "head": -40, "director": -60, "5+": -30, "6+": -35,
    "7+": -40, "8+": -45, "10+": -60, "12+": -60,
}

# Jobs that are not core ECE. Heavy negatives keep them off the top.
OFF_TRACK = {
    "cloud": -35, "devops": -35, "desktop support": -45, "it support": -45,
    "helpdesk": -45, "full stack": -30, "full-stack": -30, "java": -25,
    "python developer": -25, "data analyst": -30, "data scientist": -30,
    "sales": -40, "marketing": -40, "recruiter": -45, "customer": -35,
    "support engineer": -35, "software support": -40, "web developer": -25,
    "frontend": -25, "backend": -25, "android": -20, "business analyst": -35,
    "accountant": -50, "hr ": -40,
}

# Skills we recognise on a posting (matched against title + description).
SKILL_TAGS = [
    "Embedded C", "C++", "Python", "Verilog", "VHDL", "SystemVerilog",
    "UVM", "FPGA", "ASIC", "RTL Design", "MATLAB", "Simulink", "ANSYS",
    "HyperMesh", "OptiStruct", "FEA", "CFD", "IoT", "PCB Design",
    "Altium", "KiCad", "RTOS", "FreeRTOS", "Linux", "CAN", "I2C", "SPI",
    "UART", "ARM", "Microcontrollers", "STM32", "Arduino", "LabVIEW",
    "AutoCAD", "SolidWorks", "CREO", "CATIA", "PLC", "SCADA",
    "Signal Processing", "Antenna", "RF",
]

JOB_TYPES = {
    "fulltime": "Full-time", "parttime": "Part-time",
    "internship": "Internship", "contract": "Contract",
    "temporary": "Temporary",
}


def score_job(title, summary=""):
    """Raw points for fresher-ECE fit. No core ECE word in the title = capped low."""
    t = str(title).lower()
    s400 = str(summary or "")[:400].lower()
    core = sum(p for w, p in CORE_ECE.items() if w in t)
    support = sum(p for w, p in SUPPORT.items() if w in t)
    fresh = sum(p for w, p in FRESHER_KEYWORDS.items() if w in t or w in s400)
    senior = sum(p for w, p in SENIOR_KEYWORDS.items() if w in t)
    off = sum(p for w, p in OFF_TRACK.items() if w in t)
    raw = core + support + fresh + senior + off
    if core == 0:
        raw = min(raw, 18)
    return raw


def display_score(raw):
    """Turn raw points into a friendly 0-99 score for the dashboard."""
    if raw <= 0:
        return 5
    return min(99, round(100 * min(1.0, raw / 55) ** 0.7))


def tier(score):
    if score >= 80:
        return "hot"
    if score >= 49:
        return "good"
    return "maybe"


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


STATE_CODES = {"TN": "Tamil Nadu", "KA": "Karnataka", "KL": "Kerala",
               "AP": "Andhra Pradesh", "TS": "Telangana", "MH": "Maharashtra"}


def clean_location(loc):
    """Indeed gives locations like 'TN, IN' - make them readable."""
    m = re.fullmatch(r"([A-Z]{2}),\s*IN", loc)
    if m and m.group(1) in STATE_CODES:
        return STATE_CODES[m.group(1)] + ", India"
    return loc


def safe_str(v):
    """Cell -> clean string; NaN/None become empty."""
    return str(v).strip() if v is not None and pd.notna(v) else ""


def find_skills(title, summary):
    text = (str(title) + " " + str(summary or "")).lower()
    found = []
    for skill in SKILL_TAGS:
        if skill.lower() in text:
            found.append(skill)
    return found[:5]


def fmt_salary(row):
    """Show salary only when the listing really carries one. Never invent."""
    lo, hi = row.get("min_amount"), row.get("max_amount")
    if pd.isna(lo) and pd.isna(hi):
        return ""
    cur = safe_str(row.get("currency")) or ""
    interval = safe_str(row.get("interval"))
    try:
        lo = float(lo) if pd.notna(lo) else None
        hi = float(hi) if pd.notna(hi) else None
    except (TypeError, ValueError):
        return ""
    if cur == "INR" and interval == "yearly":
        if lo and hi:
            return f"\u20b9{lo/100000:.1f}-{hi/100000:.1f} LPA"
        if lo:
            return f"\u20b9{lo/100000:.1f}+ LPA"
        if hi:
            return f"Up to \u20b9{hi/100000:.1f} LPA"
    return ""


def collect():
    frames = []
    for location in LOCATIONS + ["Remote"]:
        for term in ROLE_TERMS:
            for site in SITES:
                try:
                    df = scrape_jobs(
                        site_name=[site],
                        search_term=term,
                        location=location,
                        results_wanted=RESULTS_WANTED,
                        hours_old=HOURS_OLD,
                        country_indeed="India",
                        linkedin_fetch_description=False,
                        verbose=0,
                    )
                    if df is not None and len(df):
                        df["search_location"] = location
                        frames.append(df)
                        print(f"  {site} / {term} / {location}: {len(df)} jobs")
                except Exception as e:
                    # One failing site or search must not stop the whole run
                    print(f"  skipped {site} / {term} / {location}: {type(e).__name__}")
    if not frames:
        return pd.DataFrame()
    all_jobs = pd.concat(frames, ignore_index=True)
    # Remove duplicates (same title + company can appear on several sites/searches)
    all_jobs["dedupe_key"] = all_jobs["title"].map(norm) + "|" + all_jobs["company"].map(norm)
    all_jobs = all_jobs.drop_duplicates(subset="dedupe_key")
    return all_jobs


def build_records(df):
    records = []
    for _, row in df.iterrows():
        title = safe_str(row.get("title"))
        if not title:
            continue
        company = safe_str(row.get("company"))
        area = safe_str(row.get("search_location"))
        location = clean_location(safe_str(row.get("location")))
        url = safe_str(row.get("job_url")) or safe_str(row.get("job_url_direct"))
        date_str = safe_str(row.get("date_posted"))[:10]
        summary = safe_str(row.get("description"))
        # Remote searches also return worldwide jobs - keep only India-based ones
        if area == "Remote" and location:
            if not re.search(r"india|\bIN\b", location, re.I):
                continue
        raw = score_job(title, summary)
        if raw < 5:
            continue  # senior and off-track roles are dropped
        score = display_score(raw)
        snippet = re.sub(r"\s+", " ", summary).strip()[:280]
        jtype = JOB_TYPES.get(safe_str(row.get("job_type")).lower(), "")
        logo = safe_str(row.get("company_logo"))
        if not logo.startswith("http"):
            logo = ""
        records.append({
            "id": hashlib.md5(norm(title + "|" + company).encode()).hexdigest()[:10],
            "title": title,
            "company": company,
            "location": location,
            "area": area,
            "site": safe_str(row.get("site")),
            "url": url,
            "date_posted": date_str,
            "score": score,
            "tier": tier(score),
            "type": jtype,
            "remote": bool(row.get("is_remote")) if pd.notna(row.get("is_remote")) else False,
            "salary": fmt_salary(row),
            "industry": safe_str(row.get("company_industry")),
            "logo": logo,
            "skills": find_skills(title, summary),
            "snippet": snippet,
        })
    records.sort(key=lambda r: (r["score"], r["date_posted"]), reverse=True)
    return records


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Job Radar - entry-level ECE jobs in Chennai, Bengaluru, Coimbatore and remote India, collected daily and ranked for fresher fit. Built by Santhosh V.">
<meta name="theme-color" content="#0a66c2">
<title>Job Radar - Entry-level ECE jobs, ranked daily</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%230a66c2'/%3E%3Ccircle cx='16' cy='16' r='8' fill='none' stroke='white' stroke-width='2'/%3E%3Ccircle cx='16' cy='16' r='3' fill='white'/%3E%3C/svg%3E">
<style>
  :root { --accent: #0a66c2; --accent-dark: #084d94; --ink: #17202b; --ink-2: #5b6470; --line: #e3e7ec;
          --bg: #f4f6f8; --card: #ffffff; --hot: #c2410c; --hot-bg: #fff3ec; --good-bg: #eef5fd; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--ink); }
  a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  nav { position: sticky; top: 0; z-index: 50; background: #fffffff2; backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
  .nav-in { max-width: 1020px; margin: 0 auto; padding: 12px 18px; display: flex; align-items: center; gap: 18px; }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 700; font-size: 1.06rem; color: var(--ink); text-decoration: none; }
  .brand svg { flex-shrink: 0; }
  .nav-links { margin-left: auto; display: flex; gap: 18px; align-items: center; }
  .nav-links a { color: var(--ink-2); text-decoration: none; font-size: 0.88rem; }
  .nav-links a:hover { color: var(--accent); }
  .updated-pill { background: var(--good-bg); color: var(--accent-dark); font-size: 0.74rem; font-weight: 600; padding: 5px 12px; border-radius: 999px; white-space: nowrap; }
  @media (max-width: 560px) { .nav-links a { display: none; } }

  .hero { background: linear-gradient(180deg, #ffffff 0%, var(--bg) 100%); border-bottom: 1px solid var(--line); }
  .hero-in { max-width: 1020px; margin: 0 auto; padding: 52px 18px 40px; }
  .eyebrow { color: var(--accent); font-size: 0.74rem; font-weight: 700; letter-spacing: 1.6px; text-transform: uppercase; }
  .hero h1 { font-size: clamp(1.7rem, 4.5vw, 2.7rem); line-height: 1.15; margin: 12px 0 10px; max-width: 620px; }
  .hero p { color: var(--ink-2); font-size: 1rem; max-width: 560px; line-height: 1.55; }
  .hero-stats { display: flex; gap: 26px; flex-wrap: wrap; margin-top: 24px; }
  .hs b { display: block; font-size: 1.45rem; color: var(--accent); }
  .hs span { color: var(--ink-2); font-size: 0.8rem; }
  .cta { display: inline-block; margin-top: 24px; background: var(--accent); color: #fff; text-decoration: none; font-weight: 600; padding: 12px 26px; border-radius: 10px; font-size: 0.95rem; }
  .cta:hover { background: var(--accent-dark); }

  .wrap { max-width: 1020px; margin: 0 auto; padding: 0 18px; }

  .toolbar { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; margin: 26px 0 6px;
             display: flex; flex-wrap: wrap; gap: 10px; align-items: center; position: sticky; top: 58px; z-index: 20; box-shadow: 0 2px 10px #17202b0d; }
  #search { flex: 1; min-width: 180px; border: 1px solid var(--line); border-radius: 9px; padding: 10px 14px; font-size: 0.92rem; color: var(--ink); background: #fbfcfd; }
  #sort { border: 1px solid var(--line); border-radius: 9px; padding: 10px 12px; font-size: 0.88rem; color: var(--ink); background: #fbfcfd; }
  .chiprows { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 4px; }
  .chip { border: 1px solid var(--line); background: var(--card); color: var(--ink-2); border-radius: 999px; padding: 7px 15px; font-size: 0.82rem; cursor: pointer; transition: all .15s; }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  .chip.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  #count { color: var(--ink-2); font-size: 0.82rem; margin: 12px 2px 14px; }

  .job { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-bottom: 12px; transition: box-shadow .15s, border-color .15s; }
  .job:hover { border-color: #c8d2dd; box-shadow: 0 4px 18px #17202b12; }
  .job.hot { border-left: 4px solid var(--hot); }
  .job-top { display: flex; gap: 14px; }
  .logo { width: 52px; height: 52px; border-radius: 10px; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
          font-weight: 700; font-size: 1.2rem; overflow: hidden; border: 1px solid var(--line); background: #f0f3f7; }
  .logo img { width: 100%; height: 100%; object-fit: contain; }
  .job-main { flex: 1; min-width: 0; }
  .job-title-row { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
  .job h3 { font-size: 1.05rem; line-height: 1.3; }
  .job h3 a { color: var(--ink); text-decoration: none; }
  .job h3 a:hover { color: var(--accent); text-decoration: underline; }
  .company-line { color: var(--ink-2); font-size: 0.88rem; margin-top: 2px; }
  .facts { display: flex; flex-wrap: wrap; gap: 4px 16px; color: var(--ink-2); font-size: 0.83rem; margin-top: 8px; }
  .facts .salary { color: #15803d; font-weight: 600; }
  .ago { color: #8a94a1; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .tag { background: #f0f3f7; color: #39424e; font-size: 0.74rem; font-weight: 600; padding: 4px 11px; border-radius: 999px; }
  .snippet { color: var(--ink-2); font-size: 0.87rem; line-height: 1.55; margin-top: 10px; display: none; }
  .snippet.open { display: block; }
  .more-btn { background: none; border: 0; color: var(--accent); font-size: 0.83rem; font-weight: 600; cursor: pointer; padding: 6px 0 0; }
  .job-side { display: flex; flex-direction: column; align-items: center; gap: 6px; flex-shrink: 0; }
  .fit { text-align: center; }
  .fit .ring { position: relative; width: 56px; height: 56px; }
  .fit .num { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem; line-height: 1; }
  .fit .num small { font-size: 0.52rem; color: #8a94a1; font-weight: 700; letter-spacing: 0.5px; margin-top: 2px; }
  .fit .tw { font-size: 0.64rem; font-weight: 700; letter-spacing: 1px; margin-top: 4px; display: block; }
  .tw.hot { color: var(--hot); } .tw.good { color: var(--accent); } .tw.maybe { color: #8a94a1; }
  .job-actions { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
  .apply { background: var(--accent); color: #fff; font-weight: 600; text-decoration: none; padding: 9px 22px; border-radius: 9px; font-size: 0.87rem; }
  .apply:hover { background: var(--accent-dark); }
  .save { background: none; border: 1px solid var(--line); border-radius: 9px; width: 38px; height: 38px; cursor: pointer; font-size: 1rem; color: #8a94a1; }
  .save.saved { color: var(--hot); border-color: var(--hot); background: var(--hot-bg); }
  @media (max-width: 560px) {
    .job-top { flex-wrap: wrap; }
    .job-side { flex-direction: row; align-items: center; }
  }

  .insights { margin: 34px 0; }
  .insights h2, .about h2 { font-size: 1.25rem; margin-bottom: 14px; }
  .insight-grid { display: grid; gap: 12px; grid-template-columns: 1fr; }
  @media (min-width: 720px) { .insight-grid { grid-template-columns: 1fr 1fr; } }
  .panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; }
  .panel h4 { color: var(--ink-2); font-size: 0.74rem; text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 14px; font-weight: 700; }
  .bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 9px; }
  .bar-row:last-child { margin-bottom: 0; }
  .bar-row .bl { width: 86px; color: var(--ink); font-size: 0.84rem; flex-shrink: 0; }
  .bar-track { flex: 1; background: #eef1f5; border-radius: 6px; height: 15px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 6px; background: linear-gradient(90deg, var(--accent-dark), var(--accent)); width: 0; transition: width .9s ease; }
  .bar-row .bn { width: 26px; text-align: right; color: var(--accent); font-weight: 700; font-size: 0.84rem; }
  .co-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid #f0f3f7; font-size: 0.88rem; }
  .co-row:last-child { border: 0; }
  .co-row b { color: var(--accent); }

  .about { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 22px 24px; margin-bottom: 30px; }
  .about p { color: var(--ink-2); font-size: 0.9rem; line-height: 1.6; margin-bottom: 10px; }
  footer { border-top: 1px solid var(--line); background: #fff; }
  .foot-in { max-width: 1020px; margin: 0 auto; padding: 22px 18px; color: #8a94a1; font-size: 0.8rem; line-height: 1.6; }
  .empty { text-align: center; color: var(--ink-2); padding: 60px 20px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
</style>
</head>
<body>
<nav aria-label="Main">
  <div class="nav-in">
    <a class="brand" href="#top" aria-label="Job Radar home">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="#0a66c2"/><circle cx="16" cy="16" r="8" fill="none" stroke="white" stroke-width="2"/><circle cx="16" cy="16" r="3" fill="white"/></svg>
      Job Radar
    </a>
    <div class="nav-links">
      <a href="#jobs">Jobs</a><a href="#insights">Insights</a><a href="#about">About</a>
      <span class="updated-pill">Updated __UPDATED_SHORT__</span>
    </div>
  </div>
</nav>

<header class="hero" id="top">
  <div class="hero-in">
    <span class="eyebrow">Updated daily &middot; LinkedIn + Indeed</span>
    <h1>Entry-level ECE jobs, ranked for fresher fit.</h1>
    <p>Job Radar collects the newest embedded, VLSI, electronics, hardware and CAE openings across Chennai, Bengaluru, Coimbatore and remote India, then scores each one so the best matches surface first.</p>
    <div class="hero-stats">
      <div class="hs"><b id="total">0</b><span>open roles</span></div>
      <div class
