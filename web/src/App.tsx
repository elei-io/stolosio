import { Anchor, Globe2, Menu, Moon, Sun, X } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme-provider"
import { cn } from "@/lib/utils"

const fleetProviders = [
  { label: "Http", slug: "http" },
  { label: "Chromium", slug: "chromium" },
  { label: "Camofoux", slug: "camofoux" },
  { label: "Lightpanda", slug: "lightpanda" },
  { label: "Browserless", slug: "browserless" },
] as const

function getPathname() {
  return window.location.pathname.replace(/\/$/, "") || "/"
}

export default function App() {
  const { theme, setTheme } = useTheme()
  const [pathname, setPathname] = useState(getPathname)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
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

  const activeProvider = fleetProviders.find(
    ({ slug }) => pathname === `/fleet/${slug}`
  )

  return (
    <div className="min-h-svh bg-background text-foreground">
      {sidebarOpen && (
        <button
          className="fixed inset-0 z-30 bg-black/35 md:hidden"
          aria-label="Close navigation"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform md:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex h-16 items-center justify-between border-b border-sidebar-border px-5">
          <a
            href="/fleet/http"
            className="flex items-center gap-3 font-semibold tracking-tight"
            onClick={(event) => {
              event.preventDefault()
              navigate("/fleet/http")
            }}
          >
            <span className="flex size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
              <Anchor className="size-4" aria-hidden="true" />
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
            <X aria-hidden="true" />
          </Button>
        </div>

        <nav className="flex-1 px-3 py-6" aria-label="Fleet navigation">
          <p className="mb-2 px-3 text-[0.6875rem] font-semibold tracking-[0.16em] text-muted-foreground">
            FLEET
          </p>
          <ul className="space-y-1">
            {fleetProviders.map(({ label, slug }) => {
              const href = `/fleet/${slug}`
              const isActive = pathname === href

              return (
                <li key={slug}>
                  <a
                    href={href}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground"
                    )}
                    onClick={(event) => {
                      event.preventDefault()
                      navigate(href)
                    }}
                  >
                    <Globe2 className="size-4" aria-hidden="true" />
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
            {theme === "dark" ? (
              <Sun aria-hidden="true" />
            ) : (
              <Moon aria-hidden="true" />
            )}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </Button>
        </div>
      </aside>

      <div className="md:pl-64">
        <header className="flex h-16 items-center border-b bg-background px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Open navigation"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu aria-hidden="true" />
          </Button>
          <span className="ml-3 font-semibold">Harbor</span>
        </header>

        <main className="flex min-h-[calc(100svh-4rem)] items-center justify-center px-6 py-16 md:min-h-svh">
          <section className="text-center">
            <p className="mb-3 font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
              {activeProvider ? `Fleet / ${activeProvider.label}` : "Fleet"}
            </p>
            <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
              Hello world
            </h1>
          </section>
        </main>
      </div>
    </div>
  )
}
