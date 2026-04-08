# Updating myOS without losing anything

This guide is for you, Tori. Follow it any time you want the newest version
of myOS on either your personal computer or your work computer. It will
never lose your settings, chat history, tasks, labels, groups, or notes.

If you only read one section, read this one.

## The short version

```
cd ~/myos
./update.sh
```

That is it. The update script does the right thing automatically. Open a
new Terminal window when it finishes, then type `myos` to start.

If you prefer to do it by hand, keep reading.

## What lives where (the cheat sheet)

Your personal stuff lives in two places on your computer. Neither of them
gets touched when you update myOS. This is on purpose.

| What it stores | Where it lives | Safe when you update? |
| --- | --- | --- |
| Your settings (dark mode, name, features, notifications) | `~/.myos/settings.json` | Yes, never touched |
| Your chat tabs and messages | `~/.myos/chat_history.json` | Yes, never touched |
| Your labels (name and color) | `~/.myos/labels.json` | Yes, never touched |
| Your groups (thread names, task lists) | `~/.myos/threads.json` | Yes, never touched |
| Which labels are on which task | `~/.myos/task_labels.json` | Yes, never touched |
| Your tasks, needles, audit log, agent history | `~/myos/.ostk/` (inside repo, but ignored by updates) | Yes, never touched |
| Your agent transcripts | `~/myos/transcripts/` (inside repo, but ignored by updates) | Yes, never touched |
| Your API keys and passwords | Your system keychain (Keychain on Mac, Secret Service on Linux) | Yes, lives outside the repo entirely |

Everything else in `~/myos/` is the program itself. You can safely delete
and reinstall it without losing any of the files above.

### One-line check you can run any time

Before updating, run this to see the files that hold your data:

```
ls -la ~/.myos/
```

After updating, run the same thing. The file list and the modification
dates should look identical. If they do, nothing was touched.

## The full update process (by hand)

Do this the first time if you want to watch what is happening. After that,
just use `./update.sh`.

### 1. Go to your myOS folder

```
cd ~/myos
```

### 2. See if you have any unsaved code changes on this computer

```
git status
```

One of three things will be true.

**A. Nothing to report.** You see "nothing to commit, working tree clean".
Skip to step 3.

**B. You see a list of files in red or yellow.** You have unsaved changes
on this computer. You have three choices. Pick one.

   1. **Tuck them away for later.** Safest option. Run:

      ```
      git stash push -m "my work on this computer before update"
      ```

      This puts your changes in a hidden pocket. You can bring them back
      later with `git stash pop`. The update will proceed on a clean slate.

   2. **Save them on a side branch.** Best if the changes are specific to
      this computer (for example, your work laptop has work-specific code).
      Think of branches like notebooks. You have one notebook for personal
      stuff and one for work stuff. This command creates a new notebook
      and puts all your changes in it:

      ```
      git checkout -b work
      git add .
      git commit -m "work computer changes"
      ```

      Now your changes live safely in a "work" notebook. Switch back to
      the main notebook to continue the update:

      ```
      git checkout main
      ```

      Later, when you want to work on your work stuff again, run
      `git checkout work` to open that notebook.

   3. **Throw them away.** Only do this if you are 100% sure you do not
      need the changes:

      ```
      git checkout .
      ```

      This deletes them forever. There is no undo.

**C. You see "Your branch is ahead of origin/main by N commits".** You
have committed changes on this computer that have not been shared. They
are safe. The update will merge cleanly. Skip to step 3.

### 3. Grab the latest version

```
git fetch
git pull
```

If you see "Already up to date.", there is nothing new. You are done.

If you see a list of files being updated, that is normal. That is the
program code changing. Your settings files in `~/.myos/` are not in the
list and will not be touched.

### 4. Re-run the installer

```
./install.sh
```

This updates the program itself. It does not touch your settings. The
installer checks if `~/.myos/settings.json` already exists and leaves it
alone if so. It does the same for the shell alias and the PATH line in
your `.zshrc`.

### 5. Open a new Terminal window and start myOS

```
myos
```

Your browser will open automatically.

## What to do if you made code changes on this computer you want to keep

This is the "work computer" situation. You have edits on the work computer
that should not go to your personal computer or to GitHub. Here is the
plain-language version.

Think of git branches like notebooks. You start with one notebook called
`main`. You can add more notebooks and switch between them any time. None
of the notebooks share pages unless you specifically move pages between
them.

### Make a "work" notebook for work-specific changes

On the work computer, once:

```
cd ~/myos
git checkout -b work
```

Now you are working in a new notebook called `work`. Any changes you save
here stay in this notebook.

When you want to update to the latest version, switch back to main first:

```
git checkout main
git pull
```

Then switch back to your work notebook and bring the updates in:

```
git checkout work
git merge main
```

That pulls the latest program updates into your work notebook without
losing your work-specific changes.

### How to tell which notebook you are in

```
git branch
```

The one with a star next to it is your current notebook.

## Troubleshooting

### "I ran the update and my settings look different"

They should not. Run `cat ~/.myos/settings.json` to check. If the file is
still there with your old settings, you are fine, the UI may just need a
refresh (press Cmd+R in the browser).

### "I see a merge conflict message"

Git is warning you that both you and the latest version changed the same
file. Do not panic. Run:

```
git status
```

It will list the files with conflicts. If none of them look like files
you recognize (they are program code, not your data), the safest thing is:

```
git merge --abort
git stash
git pull
git stash pop
```

If the conflict is in something you changed on purpose, ask myOS to help
you resolve it. Just type "help me with a git merge conflict" in the chat.

### "I want to start completely fresh"

You can delete the whole `~/myos/` folder and reinstall from scratch
without losing any of your data. Your data is in `~/.myos/` (note the dot
at the front), which is a different folder. Run:

```
rm -rf ~/myos
git clone git@github.com:torimeyer/myos.git ~/myos
cd ~/myos
./install.sh
```

When the new install runs, it will see your existing `~/.myos/` folder
and leave it alone. All your settings, chats, labels, and tasks will come
right back.

## The rule the tests enforce

There is an automated test that runs on every change to myOS. It reads
the code and makes sure no store that holds user data writes anywhere
inside the `~/myos/` folder. If anyone ever adds code that saves data in
the wrong place, the test fails and the change cannot be merged. This is
why you can trust updates to be safe.

The test lives at `api/tests/test_data_safety.py` if you ever want to see
it.
