local time = redis.call('TIME')
local now = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)

if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
if redis.call('HGET', KEYS[1], 'owner_id') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[3] then
    return 0
end

local state = redis.call('HGET', KEYS[1], 'state')
if state ~= 'acquiring' and state ~= 'connected' and state ~= 'closing' then
    return 0
end

redis.call('ZADD', KEYS[2], now + tonumber(ARGV[4]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[4])
return 1
