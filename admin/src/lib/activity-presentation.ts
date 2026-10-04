import { captureEventDetail, summarizeCommandMethods } from "@/lib/events"
import type { ActivityEvent } from "@/types/api"

export function eventDetail(event: ActivityEvent) {
  const payload = event.payload
  const captureDetail = captureEventDetail(payload)
  if (captureDetail) return captureDetail
  const commandSummary = summarizeCommandMethods(payload)
  if (commandSummary) return commandSummary
  if (
    typeof payload.from_provider === "string" &&
    typeof payload.to_provider === "string"
  ) {
    return `${payload.from_provider} → ${payload.to_provider}`
  }
  if (typeof payload.reason === "string") {
    return payload.reason.replaceAll("_", " ")
  }
  if (typeof payload.method === "string") return payload.method
  if (typeof payload.status === "number") {
    const url = typeof payload.url === "string" ? ` · ${payload.url}` : ""
    return `HTTP ${payload.status}${url}`
  }
  if (typeof payload.url === "string") return payload.url
  return event.outcome ?? "observed"
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "long",
  }).format(new Date(value))
}

export function eventTone(event: ActivityEvent) {
  if (event.outcome === "failure") return "text-rose-300"
  if (event.outcome === "interrupted") return "text-amber-300"
  if (event.event_family === "navigation") return "text-sky-300"
  if (event.event_family === "session") return "text-emerald-300"
  return "text-slate-300"
}
