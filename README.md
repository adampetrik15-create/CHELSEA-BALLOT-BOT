# Chelsea ballot bot -> Discord

Bot sleduje ticket application window-y Chelsea (domáce zápasy Premier League) a posiela ich na Discord vo vzhľade „PL BALLOT RADAR": zápas, kedy sa otvára a zatvára, stav, dátum zápasu, kategória a upozornenie na podmienky (členstvo, loyalty body).

Zdroje: oficiálny web chelseafc.com (články „Premier League ticket news … at home") a X účet @OK_Chelsea_1905 (cez X API).

## Secrets (Settings → Secrets and variables → Actions)
- `DISCORD_WEBHOOK_URL` – webhook (ďalšie: `DISCORD_WEBHOOK_URL_<hocičo>`, v jednej hodnote môže byť aj viac adries)
- `X_BEARER_TOKEN` – X API token (rovnaký ako pri Arsenal botovi)
- `AVATAR_URL` – voliteľné, verejná https adresa profilovky bota
- `RSS_FEED_URL` – voliteľné, rss.app feed účtu (netreba)

## Daily radar
Raz denne (prvý beh od 08:00 slovenského času) pošle najbližší zápas a otvorené alebo pripravované ticket application window-y. Zápasy berie z neoficiálneho ESPN API.

Voliteľné premenné (Settings → Secrets and variables → Actions → Variables):
- `RADAR_HOUR` – hodina radaru (predvolene 8); pri inej hodine uprav aj druhý cron vo workflow, inak čas určí najbližší beh
- `DAILY_RADAR` – `false` radar vypne
- `GAP_ALERT_MINUTES` – po koľkých minútach bez behu poslať upozornenie (predvolene 180, najviac raz za 24 h)

Okamžité odoslanie: Run workflow s `send_radar = true`.

## Spúšťanie
Actions → „Chelsea ballot bot" → Run workflow (`test_message = true` pošle skúšobnú správu). Pravidelne ho spúšťa cron-job.org (POST na `.../actions/workflows/ballots.yml/dispatches`, body `{"ref":"main"}`).

Výsledok posledného behu je v `last_run.txt`. Ručne pridané články: `urls.txt`.
