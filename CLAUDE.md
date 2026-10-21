# RallyForge Bot - rules for Claude (keep short; read first)

Owner: Vicky. Evony rally automation, Windows app. Repo 28vicky/rallyforge-bot. Full state: HANDOFF.md.

## Work style (owner's usage is limited - spend as little as possible)
- Edit files directly. Never paste code or whole files in chat; one-sentence summary per change.
- One micro-task at a time, then stop and ask before the next.
- Low/standard thinking for routine work. High effort only if the owner asks to debug something hard.
- Keep replies short. No recaps, no long option lists. Ask at most one question.
- Do not run audits, test servers, screenshots or background checks unless asked. No scheduled check-ins.
- Do not read big files whole: grep, then read only the lines needed.
- Do not restart the owner's app while instances run without asking.

## Parallel chats
- Several chats run at once. Follow HANDOFF.md section PARALLEL WORK: own branch, own area, no VERSION/changelog edits, one chat on the PC at a time.
- Keep thinking short. Ask the owner before anything that costs a lot of usage (live tests, screenshots, long reads, subagents, high-effort debugging).

## Release rules
- Never publish unless the owner says so. Never push changed files to main without a release (users get checksum errors); use a branch.
- Release order: bump VERSION in rally_bot.py + changelog entry (ui/changelog.js) -> push files -> `git fetch` -> `python check_release.py origin/main` -> `make_update.py --notes "..." --templates . --monsters monsters --catalog catalog` with UPDATE_KEY (from the PC's %APPDATA%\RallyForgeBot\config.json; never print it) -> push version.json last -> check_release again.
- Admin and free (no-login) installers are never shared with testers.
- Commit trailers: Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com> and Claude-Session line.

## Deploying to the dev copy on the PC
- App folder: %APPDATA%\RallyForgeBot\app\v<current>\ (current.txt). Copy changed files there; commit twice and compare sizes (first write often keeps the old size). Reload needs app close/reopen (restarts instances).

## Parked / pending
- City mapper is parked (branch keep-fixed; doc claude/city_mapper_parked_21oct.md).
- UI follow-ups from the audit: keyboard focus outlines, uneven page margin on short pages (scrollbar-gutter), small tap targets, input labels; branch ui-padding has the button-centring fix.
