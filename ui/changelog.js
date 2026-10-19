/* What's new - newest first. Add one entry per release: v = version, new = new things, fixed = things that now work. Plain words please. */
window.CHANGELOG = [
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
