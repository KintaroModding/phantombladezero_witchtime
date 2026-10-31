-- Witch Time for Phantom Blade Zero.
--
-- A dodge that makes an enemy attack miss (DodgeEnabled), or a timed parry (ParryEnabled), slows the whole world down
-- (WorldSpeed, 30 %) for DurationSeconds of real time. Not the red-light perfect dodge: the game already rewards it
-- with its own slow-down and a counter attack. Not a block (holding the parry key). With both switches off the mod
-- does nothing. A dodge or parry within ChainWindowSeconds after a Witch Time ended starts a chained one that lasts
-- ChainFactor times the one before (1 s, 0.5 s, 0.25 s, ...); after a longer pause the chain starts again at the full
-- length. A dodge or parry during a running Witch Time does nothing. PlayerFullSpeed gives the player's character
-- the inverse time factor, so the
-- player keeps full speed: the game sets the player's CustomTimeDilation every frame through the Blueprint event
-- BP_RenewalPlayer_C:SetCustomTimeDilation, so a hook on it multiplies each value right after the game wrote it
-- (the game's own hit-stop on the player keeps working); the frame loop checks the result.
--
-- The trigger: the player's dodge ability BP_PlayerDodge_Base_C (every weapon's dodge is a child of it) waits for
-- the event GameplayEvent.Actor.HitDodge.Target (or .Target.Perfect) and then calls OnEvadeDamage inside its graph.
-- The mod hooks both: the event handler EVADE_EVENT_FUNCTION (the game calls it from outside the Blueprint; its name
-- is made by the editor and can change with a game update) and OnEvadeDamage (a call inside a Blueprint did not
-- reach a hook in the test of 1.0). The red-light perfect dodge activates BP_PlayerPerfectDodge_Base_C
-- (K2_ActivateAbility): no Witch Time when it comes within RED_WINDOW_SECONDS before a dodge, and a Witch Time that
-- started up to RED_WINDOW_SECONDS before it is taken back.
--
-- The parry: the player's parry ability BP_StanceAction_Base_Player_C (every weapon's parry is a child of it; NPCs
-- have their own class) calls "On Impact" when an attack hits the parry, with the result in Result Data
-- (FDeflectResultData.DeflectResult): Deflect.Result.Normal (holding the key, a block), .Great, .Perfect (the key
-- pressed at the right moment) or .PerfectCounter. PARRY_RESULTS lists the ones that start Witch Time.
--
-- The slow-down is DemoPlayTimeDilation of the world settings (PersistentLevel.WorldSettings), a factor that the
-- engine multiplies with TimeDilation and CinematicTimeDilation for the whole world and that only replays use. Not
-- TimeDilation (SetGlobalTimeDilation): the game sets it back to 1.0 every frame (test of 1.2), and its own short
-- world slow-downs (GAAction_GlobalSlomo_C, for example x0.33 for about 0.46 s of real time on the red-light perfect
-- dodge) go there, so they now multiply with Witch Time. While a Witch Time runs, the mod checks its factor every
-- FRAME_MS and sets it again when something changed it. At the end it sets 1.0 only when the factor still has its
-- own value. Time is real time (os.clock), not game time.
--
-- The hooks need the Blueprint classes, which the game loads with the player's abilities: the mod looks for them at
-- start, after each map load and every CHECK_INTERVAL_MS while one is missing, and keeps what it found (a search by
-- name costs a few milliseconds; searches on a timer made another game stutter). LogTime logs each dodge, parry and
-- Witch Time, the game's own slow-downs and the changes of the time factors, for tests. All loops run on the game
-- thread (1.8; see the end of the file).
--
-- Settings: the player edits Scripts\config.lua; DEFAULTS holds every setting with its default. A player (or a mod
-- manager) keeps their config.lua across updates, so a new main.lua can meet an older file: a setting that the file
-- lacks, or has with another type, keeps its default, and the log says so. Read a setting only from Settings, never
-- as `config.X or default`: for a true/false setting, `false or true` is true.

local UEHelpers = require("UEHelpers")

