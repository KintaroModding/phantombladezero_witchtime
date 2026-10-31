"""Offline test of Scripts/main.lua with lupa (Lua 5.4) and small mocks of UE4SS and the game.

The mocks stand in for the UE4SS functions that the script uses (the game-thread loops LoopInGameThreadWithDelay and
LoopInGameThreadAfterFrames, the old LoopAsync, RegisterHook, RegisterLoadMapPostHook, StaticFindObject, UEHelpers)
and for the game: a clock (os.clock), the world settings with the two time factors
(DemoPlayTimeDilation for the mod, TimeDilation for the game), the player controller and pawn, and the Blueprint
classes that the game loads later. The Witch Time checks fire the hooked
functions like the game does and move the clock.

The settings checks run main.lua with the shipped Scripts/config.lua and with the files a player may have instead:
none, an empty or older one, a broken one, one without a table, one with values of the wrong type, one with a value
out of range, one with a setting the mod does not know, and one with both switches off. main.lua must start without an
error in each case, and a setting that the file does not give must keep its default. The shipped config.lua must hold
exactly the defaults of main.lua.

Usage:
    python tests/test_main.py        (needs lupa: python -m pip install lupa)

Exit code: 0 = all checks passed, 1 = a check failed.
"""

import json
import sys
import tempfile
from pathlib import Path

from lupa import lua54

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "src" / "phantombladezero_witchtime" / "Scripts" / "main.lua"
CONFIG = MAIN.parent / "config.lua"

DODGE = "/PBZDeflectSystem/Abilities/BP_PlayerDodge_Base.BP_PlayerDodge_Base_C"
RED = "/PBZDeflectSystem/Abilities/BP_PlayerPerfectDodge_Base.BP_PlayerPerfectDodge_Base_C"
GLOBAL_SLOMO = "/MagCombatAbilities/Actions/GAAction_GlobalSlomo.GAAction_GlobalSlomo_C"
SLOMO = "/MagCombatAbilities/Actions/GAAction_Slomo.GAAction_Slomo_C"
CLASS_PATHS = [DODGE, RED, GLOBAL_SLOMO, SLOMO]
TRIGGER = DODGE + ":EventReceived_DE0795E84ADED85523B251A711345C31"
EVADE = DODGE + ":OnEvadeDamage"
RED_TRIGGER = RED + ":K2_ActivateAbility"
PLAYER = "/Game/RenewalContent/Prototypes/Character/BP_RenewalPlayer.BP_RenewalPlayer_C"
PLAYER_TIME = PLAYER + ":SetCustomTimeDilation"
PARRY = "/PBZDeflectSystem/Abilities/BP_StanceAction_Base_Player.BP_StanceAction_Base_Player_C"
PARRY_IMPACT = PARRY + ":On Impact"
ALL_HOOKS = sorted([TRIGGER, EVADE, RED_TRIGGER, PARRY_IMPACT, PLAYER_TIME, GLOBAL_SLOMO + ":Received_ActionBegin",
                    SLOMO + ":Received_ActionEnd"])
HIT_DODGE = "GameplayEvent.Actor.HitDodge.Target"

