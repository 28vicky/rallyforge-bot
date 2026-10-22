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
0. (Owner, 22 Oct 09:03) NO chat publishes on its own any more. Finish on your feat/<id> branch, push it, mark the row 'ready'. The owner says when; then ONE Release chat merges all ready branches and publishes them together.
1. One task = one branch `feat/<id>` made from the latest `origin/main`. Never commit to main (only the Release chat does).
2. Stay inside your task's area (table below). Need to change code outside it? Stop and ask the owner.
3. Shared spots - add only, never reorder or rewrite: `DEFAULT_CONFIG` keys (~line 220), `DUTY_LIBRARY` (add your entry at the end), `duty_status`/`duty_record` (add your id to a tuple, do not restructure), `step` commands (~line 6615, add one `if cmd ==` line).
4. Do NOT bump VERSION or edit ui/changelog.js - the Release chat does that. Put a 1-line changelog suggestion in your branch's last commit message.
5. Before finishing: `git fetch origin main && git rebase origin/main`, `python -m py_compile rally_bot.py web_app.py`, push the branch, then update your row below (status + 1 line) in a separate small commit on main (HANDOFF.md only).
6. Owner's PC is shared: only ONE chat deploys / reopens the app at a time, and only after asking the owner. Write "PC: <task id> testing" in the PC line below while testing, clear it after.
7. Low effort by default. Ask the owner before anything costly (live tests, screenshots, long reads, subagents, high-effort debugging).

PC: free (dev copy v2.4.77 has feat/science-market + PC-only flow recorder; an update to 2.4.78 is offered and would replace it)

PUBLISHED 2.4.76 (T8 update-restart) on 21 Oct 12:20 IST; check_release OK. Not yet tested live. main = origin/main.

