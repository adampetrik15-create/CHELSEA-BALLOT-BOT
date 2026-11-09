# Chelsea ballot bot -> Discord

Bot sleduje ticket application window-y Chelsea (domáce zápasy Premier League) a posiela ich na Discord vo vzhľade „PL BALLOT RADAR": zápas, kedy sa otvára a zatvára, stav, dátum zápasu, kategória a upozornenie na podmienky (členstvo, loyalty body).

Zdroje: oficiálny web chelseafc.com (články „Premier League ticket news … at home") a X účet @OK_Chelsea_1905 (cez X API).

## Secrets (Settings → Secrets and variables → Actions)
- `DISCORD_WEBHOOK_URL` – webhook (ďalšie: `DISCORD_WEBHOOK_URL_<hocičo>`, v jednej hodnote môže byť aj viac adries)
- `X_BEARER_TOKEN` – X API token (rovnaký ako pri Arsenal botovi)
- `AVATAR_URL` – voliteľné, verejná https adresa profilovky bota
- `RSS_FEED_URL` – voliteľné, rss.app feed účtu (netreba)

## Spúšťanie
Actions → „Chelsea ballot bot" → Run workflow (`test_message = true` pošle skúšobnú správu). Pravidelne ho spúšťa cron-job.org (POST na `.../actions/workflows/ballots.yml/dispatches`, body `{"ref":"main"}`).

Výsledok posledného behu je v `last_run.txt`. Ručne pridané články: `urls.txt`.
