---
name: thridify-release
description: Ship a Thridify production release from develop to master (thridify-be + thridify-fe, plus any other Thridify repo with unreleased develop commits). Use when the user asks to release Thridify to prod, "plan a prod release", "run the release script", "cut 12.x", or to write release PRs / release notes / the #release-thridify checklist. Covers scoping, 3-part release PR notes (release, technical, QA), risk review for customer data loss, Slack posts before and after, DB backup via thridify-prod-release.sh, merge order, prod Cloud Build approval, tags + GitHub releases, and safe prod QA on admin.thridify.com.
---

# Thridify production release

One release = develop → master on every Thridify repo that has unreleased commits (in practice `aapastech/thridify-be` and `aapastech/thridify-fe`). Backend always merges and deploys first.

Do every step below in order. Keep the user's task list updated. Every outward step (PR, Slack, merge, build approval, tag) is covered by the user's release request; anything outside this list (emails to customers, backfills, flag flips, paid AI calls, deleting anything) is NOT, and needs its own explicit yes.

## 0. Ground rules (non-negotiable)

- **No customer data loss.** Before merging, read every included PR for migrations, backfills, deletes, index builds, cron/email senders, env/secret changes and destructive admin actions. If any exists, stop and get the user's explicit go-ahead with the rollback plan written down.
- **Never type passwords, OTPs or tokens.** The DB dump prompts the user for the Atlas password in a Terminal tab; wait for them.
- **Prod QA only on Thridify-owned test accounts** (e.g. it@thridify.com / demo stores). Never publish, delete, regenerate or acknowledge on a customer account. No paid AI generations in QA unless the user approves the spend (ai-cost-gate).
- Never cancel a Cloud Build that has reached its deploy step (409 collisions; dev/prod silently serves the old commit).
- Public copy (release notes, Slack) goes through the humanizer rules: plain, specific, no promotional words, no em-dash beats.
- Use worktrees for any git write; push with explicit refspecs (`git push origin HEAD:refs/heads/develop`). Never leave the user's shared checkout on another branch.

## 1. Scope the release

```bash
export PATH=$HOME/.local/bin:$PATH
for r in $(gh repo list aapastech --limit 100 --json name --jq '.[].name' | grep -i thridify); do
  gh api repos/aapastech/$r/compare/master...develop --jq "\"$r ahead \(.ahead_by) behind \(.behind_by)\"" 2>/dev/null
done
gh api repos/aapastech/thridify-be/compare/master...develop --jq '.commits[] | "\(.sha[0:8]) \(.commit.message|split("\n")[0])"'
gh api "repos/aapastech/thridify-be/compare/master...develop" --jq '.files[] | "\(.status) \(.filename)"' | grep -iE 'migrat|cron|schedul|cloudbuild|email|backfill'
```

- Versions: `gh api "repos/aapastech/<repo>/contents/package.json?ref=develop" --jq .content | base64 -d | grep -m1 '"version"'` (quote the URL in zsh). The release version is develop's version; fe and be may differ.
- Read each constituent PR body (`gh pr view <n> --json body`): it states deploy notes, flags, migrations, what was verified. Note anything "not verified in a browser" for QA.
- wonderlyar-* repos are a separate product; not part of a Thridify release.

## 2. Release PRs (develop → master), three notes each

`gh pr create --repo aapastech/<repo> --base master --head develop --title "Release <ver>: <short list>" --body-file <file>`

