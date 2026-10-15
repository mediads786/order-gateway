# One-server AWS deployment (Ubuntu 24.04, mock ERP)

This runs the existing API, worker, PostgreSQL 16, mock ERP, and Caddy on one EC2
instance with at least 2 GB RAM and a 2 GiB swap file. Odoo stays off. Shopify sends
signed `orders/create` webhooks to public HTTPS. Only Caddy publishes container
ports: TCP 80 and 443. PostgreSQL uses the existing `postgres_data` named volume.

All commands below use placeholders. Substitute values in your terminal, never in
tracked files. Do not capture bootstrap output in logs or paste it into a ticket.
The bootstrap script creates real credentials only in the target server's protected
`.env`; generated values are never printed. Both bootstrap and local verification
use `deploy/credentials.py` to generate and validate the same credential bytes.

## a. Create the AWS account and choose Free plan

1. Open [AWS Free Tier](https://aws.amazon.com/free/), choose **Create a Free Account**,
   enter your email and account name, verify email, and set a unique root password.
2. Complete contact, payment verification, and identity verification as prompted.
   Choose **Free plan** when asked for an account plan. A support-plan choice is
   separate from the account plan: select the free/basic support option if offered.
3. Sign in, enable MFA on the root account, then create an administrative daily-work
   identity following the console's IAM setup guidance. Use MFA for that identity too.
4. Pick one nearby AWS Region using the top-right Region selector and use it for
   every EC2 step below.

The Free plan ends **six months after signup or when credits run out, whichever
comes first**. It cannot be extended past six months. You can close it or upgrade
to Paid plan; paid usage can incur real charges. On expiration AWS automatically
closes the Free plan account and access to resources is lost. AWS documents a
90-day retention window during which upgrading may recover access, followed by
permanent deletion. Export backups **before** expiration; do not rely on recovery.
See [AWS account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).

## b. Set a billing budget alert first

1. Open **Billing and Cost Management** from the console search box.
2. Open **Budgets** → **Create budget** → **Use a template (simplified)**.
3. Choose **Zero spend budget** for an early warning; give it a recognizable name
   and enter your monitored email. Choose **Create budget**.
4. If you prefer a small monthly limit, use **Customize (advanced)** → **Cost budget**,
   set a low amount, and add actual and forecast email thresholds. Review whether
   credits are included in the cost filter: a net-cost alert can remain zero while
   credits are being consumed. Monitor **Credits** and the Free Tier dashboard as well.
5. Verify the budget and email recipients are listed before launching resources.

An alert is not a spending cap and can arrive after usage. Check your balance and
end date during every demo session. See [AWS budget creation](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html).

## c. Create a key pair, security group, and instance

1. Search **EC2** → **Key Pairs** (under Network & Security) → **Create key pair**.
   Name it `order-gateway-demo`. Choose RSA and `.pem` for OpenSSH, then create it.
   Save the downloaded private key in your password manager or a protected folder;
   AWS cannot download it for you again. Never copy it into this repository.
2. Open **Security Groups** → **Create security group**. Name it
   `order-gateway-demo`, select the default VPC, and add inbound rules:

   | Type | Port | Source |
   |---|---:|---|
   | SSH | 22 | **My IP**, a single current address ending in `/32` |
   | HTTP | 80 | Anywhere-IPv4 |
   | HTTPS | 443 | Anywhere-IPv4 |

   Leave normal outbound access enabled for package downloads, DNS, HTTPS, and
   certificate issuance. Do not add database, API, mock ERP, or Odoo ports. If your
   home IP changes, update only the SSH source. The host firewall permits SSH on 22;
   **the AWS security group is what restricts SSH to your current IP**.
3. Open **Instances** → **Launch instances**. Enter `order-gateway-demo` as its name.
4. Select **Ubuntu Server 24.04 LTS**, official Canonical publisher. Avoid paid
   Marketplace images. Prefer the x86_64 image for this first deployment.
5. Select **only an instance type that this launch wizard explicitly marks Free-plan
   eligible AND whose details show at least 2 GiB RAM**. For example, `t3.small` is
   a possible x86_64 choice only if the wizard marks it eligible for your account.
   Do not select a 1 GiB micro instance. If no eligible 2 GiB choice appears, stop
   and check Region/account eligibility rather than launching a paid-only choice.
   ARM `t4g.small` requires the ARM Ubuntu image and compatible image builds; use
   x86_64 unless you have tested that path. Eligibility does not mean unlimited
   zero-cost running: usage consumes Free plan credits.
6. Select your key pair. Under **Network settings** → **Edit**, use the default VPC,
   a public subnet, enable an auto-assigned public IPv4 address, and choose the
   security group created above. Do not accept a new SSH-from-anywhere rule.
7. Under **Storage**, choose an encrypted gp3 root disk, typically 20 GiB, after
   reviewing its credit cost. Keep **Delete on termination** enabled unless you
   specifically need to retain it. Under advanced metadata options require IMDSv2.
8. Review the summary and estimated cost, then choose **Launch instance**. Wait for
   **Running** and both status checks to pass. Copy the public IPv4 address for
   use in the terminal/DNS console only.

AWS eligibility changes with account date, Region, and availability. The launch
wizard is the decision point. See [EC2 Free Tier](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html).

## d. Choose a hostname

**Option 1 — your own domain.** At your DNS provider, add an **A record** for a
subdomain such as `orders.example.com` to the instance's public IPv4 address.
Use that hostname as `DOMAIN`. Remove stale AAAA records unless you have actually
configured working IPv6 routing and inbound IPv6 firewall rules. Wait for public
DNS to resolve before bootstrap. Caddy will request and renew a public certificate.

**Option 2 — no domain purchase.** Use `<ip-with-dashes>.sslip.io`, replacing each dot
of the instance's public IPv4 with a dash. Pass the resulting hostname as `DOMAIN`.
[sslip.io](https://sslip.io/) provides DNS resolution to the encoded address; it does
not provide this server's certificate. Caddy still needs DNS resolution and public
80/443 access to obtain one. This is a shared third-party DNS service without your
own domain control or an availability guarantee. DNS-rebinding filters, service
outages, CA policy and shared-domain rate limits can prevent resolution or issuance.
Test on the real instance before promising the demo. IP changes require a new
hostname/certificate and an updated Shopify webhook URL. Prefer your own domain
for a lasting deployment. This kit does not guarantee sslip.io certificate issuance.

## e. Allocate and associate an Elastic IP

1. In EC2, open **Elastic IPs** → **Allocate Elastic IP address**. Review pricing,
   choose Amazon's address pool in your Region, then **Allocate**.
2. Select it → **Actions** → **Associate Elastic IP address** → **Instance**. Select
   the gateway instance and its primary private address, then **Associate**.
3. Update your A record or sslip.io hostname to use this new public address before
   continuing. The auto-assigned address is replaced.

Elastic IP keeps the public address stable across stop/start. **Public IPv4 has an
hourly charge, including Elastic IP, which draws on eligible credits.** Retained
Elastic IPs can continue costing money while the instance is stopped. Check
[VPC pricing](https://aws.amazon.com/vpc/pricing/) for current rates. To avoid that
ongoing charge you can release the Elastic IP when done, but a future demo then
needs new DNS and possibly a new Shopify URL. Do not release it while DNS/webhooks
still depend on it.

## f. SSH, bootstrap, and smoke test

The deployment commit is intentionally **not pushed** by this task. A fresh public
clone cannot contain an unpublished commit. Until you publish it yourself, use
the copy step below to transfer the new `deploy/` directory. Bootstrap recognizes
an existing checkout, or clones the public repository if its install directory
does not exist. Never upload your local development `.env`.

1. In **Instances**, select the instance → **Connect** → **SSH client**. Copy the
   connection command, using `ubuntu` (not root) and your `.pem` file. In a Linux/macOS
   terminal set `chmod 400 /PATH/TO/KEY.pem`. On Windows OpenSSH, restrict its file
   permissions to your Windows account using the file's Security settings.

   ```bash
   ssh -i /PATH/TO/KEY.pem ubuntu@INSTANCE_PUBLIC_HOST
   ```

   Verify the SSH host key using AWS's instance console/system output or a trusted
   channel before accepting it. Never put the private key on the instance.

2. In that SSH session, prepare the checkout and protected installation:

   ```bash
   sudo apt-get update
   sudo apt-get install -y git
   sudo git clone https://github.com/mediads786/order-gateway.git /opt/order-gateway
   sudo chmod 700 /opt/order-gateway
   ```

3. If the kit is not yet published, from a **local** terminal in the reviewed
   repository copy only deployment files, then in SSH move them into place:

   ```bash
   # LOCAL terminal; no .env or secrets are transferred.
   scp -i /PATH/TO/KEY.pem -r deploy ubuntu@INSTANCE_PUBLIC_HOST:~/gateway-deploy
   # SSH session:
   sudo cp -a ~/gateway-deploy /opt/order-gateway/deploy
   sudo chown -R root:root /opt/order-gateway/deploy
   ```

   If the public repo later includes the reviewed kit, skip this transfer. The
   bootstrap itself can also be copied alone and invoked with a fresh installation
   directory once that public repo contains the kit.

4. Confirm DNS points to this instance and run:

   ```bash
   sudo bash /opt/order-gateway/deploy/ec2-bootstrap.sh YOUR_HOSTNAME
   ```

   Save the protected `.env` credentials privately in a password manager. Do not use terminal
   recording, `tee`, `bash -x`, or screen sharing. Bootstrap installs Docker from
   its official Ubuntu repository, creates swap, enables UFW, generates `.env`
   with mode 600, starts all five core services, registers API key hashes, and
   installs the nightly backup timer. It preserves an existing `.env` and never
   prints or regenerates existing secrets. A failed first start can be retried
   with the same command. If an existing swap or firewall setup is unexpected,
   inspect it rather than deleting it blindly.

5. Run the proof (it waits for HTTPS and creates one synthetic order):

   ```bash
   sudo bash /opt/order-gateway/deploy/smoke_test.sh
   ```

   Expect PASS for credential consistency, HTTPS/health, invalid Shopify HMAC
   rejection, missing/wrong Basic Auth rejection, valid Basic Auth, the separate
   `ADMIN_TOKEN` login, secure session cookies, and a signed order reaching `CONFIRMED`.
   A failed check prints its name without credentials or response bodies and exits non-zero.
   In a browser, open `https://YOUR_HOSTNAME/admin`. Enter username `gateway` and
   the generated basic-auth password, then enter the separate `ADMIN_TOKEN` in
   the app's login form. API clients use their role-specific `X-API-Key`.

6. Verify the backup schedule and take the first backup:

   ```bash
   sudo systemctl list-timers order-gateway-backup.timer
   sudo bash /opt/order-gateway/deploy/backup.sh
   ```

   Dumps are protected at `/opt/order-gateway/deploy/backups/`, with the last
   seven successful local dumps retained. Nightly means **02:15 UTC** plus up to
   five minutes; a missed run catches up after the server starts. Dump failure
   leaves no completed corrupt file. It does not delete older files on S3.
   Local backups consume disk and do not survive instance/disk loss by themselves.

Optional S3: use an instance IAM role with least-privilege `s3:PutObject` access
to `arn:aws:s3:::YOUR_BUCKET/order-gateway/*`, never root access keys. Create a
private bucket with Block Public Access enabled; review storage/transfer costs
and add a lifecycle policy for remote retention. Install the AWS CLI v2 using
[AWS's official instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
Set `S3_BACKUP_BUCKET` to its bucket name using `sudoedit /opt/order-gateway/.env`.
The backup uses server-side AES256 encryption; KMS-only bucket policies require
an explicit compatible change. If upload fails, the script fails and retains the
local dump. Check `sudo journalctl -u order-gateway-backup.service` after a test.

## g. Register Shopify and place a real dev-store order

1. In Shopify Admin open **Settings** → **Notifications** → **Webhooks** →
   **Create webhook** (labels may vary by Shopify Admin version). Choose
   **Order creation**, JSON format, and URL:
   `https://YOUR_HOSTNAME/webhooks/shopify/orders-create`. Choose a supported current
   webhook API version in Shopify. The app handles `orders/create` only.
2. Obtain Shopify's actual signing secret for this webhook configuration. It is
   supplied by Shopify; a randomly generated bootstrap secret will only validate
   the synthetic smoke test until you replace it. For Admin-created webhooks use
   the signing secret displayed in that webhook settings section. For an
   app-managed subscription use the app's client secret as documented by Shopify.
3. In SSH run `sudoedit /opt/order-gateway/.env` and replace only
   `SHOPIFY_WEBHOOK_SECRET` with the real value. Keep the single quotes and mode
   600. Do not paste the secret into a shell command, screenshot, or tracked file.
   Recreate the API and worker with the production configuration:

   ```bash
   sudo bash -c 'cd /opt/order-gateway; source deploy/common.sh; load_env; compose up -d --force-recreate --wait db api mock_erp worker caddy'
   sudo bash /opt/order-gateway/deploy/smoke_test.sh
   ```

4. Use Shopify's test delivery and inspect its delivery status. Then place one
   real **development-store** order through the store checkout using Shopify's
   test payment method. Use a product/variant with a nonempty SKU, a customer name,
   valid email or phone, a supported currency, positive quantity, and price.
   No real payment is needed for this dev-store proof.
5. Open the gateway admin page and confirm the order's source is Shopify, its
   reference matches the order, and its status becomes `CONFIRMED`. A 200 webhook
   response means intake was acknowledged; malformed/unmappable orders can still
   be stored as `REJECTED`. Inspect the order status, not just HTTP delivery.

See [Shopify webhook subscription guidance](https://shopify.dev/docs/apps/build/webhooks/subscribe)
and [HTTPS webhook HMAC validation](https://shopify.dev/docs/apps/build/webhooks/subscribe/https).
The smoke test signs compact UTF-8 JSON using base64(HMAC-SHA256(secret, raw bytes)),
matching `scripts/e2e_shopify_odoo.py`. Production traffic must come from Shopify
with the corresponding real secret. Do not change the app's signature validation.

## h. Stop/start, update, and save credits

When not demonstrating, disable/pause the Shopify subscription first so retries
do not exhaust delivery attempts. Take and export a backup, then use **EC2** →
**Instances** → select this instance → **Instance state** → **Stop instance**.
Stopping containers alone does not stop EC2 compute charges. Stopped EC2 instances
retain billable EBS storage, snapshots, and possibly Elastic IP/IPv4 charges.
Release unneeded Elastic IPs and remove unneeded snapshots/S3 backups deliberately.
Do not terminate an instance until backups and secrets have been exported.

To resume: **Start instance**, wait for status checks, confirm the Elastic IP is
still associated (or update DNS if using a new public address), SSH in, and run:

```bash
sudo bash -c 'cd /opt/order-gateway; source deploy/common.sh; load_env; wait_health'
sudo bash /opt/order-gateway/deploy/smoke_test.sh
```

Docker starts at boot and the containers use `restart: unless-stopped`. If you
explicitly stopped containers previously, the `wait_health` command starts them.
Re-enable Shopify delivery only after the HTTPS proof passes. Mock ERP state is
in-memory and is lost on restart; PostgreSQL gateway records persist.

For updates, use `sudo bash /opt/order-gateway/deploy/update.sh`. It requires a
clean Git checkout, makes a backup, pulls with fast-forward only, rebuilds, stops
the old worker while the API migrates, restarts, and runs the signed proof.
Do not mix a development base-only Compose invocation into this installation.
If the kit was transferred while unpublished, commit/publish it yourself before
using this clean-checkout update flow. No deployment command here pushes Git.
If migrations or smoke checks fail, leave Shopify paused and investigate. A Git
rollback alone does not reverse schema migrations: use the matching database
backup and app revision following [restore instructions](../deploy/restore.md).

Before credits or the six-month limit ends, take a fresh backup while the server
is running and export it. These commands use placeholders; use the basename from
`sudo ls /opt/order-gateway/deploy/backups/`:

```bash
# SSH session: place one protected dump where the ubuntu SSH user can retrieve it.
sudo install -o ubuntu -g ubuntu -m 600 /opt/order-gateway/deploy/backups/BACKUP_FILENAME.sql.gz /home/ubuntu/BACKUP_FILENAME.sql.gz
# LOCAL terminal:
scp -i /PATH/TO/KEY.pem ubuntu@INSTANCE_PUBLIC_HOST:~/BACKUP_FILENAME.sql.gz /SAFE/LOCAL/BACKUP_FOLDER/
# SSH session, after verifying the local copy:
rm -- ~/BACKUP_FILENAME.sql.gz
```

Keep credentials in a password manager and record `sudo git -C /opt/order-gateway
rev-parse HEAD` alongside the dump. Test restore before relying on backups. To
continue beyond Free plan, deliberately upgrade and budget for paid charges;
stopping does not extend the six-month period. To finish entirely, delete resources
in all Regions and close the account from Account settings after exporting data.

## i. Troubleshooting

Run operational commands from `/opt/order-gateway` with `sudo`, or enter a root
shell temporarily (`sudo -i`) and load `source deploy/common.sh; load_env` first.
Do not publish `.env`, full `docker compose config`, Docker inspection output, or
unredacted logs: environment/config output can reveal credentials.

| Symptom | Check and action |
|---|---|
| Certificate not issuing | Confirm public A record/sslip hostname resolves to the associated Elastic IP; remove bad AAAA records; allow TCP 80/443 in both security group and UFW; check outbound DNS/HTTPS, CAA restrictions, CA rate limits, and `compose logs --tail 50 caddy` locally. Wait for DNS/CA retry. Localhost certificates are private, not valid for Shopify. |
| Caddy returns 502 | Run `compose ps`; inspect API health, `compose logs --tail 50 api`, migrations and database readiness. Proxy target is `api:8000`, not a host-published port. Check disk/memory and rebuild with the update procedure. |
| Shopify webhook returns 401 | Compare the protected signing secret with Shopify's webhook configuration; recreate API after changes. HMAC must sign the exact original raw bytes with SHA256 and base64. API keys and basic-auth passwords are not Shopify signing secrets. Basic auth applies only to `/admin*`. |
| Generated Basic Auth credentials return 401 | Run `source deploy/common.sh; load_env; python3 deploy/credentials.py check` in a private operator shell. The shared generator hashes the exact password bytes with Caddy-compatible bcrypt; do not hash terminal input with `read` or include CR/LF. Keep the raw hash single-quoted in `.env`: Compose substitutes `${CADDY_BASIC_AUTH_HASH}` once and passes its dollars unchanged to Caddy. Do not change those dollars to `$$`; escaping is needed for a hash written literally into Compose YAML, not this variable value. `compose config` can display doubled dollars for serialization, so it is not a byte-for-byte view of the container environment. Never print either output to troubleshoot credentials. |
| Disk space low | Check `df -h`, `sudo du -sh /var/lib/docker /opt/order-gateway/deploy/backups`, and `docker system df`. Container logs rotate at 10 MB × 3 per service; PostgreSQL, images, system logs and dumps still grow. After a successful backup, `docker image prune` can remove unused dangling images. Expand EBS/filesystem or export/remove old dumps deliberately. Never prune volumes or delete PostgreSQL files. |
| Bootstrap fails with existing configuration | Existing `.env` and secrets are preserved. Ensure DOMAIN argument matches, `.env` mode is 600, and firewall/swap checks pass. Re-run after fixing the reported condition. Do not regenerate the DB password for an existing volume. |
| Backup timer fails | Inspect `systemctl status order-gateway-backup.service` and its journal locally; check disk space, DB health, optional AWS CLI/role/bucket access. A timer cannot run while EC2 is stopped. |

Worker health verifies its process identity and database/mock ERP connectivity; it
does not prove the worker loop is making progress. The signed smoke test provides
that end-to-end check. Log rotation bounds container log files, not all disk usage:
monitor free space and backup/database growth.

## Do not do

- Do not open port 22 to the world; use **My IP** in the security group.
- Do not use root login for daily work; SSH as `ubuntu`, use a daily AWS identity,
  and use `sudo` only for protected administrative commands.
- Do not create access keys on the AWS root account; use an EC2 IAM role for S3.
- Do not commit `.env`, private keys, credential dumps, real secrets, or server IPs.
- Do not enable the `odoo` profile, expose internal service ports, use
  `docker compose down -v`, or run `docker system prune --volumes` on this server.
- Do not regard the mock ERP as durable business storage or this single instance
  as highly available. Test dev-store orders only; a server or disk loss causes downtime.

## Local verification recipe (isolated disposable checkout)

Use a Linux/Docker-capable environment with Bash, Python 3, and Compose >=2.24.4.
Do not overwrite an existing development `.env` or use an existing production volume.
In a disposable checkout, install Python bcrypt (on Ubuntu, `python3-bcrypt`).
Use the exact bootstrap generator below, never a hand-made password/hash or a
shell `read`/`caddy hash-password` pipeline: Windows text-mode stdin can append a
carriage return to the password being hashed. The generator writes LF-only,
single-quoted `.env` entries with mode 600, verifies the password/hash pair, and
refuses to overwrite an existing `.env`; it never prints secrets.
The unique local project name keeps disposable volumes separate from development.
Run these commands only in that disposable checkout, with 80/443 available.

```bash
python3 deploy/credentials.py generate localhost --project order-gateway-local-check
source deploy/common.sh
load_env
python3 deploy/credentials.py check
check_ports
compose up -d --build --wait --wait-timeout 180 db api mock_erp worker caddy
# Register generated API keys after migrations, exactly as bootstrap does.
python3 -c 'import json,os; print(json.dumps({n:os.environ[n] for n in ("DEPLOY_OPERATOR_API_KEY","DEPLOY_APPROVER_API_KEY","DEPLOY_ADMIN_API_KEY")}))' | compose exec -T api python -c "$(cat deploy/init-api-keys.py)"
bash deploy/smoke_test.sh --local-tls
# DISPOSABLE LOCAL PROJECT ONLY: delete its containers and named volumes.
compose down --volumes --remove-orphans
rm -- .env
```

The previous local 401 had a different password/hash pair because its Windows
harness hashed a trailing CR; the original bootstrap bcrypt generation and
single-quoted Compose variable transport both authenticate correctly at runtime.
The shared generator and credential preflight prevent that divergent test path.

`--local-tls` skips trust validation only for `localhost` because Caddy uses its
local CA. Never use it for a public server. It still tests HTTPS, authentication,
HMAC rejection, and a confirmed order. The repository pytest suite is offline with
respect to Shopify/Odoo but requires real PostgreSQL and the mock ERP on its base
Compose localhost ports. Run it separately against a dedicated `_test` database
with `python -m pytest -q -m 'not live_odoo'`; do not run it against production.
The live-Odoo tests are excluded and Odoo remains off.