MOCKS = r"""
LOG = {}
print = function(...) LOG[#LOG + 1] = table.concat({...}, "") end

-- Real time: the test moves it.
CLOCK = 0
os.clock = function() return CLOCK end

local FNameMeta = { __index = { ToString = function(self) return self.text end } }
function FName(text) return setmetatable({ text = text }, FNameMeta) end

-- A game object: valid until the test says otherwise; GetAddress is unique per object.
local ObjectMeta = { __index = {
    IsValid = function(self) return self.valid end,
    GetAddress = function(self) return tostring(self) end,
} }
function OBJECT(fields)
    fields = fields or {}
    if fields.valid == nil then fields.valid = true end
    return setmetatable(fields, ObjectMeta)
end

-- The world settings: DemoPlayTimeDilation is the mod's factor, TimeDilation the game's.
WORLD = OBJECT({ DemoPlayTimeDilation = 1.0, TimeDilation = 1.0 })
PAWN = OBJECT({ CustomTimeDilation = 1.0 })
CONTROLLER = OBJECT({
    Pawn = PAWN,
    GetWorld = function(self) return OBJECT({ PersistentLevel = OBJECT({ WorldSettings = WORLD }) }) end,
})

-- The Blueprint classes that the game has loaded (path -> class object); the test fills it.
CLASSES = {}
SEARCHES = 0
function StaticFindObject(path)
    SEARCHES = SEARCHES + 1
    -- UE4SS gives an invalid object when nothing is found.
    return CLASSES[path] or OBJECT({ valid = false })
end
function FindFirstOf(name) return nil end
function FindAllOf(name) return nil end

HOOKS = {}
function RegisterHook(path, callback)
    HOOKS[path] = callback
    return 1, 2
end
MAP_HOOKS = {}
function RegisterLoadMapPostHook(callback) MAP_HOOKS[#MAP_HOOKS + 1] = callback end

LOOPS = {}
-- The game-thread loops of UE4SS's Delayed Action API (1.8), and the old LoopAsync for a UE4SS without them.
ASYNC_LOOPS = 0
function LoopAsync(ms, callback) ASYNC_LOOPS = ASYNC_LOOPS + 1 LOOPS[#LOOPS + 1] = callback end
DELAYS = {}
FRAMES = {}
EngineTickAvailable = true
function LoopInGameThreadWithDelay(ms, callback) DELAYS[#DELAYS + 1] = ms LOOPS[#LOOPS + 1] = callback return #LOOPS end
function LoopInGameThreadAfterFrames(n, callback) FRAMES[#FRAMES + 1] = n LOOPS[#LOOPS + 1] = callback return #LOOPS end
function ExecuteInGameThread(callback) callback() end

local function Invalid() return OBJECT({ valid = false }) end
package.preload["UEHelpers"] = function()
    return { GetPlayerController = function() return CONTROLLER end, GetPlayer = Invalid, GetWorld = Invalid }
end

-- Calls a hooked function like the game does: the context gives the object, an object of the class className; the
-- parameters come as RemoteUnrealParam values (:get()).
local function Param(value) return { get = function() return value end } end
-- The game's BP_RenewalPlayer_C:SetCustomTimeDilation on an object: its own code sets the factor, then the hook runs.
function GAME_SETS_PLAYER(object, value)
    object.CustomTimeDilation = value
    HOOKS["/Game/RenewalContent/Prototypes/Character/BP_RenewalPlayer.BP_RenewalPlayer_C:SetCustomTimeDilation"](
        Param(object), Param(value))
end
function CALL(path, className, fields, ...)
    local object = OBJECT(fields or {})
    object.GetClass = function() return { GetFName = function() return FName(className) end } end
    local params = {}
    for i, value in ipairs({...}) do params[i] = Param(value) end
    HOOKS[path](Param(object), table.unpack(params))
end
-- The dodge ability's event: a FGameplayEventData with the tag.
function EVENT(tag) return { EventTag = { TagName = FName(tag) } } end
-- The parry's Result Data: a FDeflectResultData with the Deflect.Result tag.
function RESULT(tag) return { DeflectResult = { TagName = FName(tag) } } end

-- Runs every timer once, as UE4SS does after their intervals.
function TICK() for i = 1, #LOOPS do LOOPS[i]() end end

function LOAD_CLASSES(paths) for _, path in ipairs(paths) do CLASSES[path] = OBJECT() end end

function HOOK_NAMES()
    local names = {}
    for path in pairs(HOOKS) do names[#names + 1] = path end
    table.sort(names)
    return table.concat(names, "\n")
end

-- The settings line of main.lua for any table: every name=value, sorted by name.
function DESCRIBE(t)
    local names = {}
    for name in pairs(t) do names[#names + 1] = tostring(name) end
    table.sort(names)
    local parts = {}
    for i, name in ipairs(names) do parts[i] = name .. "=" .. tostring(t[name]) end
    return table.concat(parts, ", ")
end
"""

