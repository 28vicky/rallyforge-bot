/* What's new - newest first. Add one entry per release: v = version, new = new things, fixed = things that now work. Plain words please. */
window.CHANGELOG = [
  { v: "2.4.14", date: "2026-10-19",
    new: ["Vikings are now in four groups (Easy, Normal, Hard, Hell) in the Monsters tab, each with its levels and power. The bot reads the level and power on the rally card to tell which group a Viking belongs to."],
    fixed: [] },
  { v: "2.4.13", date: "2026-10-19",
    new: [],
    fixed: ["Fixed an error in the account check introduced in 2.4.11 / 2.4.12."] },
  { v: "2.4.12", date: "2026-10-19",
    new: [],
    fixed: ["Updates now work even when the update address box is empty (the built-in address is used)."] },
  { v: "2.4.11", date: "2026-10-19",
    new: [],
    fixed: ["The login window no longer pops up again when you save changes on an emulator."] },
  { v: "2.4.10", date: "2026-10-19",
    new: [],
    fixed: ["Fresh installs no longer stop with \u201cJoin button image missing\u201d: the missing pictures are delivered with this update."] },
  { v: "2.4.9", date: "2026-10-19",
    new: ["Account login for the sold edition: ID + password, shows \u201cAccount: N days left\u201d in the status bar, and the owner can close an account early. The free edition is unchanged."],
    fixed: [] },
  { v: "2.4.8", date: "2026-10-19",
    new: ["With the game\u2019s Auto-Join on, an emulator can still collect pictures of unknown monsters: it only looks at the rally list and never joins by itself."],
    fixed: [] },
  { v: "2.4.7", date: "2026-10-19",
    new: ["Champions duty (Keep > Champions): choose how many of the daily 15 taps and 10 greetings each champion gets, per emulator. Inactive champions are skipped. Needs the city map (Scan city) so the bot can find the Keep."],
    fixed: [] },
  { v: "2.4.6", date: "2026-10-19", new: [], fixed: ["The What\u2019s new window now really appears after an update."] },
  { v: "2.4.5", date: "2026-10-19",
    new: ["'What's new' window (this one) after every update, and a 'What's new' button on the Updates tab."],
    fixed: [] },
  { v: "2.4.4", date: "2026-10-19",
    new: [
      "Stamina options (Settings, or on each emulator's page): which items to use, big-to-small or small-to-big, and how many. 'Full' slides the amount bar to the end.",
      "'Start by itself' switch on every emulator page: choose which emulators start when the app opens."
    ],
    fixed: [
      "Stamina refill now really uses the items (before it opened the window but never pressed Use).",
      "The bot no longer gets stuck on the Alliance page or other full-screen pages that looked like pop-ups."
    ] },
  { v: "2.3.5", date: "2026-10-19",
    new: [
      "Closing the app window now stops all bots (there is an option to keep them running).",
      "Emulators start one after the other with a gap, so the PC is not overloaded.",
      "Each emulator has its own Monsters, Tasks and Duties settings."
    ],
    fixed: [
      "Joining without a general works again when the account has none free.",
      "Stuck emulators are restarted automatically after 4 minutes without a new picture."
    ] }
];
