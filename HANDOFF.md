# RallyForge Bot - handoff (21 Oct 2026)

Owner: Vicky (vicky.2812@gmail.com, IST). Repo: 28vicky/rallyforge-bot. Evony rally automation, Windows app.

## Branches
- `origin/main` = published. Latest release 2.4.70. Do NOT publish unless the owner says so.
- `ui-polish` = main + UI polish (Universal wording, Extra Large, readable Pause/Stop/primary buttons, Monsters = universal list, tour in Setup, Auto-Join time-left line, card/button sizes, contrast fixes). Pending the owner's OK to merge/publish.
- `keep-fixed` = older base + city-mapper work (per-culture pictures, frame locator, probe-grid squares, Ideal Land seed, parallel library crawl). PARKED. Merging origin/main into it conflicts in web_app.py `crawl_catalog` (main added `merge`, keep-fixed added `helpers`).

## PC (dev) setup
- App folders: `%APPDATA%\RallyForgeBot\app\v2.4.70\` (current.txt points at it). Data: `%APPDATA%\RallyForgeBot`.
- Deploy a file: copy to the PC folder with the device file tool, list the folder to compare sizes (first write often keeps the old size - write again), then close and reopen the app (this restarts all instances - ask first).
- Instances: Rob, Stalds, Skip (city-mapper test), Kiravik, Rory (keeps joining rallies - leave alone), GK-P, K, Manhattan. Another PC with 9 more accounts is not linked.

## Standing rules
- Brief updates. Never print the update key. Publish only on the owner's word.
- Release gate order: files, then `check_release.py origin/main`, then `version.json` last.
- Admin/free installers are never shared with testers.
- Do not restart the app while the owner's tasks run without asking.
- Commit trailers: Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com> and the Claude-Session line.
- The owner is low on Claude usage: batch changes, keep replies short, avoid needless tool calls.

## Parked: city mapper
See project doc `claude/city_mapper_parked_21oct.md`. Best Japan run read 11 of 26 squares; probe grid untested.

## Pending / ideas
- Cleanup batch: remove main-bar Logs button (keep under App), remove duplicate Themes in Settings, remove Save button (autosave exists), rename "All other settings" to Advanced and move technical items behind a collapse.
- Universal control for "wait minutes after I play elsewhere" (default 10).
- Automated contrast check of every theme for the release gate.
- Check button padding across the app.
- Library update (Tavern > Recruit > Portraits) in parallel across instances (except Rory); verify name reading first.

---
## Update 21 Oct 2026, 11:25 IST (end of long chat) - PUBLISHED 2.4.75, main = origin/main
Released today: 2.4.71-2.4.75. Last two:
- 2.4.74: duties (Crazy Eggs etc.) now run by themselves when switched on (before, only if "Do the tasks" city_enabled was on - it was off everywhere, so eggs were never picked up); duty status line in log every 15 min ("[i] Duties - ..."); Viking/hidden-monster skip (monster_allowed: name "hidden" + a skip list = skip); startup screen check 540x960/240dpi (warning only, Device.ensure_screen); Pause all second pass after 4 s; layout CSS (toolbar shadow, equal dashboard columns, even Monster cards, scrollbar-gutter); button text centring.
- 2.4.75: Cerberus decided by the monster list (cerberus_allowed now always True; hidden "cerberus" setting removed from the UI).
Release procedure used: bump VERSION + changelog -> push to main -> git fetch -> check_release (mismatch is expected) -> UPDATE_KEY from PC config.json (staged with device_stage_files, never printed) -> make_update.py -> push version.json -> check_release OK.
Unverified (not run on live instances): duties fix, Pause all second pass, layout CSS on the dashboard, screen check. Owner's PC still ran 2.4.73 at 11:21; app updates itself within 30 min (reopen restarts all instances).
Open questions for owner: "hidden" skip may be too strict (about 8 per 6 min on Skip/Rob) - option: skip only if the visible part looks like a Viking. Pause all root cause for "some keep running" not found (needs a log from a failing case).
Branches: rel-2474 = main. Old branches fix-duties, fix-hidden-monster, screen-check, ui-padding are merged. keep-fixed = parked city mapper.
Roadmap (one chat per feature): project doc claude/roadmap_21oct.md. F1-F3 done (see above); next F4 Champions, F5 Market, F6 libraries, F7 UI cleanup, F8 daily tasks, F9 rally extras.
Starter prompt for a new chat: "Read CLAUDE.md and HANDOFF.md, then work on feature F4 only: Champions automation. Stop when done."
Remember: CLAUDE.md rules - short replies, one micro-task then ask, no audits, never publish unless owner says.

---
## PARALLEL WORK (from 21 Oct 11:35 IST) - several chats at once
Starter prompt for any chat: "Read CLAUDE.md and HANDOFF.md (section PARALLEL WORK), then work on task <ID> only. Stop when done."

### Rules (every chat)
1. One task = one branch `feat/<id>` made from the latest `origin/main`. Never commit to main (only the Release chat does).
2. Stay inside your task's area (table below). Need to change code outside it? Stop and ask the owner.
3. Shared spots - add only, never reorder or rewrite: `DEFAULT_CONFIG` keys (~line 220), `DUTY_LIBRARY` (add your entry at the end), `duty_status`/`duty_record` (add your id to a tuple, do not restructure), `step` commands (~line 6615, add one `if cmd ==` line).
4. Do NOT bump VERSION or edit ui/changelog.js - the Release chat does that. Put a 1-line changelog suggestion in your branch's last commit message.
5. Before finishing: `git fetch origin main && git rebase origin/main`, `python -m py_compile rally_bot.py web_app.py`, push the branch, then update your row below (status + 1 line) in a separate small commit on main (HANDOFF.md only).
6. Owner's PC is shared: only ONE chat deploys / reopens the app at a time, and only after asking the owner. Write "PC: <task id> testing" in the PC line below while testing, clear it after.
7. Low effort by default. Ask the owner before anything costly (live tests, screenshots, long reads, subagents, high-effort debugging).

PC: free

PENDING RELEASE: 2.4.76 (T8 update-restart) is ready on branch `rel-2476` (VERSION + changelog done). Publish = merge rel-2476 into main, push files, check_release, sign with UPDATE_KEY from the PC, push version.json last. Needs a session with the PC folder %APPDATA%\RallyForgeBot linked.

### Task board
| ID | Task | Branch | Area (may edit) | Clash with | Status |
|---|---|---|---|---|---|
| T1 | Champions (F4): greet check, greet timer non-blocking, live test on Rob | f4-champions | rally_bot.py "Champions" section | - | code done, not tested live |
| T2 | Market / Black Market (F5) | feat/market | rally_bot.py "black market" section + its UI panel | T5 (city code) | todo |
| T3 | Libraries (F6): generals catalogue + monster library | feat/libraries | "general catalogue", "monsters" sections, catalog/, monsters/ | T6 (monsters) | todo |
| T4 | UI cleanup (F7): Advanced section, duplicate buttons, tap targets, labels, Clean up button | feat/ui-cleanup | ui/*, web_app.py (UI routes only) | any task adding UI panels - rebase often | todo |
| T5 | Daily tasks (F8) part 1: Resource Tax, Gold Levy, Resource Collecting | feat/dailies-city | new run_* functions after the Champions section + DUTY_LIBRARY entries | T2 | todo |
| T6 | Daily tasks (F8) part 2: Alliance help/donation/gift/treasure, Mail, Quests | feat/dailies-alliance | new run_* functions + DUTY_LIBRARY entries | - | todo |
| T7 | Rally extras (F9): universal "wait after I play elsewhere", per-monster presets | feat/rally-extras | "presets", "one device" rally loop | T3 (monsters) | todo |
| T8 | Update restart: countdown, waiting-for names, click = restart now, 30 s max wait | feat/update-restart | web_app.py update/restart code + ui/app.js status bar | T4 (ui/app.js) | code done, not tested live |
| R | Release chat: merge finished branches into main, version bump, changelog, publish (owner says publish) | main | everything | - | when owner asks |

Safe to run together: T1 + T3 + T4 + T6, or T2 + T6 + T7. Avoid pairs listed in "Clash with".
