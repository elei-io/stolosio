export function summarizeCommandMethods(
  payload: Record<string, unknown>
): string | null {
  const methods = payload.methods
  if (!methods || typeof methods !== "object" || Array.isArray(methods)) {
    return null
  }
  const usages = Object.values(methods)
  const commandCount = usages.reduce((total, usage) => {
    if (!usage || typeof usage !== "object" || Array.isArray(usage)) {
      return total
    }
    const count = (usage as Record<string, unknown>).count
    return total + (typeof count === "number" ? count : 0)
  }, 0)
  const methodCount = Object.keys(methods).length
  return `${commandCount} ${commandCount === 1 ? "command" : "commands"} across ${methodCount} ${
    methodCount === 1 ? "method" : "methods"
  }`
}

export function eventTitle(event: { event_type: string }) {
  const labels: Record<string, string> = {
    "session.open": "Session opened",
    "session.closed": "Session closed",
    "session.failed": "Session failed",
    "attempt.connected": "Browser connected",
    "attempt.closed": "Browser released",
    "attempt.failed": "Browser acquisition failed",
    "command.summary": "Command summary",
    "command.failed": "Command failed",
    "command.interrupted": "Command interrupted",
    "navigation.requested": "Navigation started",
    "navigation.redirected": "Navigation redirected",
    "navigation.response": "Page response",
    "navigation.failed": "Navigation failed",
    "page.content_observed": "Page content observed",
    "page.crashed": "Page crashed",
    "console.message": "Console observation",
    "javascript.exception": "JavaScript exception",
    "provider.disconnected": "Provider disconnected",
    "capture.completed": "Capture finished",
  }
  return (
    labels[event.event_type] ??
    event.event_type.replaceAll("_", " ").replaceAll(".", " ")
  )
}

export function captureEventDetail(
  payload: Record<string, unknown>
): string | null {
  if (payload.outcome !== "captured" && payload.outcome !== "failed")
    return null
  const tiers = Array.isArray(payload.tiers) ? payload.tiers : []
  const path = tiers.includes("challenge_resolution")
    ? "challenge resolution"
    : tiers.includes("local_resolution")
      ? "internal challenge resolution"
    : tiers.includes("managed")
      ? "local browser"
      : "HTTP only"
  return `${payload.outcome === "captured" ? "Page acquired" : "Capture failed"} · ${path}${typeof payload.failure_code === "string" ? ` · ${payload.failure_code.replaceAll("_", " ")}` : ""}`
}
