declare module 'verovio/wasm' {
  const createVerovioModule: () => Promise<unknown>
  export default createVerovioModule
}

declare module 'verovio/esm' {
  export class VerovioToolkit {
    constructor(module: unknown)
    setOptions(opts: Record<string, unknown>): void
    loadData(data: string): boolean
    getPageCount(): number
    renderToSVG(page: number, opts?: Record<string, unknown>): string
    getMEI(opts?: { pageNo?: number; scoreBased?: boolean }): string
    edit(action: object): boolean
    redoLayout(): void
  }
}
