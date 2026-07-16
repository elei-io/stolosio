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
