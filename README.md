# Codex usage

This is a small, local Windows dashboard for up to three Codex accounts. It
requires Python 3 with Tkinter, the Windows `pyw` launcher, and Codex CLI on
your `PATH`. No extra Python packages are required.

Double-click `Launch.vbs`. Create a desktop shortcut to that file if desired.

Select each of the three rows and click **Connect selected account**. Sign in to
the matching account in the browser; choose a different account if the browser
remembers the previous one. Check the email shown in the row after signing in.
Repeat for all three rows. Thereafter the window refreshes on every launch and
every 5 minutes while open. Last checked shows each account's successful fetch.

**Full resets** shows the available reset count reported by Codex, refreshed with
the same request. Unavailable means the service did not return a count; zero
means it reported none available. This column only displays the count.

The table shows percentage remaining in every quota window reported by Codex,
with reset dates in this computer's local time. Weekly means a seven-day window,
not seven days until reset. Reserve weekly is the separate gpt-reserve bucket,
currently reported by the service with the normal model GPT-5.6 Luna.
Each reset date lines up with its allowance on the same line.
Missing data is never treated as zero usage. No model
prompts are submitted and no API key or extra paid service is required.

Before requesting sign-in, the dashboard asks Codex to refresh its managed
session and retries an expired quota request once. Missing authentication after
that or a recognized authentication-expiry error triggers a
sign-in prompt once per affected account until it reconnects successfully.
Choose Yes to open the browser for the first affected account; use Connect for
other accounts or if you dismissed the prompt. Network/unknown errors display
Refresh failed and retry on the next interval. Failed refreshes clear the quota
figures and mark the last check stale. They never leave old values looking live.

The Codex account interface does not supply a billing renewal date. Select a row
and use **Set renewal date** to enter the date from your billing page. It is
labelled manual, never automatically rolled forward, and marked past when due.

Account sessions are managed by Codex in separate homes under
`%LOCALAPPDATA%\CodexUsageDashboard\account-1` through `account-3`.
This does not replace the normal Codex app/CLI login. Those folders contain
sensitive login credentials: do not share or copy them into a repository.
Settings contain account emails and manually entered dates. No quota snapshots
are saved. Reconnecting a row to a different email preserves its renewal date
but marks it **verify** so you can confirm it belongs to the new account.
Open windows reread saved renewal dates every five seconds, so changing a date
in one window also updates another without losing it on the next launch.

The launcher uses `pyw -3`, so it does not require a machine-specific Python
path. To run with a different Python installation, edit `Launch.vbs` or run
`python dashboard.py` from a terminal. The dashboard needs an internet
connection to retrieve current account usage.

Protocol reference: https://learn.chatgpt.com/docs/app-server
