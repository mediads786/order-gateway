# Portfolio asset checklist

## Screenshots

1600x900 browser window, no bookmarks bar, no tokens, keys or personal data visible.

- [ ] Orders list with a mix of `CONFIRMED`, `RETRYING` and `FAILED_DEAD` (status counts visible)
- [ ] Order detail of a retried order with the **full audit trail** (hero image, shows resilience at a glance)
- [ ] `FAILED_DEAD` order with the Retry button visible
- [ ] Architecture diagram (render the README Mermaid diagram)
- [ ] Terminal: `pytest` summary line (take it after Module 5 so the count is current)
- [ ] Terminal: `docker compose ps` with all services healthy
- [ ] Optional: Odoo sale order created by the gateway, and the stock move created by a shipment

## GIF or short video

Free tool: ScreenToGif. 30 seconds or less, under 8 MB, 1280 px wide.

- [ ] Scene: send order, ERP set to fail, `RETRYING` with attempts climbing, ERP recovered, order `CONFIRMED`
- [ ] Optional second clip (30 seconds): `FAILED_DEAD`, click Retry, `CONFIRMED`

## Where to use them

- [ ] GitHub README: hero GIF at the top, architecture diagram below it
- [ ] Portfolio page and case study (problem, approach, result, what I would do next)
- [ ] Fiverr and Upwork gig images and descriptions (same visual style as the existing gig covers)
- [ ] LinkedIn post and Featured section: GIF plus one paragraph plus repo link

## Repo hygiene before publishing

- [ ] `.env` is not tracked: `git ls-files | Select-String "(^|/)\.env$"` prints nothing
- [ ] No secret in history: `git log -p | Select-String -Pattern "ODOO_API_KEY=.+|ADMIN_TOKEN=.+|SECRET=.+"` shows placeholders only
- [ ] README states what is verified and what is not
