# Sharpz Basketball — getbasketballiq.com

Static site. No build step, no framework, no dependencies.

    index.html        the board (tabs, tables, status strip)
    research.html     how the board is built — three findings
    minutes.html      article: minutes can be forecast
    misses.html       article: five ways we looked and found nothing
    data/status.json  the only file that changes daily
    publish.sh        regenerates status.json and pushes

## Deploying

1. Push these files to the repo root.
2. Settings -> Pages -> Source: `main`, folder `/ (root)`.
3. Settings -> Pages -> Custom domain: `getbasketballiq.com`.
   If another repo currently holds that domain, clear it there first;
   GitHub allows one repo per domain. DNS does not change.
4. Tick "Enforce HTTPS" once the certificate is issued.

## Daily

    ./publish.sh

Run it after `grade.py`. It rewrites `data/status.json` from
`~/kalshi_data/runs.jsonl` and `picks.jsonl`, then commits and pushes.
The page fetches that file at load, so the HTML never needs rebuilding.

If the fetch fails the status strip falls back to sensible defaults
rather than rendering blank.

## Changing the tables

The player and team rows live in the `<script>` block at the bottom of
`index.html`, as the arrays `PRA`, `PR`, `COL`, `MID` and `EUR`. Edit
them in place; the tables render from those arrays.
