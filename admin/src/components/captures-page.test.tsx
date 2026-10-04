import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { expect, it, vi } from "vitest"
import { CapturesPage } from "@/components/captures-page"

it("displays internal resolution in capture history", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.startsWith("/v1/admin/sessions?")
        ? Response.json({
            sessions: [
              {
                id: "capture-id",
                workload: "capture",
                capture_hostname: "example.test",
                capture_outcome: "captured",
                capture_path: "local_resolution",
                capture: null,
                created_at: "2026-10-04T00:00:00Z",
                duration_seconds: 2,
                modeled_cost_units: 0,
              },
            ],
            next_cursor: null,
          })
        : Response.json({ detail: "Overview unavailable" }, { status: 503 })
    )
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <CapturesPage navigate={vi.fn()} />
    </QueryClientProvider>
  )
  expect(
    await screen.findByRole("button", {
      name: /example.test.*Internal challenge resolution/,
    })
  ).toBeTruthy()
})
