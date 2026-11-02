# BLS release dates (CPI, PPI, Employment Situation): machine-fetchable sources
Checked 2026-10-29 from the box with curl.

| URL | headers | result |
|---|---|---|
| https://www.bls.gov/schedule/news_release/bls.ics (also cpi.htm, ppi.htm, empsit.htm, /schedule/2026/home.htm) | default curl UA, or a browser UA `Mozilla/5.0 …` | **403** |
| same | `User-Agent: options-spec-research/1.1 (paper-trading calendar check; contact: <email>)` | **200**. ICS: `text/calendar`, 313 VEVENTs, Last-Modified Wed, 10 Jun 2026 16:56:37 GMT |
| same | `User-Agent: <email>` only | 200 |
| same | `User-Agent: options-spec-research/1.1` (no email) | 403 |
| https://api.stlouisfed.org/fred/release/dates?release_id=10&file_type=json | none | 400 "Variable api_key is not set" (free key needed; release_id 10 = CPI, 46 = PPI, 50 = Employment Situation) |
| https://fred.stlouisfed.org/releases/calendar?rid=10&y=2026 (rid 46, 50) | default curl UA | 200 HTML; dates match BLS |

The deciding factor is a contact **email in the User-Agent**. Use a real address you monitor, and keep the request rate low (BLS policy).

```
curl -sA "options-spec-research/1.1 (contact: you@example.com)" https://www.bls.gov/schedule/news_release/bls.ics
BEGIN:VCALENDAR
PRODID:-//Department of Labor//Bureau of Labor Statistics//EN
X-WR-CALNAME:BLS.gov Economic News Release Schedule
X-WR-TIMEZONE:US-Eastern
...
DTSTART;TZID=US-Eastern:20261110T083000
SUMMARY:Consumer Price Index
```

Remaining 2026 dates, all 08:30 ET. BLS ICS, the BLS schedule pages and FRED agree.
| release | dates | Berlin |
|---|---|---|
| Employment Situation (Oct data) | Fri 2026-11-06 | 14:30 CET |
| CPI (Oct) | Tue 2026-11-10 | 14:30 CET |
| PPI (Oct) | Fri 2026-11-13 | 14:30 CET |
| Employment Situation (Nov) | Fri 2026-12-04 | 14:30 CET |
| CPI (Nov) | Thu 2026-12-10 | 14:30 CET |
| PPI (Nov) | Tue 2026-12-15 | 14:30 CET |
Already announced for early 2027: Employment Situation Jan 08, CPI Jan 13, PPI Jan 14.