# The files a player may have instead of the shipped config.lua: (case, content or None for no file, a text that
# main.lua must log). "wrong types" is made from the shipped file: every value gets another type.
PLAYER_CONFIGS = [
    ("no config.lua", None, "No config.lua"),
    ("an empty or older config.lua", "return {}\n", None),
    ("a broken config.lua", "return {\n    DodgeEnabled =\n", "config.lua not read"),
    ("a config.lua without a table", "-- nothing here\n", "does not return a table"),
    ("a config.lua with wrong types", "wrong types", "must be a"),
    ("a config.lua with a value out of range", "return { WorldSpeed = 0 }\n", "WorldSpeed must be from"),
    ("a config.lua with an unknown setting", "return { NoSuchSetting = 1 }\n", "unknown setting NoSuchSetting"),
]

TIMERS = 2


def lua_path(path: Path) -> str:
    return "[[" + path.as_posix() + "]]"


def run(config_folder: Path, before: str = ""):
    """Runs main.lua with require("config") reading config.lua in config_folder, and its timers three times; before is
    Lua code that changes the mocks first (another UE4SS). A Lua error that stops main.lua becomes an "Error" line in
    the log, so the checks fail instead of the test."""
    lua = lua54.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(MOCKS)
    if before:
        lua.execute(before)
    lua.execute(f'package.path = {lua_path(config_folder)} .. "/?.lua"; package.cpath = ""')
    g = lua.globals()
    try:
        lua.execute(MAIN.read_text(encoding="utf-8"))
        for _ in range(3):
            lua.execute("TICK()")
    except lua54.LuaError as error:
        g.LOG[len(g.LOG) + 1] = "Error: main.lua stopped: " + str(error).splitlines()[0]
    return lua, g, [g.LOG[i].rstrip() for i in range(1, len(g.LOG) + 1)]


def settings_line(log):
    lines = [line.split("] Settings: ", 1)[1] for line in log if "] Settings: " in line]
    return lines[0] if len(lines) == 1 else None


def new_lines(g, start):
    """The log lines written since the line count start."""
    return [g.LOG[i].rstrip() for i in range(start + 1, len(g.LOG) + 1)]


