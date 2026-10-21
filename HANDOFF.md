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
