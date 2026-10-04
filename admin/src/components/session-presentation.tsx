import { ArrowRight, CheckCircle2, CircleDot, XCircle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { ActivityProvider, StolosioSessionState } from "@/types/api"

import { providerLabels } from "@/lib/session-format"

export function StateBadge({ state }: { state: StolosioSessionState }) {
  const terminal = state === "closed"
  const failed = state === "failed"
  const Icon = terminal ? CheckCircle2 : failed ? XCircle : CircleDot
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 capitalize",
        terminal &&
          "border-emerald-500/25 text-emerald-700 dark:text-emerald-400",
        failed && "border-destructive/25 text-destructive",
        !terminal &&
          !failed &&
          "border-amber-500/25 text-amber-700 dark:text-amber-400"
      )}
    >
      <Icon className="size-3" />
      {state}
    </Badge>
  )
}

export function ProviderPath({ providers }: { providers: ActivityProvider[] }) {
  if (!providers.length) {
    return <span className="text-muted-foreground">No attempt</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {providers.map((provider, index) => (
        <span className="contents" key={`${provider}-${index}`}>
          {index > 0 && <ArrowRight className="size-3 text-muted-foreground" />}
          <Badge variant="secondary">{providerLabels[provider]}</Badge>
        </span>
      ))}
    </span>
  )
}
