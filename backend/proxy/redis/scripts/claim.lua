local time = redis.call('TIME')
local now = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local queued = redis.call('ZRANGE', KEYS[3], 0, -1)
for _, session_id in ipairs(queued) do
    if redis.call('EXISTS', ARGV[8] .. session_id) == 0 then
        redis.call('ZREM', KEYS[3], session_id)
    end
end

if redis.call('EXISTS', KEYS[1]) == 0 then
    return {'missing'}
end
if redis.call('HGET', KEYS[1], 'owner_id') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[3] then
    return {'lost'}
end

local head = redis.call('ZRANGE', KEYS[3], 0, 0)
if #head == 0 or head[1] ~= ARGV[1] then
    return {'waiting'}
end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[5]) then
    return {'waiting'}
end

redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[6]), ARGV[1])
redis.call('HSET', KEYS[1], 'state', 'acquiring', 'acquiring_at_ms', now)
redis.call('PEXPIRE', KEYS[1], ARGV[6])
redis.call(
    'XADD', KEYS[4], 'MAXLEN', '~', ARGV[7], '*',
    'event', 'session.acquiring',
    'session_id', ARGV[1],
    'requested_provider', redis.call('HGET', KEYS[1], 'requested_provider'),
    'resolved_provider', ARGV[4],
    'timestamp_ms', now,
    'reason', ''
)
return {'acquiring'}
