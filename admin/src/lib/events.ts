export function summarizeCommandMethods(
  payload: Record<string, unknown>
): string | null {
  const methods = payload.methods
  if (!methods || typeof methods !== "object" || Array.isArray(methods)) {
    return null
  }
  const usages = Object.values(methods)
  const commandCount = usages.reduce((total, usage) => {
    if (!usage || typeof usage !== "object" || Array.isArray(usage)) {
      return total
    }
    const count = (usage as Record<string, unknown>).count
    return total + (typeof count === "number" ? count : 0)
  }, 0)
  const methodCount = Object.keys(methods).length
  return `${commandCount} ${commandCount === 1 ? "command" : "commands"} across ${methodCount} ${
    methodCount === 1 ? "method" : "methods"
  }`
}
