local time = redis.call('TIME')
local now = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local queued = redis.call('ZRANGE', KEYS[3], 0, -1)
for _, session_id in ipairs(queued) do
    if redis.call('EXISTS', ARGV[12] .. session_id) == 0 then
        redis.call('ZREM', KEYS[3], session_id)
    end
end

local function event(state, reason)
    redis.call(
        'XADD', KEYS[5], 'MAXLEN', '~', ARGV[11], '*',
        'event', 'session.' .. state,
        'session_id', ARGV[1],
        'requested_provider', ARGV[4],
        'resolved_provider', ARGV[5],
        'timestamp_ms', now,
        'reason', reason
    )
end

redis.call(
    'HSET', KEYS[1],
    'session_id', ARGV[1],
    'owner_id', ARGV[2],
    'lease_token', ARGV[3],
    'state', 'requested',
    'requested_provider', ARGV[4],
    'resolved_provider', ARGV[5],
    'created_at_ms', now
)
event('requested', '')

local active_count = redis.call('ZCARD', KEYS[2])
local queue_count = redis.call('ZCARD', KEYS[3])
if queue_count == 0 and active_count < tonumber(ARGV[6]) then
    redis.call('ZADD', KEYS[2], now + tonumber(ARGV[8]), ARGV[1])
    redis.call('HSET', KEYS[1], 'state', 'acquiring', 'acquiring_at_ms', now)
    redis.call('PEXPIRE', KEYS[1], ARGV[8])
    event('acquiring', '')
    return {'acquiring', tostring(now)}
end

if queue_count < tonumber(ARGV[7]) then
    local sequence = redis.call('INCR', KEYS[4])
    redis.call('ZADD', KEYS[3], sequence, ARGV[1])
    redis.call('HSET', KEYS[1], 'state', 'queued', 'queued_at_ms', now)
    redis.call('PEXPIRE', KEYS[1], ARGV[9])
    event('queued', '')
    return {'queued', tostring(now)}
end

redis.call(
    'HSET', KEYS[1],
    'state', 'failed',
    'closed_at_ms', now,
    'terminal_reason', 'provider_queue_full'
)
redis.call('PEXPIRE', KEYS[1], ARGV[10])
event('failed', 'provider_queue_full')
return {'full', tostring(now)}
