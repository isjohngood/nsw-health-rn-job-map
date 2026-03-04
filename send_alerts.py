"""
send_alerts.py — GitHub Actions email alert script.

Reads jobs.json (current jobs) and previous_urls.pkl (URLs from before this
scrape), computes new jobs, applies optional criteria from environment variables,
and sends an email via the Resend API if any new matching jobs are found.

Required env vars:
    RESEND_API_KEY      Resend API key (https://resend.com)
    ALERT_EMAIL         Recipient email address

Optional env vars:
    ALERT_LOCATIONS     Comma-separated location substrings, e.g. "Sydney,Newcastle"
    ALERT_INCENTIVES_ONLY  "true" to only alert for incentivised jobs

Usage (called by GitHub Actions after processjobs.py):
    python send_alerts.py
"""

import json
import os
import pickle
import urllib.request
import urllib.error

RESEND_API_URL = "https://api.resend.com/emails"
JOBS_FILE = "jobs.json"
PREV_URLS_FILE = "previous_urls.pkl"


def load_previous_urls():
    if not os.path.exists(PREV_URLS_FILE):
        print(f"  {PREV_URLS_FILE} not found — treating all jobs as new")
        return set()
    try:
        with open(PREV_URLS_FILE, "rb") as f:
            data = pickle.load(f)
        return data.get("urls", set())
    except Exception as e:
        print(f"  Failed to load {PREV_URLS_FILE}: {e}")
        return set()


def load_jobs():
    if not os.path.exists(JOBS_FILE):
        print(f"  {JOBS_FILE} not found — no jobs to process")
        return []
    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def has_incentives(job):
    inc = job.get("Incentives", "") or ""
    title = job.get("Job Title", "") or ""
    kw = ["incentive", "salary packaging", "annual leave"]
    return any(k in inc.lower() or k in title.lower() for k in kw)


def job_matches_criteria(job, alert_locations, incentives_only):
    if incentives_only and not has_incentives(job):
        return False
    if alert_locations:
        loc = (job.get("Location") or "").lower()
        if not any(al in loc for al in alert_locations):
            return False
    return True


def build_email_html(new_jobs):
    rows = ""
    for job in new_jobs:
        title = job.get("Job Title", "N/A")
        loc = job.get("Location", "N/A")
        due = job.get("Due Date", "N/A") or "Open Until Filled"
        inc = "Yes" if has_incentives(job) else "No"
        url = job.get("URL", "#")
        rows += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd">{title}</td>
            <td style="padding:8px;border:1px solid #ddd">{loc}</td>
            <td style="padding:8px;border:1px solid #ddd">{due}</td>
            <td style="padding:8px;border:1px solid #ddd">{inc}</td>
            <td style="padding:8px;border:1px solid #ddd"><a href="{url}">View Job</a></td>
        </tr>"""

    return f"""
<html><body style="font-family:sans-serif;max-width:800px;margin:auto">
<h2 style="color:#1d4ed8">NSW Health RN Job Alerts</h2>
<p>There are <strong>{len(new_jobs)} new matching job{'s' if len(new_jobs) != 1 else ''}</strong>
since the last scrape.</p>
<table style="border-collapse:collapse;width:100%">
  <thead>
    <tr style="background:#1d4ed8;color:white">
      <th style="padding:8px;text-align:left">Job Title</th>
      <th style="padding:8px;text-align:left">Location</th>
      <th style="padding:8px;text-align:left">Due Date</th>
      <th style="padding:8px;text-align:left">Incentives</th>
      <th style="padding:8px;text-align:left">Link</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p style="margin-top:20px;color:#666;font-size:0.9em">
  You're receiving this because you configured NSW RN Job Alerts via GitHub Actions.
</p>
</body></html>"""


def send_email(api_key, to_addr, subject, html_body):
    payload = json.dumps({
        "from": "NSW RN Job Alerts <alerts@resend.dev>",
        "to": [to_addr],
        "subject": subject,
        "html": html_body
    }).encode("utf-8")

    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            print(f"  Email sent successfully: {body}")
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"  Resend API error {e.code}: {err_body}")
        return False
    except Exception as e:
        print(f"  Failed to send email: {e}")
        return False


def main():
    print("=== send_alerts.py ===")

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    alert_email = os.environ.get("ALERT_EMAIL", "").strip()

    if not api_key:
        print("RESEND_API_KEY not set — skipping email alerts")
        return
    if not alert_email:
        print("ALERT_EMAIL not set — skipping email alerts")
        return

    # Optional criteria
    locations_raw = os.environ.get("ALERT_LOCATIONS", "").strip()
    alert_locations = [l.strip().lower() for l in locations_raw.split(",") if l.strip()] if locations_raw else []
    incentives_only = os.environ.get("ALERT_INCENTIVES_ONLY", "").strip().lower() == "true"

    print(f"Config: to={alert_email}, locations={alert_locations or 'any'}, "
          f"incentives_only={incentives_only}")

    # Load data
    previous_urls = load_previous_urls()
    jobs = load_jobs()
    print(f"Loaded {len(jobs)} current jobs, {len(previous_urls)} previous URLs")

    # Find new jobs
    new_jobs = [j for j in jobs if j.get("URL") and j["URL"] not in previous_urls]
    print(f"New jobs (not in previous_urls): {len(new_jobs)}")

    # Apply criteria filter
    matching = [j for j in new_jobs if job_matches_criteria(j, alert_locations, incentives_only)]
    print(f"Matching jobs after criteria filter: {len(matching)}")

    if not matching:
        print("No new matching jobs — no email sent.")
        return

    # Build and send email
    subject = f"NSW Health RN Jobs: {len(matching)} new job{'s' if len(matching) != 1 else ''}"
    html = build_email_html(matching)
    print(f"Sending email to {alert_email}...")
    send_email(api_key, alert_email, subject, html)


if __name__ == "__main__":
    main()
