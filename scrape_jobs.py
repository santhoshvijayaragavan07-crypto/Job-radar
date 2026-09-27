"""
Job Radar - daily job collector and ranker for ECE freshers.
Runs once a day via GitHub Actions, or manually with: python scrape_jobs.py
Outputs: index.html (dashboard), jobs.csv, data/jobs.json
"""

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

# Points added when a word appears in the job title
ROLE_KEYWORDS = {
    "embedded": 20, "firmware": 18, "vlsi": 20, "asic": 20, "fpga": 18,
    "rtl": 18, "verification": 12, "soc": 12, "electronics": 15,
    "hardware": 12, "iot": 12, "pcb": 12, "circuit": 10, "analog": 12,
    "cae": 18, "fea": 18, "fem": 12, "ansys": 15, "hypermesh": 15,
    "design engineer": 8, "test engineer": 8, "validation": 8,
    "ece": 10, "trainee engineer": 12,
}

# Points added when the job looks fresher-friendly
FRESHER_KEYWORDS = {
    "fresher": 30, "freshers": 30, "entry level": 25, "entry-level": 25,
    "trainee": 25, "graduate": 20, "graduates": 20, "junior": 15,
    "intern": 8, "internship": 8, "campus": 15, "0-1": 15, "0-2": 15,
    "0 - 1": 15, "0 - 2": 15, "get ": 10, "apprentice": 15,
}

# Points removed when the job looks too senior (negative = bad for freshers)
SENIOR_KEYWORDS = {
    "senior": -40, "sr.": -40, "sr ": -40, "lead": -40, "principal": -50,
    "manager": -50, "staff engineer": -30, "staff -": -30, "staff ": -30, "architect": -25, "head": -40,
    "director": -60, "5+": -30, "6+": -35, "7+": -40, "8+": -45,
    "10+": -60, "12+": -60,
}


def score_job(title, summary=""):
    """Higher score = better match for an ECE fresher."""
    text = (str(title) + " " + str(summary)[:400]).lower()
    score = 0
    for word, pts in ROLE_KEYWORDS.items():
        if word in text:
            score += pts
    for word, pts in FRESHER_KEYWORDS.items():
        if word in text:
            score += pts
    for word, pts in SENIOR_KEYWORDS.items():
        if word in text:
            score += pts
    return score


