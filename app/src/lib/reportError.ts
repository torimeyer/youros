export function reportError(label: string, err: unknown): void {
  console.error(`[${label}]`, err)
}