local MOD_NAME = "Witch Time"
local VERSION = "1.8"
local CHECK_INTERVAL_MS = 2000
-- How often a running Witch Time checks the world speed and its end when UE4SS has no engine tick hook (else: every
-- frame).
local FRAME_MS = 16
-- After a Witch Time ends, changes of the world speed are still logged this long (real seconds; LogTime).
local WATCH_AFTER_SECONDS = 0.5
-- Log at most this many error lines in total.
local MAX_ERROR_LOGS = 5
-- Log at most this many LogTime lines per game start, so a long session does not fill the log.
local MAX_TIME_LOGS = 400
-- Per Witch Time, log only the first few set-backs of a changed factor; the end line gives the count.
local MAX_SETBACK_LOGS = 3

-- A red-light perfect dodge and a dodge this close together (real seconds) are the same dodge.
local RED_WINDOW_SECONDS = 0.2

local DODGE_CLASS = "/PBZDeflectSystem/Abilities/BP_PlayerDodge_Base.BP_PlayerDodge_Base_C"
local EVADE_EVENT_FUNCTION = "EventReceived_DE0795E84ADED85523B251A711345C31"
local EVADE_EVENT_TAG = "GameplayEvent.Actor.HitDodge.Target"
local PERFECT_DODGE_CLASS = "/PBZDeflectSystem/Abilities/BP_PlayerPerfectDodge_Base.BP_PlayerPerfectDodge_Base_C"
local PARRY_CLASS = "/PBZDeflectSystem/Abilities/BP_StanceAction_Base_Player.BP_StanceAction_Base_Player_C"
-- The parry results that start Witch Time: only the timed press (the user, 2026-10-29: holding the key to block is
-- not a parry). The others are logged with LogTime.
local PARRY_RESULTS = {
    ["Deflect.Result.Perfect"] = true,
}
local GLOBAL_SLOMO_CLASS = "/MagCombatAbilities/Actions/GAAction_GlobalSlomo.GAAction_GlobalSlomo_C"
local SLOMO_CLASS = "/MagCombatAbilities/Actions/GAAction_Slomo.GAAction_Slomo_C"
-- The player character's Blueprint (every hero is a child of it) and its event that sets the player's time factor.
local PLAYER_CLASS = "/Game/RenewalContent/Prototypes/Character/BP_RenewalPlayer.BP_RenewalPlayer_C"
local PLAYER_TIME_FUNCTION = "SetCustomTimeDilation"

-- Every setting with its default; config.lua ships the same names and values (tests\test_main.py checks it).
local DEFAULTS = {
    DodgeEnabled = true,
    ParryEnabled = true,
    WorldSpeed = 0.3,
    DurationSeconds = 1.0,
    ChainWindowSeconds = 2.0,
    ChainFactor = 0.5,
    PlayerFullSpeed = true,
    LogTime = false,
}

-- Number settings outside these limits use their default.
local LIMITS = {
    WorldSpeed = { 0.05, 1.0 },
    DurationSeconds = { 0.05, 10.0 },
    ChainWindowSeconds = { 0.0, 10.0 },
    ChainFactor = { 0.0, 1.0 },
}

local errors = 0
local timeLogs = 0

local function Log(message)
    print(string.format("[%s] %s\n", MOD_NAME, message))
end

local function LogError(message)
    errors = errors + 1
    if errors <= MAX_ERROR_LOGS then
        Log(message)
    end
end

