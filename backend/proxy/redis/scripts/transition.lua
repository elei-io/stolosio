local time = redis.call('TIME')
local now = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)

if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
if redis.call('HGET', KEYS[1], 'owner_id') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[3] then
    return 0
end

local current = redis.call('HGET', KEYS[1], 'state')
local target = ARGV[6]
local valid = (current == 'acquiring' and target == 'connected')
    or (current == 'connected' and target == 'closing')
if not valid then
    return 0
end

redis.call('HSET', KEYS[1], 'state', target, target .. '_at_ms', now)
redis.call('PEXPIRE', KEYS[1], ARGV[7])
redis.call(
    'XADD', KEYS[2], 'MAXLEN', '~', ARGV[8], '*',
    'event', 'session.' .. target,
    'session_id', ARGV[1],
    'requested_provider', ARGV[4],
    'resolved_provider', ARGV[5],
    'timestamp_ms', now,
    'reason', ''
)
return 1