### Task board
| ID | Task | Branch | Area (may edit) | Clash with | Status |
|---|---|---|---|---|---|
| T1 | Champions (F4): greet check, greet timer non-blocking, live test on Rob | f4-champions | rally_bot.py "Champions" section | - | code done, not tested live |
| T2 | Market / Black Market (F5) + Alliance Science | feat/science-market | rally_bot.py "black market", "alliance science" sections, duty entries, world-map recovery | T5 (city code) | Alliance Science WORKS (K). BM floors done, but the Market is not found on cities without a map. See project docs claude/science_market_shrine_21oct.md + claude/city_camera_facts_21oct.md |
| T3 | Libraries (F6): generals catalogue + monster library | feat/libraries | "general catalogue", "monsters" sections, catalog/, monsters/ | T6 (monsters) | todo |
| T4 | UI cleanup (F7): Advanced section, duplicate buttons, tap targets, labels, Clean up button | feat/ui-cleanup | ui/*, web_app.py (UI routes only) | any task adding UI panels - rebase often | todo |
| T5 | Daily tasks (F8) part 1: Resource Tax, Gold Levy, Resource Collecting | feat/dailies-city | new run_* functions after the Champions section + DUTY_LIBRARY entries | T2 | todo |
| T6 | Daily tasks (F8) part 2: Alliance help/donation/gift/treasure, Mail, Quests | feat/dailies-alliance | new run_* functions + DUTY_LIBRARY entries | - | todo |
| T7 | Rally extras (F9): universal "wait after I play elsewhere", per-monster presets | feat/rally-extras | "presets", "one device" rally loop | T3 (monsters) | todo |
| T8 | Update restart: countdown, waiting-for names, click = restart now, 30 s max wait | feat/update-restart | web_app.py update/restart code + ui/app.js status bar | T4 (ui/app.js) | code done, not tested live |
| T9 | R1 core engine: duty scheduler with next-run per instance + Itinerary view, march-slot tracker, start-up health checks (RAM, game version) | feat/engine-itinerary | rally_bot.py duty scheduler/duty_status + one new UI view | T4 (ui), T5/T6 (duty entries) - add only | todo |
| T10 | S1 Rally proof: missed-rally report (each rally not joined + reason), joins per hour per account, daily summary on dashboard + Telegram | feat/rally-proof | rally loop logging (add only) + one dashboard panel | T7 (rally loop), T9 (scheduler) - rebase often | todo |
| T11 | S2 Phone control: mobile layout of dashboard, LAN access with PIN, Telegram alerts (kick, stuck, no stamina, lag streak) + commands status/pause/resume | feat/phone-control | web_app.py (server/auth/routes) + ui mobile CSS + telegram code | T4 (ui) | todo |
| T12 | Popup fix: tap usual X spot after outside taps fail | feat/popup-x | rally_bot.py popup handlers | - | published 2.4.78, not tested live |
| T13 | UI redesign to the mockup (sidebar button top-left, Overview, Routines cards, status bar, type scale, Start/Pause/Stop all states, dropdowns inside the window) | dashboard-f7 | ui/app.js, ui/pages.js, ui/app.css | T4, T8 (ui) | published 2.4.84 (22 Oct 09:40) was ready - merges cleanly onto 2.4.83; live on the owner's PC 22 Oct 09:17 (statuses, joins, duty badges, Routines switch saves) - claude/ui_redesign_f7_22oct.md |
| T14 | Daily Rewards window: tap Check-in | feat/daily-checkin | rally_bot.py clear_popups + daily_checkin | T12 (popups) | published 2.4.84 (22 Oct 09:40) was ready - tested on the owner's photo only, not live - claude/daily_checkin_22oct.md |
| T15 | Monster pictures: Arachne, Behemoth, Bird of Hurricane, Leviathan, Serpopard, Taotie, Temple Guard, Viking #3 | monsters-arachne | monsters/ | T3 | published 2.4.84 (22 Oct 09:40) was ready - Ymir still has no picture - claude/monster_repo.md |
| T16 | Log export: Save log box (1 h..7 days, one/all instances), zip in Downloads, logs kept 7 days | feat/log-export | rally_bot.py log code, web_app.py save_log route, ui/app.js | T13 (ui/app.js) - trial merge with T13-T15 OK 22 Oct 09:30 | published 2.4.84 (22 Oct 09:40) was ready - tested on a real log in the cloud, not in the app UI - claude/log_export_22oct.md |
| T17 | Black Market fast refresh (~4 s per refresh) - was on the owner PC only | feat/bm-fast-refresh | rally_bot.py _run_market refresh loop | T2 (black market) | published 2.4.85 (22 Oct 09:45) was ready - ran live on K 21-22 Oct (310 refreshes, 28 VIT 50 buys, no errors); made from 2.4.84 - claude/pc_deploy_2481_21oct_2305.md |
| T18 | Record a task (beta) for every user + "Save recordings as zip" so testers (tester 1) can record flows for new duties | feat/flow-record-beta | ui/pages.js settings page, ui/dialogs.js flowRecordFlow text, web_app.py flow_zip | T4 (ui) | published 2.4.85 (22 Oct 09:45) was ready - local test as a non-owner: panel shows, zip of recordings made; recorder itself is the 21 Oct one (used live on the owner PC) |
| T19 | Logs page: top "Logs" button opens Control settings > Logs with Time (1 h-7 days) + Instance + Save as zip, Copy, Report, Open folder, live log; Save row also on each instance's Itinerary & Logs; log card removed from Setup > Settings | feat/logs-page | ui/app.js (tabs, Logs button, saveLogRow replaces saveLog modal), ui/pages.js (PAGES.logs, instHistory, settings), ui/app.css (.logsave) | T4 (ui) | published 2.4.86 (22 Oct) was ready - local test: page + instance save row shown, 42-step click-through OK; not yet tried on the PC |
| T20 | Black Market picture picker: cards with picture + plain name (BM 44 = Stamina 50 x10 etc.), categories, search, Stamina only, Clear all, pay per card; settings keep the BM NN names so saved choices still work | feat/bm-picker | ui/pages.js (PAGES.market, BM_INFO), ui/app.css (.bm*), web_app.py (market_pictures, /api/market_img) | T4 (ui) | published 2.4.86 (22 Oct) was ready - local test OK; names are best guesses from the pictures, owner to correct |
| T21 | Black Market crash after a buy (goods count changed -> numpy shapes), Stamina only sets gems, building name repair (fix_building_name), retry tap / camera move on plot search, Black Market icon retry, duty shown under Now executing | fix/bm-sold-out | rally_bot.py, ui/pages.js | - | published 2.4.87 (22 Oct) - found on Manhattan 10:58 (bought 1 x Stamina 50 x10 then crashed) |
| T22 | Champions: tap the 'Don't show this again for today' tick at x 216 (old tap was 150 px left of the text centre = empty space) | fix/champ-tick | rally_bot.py ch_do_taps | - | published 2.4.88 (22 Oct) - seen live on Manhattan 11:07 |
| T23 | Instance list chip always shows what the bot is doing (joining Hydra 5, Black Market, Champions, stamina, Auto-Join...), "idle" only when nothing; joins count moved to the chip tooltip; bot sends an Idle step when city tasks end | fix/side-duty-chip | ui/app.js (busyTask, chip, poll redraw), rally_bot.py run_city_tasks | T4 (ui) | ready (22 Oct) - local test OK |
| T25 | Faster start: the start gap (40 s) is skipped for an emulator that is already running with Evony on screen (2 s instead); the gap stays for emulators that must boot/load | fix/fast-start | web_app.py start_all | T24 (different file) | ready (22 Oct) - syntax OK, not yet live-tested |
| T26 | Instance list chip with timers: "starts in 0:38" while waiting its turn in Start all, "starting"/"loading"/"restarting 1:05" (counts up) while the emulator boots or Evony loads, "kicked 4:12" (counts down; timer moved from the small line into the chip). Stop during Start all no longer starts it later. Includes T23 + T25 (merged in) | feat/start-chip | rally_bot.py LOADING (wait_stable/bring_online wrappers, force_restart_game), web_app.py status_list + start_eta, ui/app.js renderSide + tickPauseTimers, ui/app.css | T23, T25 (contains both) | ready (22 Oct) - syntax OK, not yet live-tested |
| T24 | Empty Alliance War list never refreshed (Dida/Buttercup/Princess sat on it while others joined): after 3 empty looks reopen Alliance War (45 s apart when other instances see rallies, else every 5 min), 3 reopens with allies busy -> restart Evony. Game stuck on loading/"Connecting": after half the load timeout (min 90 s) close + reopen Evony once | fix/empty-rally-list | rally_bot.py wait_stable, others_see_rallies, rally loop (not target) | T23 (rally_bot.py, different functions) | ready (22 Oct) - syntax OK, not yet live-tested - claude/stuck_rally_screen_22oct.md |
| T27 | Monster known only by its name (no picture, e.g. Ymir): the first time its name is read on a fully visible card, the portrait is saved to monsters\\<Name>.png (learn_monster), so later rallies are recognised by picture. Ymir joins checked 22 Oct: read by name, level 6 (722.6M), joins march fine | feat/learn-by-name | rally_bot.py find_target (name-read branch) | none | ready (22 Oct) - syntax OK, not yet live-tested |
| R | Release chat: merge finished branches into main, version bump, changelog, publish (owner says publish) | main | everything | - | when owner asks |

Safe to run together: T1 + T3 + T4 + T6, or T2 + T6 + T7. Avoid pairs listed in "Clash with".

### Ragebot parity (21 Oct 13:55 IST)
Study: project doc claude/ragebot_full_study.md. Plan: claude/ragebot_parity_milestones.md (R1-R9).
Mapping: R1 = T9, R2 = T7 (scope extended with Ragebot rally extras), R3 = T4 (plus copy settings, profiles, filter box, Next Run), R4 = T6 (+ T5).
Stand-out plan: project doc claude/compare_and_stand_out.md (S1-S5). S1 = T10, S2 = T11.

---
## Update 21 Oct 18:35 IST - T2 chat (Alliance Science + Black Market) ended
- Branch feat/science-market (NOT merged, NOT published): Alliance Science duty (tested OK on K, 13/13), Black Market duty with gem/gold floors + 3 free refreshes default, world map recognised (recovery taps the globe; Back arrow only on red-title full pages - fixed a profile-page loop), "city zoomed in too far" warning + City page tip, test duty "city camera reset (globe)".
- Proven on K: globe (world map -> city) does NOT reset the camera. Shrine-in-middle-at-game-start is unverified.
- Shrine-first navigation experiments parked on branch exp/shrine-nav (not used).
- Next: (1) save the first city picture after every game start to verify the start anchor; (2) proper city navigation per claude/city_camera_facts_21oct.md; (3) Black Market route via the Tavern (owner says there is one - record it).
- Starter prompt: "Read CLAUDE.md, HANDOFF.md and project docs claude/city_camera_facts_21oct.md and claude/science_market_shrine_21oct.md. Work on branch feat/science-market. First task: add a capture that saves the first city picture after every game start."

---
## 21 Oct 18:55 IST - PUBLISHED 2.4.79 (Release)
- Only change: "Bird of Hurricane" added to MONSTER_CATALOG (Limited-time event, level 1) so it has an on/off switch. Bot was joining it on all accounts (not in any skip list).
- Owner still has to switch it off per account (or Claude writes config.json with the app closed; prepared copy adds "Bird of Hurricane": [1] to global + every per-device monster_skip).
- Installing 2.4.79 on the PC replaces the dev copy v2.4.77 (feat/science-market + PC-only flow recorder).


### 2.4.82 (22 Oct 08:55 IST) - log-fix chat
Published: rally on screen joined before a due task (tasks_first no longer skips a joinable rally - the setting now has no effect, remove it in a later UI cleanup); Crazy Eggs Event Center/Windmill fix (feat/eggs-check); stuck guard - Alliance War row by words, restart Evony after 6 taps or 4 lag waits in a row (feat/stuck-guard). Not tested live.

### 2.4.83 (22 Oct 09:05 IST) - log-fix chat
Published: stale rally list check (feat/stale-list) - empty list while a same-alliance instance sees rallies -> reopen, then restart Evony. Alliance learned automatically (two instances read the same rally). Not tested live.
Idea from owner (later): per-instance "Account" section - region (e.g. Europe), server number, keep ID, alliance; later the bot reads these by itself. Then use it for alliance grouping. Also later: check alliance chat for new rallies as a second stale-list proof.

Note (22 Oct 09:04): another chat is saving all monster pictures - they must go in monsters/ on its feat/ branch; the joint release ships them via make_update --monsters monsters.

T16 log export (feat/log-export; was written as "T13", which was already the UI redesign): READY for the joint release - Save log box with time range (1 h..7 days) + one/all instances, zip in Downloads, logs kept 7 days. Tested on a real log file in the cloud, not in the app UI.

### 2.4.84 (22 Oct 09:40 IST) - joint release (first under the "one Release chat" rule)
Published T13 dashboard-f7 (UI redesign to the mockup, live-checked on the owner's PC 09:17), T14 feat/daily-checkin (Daily Rewards -> tap Check-in; photo-tested only), T15 monsters-arachne (8 monster pictures; monsters/ = 28), T16 feat/log-export (Save log by time + instance, zip, logs kept 7 days). Merge had no conflicts; syntax + 42-step UI click-through OK; check_release "version 2.4.84: OK". Clean-up: a stray screenshot (ui/ov.png) was removed before merging; dead tasks_first setting still to remove. NOT in the release: the owner PC's fast Black Market refresh (rally_bot.py on the PC only) - the PC must not install 2.4.84 until that is ported.

### 2.4.85 (22 Oct 09:45 IST) - joint release
Published T17 feat/bm-fast-refresh (Black Market refresh ~4 s, ran live on K) and T18 feat/flow-record-beta (Record a task (beta) for every user + Save recordings as zip). No conflicts; syntax + 42-step click-through OK; check_release "version 2.4.85: OK". The owner PC can now install updates normally (nothing PC-only is left).

