import {
  Activity,
  Anchor,
  Boxes,
  CircleDot,
  Coins,
  Gauge,
  FileText,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react"
import { type ComponentType, lazy, Suspense, useEffect, useState } from "react"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const CapturesPage = lazy(() =>
  import("@/components/captures-page").then((module) => ({
    default: module.CapturesPage,
  }))
)
const ActivityPage = lazy(() =>
  import("@/components/activity-page").then((module) => ({
    default: module.ActivityPage,
  }))
)
const CostPage = lazy(() =>
  import("@/components/cost-page").then((module) => ({
    default: module.CostPage,
  }))
)
const FleetsPage = lazy(() =>
  import("@/components/fleets-page").then((module) => ({
    default: module.FleetsPage,
  }))
)
const OverviewPage = lazy(() =>
  import("@/components/overview-page").then((module) => ({
    default: module.OverviewPage,
  }))
)
const PolicyPage = lazy(() =>
  import("@/components/policy-page").then((module) => ({
    default: module.PolicyPage,
  }))
)
const SessionsPage = lazy(() =>
  import("@/components/sessions-page").then((module) => ({
    default: module.SessionsPage,
  }))
)

type NavigationItem = {
  description: string
  href: string
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>
  label: string
}

const navigationItems: NavigationItem[] = [
  {
    label: "Overview",
    href: "/overview",
    icon: Gauge,
    description: "Gateway health, demand, and capacity at a glance.",
  },
  {
    label: "Sessions",
    href: "/sessions",
    icon: CircleDot,
    description: "Search session history and inspect individual timelines.",
  },
  {
    label: "Captures",
    href: "/captures",
    icon: FileText,
    description: "Page acquisition outcomes and evidence.",
  },
  {
    label: "Usage",
    href: "/cost",
    icon: Coins,
    description: "Provider usage, modeled spend, and CDP action attribution.",
  },
  {
    label: "Capacity",
    href: "/fleets",
    icon: Boxes,
    description: "Provider capacity, instances, queues, and scaling policy.",
  },
  {
    label: "Settings",
    href: "/policy",
    icon: ShieldCheck,
    description: "Global request blocking and provider cost rates.",
  },
  {
    label: "Events",
    href: "/activity",
    icon: Activity,
    description: "A live, filtered stream of events across Stolosio sessions.",
  },
]

function getPathname() {
  const pathname = window.location.pathname.replace(/\/$/, "") || "/"
  return pathname === "/" ? "/overview" : pathname
}

export default function App() {
  const { theme, setTheme } = useTheme()
  const [pathname, setPathname] = useState(getPathname)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState({}, "", "/overview")
    }

    const handlePopState = () => setPathname(getPathname())
    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

  const navigate = (href: string) => {
    if (href !== pathname) {
      window.history.pushState({}, "", href)
      setPathname(new URL(href, window.location.origin).pathname)
    }
    setSidebarOpen(false)
  }

  const activeItem =
    navigationItems.find(
      ({ href }) => pathname === href || pathname.startsWith(`${href}/`)
    ) ?? navigationItems[0]
  const fleetProvider = pathname.startsWith("/fleets/")
    ? pathname.slice("/fleets/".length)
    : undefined
  const captureId = pathname.startsWith("/captures/")
    ? pathname.slice("/captures/".length)
    : undefined
  const sessionId = pathname.startsWith("/sessions/")
    ? pathname.slice("/sessions/".length)
    : undefined

  return (
    <div className="min-h-svh bg-background text-foreground">
      {sidebarOpen && (
        <Button
          type="button"
          variant="ghost"
          className="fixed inset-0 z-30 h-auto w-auto rounded-none bg-black/35 p-0 hover:bg-black/35 md:hidden"
          aria-label="Close navigation"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform md:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex h-14 items-center justify-between border-b border-sidebar-border px-4">
          <a
            href="/overview"
            className="flex items-center gap-2.5 font-semibold tracking-tight"
            onClick={(event) => {
              event.preventDefault()
              navigate("/overview")
            }}
          >
            <span className="flex size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
              <Anchor className="size-4" aria-hidden />
            </span>
            Stolosio
          </a>
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label="Close navigation"
            onClick={() => setSidebarOpen(false)}
          >
            <X aria-hidden />
          </Button>
        </div>

        <nav className="flex-1 px-3 py-4" aria-label="Stolosio navigation">
          <p className="mb-2 px-3 text-[0.6875rem] font-semibold tracking-[0.16em] text-muted-foreground">
            OPERATE
          </p>
          <ul className="space-y-1">
            {navigationItems.map(({ href, icon: Icon, label }) => {
              const isActive =
                pathname === href || pathname.startsWith(`${href}/`)

              return (
                <li key={href}>
                  <a
                    href={href}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground"
                    )}
                    onClick={(event) => {
                      event.preventDefault()
                      navigate(href)
                    }}
                  >
                    <Icon className="size-4" aria-hidden />
                    {label}
                  </a>
                </li>
              )
            })}
          </ul>
        </nav>

        <div className="border-t border-sidebar-border p-3">
          <Button
            variant="ghost"
            className="w-full justify-start gap-3 text-muted-foreground"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </Button>
        </div>
      </aside>

      <div className="md:pl-56">
        <header className="flex h-16 items-center border-b bg-background px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Open navigation"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu aria-hidden />
          </Button>
          <span className="ml-3 font-semibold">Stolosio</span>
          <span className="ml-2 text-sm text-muted-foreground">
            / {activeItem.label}
          </span>
        </header>

        <Suspense
          fallback={
            <div role="status" className="p-6 text-muted-foreground">
              Loading page…
            </div>
          }
        >
          {activeItem.href === "/overview" ? (
            <OverviewPage navigate={navigate} />
          ) : activeItem.href === "/activity" ? (
            <ActivityPage navigate={navigate} />
          ) : activeItem.href === "/fleets" ? (
            <FleetsPage provider={fleetProvider} navigate={navigate} />
          ) : activeItem.href === "/policy" ? (
            <PolicyPage />
          ) : activeItem.href === "/sessions" ? (
            <SessionsPage sessionId={sessionId} navigate={navigate} />
          ) : activeItem.href === "/captures" ? (
            <CapturesPage sessionId={captureId} navigate={navigate} />
          ) : activeItem.href === "/cost" ? (
            <CostPage navigate={navigate} />
          ) : null}
        </Suspense>
      </div>
    </div>
  )
}