Body structure (copy fe#677 / be#445 / fe#641):
1. Header: version from → to, number of PRs, **merge order** line (be first; fe "merge after the backend release is live"), a table `PR | What | Live for customers?`.
2. `## 1. Release notes (for customers)`: written to be shared with brands as-is; bold group titles + bullets; say what changes for them; mention dormant features only as "nothing changes yet".
3. `## 2. Technical notes (for the team)`: per PR, files/mechanics; `### Deploy` (migrations, env, secrets, approval, viewer cache note); `### Rollback`.
4. `## 3. QA notes (run on prod after deploy)`: checkbox list, test accounts only.

Then check mergeability: `gh pr view <n> --json mergeable,mergeStateStatus`. If CONFLICTING, master has release merge commits develop lacks: in a worktree off `origin/develop`, `git merge --no-ff origin/master`, verify `git diff --stat origin/develop HEAD` is empty or only expected, push `HEAD:refs/heads/develop`. `UNSTABLE` only means checks are failing/pending (GitHub Actions quota) and is OK if each PR documented local checks.

## 3. Slack: release notes + pre-release checklist

Channel **#release-thridify** = `C07D2F85MEH` (private). Post one message: "*Releasing Thridify frontend X + backend Y to prod*", the customer release notes, then a numbered **Pre-release checklist** with links to both PRs and :white_check_mark: / :hourglass_flowing_sand: per item (PRs + notes, master merged into develop, risk review, dev verification, DB dump, merge/approve, tags, prod QA). Use `slack_send_message`.

## 4. DB backup via the release script

Run in a Terminal tab (user types the password):
```
zsh /Users/adi/Documents/Claude-Workspace/thridify-prod-release.sh
```
- Step 1 runs `mongodump` (prompts for the Atlas password) into `~/Library/CloudStorage/OneDrive-Personal/Thridify/Database backup/prod-db/<timestamp>`.
- Verify the dump before merging: size comparable to the previous dump (tens of MB, not ~1 MB), all ~44+ collections present, `gunzip -t` on the `.gz` files, `users.metadata.json.gz` uuid matches earlier prod dumps (same cluster). A tiny dump means the wrong cluster/db.
- The script then asks yes/no for: release notes in Slack → PRs merged → builds green → tags created → tag builds green. The user answers in the tab; do the work between prompts and tell them when to say yes.

## 5. Merge + deploy

1. Merge be: `gh pr merge <be> --repo aapastech/thridify-be --merge` (merge commit, never squash).
2. Approve the prod backend build (Cloud Build, project `thridify`, region `europe-west1`, trigger prod-admin-be-github-build-code). Find it: `gcloud builds list --project=thridify --region=europe-west1 --limit=5 --format='value(id,status,substitutions.BRANCH_NAME,createTime)'` (status `PENDING` awaiting approval). Approve (no gcloud verb):
   ```bash
   curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H 'Content-Type: application/json' \
     "https://cloudbuild.googleapis.com/v1/projects/thridify/locations/europe-west1/builds/<id>:approve" \
     -d '{"approvalResult":{"decision":"APPROVED"}}'
   ```
   Wait for SUCCESS, then confirm the serving version with the prod build-info URL in `~/.config/thridify/build-info-urls.txt` (reports version + commit).
3. Merge fe after be is live: `gh pr merge <fe> --repo aapastech/thridify-fe --merge`. Cloudflare Pages builds admin + viewer from master (no approval). Confirm: `curl -s https://viewer.thridify.com/viewer/releases.json` shows the merge commit, and admin.thridify.com serves the new bundle.
4. `mviewer.js` is `max-age=300, stale-while-revalidate=86400`: a browser's first load after deploy may run the old viewer; reload twice when checking.

## 6. Tags + GitHub releases

From master, one per repo, tag = bare version (e.g. `12.19.2`, no `v`):
```bash
gh release create 12.19.2 --repo aapastech/thridify-fe --target master --title "12.19.2" --notes-file fe-release-notes.md --latest
gh release create 12.18.1 --repo aapastech/thridify-be --target master --title "12.18.1" --notes-file be-release-notes.md
```
Notes = the PR's release notes + included-PR table + technical notes + link to the release PR (extract sections 1 and 2 from the PR body file). Check `gcloud builds list --project=thridify --region=europe-west1 --limit=4` afterwards: in the 12.19.2 run tags triggered no builds; if one appears, it must go green (and may need approval).

## 7. Prod QA (admin.thridify.com + viewer)

Run the QA notes from both PRs in Claude in Chrome (`mcp__claude-in-chrome__*`; the user's standing rule allows it for signed-in work). If admin.thridify.com shows the login page, ask the user to sign in with a Thridify-owned account (it@aapastech.com, Demo plan, has been used; confirm the email on `/user-profile` before any write) and stop until they say so. Never type credentials.

What worked in the 12.19.2 run:
- The admin's auth token is not readable from page JS (fetch to api.thridify.com from the console fails CORS/401), so verify through the UI and `read_network_requests` (start tracking, then reload) instead of calling the API by hand.
- Public checks without auth: `curl https://api.thridify.com/v1/<public route>`, prod build-info URL, `viewer.thridify.com/viewer/releases.json`, string search across `admin.thridify.com/assets/*.js` chunks (features live in lazy chunks such as `AdminApp-*.js`, not `index-*.js`).
- Viewer smoke: `viewer.thridify.com/p/k5h0f_` (Store Modern Sofa) and the Tyashin prod demo store `demo.thridify.com/products/armchair`: find `<model-viewer>` through shadow roots, check `loaded`, read console errors.
- Copilot (Thea) questions and lifestyle/avatar generations are paid calls: check the UI text only unless the user approves spend.
- Terminal prompts of the release script are answered by the user; tell them which answers to give (`yes` once the step is done).

Rules:
- Read-only checks first (versions, endpoints, pages render, no console errors).
- Writes only on Thridify-owned test accounts and only reversible ones (acknowledge a notice, publish a test product). Record before/after values.
- Never trigger emails, bulk operations, deletes, backfills or paid generations.
- Anything that cannot be checked off-device (Android AR, iOS Quick Look) → ask the user to check on a phone, say so in the checklist.

## 8. Slack: post-release checklist + done

Post in #release-thridify, same shape the team uses:
```
1. Latest database dump is taken: <folder name, size, collections>. :white_check_mark:
2. <link to the release-notes message|Release notes published>. :white_check_mark:
3. <be PR link|thridify-be> and <fe PR link|thridify-fe> rebased with master and merged into master. :white_check_mark:
4. <release links|Release tags created from master, release notes on GitHub for both repos>. :white_check_mark:
5. Prod backend build approved and deployed (<build id>, build-info reports <version>). :white_check_mark:
6. Prod QA on admin.thridify.com / viewer: <what was checked>. :white_check_mark:
7. Plugins (Tyashin, WooCommerce, BigCommerce, Shopify) test stores checked, or say which were not.
```
List honestly what was NOT checked in prod and why (paid generation, needs a phone, needs a throwaway product). Then a separate short message: `------------------------------ Released <fe ver> (frontend) + <be ver> (backend) to prod ------------------------------`.

Timing reference (12.19.2 run): be prod build ~4 min after approval; Cloudflare Pages admin + viewer ~3–5 min after the fe merge.

## 9. Afterwards

- Close the release worktrees; update memory/backlog with versions, PR numbers, build id, dump folder.
- Anything owed (device checks, follow-up fixes) goes into the backlog with a link.

## Reference

- Repos: aapastech/thridify-be (Cloud Functions/Run via Cloud Build, prod needs approval), aapastech/thridify-fe (Cloudflare Pages: admin + viewer; versioned viewer releases with `npm run viewer:pin`).
- Release script: `~/Documents/Claude-Workspace/thridify-prod-release.sh`.
- Past releases to copy from: be#445 + fe#677 (12.18.1/12.19.2), be#420 + fe#641 (12.12.0), fe#663 (hotfix 12.15.2).