def tier(score):
    if score >= 40:
        return "hot"
    if score >= 20:
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
        if safe_str(row.get("search_location")) == "Remote" and location:
            if not re.search(r"india|\bIN\b", location, re.I):
                continue
        s = score_job(title, summary or "")
        if s < 5:  # drop senior and unrelated roles
            continue  # clearly senior roles are dropped
        records.append({
            "title": title,
            "company": company,
            "location": location,
            "area": area,
            "site": safe_str(row.get("site")),
            "url": url,
            "date_posted": date_str,
            "score": s,
            "tier": tier(s),
        })
    records.sort(key=lambda r: (r["score"], r["date_posted"]), reverse=True)
    return records


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Radar - Santhosh V</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #e6edf3; min-height: 100vh; }
  header { text-align: center; padding: 48px 16px 24px; background: linear-gradient(135deg, #0d1117 0%, #161b27 60%, #1a2233 100%); }
  h1 { font-size: 2.4rem; letter-spacing: 1px; }
  h1 .radar { color: #4da3ff; }
  .tagline { color: #8b949e; margin-top: 8px; font-size: 0.95rem; }
  .stats { display: flex; justify-content: center; gap: 16px; flex-wrap: wrap; margin: 28px auto 8px; max-width: 900px; padding: 0 16px; }
  .stat { background: #161b27; border: 1px solid #2a3242; border-radius: 14px; padding: 16px 28px; min-width: 130px; animation: rise .6s ease both; }
  .stat .num { font-size: 1.8rem; font-weight: 700; color: #4da3ff; }
  .stat .lbl { color: #8b949e; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; align-items: center; margin: 24px auto; max-width: 900px; padding: 0 16px; }
  .chip { background: #161b27; border: 1px solid #2a3242; color: #c9d1d9; border-radius: 999px; padding: 8px 18px; cursor: pointer; font-size: 0.85rem; transition: all .2s; }
  .chip:hover { border-color: #4da3ff; }
  .chip.active { background: #4da3ff; color: #0d1117; font-weight: 600; border-color: #4da3ff; }
  #search { background: #161b27; border: 1px solid #2a3242; color: #e6edf3; border-radius: 999px; padding: 9px 18px; width: min(320px, 90vw); font-size: 0.9rem; outline: none; }
  #search:focus { border-color: #4da3ff; }
  main { max-width: 900px; margin: 0 auto; padding: 0 16px 60px; }
  .job { background: #161b27; border: 1px solid #2a3242; border-radius: 14px; padding: 18px 22px; margin-bottom: 14px; display: flex; justify-content: space-between; gap: 16px; align-items: center; animation: rise .5s ease both; transition: border-color .2s, transform .2s; }
  .job:hover { border-color: #4da3ff; transform: translateY(-2px); }
  .job .info h3 { font-size: 1.05rem; margin-bottom: 4px; }
  .job .meta { color: #8b949e; font-size: 0.85rem; display: flex; flex-wrap: wrap; gap: 6px 14px; }
  .badge { font-size: 0.72rem; font-weight: 700; padding: 4px 12px; border-radius: 999px; white-space: nowrap; }
  .badge.hot { background: #ff56301f; color: #ff7a4d; border: 1px solid #ff5630; }
  .badge.good { background: #4da3ff1f; color: #4da3ff; border: 1px solid #4da3ff; }
  .badge.maybe { background: #8b949e1f; color: #8b949e; border: 1px solid #8b949e55; }
  .apply { background: #4da3ff; color: #0d1117; font-weight: 600; text-decoration: none; padding: 9px 20px; border-radius: 10px; font-size: 0.85rem; white-space: nowrap; }
  .apply:hover { background: #6cb4ff; }
  footer { text-align: center; color: #586069; font-size: 0.8rem; padding: 24px; }
  @keyframes rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
  @media (max-width: 560px) { .job { flex-direction: column; align-items: flex-start; } }
</style>
</head>
<body>
<header>
  <h1>📡 Job <span class="radar">Radar</span></h1>
  <p class="tagline">Fresh ECE opportunities - embedded, VLSI, electronics, hardware &amp; CAE - refreshed daily.</p>
</header>
<div class="stats">
  <div class="stat"><div class="num" id="total">0</div><div class="lbl">Jobs found</div></div>
  <div class="stat"><div class="num" id="hot">0</div><div class="lbl">Hot matches</div></div>
  <div class="stat"><div class="num" id="companies">0</div><div class="lbl">Companies</div></div>
  <div class="stat"><div class="num" id="updated" style="font-size:1rem;padding-top:8px">-</div><div class="lbl">Last updated</div></div>
</div>
<div class="controls">
  <button class="chip active" data-f="all">All</button>
  <button class="chip" data-f="hot">🔥 Hot</button>
  <button class="chip" data-f="Chennai">Chennai</button>
  <button class="chip" data-f="Bengaluru">Bengaluru</button>
  <button class="chip" data-f="Coimbatore">Coimbatore</button>
  <button class="chip" data-f="Remote">Remote</button>
  <input id="search" placeholder="Search title or company...">
</div>
<main id="jobs"></main>
<footer>Built by Santhosh V - updated automatically every morning (6 AM IST) with GitHub Actions.</footer>
<script>
const JOBS = __JOBS_JSON__;
const UPDATED = "__UPDATED__";
let filter = "all", query = "";
const el = id => document.getElementById(id);
el("total").textContent = JOBS.length;
el("hot").textContent = JOBS.filter(j => j.tier === "hot").length;
el("companies").textContent = new Set(JOBS.map(j => j.company)).size;
el("updated").textContent = UPDATED;
function esc(s){const d=document.createElement("div");d.textContent=s||"";return d.innerHTML;}
function render(){
  const list = JOBS.filter(j => {
    if (filter === "hot" && j.tier !== "hot") return false;
    if (["Chennai","Bengaluru","Coimbatore","Remote"].includes(filter) &&
        j.area !== filter) return false;
    if (query && !(j.title+" "+j.company).toLowerCase().includes(query)) return false;
    return true;
  });
  el("jobs").innerHTML = list.length ? list.map((j,i) => `
    <div class="job" style="animation-delay:${Math.min(i*40,400)}ms">
      <div class="info">
        <h3>${esc(j.title)}</h3>
        <div class="meta">
          <span>🏢 ${esc(j.company)}</span><span>📍 ${esc(j.location)}</span>
          <span>🗓 ${esc(j.date_posted)}</span><span>via ${esc(j.site)}</span>
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <span class="badge ${j.tier}">${j.tier === "hot" ? "🔥 HOT" : j.tier.toUpperCase()}</span>
        <a class="apply" href="${esc(j.url)}" target="_blank" rel="noopener">Apply →</a>
      </div>
    </div>`).join("") : '<p style="text-align:center;color:#8b949e;padding:40px">No jobs match this filter.</p>';
}
document.querySelectorAll(".chip").forEach(c => c.onclick = () => {
  document.querySelectorAll(".chip").forEach(x => x.classList.remove("active"));
  c.classList.add("active"); filter = c.dataset.f; render();
});
el("search").oninput = e => { query = e.target.value.toLowerCase(); render(); };
render();
</script>
</body>
</html>
"""


def main():
    print("Collecting jobs...")
    df = collect()
    records = build_records(df) if len(df) else []
    print(f"Total unique jobs after filtering: {len(records)}")

    os.makedirs("data", exist_ok=True)
    with open("data/jobs.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    pd.DataFrame(records).to_csv("jobs.csv", index=False)

    ist = timezone(timedelta(hours=5, minutes=30))
    updated = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")
    html = (DASHBOARD_TEMPLATE
            .replace("__JOBS_JSON__", json.dumps(records, ensure_ascii=False))
            .replace("__UPDATED__", updated))
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Dashboard written to index.html")


if __name__ == "__main__":
    main()
