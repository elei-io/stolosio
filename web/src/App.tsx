import {
  Activity,
  Anchor,
  Boxes,
  CircleDot,
  Coins,
  Gauge,
  Globe2,
  Menu,
  Moon,
  Network,
  Sun,
  X,
} from "lucide-react"
import { type ComponentType, useEffect, useState } from "react"

import { useTheme } from "@/components/theme-provider"
import { ActivityPage } from "@/components/activity-page"
import { CostPage } from "@/components/cost-page"
import { DomainsPage } from "@/components/domains-page"
import { FleetsPage } from "@/components/fleets-page"
import { OverviewPage } from "@/components/overview-page"
import { RoutingPage } from "@/components/routing-page"
import { SessionsPage } from "@/components/sessions-page"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

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
    label: "Activity",
    href: "/activity",
    icon: Activity,
    description: "A live, filtered stream of events across Harbor sessions.",
  },
  {
    label: "Sessions",
    href: "/sessions",
    icon: CircleDot,
    description: "Search session history and inspect individual timelines.",
  },
  {
    label: "Cost",
    href: "/cost",
    icon: Coins,
    description: "Provider usage, modeled spend, and CDP action attribution.",
  },
  {
    label: "Fleets",
    href: "/fleets",
    icon: Boxes,
    description: "Provider capacity, instances, queues, and scaling policy.",
  },
  {
    label: "Routing",
    href: "/routing",
    icon: Network,
    description: "Automatic provider selection, costs, and routing policy.",
  },
  {
    label: "Domains",
    href: "/domains",
    icon: Globe2,
    description: "Per-domain provider support and runtime transition evidence.",
  },
]

function getPathname() {
  const pathname = window.location.pathname.replace(/\/$/, "") || "/"
  return pathname === "/" ? "/activity" : pathname
}

function StubPage({ item }: { item: NavigationItem }) {
  const Icon = item.icon

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-7xl items-center px-6 py-20 lg:px-10">
      <section className="max-w-xl">
        <span className="mb-6 flex size-11 items-center justify-center rounded-lg border bg-card shadow-sm">
          <Icon className="size-5 text-muted-foreground" aria-hidden />
        </span>
        <p className="mb-3 font-mono text-xs font-medium tracking-[0.18em] text-muted-foreground uppercase">
          Harbor console
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          {item.label}
        </h1>
        <p className="mt-4 text-base leading-7 text-muted-foreground">
          {item.description}
        </p>
        <div className="mt-8 inline-flex items-center rounded-full border bg-muted/40 px-3 py-1.5 text-xs font-medium text-muted-foreground">
          Planned workspace
        </div>
      </section>
    </main>
  )
}

export default function App() {
  const { theme, setTheme } = useTheme()
  const [pathname, setPathname] = useState(getPathname)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState({}, "", "/activity")
    }

    const handlePopState = () => setPathname(getPathname())
    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

  const navigate = (href: string) => {
    if (href !== pathname) {
      window.history.pushState({}, "", href)
      setPathname(href)
    }
    setSidebarOpen(false)
  }

  const activeItem =
    navigationItems.find(
      ({ href }) => pathname === href || pathname.startsWith(`${href}/`)
    ) ?? navigationItems[1]
  const fleetProvider = pathname.startsWith("/fleets/")
    ? pathname.slice("/fleets/".length)
    : undefined
  const domainId = pathname.startsWith("/domains/")
    ? Number(pathname.slice("/domains/".length))
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
            href="/activity"
            className="flex items-center gap-2.5 font-semibold tracking-tight"
            onClick={(event) => {
              event.preventDefault()
              navigate("/activity")
            }}
          >
            <span className="flex size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
              <Anchor className="size-4" aria-hidden />
            </span>
            Harbor
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

        <nav className="flex-1 px-3 py-4" aria-label="Harbor navigation">
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
          <span className="ml-3 font-semibold">Harbor</span>
          <span className="ml-2 text-sm text-muted-foreground">
            / {activeItem.label}
          </span>
        </header>

        {activeItem.href === "/overview" ? (
          <OverviewPage navigate={navigate} />
        ) : activeItem.href === "/activity" ? (
          <ActivityPage navigate={navigate} />
        ) : activeItem.href === "/fleets" ? (
          <FleetsPage provider={fleetProvider} navigate={navigate} />
        ) : activeItem.href === "/routing" ? (
          <RoutingPage />
        ) : activeItem.href === "/sessions" ? (
          <SessionsPage sessionId={sessionId} navigate={navigate} />
        ) : activeItem.href === "/cost" ? (
          <CostPage navigate={navigate} />
        ) : activeItem.href === "/domains" ? (
          <DomainsPage
            domainId={Number.isInteger(domainId) ? domainId : undefined}
            navigate={navigate}
          />
        ) : (
          <StubPage item={activeItem} />
        )}
      </div>
    </div>
  )
}
