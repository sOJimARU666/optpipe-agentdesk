# Source captures for the macro-calendar tests

| file | provenance |
|---|---|
| `fed_fomccalendars.htm`, `bls_*.md`, `treasury_bill_rates_2026.csv` | real captures by Doodle (see each test's notes) |
| `fred_calendar_rid10_2026.html`, `rid46`, `rid50` | **real captures**: `GET https://fred.stlouisfed.org/releases/calendar?rid=<id>&y=2026` from the box on 2026-10-29 20:50 CET (Europe/Berlin), default curl UA, HTTP 200. rid 10 = CPI, 46 = PPI, 50 = Employment Situation. The page says "All times are US Central Time" (7:30 am CT = 08:30 ET). |
| `bls_schedule_handmade.ics` | **HAND-MADE, not a capture.** BLS only serves `bls.ics` to a User-Agent with a contact email, and no email is configured (`sources.bls_contact_email` is empty), so the file was not fetched. It follows the format in Doodle's `spec/bls_release_sources.md` (PRODID, X-WR-TIMEZONE:US-Eastern, `DTSTART;TZID=US-Eastern:…`, SUMMARY names). Dates are the ones Doodle confirmed (BLS ICS = BLS pages = FRED). Non-target events, one folded SUMMARY line and one UTC `DTSTART…Z` are there on purpose to exercise the parser. |
