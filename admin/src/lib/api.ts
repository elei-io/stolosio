import type { ApiErrorResponse } from "@/types/api"

const DEFAULT_API_ERROR = "Something went wrong. Please try again."

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  return typeof value === "object" && value !== null
}

export function extractApiError(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }

  if (isApiErrorResponse(error)) {
    if (typeof error.detail === "string" && error.detail) {
      return error.detail
    }
    if (typeof error.message === "string" && error.message) {
      return error.message
    }
  }

  return DEFAULT_API_ERROR
}

export async function apiRequest<T>(
  url: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}