-- DEFAULTS, with the values of Scripts\config.lua over them.
local function ReadSettings()
    local settings = {}
    for name, value in pairs(DEFAULTS) do
        settings[name] = value
    end
    local ok, config = pcall(require, "config")
    if not ok then
        local message = tostring(config)
        if message:find("module 'config' not found", 1, true) then
            Log("No config.lua; every setting uses its default.")
        else
            -- A broken file: Lua's line number and reason, without the long file path.
            local line, reason = message:match("config%.lua:(%d+): ([^\n]*)")
            local detail = line and ("line " .. line .. ": " .. reason) or message:gsub("%s*\n%s*", " ")
            Log("config.lua not read (" .. detail .. "); every setting uses its default.")
        end
        return settings
    end
    if type(config) ~= "table" then
        Log("config.lua does not return a table; every setting uses its default.")
        return settings
    end
    for name, value in pairs(config) do
        local default = DEFAULTS[name]
        if default == nil then
            Log("config.lua: unknown setting " .. tostring(name) .. " ignored.")
        elseif type(value) ~= type(default) then
            Log(string.format("config.lua: %s must be a %s; the default %s is used.", name, type(default),
                tostring(default)))
        else
            local limit = LIMITS[name]
            if limit and (value < limit[1] or value > limit[2]) then
                Log(string.format("config.lua: %s must be from %s to %s; the default %s is used.", name,
                    tostring(limit[1]), tostring(limit[2]), tostring(default)))
            else
                settings[name] = value
            end
        end
    end
    return settings
end

-- One line for the log: every setting, sorted by name.
local function Describe(settings)
    local names = {}
    for name in pairs(settings) do
        names[#names + 1] = name
    end
    table.sort(names)
    local parts = {}
    for i, name in ipairs(names) do
        parts[i] = name .. "=" .. tostring(settings[name])
    end
    return table.concat(parts, ", ")
end

Log(VERSION .. " loaded.")
local Settings = ReadSettings()
Log("Settings: " .. Describe(Settings))

if not Settings.DodgeEnabled and not Settings.ParryEnabled then
    Log("DodgeEnabled and ParryEnabled are both false in config.lua: the mod does nothing.")
    return
end

-- Real time in seconds (wall time on Windows), so a Witch Time lasts the same however slow the game runs.
local function Now()
    return os.clock()
end

local function Near(a, b)
    return type(a) == "number" and type(b) == "number" and math.abs(a - b) < 0.001
end

local function LogTime(message)
    if not Settings.LogTime then
        return
    end
    timeLogs = timeLogs + 1
    if timeLogs <= MAX_TIME_LOGS then
        Log(string.format("%.3f %s", Now(), message))
    elseif timeLogs == MAX_TIME_LOGS + 1 then
        Log("LogTime: " .. MAX_TIME_LOGS .. " lines reached; no more time lines until the next game start.")
    end
end

local function Valid(object)
    return object ~= nil and object:IsValid()
end

local function ClassName(object)
    local ok, name = pcall(function()
        return object:GetClass():GetFName():ToString()
    end)
    return ok and name or "?"
end

-- Kept objects: the controller and the world settings are checked before each use and searched again only when they
-- are gone (a new map has new ones).
local controller = nil
local worldSettings = nil

local function GetController()
    if not Valid(controller) then
        controller = UEHelpers.GetPlayerController()
    end
    return Valid(controller) and controller or nil
end

local function GetWorldSettings()
    if not Valid(worldSettings) then
        worldSettings = nil
        local pc = GetController()
        local world = pc and pc:GetWorld()
        local level = Valid(world) and world.PersistentLevel or nil
        if Valid(level) then
            worldSettings = level.WorldSettings
        end
    end
    return Valid(worldSettings) and worldSettings or nil
end

-- The mod's factor of the world speed (1.0 = normal).
local function GetWorldSpeed()
    return GetWorldSettings().DemoPlayTimeDilation
end

local function SetWorldSpeed(speed)
    GetWorldSettings().DemoPlayTimeDilation = speed
end

-- The game's own factor, for the log.
local function GetGameSpeed()
    return GetWorldSettings().TimeDilation
end

-- The running Witch Time and the chain. Times are Now() values.
local active = false
local startTime = 0
local endTime = 0
local length = 0
local chainStep = 0
local lastEnd = nil
local lastLength = 0
-- LogTime: changes of the world speed are logged until this time; lastSpeed is the last value seen. setBacks counts
-- how often something changed the factor during the running Witch Time.
local watchUntil = 0
local lastSpeed = nil
local setBacks = 0
local playerSetBacks = 0
-- PlayerFullSpeed. The game sets the player's CustomTimeDilation every frame through PLAYER_TIME_FUNCTION (its own
-- per-actor slow-downs, such as hit-stop; 1.0 otherwise; test of 1.3). The hook on it keeps the game's last value
-- and, during a Witch Time, multiplies it right after the game wrote it. playerPawn is the pawn the mod changed,
-- playerHookFixes counts the hook's changes in the running Witch Time.
local playerPawn = nil
local playerGameFactor = 1.0
local playerHookFixes = 0
-- The last red-light perfect dodge, and the chain as it was before the running Witch Time (to take it back).
local redTime = nil
local chainBefore = nil

