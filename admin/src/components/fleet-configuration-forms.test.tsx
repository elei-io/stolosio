import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { describe, expect, it, vi } from "vitest"

import {
  ConfigurationForm,
  ExternalCapacityForm,
} from "@/components/fleet-configuration-forms"
import type { FleetConfiguration } from "@/types/api"

const configuration: FleetConfiguration = {
  provider: "browserless",
  enabled: true,
  minimum_instances: 1,
  maximum_instances: 4,
  session_capacity_per_instance: 5,
  scale_down_cooldown_seconds: 30,
  max_queued_attempts: 100,
  desired_instances: 1,
  configuration_version: 1,
  controller_status: null,
}

function renderForm(form: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(<QueryClientProvider client={client}>{form}</QueryClientProvider>)
  return client
}

describe("capacity mutations", () => {
  it("saves fleet values and refreshes configurations", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json(configuration))
    vi.stubGlobal("fetch", fetch)
    const success = vi.spyOn(toast, "success").mockImplementation(() => "toast")
    const client = renderForm(
      <ConfigurationForm configuration={configuration} />
    )
    const refresh = vi.spyOn(client, "invalidateQueries")
    const user = userEvent.setup()
    await user.clear(
      screen.getByRole("spinbutton", { name: /Maximum instances/ })
    )
    await user.type(
      screen.getByRole("spinbutton", { name: /Maximum instances/ }),
      "6"
    )
    await user.click(screen.getByRole("button", { name: "Save changes" }))
    await waitFor(() => expect(success).toHaveBeenCalled())
    expect(fetch).toHaveBeenCalledWith(
      "/v1/admin/fleets/browserless",
      expect.objectContaining({ method: "PATCH" })
    )
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      minimum_instances: 1,
      maximum_instances: 6,
    })
    expect(refresh).toHaveBeenCalledWith({ queryKey: ["fleet-configurations"] })
  })

  it("rejects invalid fleet bounds before sending a mutation", async () => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    const error = vi.spyOn(toast, "error").mockImplementation(() => "toast")
    renderForm(<ConfigurationForm configuration={configuration} />)
    const user = userEvent.setup()
    await user.clear(
      screen.getByRole("spinbutton", { name: /Minimum instances/ })
    )
    await user.type(
      screen.getByRole("spinbutton", { name: /Minimum instances/ }),
      "5"
    )
    await user.click(screen.getByRole("button", { name: "Save changes" }))
    expect(error).toHaveBeenCalledWith(
      "Minimum instances cannot exceed maximum instances"
    )
    expect(fetch).not.toHaveBeenCalled()
  })

  it("surfaces provider mutation errors through the API error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ detail: "Capacity rejected" }, { status: 422 })
        )
    )
    const error = vi.spyOn(toast, "error").mockImplementation(() => "toast")
    renderForm(
      <ExternalCapacityForm
        capacity={{
          provider: "browserless_cloud",
          enabled: false,
          max_active_sessions: 15,
          max_queued_attempts: 100,
          configuration_version: 1,
        }}
      />
    )
    const user = userEvent.setup()
    await user.clear(
      screen.getByRole("spinbutton", { name: /Maximum concurrent sessions/ })
    )
    await user.type(
      screen.getByRole("spinbutton", { name: /Maximum concurrent sessions/ }),
      "20"
    )
    await user.click(screen.getByRole("button", { name: "Save limits" }))
    await waitFor(() => expect(error).toHaveBeenCalledWith("Capacity rejected"))
  })
})
