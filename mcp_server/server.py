"""
MCP server exposing DOU job scraping and email tools.
The agent (MCP client) calls these tools; the LLM decides which to call and when.

Job results are stored server-side so the LLM never has to pass large JSON back as arguments.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dou-jobs", host="0.0.0.0", port=8000)

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

DOU_URL = "https://jobs.dou.ua/vacancies/"
MAX_JOBS_PER_CATEGORY = 15
SEEN_URLS_FILE = "/app/state/seen_urls.json"

# Server-side job store — populated by scrape_jobs, consumed by send_email
_job_store: list[dict] = []


def _load_seen_urls() -> set[str]:
    if os.path.exists(SEEN_URLS_FILE):
        with open(SEEN_URLS_FILE) as f:
            return set(json.load(f))
    return set()


def _save_seen_urls(urls: set[str]):
    os.makedirs(os.path.dirname(SEEN_URLS_FILE), exist_ok=True)
    with open(SEEN_URLS_FILE, "w") as f:
        json.dump(list(urls), f)


def _scrape(category: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "uk,en-US;q=0.9,en;q=0.8",
    })
    session.get(DOU_URL, timeout=30)
    resp = session.get(DOU_URL, params={"category": category}, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    jobs = []
    for i, item in enumerate(soup.select("li.l-vacancy")):
        if i >= MAX_JOBS_PER_CATEGORY:
            break
        title_el = item.select_one("a.vt")
        if not title_el:
            continue
        company_el = item.select_one("a.company")
        salary_el = item.select_one("span.salary")
        cities_el = item.select_one("span.cities")
        date_el = item.select_one("div.date")
        jobs.append({
            "category": category,
            "title": title_el.get_text(strip=True),
            "url": title_el.get("href", ""),
            "company": company_el.get_text(strip=True) if company_el else "",
            "salary": salary_el.get_text(strip=True) if salary_el else "",
            "location": cities_el.get_text(strip=True) if cities_el else "",
            "date": date_el.get_text(strip=True) if date_el else "",
        })
    return jobs


def _build_html(jobs: list[dict], intro: str) -> str:
    rows = "".join(
        f"""<tr>
          <td><a href="{j['url']}">{j['title']}</a></td>
          <td>{j['company']}</td>
          <td>{j.get('salary') or '—'}</td>
          <td>{j['location']}</td>
          <td>{j['date']}</td>
          <td>{j.get('category', '')}</td>
        </tr>"""
        for j in jobs
    )
    return f"""<html><body style="font-family:sans-serif;max-width:900px;margin:auto">
<h2>DOU Jobs Digest</h2>
<p>{intro}</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;font-size:14px;width:100%">
  <thead style="background:#f0f0f0">
    <tr><th>Title</th><th>Company</th><th>Salary</th><th>Location</th><th>Date</th><th>Category</th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p style="color:#888;font-size:12px">{len(jobs)} listings</p>
</body></html>"""


def _send_gmail(subject: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
        smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())


@mcp.tool()
def scrape_jobs(category: str) -> str:
    """
    Scrape job listings from DOU for a given category (e.g. 'JavaScript', 'AI/ML', 'Node.js').
    Only new listings (not seen in previous runs) are stored. Call once per category.
    Returns a short summary of what was found.
    """
    global _job_store
    try:
        jobs = _scrape(category)
        seen = _load_seen_urls()
        new_jobs = [j for j in jobs if j["url"] not in seen]
        _job_store.extend(new_jobs)
        titles = [j["title"] for j in new_jobs[:5]]
        return (
            f"Found {len(jobs)} {category} jobs, {len(new_jobs)} new. "
            + (f"Examples: {'; '.join(titles)}. " if titles else "No new listings. ")
            + f"Total new jobs stored so far: {len(_job_store)}."
        )
    except Exception as e:
        return f"Error scraping {category}: {e}"


@mcp.tool()
def send_email(subject: str, intro: str) -> str:
    """
    Send a digest email with all jobs collected by scrape_jobs so far.
    Call this after scraping all categories.

    Args:
        subject: Email subject line.
        intro: 2-3 sentence summary of what was found.
    """
    global _job_store

    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASSWORD):
        return "Email not configured — set EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD in .env"

    if not _job_store:
        return "No jobs stored — call scrape_jobs first."

    try:
        html = _build_html(_job_store, intro)
        _send_gmail(subject, html)
        # Mark these URLs as seen so they're skipped next run
        seen = _load_seen_urls()
        seen.update(j["url"] for j in _job_store)
        _save_seen_urls(seen)
        count = len(_job_store)
        _job_store = []  # reset for next run
        return f"Email sent to {EMAIL_TO} with {count} new listings."
    except Exception as e:
        return f"Error sending email: {e}"


if __name__ == "__main__":
    mcp.run(transport="sse")
