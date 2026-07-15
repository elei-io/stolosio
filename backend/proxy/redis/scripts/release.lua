local time = redis.call('TIME')
local now = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)

if redis.call('EXISTS', KEYS[1]) == 0 then
    redis.call('ZREM', KEYS[2], ARGV[1])
    redis.call('ZREM', KEYS[3], ARGV[1])
    return 1
end

local current = redis.call('HGET', KEYS[1], 'state')
if current == 'closed' or current == 'failed' then
    return 1
end
if redis.call('HGET', KEYS[1], 'owner_id') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[3] then
    return 0
end

redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call(
    'HSET', KEYS[1],
    'state', ARGV[6],
    'closed_at_ms', now,
    'terminal_reason', ARGV[7]
)
redis.call('PEXPIRE', KEYS[1], ARGV[8])
redis.call(
    'XADD', KEYS[4], 'MAXLEN', '~', ARGV[9], '*',
    'event', 'session.' .. ARGV[6],
    'session_id', ARGV[1],
    'requested_provider', ARGV[4],
    'resolved_provider', ARGV[5],
    'timestamp_ms', now,
    'reason', ARGV[7]
)
return 1
