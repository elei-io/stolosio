import { Anchor, SunMoon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme-provider"

export default function App() {
  const { setTheme } = useTheme()

  const toggleTheme = () => {
    const isDark = document.documentElement.classList.contains("dark")
    setTheme(isDark ? "light" : "dark")
  }

  return (
    <main className="relative flex min-h-svh items-center justify-center overflow-hidden bg-background px-6 py-16">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,color-mix(in_oklch,var(--primary)_8%,transparent),transparent_38%)]" />

      <section className="relative w-full max-w-xl rounded-3xl border bg-card/90 p-8 shadow-sm backdrop-blur sm:p-12">
        <div className="mb-10 flex items-center gap-3 text-sm font-medium">
          <span className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Anchor className="size-4" aria-hidden="true" />
          </span>
          Harbor
        </div>

        <div className="space-y-4">
          <p className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
            Web control plane
          </p>
          <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
            Hello, Harbor.
          </h1>
          <p className="max-w-md text-base leading-7 text-muted-foreground">
            The frontend is ready for fleet monitoring, session diagnostics, and
            the browser control plane to come.
          </p>
        </div>

        <div className="mt-10 flex items-center justify-between border-t pt-6">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="size-2 rounded-full bg-emerald-500" />
            Ready to build
          </span>
          <Button
            variant="outline"
            size="icon"
            aria-label="Toggle color theme"
            onClick={toggleTheme}
          >
            <SunMoon aria-hidden="true" />
          </Button>
        </div>
      </section>
    </main>
  )
}
