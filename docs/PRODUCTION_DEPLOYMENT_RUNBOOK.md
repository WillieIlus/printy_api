# Production Deployment Runbook

Targets:
- Frontend: `https://printy.ke`
- API: `https://api.printy.ke`

This runbook documents the deployment flow only. It does not execute deployment.

## Preconditions

- production env values are prepared
- Daraja production credentials are verified
- `MPESA_CALLBACK_URL` is set to `https://api.printy.ke/api/payments/mpesa/callback/`
- both repos point at the intended production branch

## Backend deployment

### One command (preferred)

`scripts/deploy.sh` encodes the manual sequence below into a single fail-fast command. It installs
dependencies, runs `check --deploy`, applies migrations, refreshes the Sites framework and static
files, restarts gunicorn, and then probes the live API. On failure it prints the failing step, the
gunicorn and nginx logs, and the command to roll back to the previous commit.

Install it once per server (it lives in the repo, so it is reviewed and version-controlled):

```bash
sudo su - <app-user>
cd ~/printy_api
git pull origin main
chmod +x scripts/deploy.sh          # ensure the exec bit survived the checkout
ln -sf "$PWD/scripts/deploy.sh" ~/deploy_printy.sh
```

Then, for every deploy:

```bash
./deploy_printy.sh
```

Useful overrides:

```bash
PRINTY_MIGRATE_PLAN=1 ./deploy_printy.sh   # list pending migrations, change nothing
PRINTY_RELOAD_NGINX=1 ./deploy_printy.sh   # also reload nginx (only after a config change)
PRINTY_SKIP_PIP=1    ./deploy_printy.sh   # skip dependency install
```

It refuses to run if `.env` is missing or empty, and takes an exclusive `flock` so two deploys cannot
race each other through `git pull`, `migrate` and the gunicorn restart. A transcript is appended to
`deploy.log` in the app directory (`*.log` is gitignored).

### Manual sequence

The equivalent commands, if you need to run them by hand. Note the two `create_*_user` calls are
one-time setup and are intentionally *not* part of the automated script:

```bash
ssh <droplet>
sudo su - <app-user>
cd ~/printy_api
git pull origin main
source env/bin/activate
pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate
python manage.py configure_site
python manage.py collectstatic --noinput

# Create the system accounts once, then pin their IDs in .env so the
# fallback manager and house broker are stable across deploys.
python manage.py create_printy_manager_user
python manage.py create_printy_house_broker_user
# -> copy PRINTY_MANAGER_USER_ID / PRINTY_HOUSE_BROKER_USER_ID into .env
#    (examples: PRINTY_MANAGER_USER_ID=1, PRINTY_HOUSE_BROKER_USER_ID=2)
sudo systemctl restart gunicorn
sudo systemctl restart nginx
sudo systemctl status gunicorn --no-pager
sudo systemctl status nginx --no-pager
journalctl -u gunicorn -n 80 --no-pager
sudo tail -n 80 /var/log/nginx/error.log
```

`create_printy_manager_user` and `create_printy_house_broker_user` are one-time setup and are
intentionally not part of the automated script; running them on every deploy is unnecessary churn.

## Frontend deployment

If the frontend lives on the same droplet:

```bash
cd ~/printy_ui
git pull origin main
cp .env.example .env.production  # then edit NUXT_PUBLIC_* if different from defaults
yarn install --frozen-lockfile
yarn typecheck
yarn build
```

Then deploy according to the actual hosting target:
- if running Nuxt SSR on the droplet, ensure the process manager serves `.output/server/index.mjs`
- if building static assets only, confirm the hosting target expects static output and not SSR
- if no lockfile exists in the real deployment checkout, fall back to plain `yarn install`

## Rollback notes

- rollback by checking out the previous known-good commit in each repo
- rebuild/restart the affected service
- never rollback the DB schema blindly; review migrations first
- if a migration is already applied, prefer forward-fix over destructive reversal unless you have a verified rollback plan

## Common failures and fixes

### `python manage.py check --deploy` fails

- missing secure settings or env values
- invalid `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, or `CORS_ALLOWED_ORIGINS`
- missing `SECRET_KEY`

### frontend still calls localhost

- verify `NUXT_PUBLIC_API_BASE_URL=https://api.printy.ke/api`
- grep built artifacts or runtime config for `127.0.0.1` and `localhost`
- confirm the deployed frontend picked up the correct env at build time

### Nuxt SSR/static target is wrong

- if `.output/public` is missing, confirm the build actually completed
- if `.output/server/index.mjs` is missing, confirm the target build is SSR-capable and not a partial artifact
- if the host expects SSR, make sure it runs `.output/server/index.mjs`
- if the host expects static export, verify you are not using an SSR-only deploy path
- confirm `nuxt.config.ts` has the intended Nitro preset/runtime behavior for the target

### API returns CORS errors

- verify `CORS_ALLOWED_ORIGINS` includes `https://printy.ke` and `https://www.printy.ke`
- verify requests are going to `https://api.printy.ke/api`
- verify the browser is not still using an older frontend bundle with localhost config

### media files fail

- verify `MEDIA_URL` and backend/media serving strategy on the droplet
- confirm nginx routes `/media/` correctly to the backend or storage target
- confirm uploaded files exist on disk or in the configured storage backend

### activation email links are wrong

- verify `FRONTEND_URL=https://printy.ke`
- run `python manage.py configure_site` after `SITE_DOMAIN` changes
- confirm emails now render `/auth/confirm-email` and `/auth/reset-password` on `printy.ke`

### M-Pesa callback fails

- verify Daraja portal callback URL matches `https://api.printy.ke/api/payments/mpesa/callback/`
- verify `MPESA_ENV`, shortcode, passkey, consumer key, and consumer secret all belong to the same environment
- check nginx/gunicorn logs for callback requests and response codes
- confirm no firewall or proxy issue blocks Safaricom reachability
