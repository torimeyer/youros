# Recovery guide

If something in your setup breaks, these three steps cover the most common paths.

## 1. Check what is wrong

```bash
scripts/youros-doctor.sh
```

Prints a green or red line for each check: backend, frontend, settings file, kernel socket, and any tools you have connected. Each red line includes the exact command to fix it.

## 2. Save your setup

Before anything big changes (new machine, fresh install, major update), pack your current setup to a file:

```bash
scripts/youros-bail.sh pack ~/youros-backup.yourosbail
```

This saves your tasks, notes, specs, settings, and connected-tool configs to a single portable file.

## 3. Restore your setup

To bring a saved setup back:

```bash
scripts/youros-bail.sh unpack ~/youros-backup.yourosbail
```

That restores everything the pack saved. If you are moving to a new machine, run the installer first, then unpack.
