-- #92: per-frame wall time (ms, msvcrt clock() = 1 ms resolution under Wine), flushed every 300 frames
local f = io.open("frametime.txt", "w")
local last = os.clock(); local buf = {}
emu.registerafter(function()
  local t = os.clock(); buf[#buf + 1] = string.format("%.1f", (t - last) * 1000); last = t
  if #buf >= 300 then f:write(table.concat(buf, "\n") .. "\n"); f:flush(); buf = {} end
end)
