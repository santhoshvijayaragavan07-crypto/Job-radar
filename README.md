# Job-radar# Job Radar - Santhosh V

My personal job radar. Every morning it collects fresh ECE jobs (embedded,
VLSI, electronics, hardware, CAE) in Chennai, Bengaluru, Coimbatore and remote,
scores them for fresher-fit, and updates my live dashboard.

## How it works

- `scrape_jobs.py` collects jobs from Indeed and LinkedIn (via JobSpy),
  removes duplicates, drops senior roles, and scores each job.
- `.github/workflows/daily.yml` runs the script every day at 6 AM IST
  (GitHub Actions) and saves the new dashboard.
- `index.html` is the dashboard. It is a plain static page, so any static
  host (Vercel, GitHub Pages, Netlify) can serve it.

## Setup (about 10 minutes, no coding needed)

1. On github.com, create a new **public** repository called `job-radar`.
2. Upload these files (Add file -> Upload files). Keep the folder structure:
   - `scrape_jobs.py`
   - `requirements.txt`
   - `.github/workflows/daily.yml` (create with "Add file -> Create new file"
     and type the full path as the name)
3. Go to the **Actions** tab, enable workflows, select "Daily Job Radar" and
   click **Run workflow** once. Wait for the green tick (2-5 minutes).
4. On vercel.com, sign up with GitHub, choose **Add New -> Project**, import
   `job-radar`, leave all settings at default (Framework: Other), and Deploy.
5. Done. Your dashboard is live at your Vercel link, and it refreshes itself
   every morning.

## Make it yours

Edit the marked section at the top of `scrape_jobs.py` to change roles,
cities, sites or how many jobs it pulls. Commit the change and the next
daily run uses it.

## Note

Job sites sometimes block automated requests, so some days a source may
return fewer jobs. The script uses whatever succeeds, so the dashboard
always stays up.