def main() -> None:
    version = json.loads((ROOT / "mod.json").read_text(encoding="utf-8"))["version"]
    failures = []

    def check(condition, text):
        print(("ok    " if condition else "FAIL  ") + text)
        if not condition:
            failures.append(text)

    # The mod as players get it, with the shipped config.lua.
    lua, g, log = run(CONFIG.parent)
    for line in log:
        print("      log: " + line)
    check(len(g.LOOPS) == TIMERS, f"{TIMERS} timers")
    check(g.ASYNC_LOOPS == 0 and list(g.DELAYS.values()) == [2000] and list(g.FRAMES.values()) == [1],
          "both loops on the game thread (1.8): the 2 s check with LoopInGameThreadWithDelay, the fast step every "
          "frame with LoopInGameThreadAfterFrames; no LoopAsync")
    _, g_tick, _ = run(CONFIG.parent, "EngineTickAvailable = false")
    check(g_tick.ASYNC_LOOPS == 0 and list(g_tick.DELAYS.values()) == [2000, 16] and len(g_tick.FRAMES) == 0,
          "without the engine tick hook the fast step runs every 16 ms, still on the game thread")
    _, g_old, log_old = run(CONFIG.parent, "LoopInGameThreadWithDelay = nil LoopInGameThreadAfterFrames = nil")
    check(g_old.ASYNC_LOOPS == 2 and len(g_old.LOOPS) == TIMERS
          and any("no game-thread loops" in line for line in log_old)
          and not any("Error" in line for line in log_old),
          "a UE4SS without the Delayed Action API gets the old LoopAsync loops; the log says so")
    check(len(g.MAP_HOOKS) == 1, "one map load hook")
    check(any(f"{version} loaded." in line for line in log), f"load line with the version {version} of mod.json")
    check(not any("Error" in line for line in log), "no error line after three timer runs")
    check(not any("config.lua" in line for line in log), "the shipped config.lua is read without a warning")
    shipped = lua.eval(f"DESCRIBE(dofile({lua_path(CONFIG)}))")
    check(settings_line(log) == shipped, "the settings line shows the values of the shipped config.lua")

    with tempfile.TemporaryDirectory() as temp:
        folders = {}
        extra = [("both off", "return { DodgeEnabled = false, ParryEnabled = false }\n", None),
                 ("dodge off", "return { DodgeEnabled = false }\n", None),
                 ("parry off", "return { ParryEnabled = false }\n", None),
                 ("PlayerFullSpeed", "return { PlayerFullSpeed = true, LogTime = true }\n", None),
                 ("PlayerFullSpeed off", "return { PlayerFullSpeed = false }\n", None),
                 ("LogTime on", "return { LogTime = true }\n", None),
                 ("LogTime off", "return { LogTime = false }\n", None)]
        for case, content, _ in PLAYER_CONFIGS + extra:
            folder = Path(temp) / str(len(folders))
            folder.mkdir()
            if content == "wrong types":
                content = lua.eval(f"""(function()
                    local lines = {{}}
                    for name, value in pairs(dofile({lua_path(CONFIG)})) do
                        lines[#lines + 1] = string.format("    [%q] = %s,", name, type(value) == "string" and "0" or '"wrong"')
                    end
                    return "return {{\\n" .. table.concat(lines, "\\n") .. "\\n}}\\n"
                end)()""")
            if content is not None:
                (folder / "config.lua").write_text(content, encoding="utf-8")
            folders[case] = folder

        # The defaults of main.lua: the settings line with an empty config.lua.
        defaults = settings_line(run(folders["an empty or older config.lua"])[2])
        check(defaults == shipped, f"config.lua ships exactly the defaults of main.lua ({defaults})")

        for case, _, expected in PLAYER_CONFIGS:
            _, g2, log2 = run(folders[case])
            fine = (len(g2.LOOPS) == TIMERS and settings_line(log2) == defaults
                    and not any("Error" in line for line in log2))
            if expected is None:
                fine = fine and not any("config.lua" in line for line in log2)
            else:
                fine = fine and any(expected in line for line in log2)
            if case == "a config.lua with wrong types":
                count = len(list(lua.eval(f"dofile({lua_path(CONFIG)})").keys()))
                fine = fine and sum("must be a " in line for line in log2) == count
            check(fine, f"{case}: starts, every setting at its default"
                  + (f", logs '{expected}'" if expected else ", no warning"))
            if not fine:
                for line in log2:
                    print("      log: " + line)

        _, g2, log2 = run(folders["both off"])
        line = settings_line(log2) or ""
        check(len(g2.LOOPS) == 0 and len(g2.HOOKS) == 0 and "DodgeEnabled=false" in line
              and "ParryEnabled=false" in line and any("the mod does nothing" in entry for entry in log2),
              "both switches off: no timer, no hook, the log says the mod does nothing")

        # Witch Time with the shipped settings (30 %, 1 s, chain window 2 s, factor 0.5) and LogTime on, so the log
        # shows what the mod does.
        lua, g, _ = run(folders["LogTime on"])
        check(len(g.HOOKS) == 0, "no hook while the game has not loaded the classes")
        searches = g.SEARCHES
        lua.execute("TICK()")
        check(g.SEARCHES - searches == 6, "while the classes are missing: one search per class and timer run")
        lua.execute(f"LOAD_CLASSES({{'{DODGE}', '{RED}', '{PARRY}', '{GLOBAL_SLOMO}', '{SLOMO}', '{PLAYER}'}})")
        lua.execute("TICK()")
        check(lua.eval("HOOK_NAMES()") == "\n".join(ALL_HOOKS),
              "all seven hooks set when the classes are loaded (dodge, parry and PlayerFullSpeed are on by default)")
        searches = g.SEARCHES
        lua.execute("TICK() TICK()")
        check(g.SEARCHES == searches, "no search once every class is found")

        def at(time, code=""):
            lua.execute(f"CLOCK = {time} {code} TICK()")

        def dodge(time, tag=HIT_DODGE):
            lua.execute(f"CLOCK = {time} CALL('{TRIGGER}', 'GA_Hun_AdvancedDash_Tang_C', nil, EVENT('{tag}'))")

        def evade(time, perfect):
            lua.execute(f"CLOCK = {time} CALL('{EVADE}', 'GA_Hun_AdvancedDash_Tang_C', nil, 0, 0, {str(perfect).lower()})")

        def red(time):
            lua.execute(f"CLOCK = {time} CALL('{RED_TRIGGER}', 'GA_Hun_Tang_PerfectDodge_C')")

        mark = len(g.LOG)
        dodge(5.0, "GameplayEvent.Ability.Action.Something")
        check(g.WORLD.DemoPlayTimeDilation == 1.0 and any("other dodge event" in line for line in new_lines(g, mark)),
              "another event of the dodge ability starts nothing")

        mark = len(g.LOG)
        dodge(10.0)
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9 and g.WORLD.TimeDilation == 1.0,
              "a dodge that makes an attack miss sets the mod's factor to 0.3, not the game's TimeDilation")
        check(any("Witch Time 1: 1.00 s at 30 %" in line and "HitDodge.Target" in line
                  and "the game's TimeDilation 1.000" in line for line in new_lines(g, mark)),
              "log: Witch Time 1: 1.00 s, with the event and the game's factor")
        mark = len(g.LOG)
        evade(10.0, False)
        check(any("during Witch Time" in line and "nothing" in line for line in new_lines(g, mark)),
              "OnEvadeDamage right after the event (the same dodge) does nothing")
        at(10.5)
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9, "still 0.3 after 0.5 s")
        mark = len(g.LOG)
        at(10.6, "WORLD.DemoPlayTimeDilation = 0.33")
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9 and any("something set the factor to 0.330" in line
                                                      for line in new_lines(g, mark)),
              "a change by the game during Witch Time is set back and logged")
        mark = len(g.LOG)
        for step in range(5):
            at(10.7 + step * 0.02, "WORLD.DemoPlayTimeDilation = 1.0")
        lines = new_lines(g, mark)
        check(sum("something set the factor" in line for line in lines) == 2
              and abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9,
              "a factor changed every frame: set back every time, logged only 3 times per Witch Time")
        at(10.99)
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9, "still 0.3 just before 1 s")
        mark = len(g.LOG)
        at(11.0)
        lines = new_lines(g, mark)
        check(g.WORLD.DemoPlayTimeDilation == 1.0 and any("Witch Time 1 ended after 1.000 s" in line for line in lines),
              "after 1 s real time: the factor is 1.0 again, end logged")
        check(any("set back 6 times in this Witch Time" in line for line in lines), "the end line counts the set-backs")

        mark = len(g.LOG)
        dodge(12.0, HIT_DODGE + ".Perfect")
        check(any("Witch Time 2: 0.50 s" in line for line in new_lines(g, mark)),
              "a perfect (white) dodge 1 s after the end: chained, 0.5 s")
        mark = len(g.LOG)
        dodge(12.2)
        check(any("during Witch Time" in line and "nothing" in line for line in new_lines(g, mark))
              and abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9, "a dodge during Witch Time does nothing")
        at(12.49)
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9, "the chained one is not made longer by the second dodge")
        at(12.5)
        check(g.WORLD.DemoPlayTimeDilation == 1.0, "the chained one ends after 0.5 s")

        mark = len(g.LOG)
        dodge(13.0)
        red(13.1)
        check(g.WORLD.DemoPlayTimeDilation == 1.0 and any("taken back: red-light perfect dodge" in line for line in new_lines(g, mark)),
              "a red-light perfect dodge right after the dodge takes the Witch Time back")
        at(13.4)
        mark = len(g.LOG)
        dodge(13.5)
        check(any("Witch Time 3: 0.25 s" in line for line in new_lines(g, mark)),
              "the chain is as before the taken-back one (step 3, 0.25 s)")
        at(13.75)
        mark = len(g.LOG)
        red(20.0)
        dodge(20.1)
        evade(20.1, True)
        check(g.WORLD.DemoPlayTimeDilation == 1.0
              and sum("the game's own reward, no Witch Time" in line for line in new_lines(g, mark)) == 2,
              "a dodge right after a red-light perfect dodge starts nothing")

        mark = len(g.LOG)
        evade(24.0, False)
        check(any("Witch Time 1: 1.00 s" in line and "OnEvadeDamage normal" in line for line in new_lines(g, mark)),
              "OnEvadeDamage alone starts it too (when the event hook misses); more than 2 s after: 1 s again")
        mark = len(g.LOG)
        at(25.0, "WORLD.DemoPlayTimeDilation = 0.33")
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.33) < 1e-9 and any("left alone" in line for line in new_lines(g, mark)),
              "at the end, a slow-down of the game is left alone")
        mark = len(g.LOG)
        at(25.1, "WORLD.DemoPlayTimeDilation = 1.0")
        check(any("after the end: factor 1.000" in line for line in new_lines(g, mark)),
              "a change right after the end is logged")

        lua.execute("WORLD.DemoPlayTimeDilation = 1.0")
        dodge(30.0)
        mark = len(g.LOG)
        # Only the slow loop (LOOPS[1]) runs: the fast loop missed the end.
        lua.execute("CLOCK = 31.3 LOOPS[1]()")
        check(g.WORLD.DemoPlayTimeDilation == 1.0 and any("safety check" in line for line in new_lines(g, mark)),
              "the safety check ends a Witch Time whose frame loop was missed")

        mark = len(g.LOG)
        dodge(40.0)
        lua.execute("MAP_HOOKS[1]()")
        check(any("Map load during Witch Time" in line for line in new_lines(g, mark)),
              "a map load drops a running Witch Time")
        lua.execute("WORLD.DemoPlayTimeDilation = 1.0")
        mark = len(g.LOG)
        dodge(40.5)
        check(any("Witch Time 1: 1.00 s" in line for line in new_lines(g, mark)),
              "after a map load the next one is a full 1 s again")
        at(41.5)

        mark = len(g.LOG)
        lua.execute(f"CLOCK = 50 CALL('{GLOBAL_SLOMO}:Received_ActionBegin', 'GAAction_GlobalSlomo_C', {{ Multiplier = 0.33 }})")
        lua.execute(f"CALL('{SLOMO}:Received_ActionEnd', 'GAAction_GlobalSlomo_C')")
        lua.execute(f"CALL('{SLOMO}:Received_ActionEnd', 'GAA_TempPerfectDodgeSlomo_C')")
        lines = new_lines(g, mark)
        check(any("game slow-down begins (GAAction_GlobalSlomo, x0.33)" in line for line in lines)
              and sum("game slow-down ends" in line for line in lines) == 1,
              "the game's world slow-downs are logged, other slow-down ends are not")

        def parry(time, result):
            lua.execute(f"CLOCK = {time} CALL('{PARRY_IMPACT}', 'GA_Hun_Tang_Stance_C', nil, 1, RESULT('{result}'))")

        mark = len(g.LOG)
        parry(60.0, "Deflect.Result.Normal")
        check(g.WORLD.DemoPlayTimeDilation == 1.0
              and any("parry Deflect.Result.Normal" in line and "no Witch Time" in line for line in new_lines(g, mark)),
              "a block (Deflect.Result.Normal) starts nothing and is logged")
        mark = len(g.LOG)
        parry(60.5, "Deflect.Result.Great")
        check(g.WORLD.DemoPlayTimeDilation == 1.0 and any("Deflect.Result.Great" in line for line in new_lines(g, mark)),
              "Deflect.Result.Great starts nothing (not in PARRY_RESULTS yet) and is logged")
        mark = len(g.LOG)
        parry(61.0, "Deflect.Result.Perfect")
        check(abs(g.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9
              and any("Witch Time 1: 1.00 s" in line and "parry Deflect.Result.Perfect" in line
                      for line in new_lines(g, mark)),
              "a timed parry (Deflect.Result.Perfect) starts Witch Time")
        mark = len(g.LOG)
        parry(61.3, "Deflect.Result.Perfect")
        check(any("during Witch Time" in line for line in new_lines(g, mark)), "a parry during Witch Time does nothing")
        at(62.0)
        mark = len(g.LOG)
        dodge(62.5)
        check(any("Witch Time 2: 0.50 s" in line for line in new_lines(g, mark)),
              "a dodge after a parry's Witch Time continues the same chain")
        at(63.0)

        lua.execute(f"CLASSES['{DODGE}'].valid = false HOOKS = {{}}")
        mark = len(g.LOG)
        lua.execute("TICK()")
        check(any("The dodge class was unloaded" in line for line in new_lines(g, mark)), "an unloaded class is noticed")
        lua.execute(f"LOAD_CLASSES({{'{DODGE}'}}) TICK()")
        check(lua.eval(f"HOOKS['{TRIGGER}'] ~= nil and HOOKS['{EVADE}'] ~= nil"), "its hooks are set again when it is back")
        check(not any("Error" in g.LOG[i] for i in range(1, len(g.LOG) + 1)), "no error line in the whole Witch Time run")
        check(g.PAWN.CustomTimeDilation == 1.0, "after the run the player has the game's factor (1.0) again")

        # One switch off: only the other trigger's hooks.
        all_classes = f"LOAD_CLASSES({{'{DODGE}', '{RED}', '{PARRY}', '{PLAYER}'}}) TICK()"
        lua7, g7, _ = run(folders["dodge off"])
        lua7.execute(all_classes)
        check(lua7.eval("HOOK_NAMES()") == "\n".join(sorted([PARRY_IMPACT, PLAYER_TIME])),
              "DodgeEnabled = false: no dodge or red-light hook, the parry hook is set")
        lua7.execute(f"CLOCK = 5 CALL('{PARRY_IMPACT}', 'X', nil, 1, RESULT('Deflect.Result.Perfect'))")
        check(abs(g7.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9, "DodgeEnabled = false: a timed parry still starts it")
        lua8, g8, _ = run(folders["parry off"])
        lua8.execute(all_classes)
        check(lua8.eval("HOOK_NAMES()") == "\n".join(sorted([TRIGGER, EVADE, RED_TRIGGER, PLAYER_TIME])),
              "ParryEnabled = false: no parry hook, the dodge hooks are set")

        # PlayerFullSpeed = false: no player hook, the player is never touched.
        lua6, g6, _ = run(folders["PlayerFullSpeed off"])
        lua6.execute(f"LOAD_CLASSES({{'{DODGE}', '{RED}', '{PLAYER}'}}) TICK()")
        check(lua6.eval(f"HOOKS['{PLAYER_TIME}'] == nil"), "PlayerFullSpeed = false: no player time hook")
        lua6.execute(f"CLOCK = 5 CALL('{TRIGGER}', 'X', nil, EVENT('{HIT_DODGE}')) CLOCK = 5.5 TICK()")
        check(abs(g6.WORLD.DemoPlayTimeDilation - 0.3) < 1e-9 and g6.PAWN.CustomTimeDilation == 1.0,
              "PlayerFullSpeed = false: the world slows, the player is not touched")

        # PlayerFullSpeed without the player hook (its class not loaded): the frame loop keeps the factor 1 / 0.3.
        lua3, g3, _ = run(folders["PlayerFullSpeed"])
        lua3.execute(f"LOAD_CLASSES({{'{DODGE}'}}) TICK() CLOCK = 5 CALL('{TRIGGER}', 'X', nil, EVENT('{HIT_DODGE}'))")
        check(abs(g3.PAWN.CustomTimeDilation - 1 / 0.3) < 1e-6, "PlayerFullSpeed: the player's time factor is 1/0.3")
        lua3.execute("CLOCK = 5.2 PAWN.CustomTimeDilation = 0.1 TICK()")
        check(abs(g3.PAWN.CustomTimeDilation - 1 / 0.3) < 1e-6, "PlayerFullSpeed, no hook: the frame loop sets it again")
        lua3.execute("CLOCK = 6 TICK()")
        check(g3.PAWN.CustomTimeDilation == 1.0 and g3.WORLD.DemoPlayTimeDilation == 1.0,
              "PlayerFullSpeed: both back to 1.0 at the end")

        # PlayerFullSpeed with the hook on BP_RenewalPlayer_C:SetCustomTimeDilation (the game writes every frame).
        lua5, g5, _ = run(folders["PlayerFullSpeed"])
        lua5.execute(f"LOAD_CLASSES({{'{DODGE}', '{PLAYER}'}}) TICK()")
        check(lua5.eval(f"HOOKS['{PLAYER_TIME}'] ~= nil"), "PlayerFullSpeed: the player time hook is set")
        lua5.execute("GAME_SETS_PLAYER(PAWN, 1.0)")
        check(g5.PAWN.CustomTimeDilation == 1.0, "the hook changes nothing outside Witch Time")
        lua5.execute(f"CLOCK = 5 CALL('{TRIGGER}', 'X', nil, EVENT('{HIT_DODGE}')) GAME_SETS_PLAYER(PAWN, 1.0)")
        check(abs(g5.PAWN.CustomTimeDilation - 1 / 0.3) < 1e-6, "the game's 1.0 becomes 1/0.3 right after it is written")
        mark = len(g5.LOG)
        lua5.execute("GAME_SETS_PLAYER(PAWN, 0.1) CLOCK = 5.1 TICK()")
        check(abs(g5.PAWN.CustomTimeDilation - 0.1 / 0.3) < 1e-6
              and not any("set again" in line for line in new_lines(g5, mark)),
              "the game's hit-stop on the player (0.1) is kept and multiplied; the frame loop agrees")
        lua5.execute("OTHER = OBJECT({ CustomTimeDilation = 1.0 }) GAME_SETS_PLAYER(OTHER, 0.5) GAME_SETS_PLAYER(PAWN, 1.0)")
        check(g5.OTHER.CustomTimeDilation == 0.5 and abs(g5.PAWN.CustomTimeDilation - 1 / 0.3) < 1e-6,
              "another player character is left alone")
        mark = len(g5.LOG)
        lua5.execute("CLOCK = 6 TICK()")
        check(g5.PAWN.CustomTimeDilation == 1.0
              and any("the game set its time factor 3 times" in line for line in new_lines(g5, mark)),
              "at the end the player gets the game's value back; the end line counts the hook's changes")
        lua5.execute("GAME_SETS_PLAYER(PAWN, 1.0)")
        check(g5.PAWN.CustomTimeDilation == 1.0, "after the end the hook changes nothing")

        # LogTime off: only the hooks that start or stop Witch Time, no time lines.
        lua4, g4, _ = run(folders["LogTime off"])
        lua4.execute(f"LOAD_CLASSES({{'{DODGE}', '{RED}', '{GLOBAL_SLOMO}', '{SLOMO}'}}) TICK()")
        check(lua4.eval("HOOK_NAMES()") == "\n".join(sorted([TRIGGER, EVADE, RED_TRIGGER])),
              "LogTime = false: only the dodge and red-light hooks")
        mark = len(g4.LOG)
        lua4.execute(f"CLOCK = 5 CALL('{TRIGGER}', 'X', nil, EVENT('{HIT_DODGE}')) CLOCK = 6 TICK()")
        check(len(g4.LOG) == mark and g4.WORLD.DemoPlayTimeDilation == 1.0, "LogTime = false: Witch Time runs and ends without log lines")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