local function PlayerFactor()
    return 1.0 / Settings.WorldSpeed
end

local function PlayerTarget()
    return playerGameFactor * PlayerFactor()
end

local function ControlledPawn()
    local pc = GetController()
    local pawn = pc and pc.Pawn
    return Valid(pawn) and pawn or nil
end

-- The frame loop's check: the player's factor must be the game's value times PlayerFactor. With the hook in place a
-- difference means that the game wrote after the hook (see the end line's count).
local function KeepPlayerSpeed()
    local pawn = ControlledPawn()
    if pawn == nil then
        return
    end
    local factor = pawn.CustomTimeDilation
    if not Near(factor, PlayerTarget()) then
        if playerPawn ~= nil then
            playerSetBacks = playerSetBacks + 1
            if playerSetBacks <= MAX_SETBACK_LOGS then
                LogTime(string.format("the player's time factor was %.3f, not %.3f: set again", factor,
                    PlayerTarget()))
            end
        end
        pawn.CustomTimeDilation = PlayerTarget()
        playerPawn = pawn
    end
end

local function RestorePlayerSpeed()
    if Valid(playerPawn) and Near(playerPawn.CustomTimeDilation, PlayerTarget()) then
        playerPawn.CustomTimeDilation = playerGameFactor
    end
    playerPawn = nil
end

-- The hook on PLAYER_TIME_FUNCTION, after the game's own code: the game's value, multiplied during a Witch Time.
local function OnPlayerTimeSet(object, value)
    local pawn = ControlledPawn()
    if pawn == nil or object == nil or object:GetAddress() ~= pawn:GetAddress() or type(value) ~= "number" then
        return
    end
    playerGameFactor = value
    if active and Settings.PlayerFullSpeed then
        object.CustomTimeDilation = value * PlayerFactor()
        playerPawn = pawn
        playerHookFixes = playerHookFixes + 1
    end
end

local function EndWitchTime(reason)
    active = false
    local now = Now()
    lastEnd = now
    lastLength = length
    watchUntil = now + WATCH_AFTER_SECONDS
    local speed = GetWorldSpeed()
    if Near(speed, Settings.WorldSpeed) then
        SetWorldSpeed(1.0)
        lastSpeed = 1.0
    else
        -- Not our value any more: a slow-down of the game runs, so leave it alone.
        LogTime(string.format("factor at the end is %.3f (not ours): left alone", speed))
        lastSpeed = speed
    end
    RestorePlayerSpeed()
    if setBacks > MAX_SETBACK_LOGS then
        LogTime(string.format("the factor was changed and set back %d times in this Witch Time", setBacks))
    end
    if playerSetBacks > MAX_SETBACK_LOGS then
        LogTime(string.format("the player's time factor was changed and set back %d times", playerSetBacks))
    end
    if playerHookFixes > 0 then
        LogTime(string.format("player: the game set its time factor %d times, the hook multiplied each",
            playerHookFixes))
    end
    LogTime(string.format("Witch Time %d ended after %.3f s real time (%s)", chainStep, now - startTime, reason))
end

local function StartWitchTime(source)
    local now = Now()
    if active then
        LogTime("dodge or parry during Witch Time (" .. source .. "): nothing")
        return
    end
    if not GetWorldSettings() then
        LogError("No player controller or world settings: Witch Time skipped.")
        return
    end
    chainBefore = { step = chainStep, lastEnd = lastEnd, lastLength = lastLength }
    if lastEnd ~= nil and now - lastEnd <= Settings.ChainWindowSeconds then
        chainStep = chainStep + 1
        length = lastLength * Settings.ChainFactor
    else
        chainStep = 1
        length = Settings.DurationSeconds
    end
    local before = GetWorldSpeed()
    SetWorldSpeed(Settings.WorldSpeed)
    active = true
    startTime = now
    endTime = now + length
    lastSpeed = Settings.WorldSpeed
    setBacks = 0
    playerSetBacks = 0
    playerHookFixes = 0
    if Settings.PlayerFullSpeed then
        KeepPlayerSpeed()
    end
    LogTime(string.format("Witch Time %d: %.2f s at %.0f %% (%s; factor before %.3f, the game's TimeDilation %.3f)",
        chainStep, length, Settings.WorldSpeed * 100, source, before, GetGameSpeed()))
end

-- A dodge made an enemy attack miss.
local function OnEvade(source)
    local now = Now()
    if redTime ~= nil and now - redTime <= RED_WINDOW_SECONDS then
        LogTime(string.format("dodge %.3f s after a red-light perfect dodge (%s): the game's own reward, no Witch Time",
            now - redTime, source))
        return
    end
    StartWitchTime(source)
end

-- The red-light perfect dodge: the game rewards it itself, so a Witch Time that this dodge started is taken back.
local function OnRedPerfectDodge(source)
    local now = Now()
    redTime = now
    LogTime("red-light perfect dodge (" .. source .. ")")
    if active and now - startTime <= RED_WINDOW_SECONDS then
        EndWitchTime(string.format("taken back: red-light perfect dodge %.3f s after its start", now - startTime))
        chainStep = chainBefore.step
        lastEnd = chainBefore.lastEnd
        lastLength = chainBefore.lastLength
    end
end

-- An attack hit the player's parry; result is its Deflect.Result tag.
local function OnParry(source, result)
    if PARRY_RESULTS[result] then
        StartWitchTime(source .. ", parry " .. result)
    else
        LogTime("parry " .. result .. " (" .. source .. "): no Witch Time")
    end
end

-- Runs every FRAME_MS on the game thread while a Witch Time runs or its end is watched.
local function Frame()
    local now = Now()
    if active then
        if now >= endTime then
            -- First the end, so a slow-down that the game starts in this frame is left alone.
            EndWitchTime("end")
            return
        end
        local speed = GetWorldSpeed()
        if not Near(speed, Settings.WorldSpeed) then
            setBacks = setBacks + 1
            if setBacks <= MAX_SETBACK_LOGS then
                LogTime(string.format("+%.3f s: something set the factor to %.3f; set back to %.3f",
                    now - startTime, speed, Settings.WorldSpeed))
            end
            SetWorldSpeed(Settings.WorldSpeed)
        end
        if Settings.PlayerFullSpeed then
            KeepPlayerSpeed()
        end
    elseif now < watchUntil then
        local speed = GetWorldSpeed()
        if not Near(speed, lastSpeed) then
            LogTime(string.format("+%.3f s after the end: factor %.3f", now - lastEnd, speed))
            lastSpeed = speed
        end
    end
end

-- The hooks, by Blueprint class. "observe" hooks only log (LogTime) and are set only with LogTime = true.
-- context and the parameters are UE4SS RemoteUnrealParam values: :get() gives the value.
local function Context(context)
    local ok, object = pcall(function()
        return context:get()
    end)
    return ok and object or nil
end

local function EventTagOf(payload)
    local ok, tag = pcall(function()
        return payload:get().EventTag.TagName:ToString()
    end)
    return ok and tag or "?"
end

local function NumberOf(param)
    local ok, value = pcall(function()
        return param:get()
    end)
    return ok and value or nil
end

local function BoolOf(param)
    local ok, value = pcall(function()
        return param:get()
    end)
    return ok and value == true
end

-- The Deflect.Result tag of a FDeflectResultData parameter.
local function DeflectResultOf(resultData)
    local ok, tag = pcall(function()
        return resultData:get().DeflectResult.TagName:ToString()
    end)
    return ok and tag or "?"
end

-- "switch" names the setting a group needs (DodgeEnabled, ParryEnabled, PlayerFullSpeed); "observe" groups and hooks
-- only log and are set only with LogTime = true.
local HOOK_GROUPS = {
    {
        label = "dodge",
        path = DODGE_CLASS,
        switch = "DodgeEnabled",
        hooks = {
            {
                -- The handler of the dodge ability's event wait: GameplayEvent.Actor.HitDodge.Target(.Perfect).
                name = EVADE_EVENT_FUNCTION,
                callback = function(context, payload)
                    local tag = EventTagOf(payload)
                    local source = ClassName(Context(context))
                    if tag:sub(1, #EVADE_EVENT_TAG) == EVADE_EVENT_TAG then
                        LogTime("dodge event " .. tag .. " (" .. source .. ")")
                        OnEvade(source .. ", " .. tag:sub(#"GameplayEvent.Actor." + 1))
                    else
                        LogTime("other dodge event " .. tag .. " (" .. source .. ")")
                    end
                end,
            },
            {
                -- Called inside the Blueprint right after the event; may not reach the hook (see the top).
                name = "OnEvadeDamage",
                callback = function(context, sourceAvatar, sourceBullet, perfect)
                    local source = ClassName(Context(context))
                    local kind = BoolOf(perfect) and "perfect" or "normal"
                    LogTime("OnEvadeDamage, " .. kind .. " (" .. source .. ")")
                    OnEvade(source .. ", OnEvadeDamage " .. kind)
                end,
            },
        },
    },
    {
        label = "red-light perfect dodge",
        path = PERFECT_DODGE_CLASS,
        switch = "DodgeEnabled",
        hooks = {
            {
                -- Fires once per red-light perfect dodge, for the weapon's child class (game test of 1.0:
                -- GA_Hun_Tang_PerfectDodge_C), after the game's own slow-down has begun.
                name = "K2_ActivateAbility",
                callback = function(context)
                    OnRedPerfectDodge(ClassName(Context(context)))
                end,
            },
        },
    },
    {
        label = "parry",
        path = PARRY_CLASS,
        switch = "ParryEnabled",
        hooks = {
            {
                -- An attack hit the parry; Result Data holds the Deflect.Result tag.
                name = "On Impact",
                callback = function(context, handle, resultData)
                    OnParry(ClassName(Context(context)), DeflectResultOf(resultData))
                end,
            },
        },
    },
    {
        -- Only with PlayerFullSpeed = true (the default since 1.5).
        label = "player time",
        path = PLAYER_CLASS,
        switch = "PlayerFullSpeed",
        hooks = {
            {
                name = PLAYER_TIME_FUNCTION,
                callback = function(context, customDilation)
                    OnPlayerTimeSet(Context(context), NumberOf(customDilation))
                end,
            },
        },
    },
    {
        label = "game slow-down begin",
        path = GLOBAL_SLOMO_CLASS,
        observe = true,
        hooks = {
            {
                name = "Received_ActionBegin",
                observe = true,
                callback = function(context)
                    local action = Context(context)
                    local multiplier = action and action.Multiplier or "?"
                    LogTime("game slow-down begins (GAAction_GlobalSlomo, x" .. tostring(multiplier) .. ")")
                end,
            },
        },
    },
    {
        label = "game slow-down end",
        path = SLOMO_CLASS,
        observe = true,
        hooks = {
            {
                name = "Received_ActionEnd",
                observe = true,
                callback = function(context)
                    local name = ClassName(Context(context))
                    if name:find("GlobalSlomo", 1, true) then
                        LogTime("game slow-down ends (" .. name .. ")")
                    end
                end,
            },
        },
    },
}

-- Sets the hooks of every class that is loaded now; a class that was unloaded gets them again when it is back.
local function TryHooks()
    for _, group in ipairs(HOOK_GROUPS) do
        if group.class ~= nil and not Valid(group.class) then
            group.class = nil
            Log("The " .. group.label .. " class was unloaded; its hooks are set again when it is back.")
        end
        local wanted = (Settings.LogTime or not group.observe) and (group.switch == nil or Settings[group.switch])
        if group.class == nil and wanted then
            local class = StaticFindObject(group.path)
            if Valid(class) then
                group.class = class
                local names = {}
                for _, hook in ipairs(group.hooks) do
                    if Settings.LogTime or not hook.observe then
                        local ok, err = pcall(RegisterHook, group.path .. ":" .. hook.name, function(context, ...)
                            local done, problem = pcall(hook.callback, context, ...)
                            if not done then
                                LogError("Error in " .. hook.name .. ": " .. tostring(problem))
                            end
                        end)
                        if ok then
                            names[#names + 1] = hook.name
                        else
                            LogError("Hook " .. hook.name .. " not set: " .. tostring(err))
                        end
                    end
                end
                Log("Hooked (" .. group.label .. "): " .. table.concat(names, ", "))
            end
        end
    end
end

-- The slow work: hooks, and a Witch Time that is late (a missed frame loop) ends here at the latest.
local function Tick()
    TryHooks()
    if active and Now() > endTime + 0.25 then
        EndWitchTime("late, ended by the safety check")
    end
end

-- A new map has its own world speed (1.0), and the kept objects belong to the old map.
RegisterLoadMapPostHook(function()
    local ok, err = pcall(function()
        if active then
            active = false
            -- The pawn usually goes with the old map; one that stays gets the game's factor back.
            pcall(RestorePlayerSpeed)
            LogTime("Map load during Witch Time " .. chainStep .. ": dropped; the new map runs at full speed.")
        end
        lastEnd = nil
        playerGameFactor = 1.0
        watchUntil = 0
        redTime = nil
        controller = nil
        worldSettings = nil
        playerPawn = nil
        TryHooks()
    end)
    if not ok then
        LogError("Error after the map load: " .. tostring(err))
    end
end)

local started, problem = pcall(TryHooks)
if not started then
    LogError("Error at start: " .. tostring(problem))
end

local function CheckStep()
    local ok, err = pcall(Tick)
    if not ok then
        LogError("Error: " .. tostring(err))
    end
end

-- The fast step does nothing while no Witch Time runs and no end is watched.
local function FrameStep()
    if not (active or Now() < watchUntil) then
        return
    end
    local ok, err = pcall(Frame)
    if not ok then
        LogError("Error: " .. tostring(err))
        if active then
            -- Never leave the game slow: end it, and if even that fails, the safety check ends it.
            pcall(EndWitchTime, "error")
        end
    end
end

-- Both loops run on the game thread (UE4SS's Delayed Action API), the same thread as the hooks: 1.7 used LoopAsync,
-- whose callback runs on a second thread in the same Lua state, and a player's crash report showed a damaged state
-- (2026-10-30: "attempt to compare number with function" on a line that compares two numbers, then UE4SS's "Ref was
-- not function"). The fast step runs once per frame when UE4SS has its engine tick hook (EngineTickAvailable), else
-- every FRAME_MS. A UE4SS without the Delayed Action API gets the old LoopAsync loops, and the log says so.
if type(LoopInGameThreadWithDelay) == "function" then
    LoopInGameThreadWithDelay(CHECK_INTERVAL_MS, CheckStep)
    if EngineTickAvailable == true and type(LoopInGameThreadAfterFrames) == "function" then
        LoopInGameThreadAfterFrames(1, FrameStep)
    else
        LoopInGameThreadWithDelay(FRAME_MS, FrameStep)
    end
else
    Log("This UE4SS has no game-thread loops (LoopInGameThreadWithDelay): the older LoopAsync is used; a newer UE4SS "
        .. "is safer.")
    LoopAsync(CHECK_INTERVAL_MS, function()
        ExecuteInGameThread(CheckStep)
        -- false keeps the loop running.
        return false
    end)
    LoopAsync(FRAME_MS, function()
        if active or Now() < watchUntil then
            ExecuteInGameThread(FrameStep)
        end
        return false
    end)
end
