import type { ActivityProvider } from "@/types/api"
import { duration } from "@/lib/observability"

export function formatDate(value: string | null) {
  if (!value) return "—"
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value))
}

export function formatDuration(seconds: number | null) {
  return seconds === null ? "In progress" : duration(seconds * 1000)
}

export function formatMilliseconds(milliseconds: number | null) {
  return duration(milliseconds)
}

export function humanize(value: string | null) {
  return value?.replaceAll("_", " ") ?? "—"
}

export const providerLabels: Record<ActivityProvider, string> = {
  browserless_cloud: "Browserless cloud",
  browserless: "Browserless",
}
