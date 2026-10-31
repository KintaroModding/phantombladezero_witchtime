# Witch Time

A dodge through an enemy attack, or a timed parry, slows the whole world down for a moment, like Witch Time.

A [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) Lua mod for Phantom Blade Zero (Unreal Engine 5.7), made with
game version `CL-301524_JKS-17219` and UE4SS `v3.0.1-1183-gcdbc0b66`. This README is for developers; what the mod does and
how to install it are in [openmods.md](openmods.md), the mod page on [openmods](https://openmods.nexus).

## How it works

- **Trigger:** the player's dodge ability `BP_PlayerDodge_Base_C` (`/PBZDeflectSystem/Abilities/BP_PlayerDodge_Base`;
  every weapon's dodge, for example `GA_Hun_AdvancedDash_Tang_C`, is a child of it) waits for the event
  `GameplayEvent.Actor.HitDodge.Target` (or `.Target.Perfect`) when an attack hits the player during the dodge, and
  then calls `OnEvadeDamage(SourceAvatar, SourceBullet, Perfect)` inside its graph. The script hooks the event
  handler `EventReceived_DE0795E84ADED85523B251A711345C31` (its name comes from the editor and can change with a game
  update) and `OnEvadeDamage`; whichever comes first starts Witch Time.
- **Not the red-light perfect dodge:** the game rewards it itself (its own slow-down and a counter attack). It
  activates `BP_PlayerPerfectDodge_Base_C` (`K2_ActivateAbility`, hooked): a dodge within 0.2 s after it starts no
  Witch Time, and a Witch Time that started up to 0.2 s before it is taken back (the chain as before).
- **Parry trigger:** a hook on `On Impact` of the player's parry ability `BP_StanceAction_Base_Player_C`
  (`/PBZDeflectSystem/Abilities/BP_StanceAction_Base_Player`; every weapon's parry, for example `GA_Hun_Tang_Stance_C`,
  is a child of it and none overrides `On Impact`; NPCs use `BP_StanceAction_Base_NPC_C`). The game calls it when an
  attack hits the parry; `Result Data` (`FDeflectResultData`) holds `DeflectResult`: `Deflect.Result.Normal` (holding
  the key, a block), `.Great`, `.Perfect` (the key pressed at the right moment), `.PerfectCounter`. Only the results in
  `PARRY_RESULTS` start Witch Time (now `.Perfect`); every other result is logged with `LogTime`.
- **Switches:** `DodgeEnabled` sets the dodge and red-light hooks, `ParryEnabled` the parry hook; with both `false`
  the script sets no hook and no timer.
- **Classes:** the game loads the classes with the player's abilities, so the script looks for them at start,
  after each map load (`RegisterLoadMapPostHook`) and every 2 seconds while one is missing, and sets the hooks once
  a class is there (again after it was unloaded).
- **Slow-down:** `DemoPlayTimeDilation` = `WorldSpeed` on the world settings (`PersistentLevel.WorldSettings` of the
  player controller's world). The engine multiplies it with `TimeDilation` and `CinematicTimeDilation` for the whole
  world; only replays use it otherwise. Not `TimeDilation` (`SetGlobalTimeDilation`): the game sets that back to 1.0
  every frame (seen in the test of 1.2), and its own short world slow-downs (`GAAction_GlobalSlomo_C`, for example
  x0.33 for about 0.46 s of real time on the red-light perfect dodge) go there, so they multiply with Witch Time. A
  step once per frame (only while a Witch Time runs) ends it after `DurationSeconds` of real time (`os.clock`) and
  sets the factor back to 1.0, but only when it still has the mod's value; while it runs, the step sets the factor
  again when something changed it. A safety check in the 2-second loop ends a Witch Time that the frame step missed;
  a map load drops it (the new map runs at full speed).
- **Loops (1.8):** both loops run on the game thread, the same thread as the hooks, through UE4SS's Delayed Action
  API: `LoopInGameThreadWithDelay(2000)` for the check and `LoopInGameThreadAfterFrames(1)` for the frame step (every
  16 ms with `LoopInGameThreadWithDelay` when UE4SS has no engine tick hook, `EngineTickAvailable`). 1.7 used
  `LoopAsync`, whose callback runs on a second thread in the same Lua state as the hooks; a player's crash report
  showed a damaged state (`attempt to compare number with function` on a line that compares two numbers, then
  UE4SS's `Ref was not function`). A UE4SS without the Delayed Action API keeps the old `LoopAsync` loops, and the log
  says so. Game test of 1.8: five Witch Times ended after 1.007 to 1.008 s (planned 1.00 s), a chained one after
  0.508 s (planned 0.50 s).
- **Chain:** a dodge or parry within `ChainWindowSeconds` after the end starts a Witch Time of `ChainFactor` times the
  last length (dodges and parries share one chain); a dodge or parry during a Witch Time does nothing.
- **PlayerFullSpeed** (on by default): the pawn's `CustomTimeDilation` = the game's value x 1 / `WorldSpeed` during a
  Witch Time. The
  game writes the player's factor every frame through the Blueprint event
  `BP_RenewalPlayer_C:SetCustomTimeDilation` (`/Game/RenewalContent/Prototypes/Character/BP_RenewalPlayer`; its own
  per-actor slow-downs such as hit-stop, 1.0 otherwise). A hook on it (set only with `PlayerFullSpeed = true`)
  multiplies each value right after the game wrote it, only for the controlled pawn; the frame step checks the result
  and sets it again when the hook missed a write (in the game test of 1.4 it never did: about 110 writes per Witch
  Time, all multiplied). At the end, and at a map load, the pawn gets the game's last value back.
- **LogTime:** logs each dodge event, each red-light perfect dodge, each Witch Time and its real length, the game's
  own world slow-downs (hooks on `GAAction_GlobalSlomo_C:Received_ActionBegin` and
  `GAAction_Slomo_C:Received_ActionEnd`), the first changes of the factor in each Witch Time and their count, and
  every change of the factor up to 0.5 s after the end. Without it,
  only the three hooks that start or stop Witch Time are set.

## Deploy and test

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1     # copy the mod folder into the game's ue4ss\Mods
python tests\test_main.py                                 # offline test of main.lua (needs lupa)
```

`deploy.ps1` copies `src\phantombladezero_witchtime\` to
`<game folder>\platform\steam\ue4ss\Mods\phantombladezero_witchtime\`. The game folder is `gameDir` of the game's
profile in the modding workspace, else `$env:UE5_GAMES_DIR\PBZ`; `-GameDir` or `-ModsDir` override it. The mod folder
holds `enabled.txt`, so UE4SS starts it without an entry in `mods.txt`. An existing `Scripts\config.lua` in the game
stays (the tester's settings); `-ResetConfig` copies the shipped one. UE4SS loads Lua mods at game start. The mod
writes its lines into `ue4ss\UE4SS.log`, for example `[Witch Time] 1.7 loaded.` (more lines with `LogTime = true`).

`tests\test_main.py` runs `main.lua` in Lua 5.4 ([lupa](https://pypi.org/project/lupa/)) with mocks of the UE4SS
functions that the script uses. It finds syntax and logic errors before a game start.

Versioning: every change raises the second number (1.0 -> 1.1) in `mod.json` and in `main.lua`:
`python scripts\bump_version.py`.

## Settings

`Scripts/config.lua` holds the player's settings, `DEFAULTS` in `main.lua` every setting with its default. Players and
mod managers keep their own `config.lua` across updates, so a new `main.lua` can meet an older file: a setting that
the file lacks, or has with another type, keeps its default, and the log says so (`ReadSettings`). Add each setting to
both files with the same value, and read it only from `Settings`, never as `config.X or default` (for a true/false
setting, `false or true` is true). `tests\test_main.py` checks that `config.lua` ships exactly the defaults, and runs
`main.lua` with a missing, empty, broken and wrong `config.lua`.

## Project layout

```
src/phantombladezero_witchtime/   The UE4SS mod folder: Scripts/main.lua, Scripts/config.lua, enabled.txt
tests/test_main.py    Offline test of main.lua with mocks of UE4SS and the game
docs/                 Cover and screenshots
scripts/              bump_version.py
deploy.ps1            Copies the mod folder into the game's ue4ss\Mods
mod.json              Name, version, game, engine and UE4SS version
```

## License

MIT, see [LICENSE](LICENSE).
