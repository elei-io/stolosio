import { useQuery } from "@tanstack/react-query"
import { extractApiError } from "@/lib/api"
import type { CapturePath } from "@/types/contracts"
import type { OperationsOverview, OverviewWindow } from "@/types/api"

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return response.json() as Promise<T>
}

export function useOverview(window: OverviewWindow) {
  return useQuery({
    queryKey: ["operations-overview", window],
    queryFn: () =>
      fetchJson<OperationsOverview>(`/v1/admin/overview?window=${window}`),
    refetchInterval: 15_000,
  })
}

export const number = (value: number) => new Intl.NumberFormat().format(value)
export const plural = (value: number, noun: string) =>
  `${number(value)} ${noun}${value === 1 ? "" : "s"}`
export const percent = (part: number, total: number) =>
  total ? `${((100 * part) / total).toFixed(1)}%` : "—"
export const humanize = (value: string) =>
  value.replaceAll("_", " ").replaceAll(".", " ")
export function duration(ms: number | null) {
  if (ms === null) return "—"
  if (ms === 0) return "0s"
  if (ms < 1000) return `${Math.round(ms)}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${(seconds / 3600).toFixed(1)}h`
}
export const pathLabels = {
  http: "HTTP only",
  managed: "Local browser",
  local_resolution: "Internal challenge resolution",
  challenge_resolution: "External challenge resolution",
} satisfies Record<CapturePath, string>
