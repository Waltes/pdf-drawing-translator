A follow-up fix to 1.0.3's key recovery

What 1.0.3 got wrong: when the key was restored from `apikey.dat` after the configuration
had been deleted, the key came back into the settings but the AI toggle stayed off. The
client-side check reads `enabled` from the config, and an explicit `False` makes the endpoint
report itself as unusable. So the key was visibly sitting in the field, yet translating threw
"the AI endpoint is incomplete: missing parameter" - with the key, the base URL and the model
name all present. Impossible to make sense of from the outside.

The recovery path now sets the toggle on along with the key. A deleted configuration means
there is no user intent to preserve - nobody switched it off - so restoring it as enabled is
the only sensible reading.

This only triggered in the narrow case where `apikey.dat` exists but `config.bak.json` does
not. A normal uninstall keeps the backup too (it lives above the `data` folder), so that path
restored `enabled` from the backup and was fine. Which is also why 1.0.3 was not pulled.

Also in this release:

- `filevers` / `prodvers` in the version resource were still `(1, 0, 3, 0)`. The dot-form
  strings had been updated, the tuple form had not, so the numeric version shown in file
  properties lagged behind. Both now read 1.0.4.0.
- The installer header still described the old half-translated Chinese language file. That
  comment had been stale since the language pack was rebuilt from the official Inno 6.5
  translation; it now says what the build actually does.
- Both user guides gained a line under Installation: to upgrade, run the new installer over
  the top. Do not uninstall first - uninstalling deletes the settings folder and takes the
  saved key with it. This matters most for anyone coming from 1.0.2, where the uninstaller
  removed the whole folder and no `apikey.dat` existed yet.

Verified on the packaged build: self-test 21/21, no failures. End-to-end with an isolated
data folder - deleted `data`, left only `apikey.dat`, launched: key restored (35 chars, exact
match), `enabled` true, endpoint reports available with no missing fields. Clearing the key
manually does not resurrect it. Installer compiled with zero warnings, 1078 files packed,
matching the build output plus the five extra files.

Install with `PDFTranslator_Setup_1.0.4.exe` below.
