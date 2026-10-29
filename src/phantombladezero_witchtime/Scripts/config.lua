-- Settings of Witch Time. Change a value, save the file, and start the game again: UE4SS reads it at game start.
-- A setting that this file does not have keeps its default, so an older copy of the file still works after an update.
return {
    DodgeEnabled = true,        -- a dodge that makes an attack miss starts Witch Time (not the red-light perfect dodge)
    ParryEnabled = true,        -- a parry at the right moment starts Witch Time (not a block); both false: mod off
    WorldSpeed = 0.3,           -- the world's speed during Witch Time (0.3 = 30 %; from 0.05 to 1.0)
    DurationSeconds = 1.0,      -- real seconds of the first Witch Time (from 0.05 to 10)
    ChainWindowSeconds = 2.0,   -- a dodge or parry this many seconds after a Witch Time ended starts a chained one
    ChainFactor = 0.5,          -- each chained Witch Time lasts this times the one before (from 0 to 1)
    PlayerFullSpeed = true,     -- true: you keep full speed while the world is slow; false: you slow down too
    LogTime = false,            -- true: ue4ss\UE4SS.log names each dodge, parry and Witch Time (for tests)
}
