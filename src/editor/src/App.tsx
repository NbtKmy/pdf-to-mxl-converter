import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import createVerovioModule from 'verovio/wasm'
import { VerovioToolkit } from 'verovio/esm'
import './App.css'

type Status = 'init' | 'loading' | 'ready' | 'error'

type BBox = { ulx: number; uly: number; lrx: number; lry: number }
type Surface = {
  n: number
  width: number
  height: number
  graphicURL: string
  zones: Map<string, BBox>
}
type Facsimile = {
  surfaces: Map<number, Surface>
  surfaceOrder: number[]
  zoneToSurface: Map<string, number>
  measureToZone: Map<string, string>
  zoneToMeasure: Map<string, string>
}

const MEI_NS = 'http://www.music-encoding.org/ns/mei'
const XML_NS = 'http://www.w3.org/XML/1998/namespace'

const RENDER_OPTIONS = {
  scale: 35,
  pageWidth: 2100,
  pageHeight: 2970,
  pageMarginLeft: 50,
  pageMarginRight: 50,
  pageMarginTop: 50,
  pageMarginBottom: 50,
  breaks: 'auto',
  adjustPageHeight: true,
  svgViewBox: true,
  svgRemoveXlink: true,
  footer: 'none',
  header: 'none',
}

function getJobId(): string | null {
  const m = window.location.pathname.match(/\/edit\/([^/]+)/)
  return m ? m[1] : null
}

function parseFacsimile(xmlDoc: XMLDocument): Facsimile {
  const surfaces = new Map<number, Surface>()
  const surfaceOrder: number[] = []
  const zoneToSurface = new Map<string, number>()
  const surfaceEls = xmlDoc.getElementsByTagNameNS(MEI_NS, 'surface')
  for (let i = 0; i < surfaceEls.length; i++) {
    const s = surfaceEls[i]
    const n = Number(s.getAttribute('n') ?? i + 1)
    const width = Number(s.getAttribute('lrx') ?? '0')
    const height = Number(s.getAttribute('lry') ?? '0')
    const graphic = s.getElementsByTagNameNS(MEI_NS, 'graphic')[0]
    const graphicURL = graphic?.getAttribute('target') ?? ''
    const zones = new Map<string, BBox>()
    const zoneEls = s.getElementsByTagNameNS(MEI_NS, 'zone')
    for (let j = 0; j < zoneEls.length; j++) {
      const z = zoneEls[j]
      const id = z.getAttributeNS(XML_NS, 'id') ?? z.getAttribute('xml:id')
      if (!id) continue
      zones.set(id, {
        ulx: Number(z.getAttribute('ulx') ?? '0'),
        uly: Number(z.getAttribute('uly') ?? '0'),
        lrx: Number(z.getAttribute('lrx') ?? '0'),
        lry: Number(z.getAttribute('lry') ?? '0'),
      })
      zoneToSurface.set(id, n)
    }
    surfaces.set(n, { n, width, height, graphicURL, zones })
    surfaceOrder.push(n)
  }
  surfaceOrder.sort((a, b) => a - b)

  const measureToZone = new Map<string, string>()
  const zoneToMeasure = new Map<string, string>()
  const measureEls = xmlDoc.getElementsByTagNameNS(MEI_NS, 'measure')
  for (let i = 0; i < measureEls.length; i++) {
    const m = measureEls[i]
    const id = m.getAttributeNS(XML_NS, 'id') ?? m.getAttribute('xml:id')
    const facs = m.getAttribute('facs')
    if (!id || !facs) continue
    const zoneId = facs.replace(/^#/, '')
    measureToZone.set(id, zoneId)
    zoneToMeasure.set(zoneId, id)
  }
  return { surfaces, surfaceOrder, zoneToSurface, measureToZone, zoneToMeasure }
}

function listMeasureIds(xmlDoc: XMLDocument): string[] {
  const ms = xmlDoc.getElementsByTagNameNS(MEI_NS, 'measure')
  const out: string[] = []
  for (let i = 0; i < ms.length; i++) {
    const id = ms[i].getAttributeNS(XML_NS, 'id') ?? ms[i].getAttribute('xml:id')
    if (id) out.push(id)
  }
  return out
}

function App() {
  const [status, setStatus] = useState<Status>('init')
  const [error, setError] = useState<string | null>(null)
  const [meiText, setMeiText] = useState<string | null>(null)
  const [meiDoc, setMeiDoc] = useState<XMLDocument | null>(null)
  const [facsimile, setFacsimile] = useState<Facsimile | null>(null)
  const [scorePage, setScorePage] = useState(1)
  const [scorePageCount, setScorePageCount] = useState(0)
  const [imagePage, setImagePage] = useState(1)
  const [selectedMeasureId, setSelectedMeasureId] = useState<string | null>(null)
  const toolkitRef = useRef<VerovioToolkit | null>(null)
  const svgContainerRef = useRef<HTMLDivElement | null>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)

  const jobId = useMemo(() => getJobId(), [])
  const measureIds = useMemo(() => (meiDoc ? listMeasureIds(meiDoc) : []), [meiDoc])
  const currentSurface = facsimile?.surfaces.get(imagePage) ?? null
  const selectedZoneId = selectedMeasureId
    ? facsimile?.measureToZone.get(selectedMeasureId) ?? null
    : null

  // Boot Verovio + fetch MEI for the job.
  useEffect(() => {
    if (!jobId) {
      setError('URL から job_id を取得できませんでした。/edit/<job_id> でアクセスしてください。')
      setStatus('error')
      return
    }
    let cancelled = false
    setStatus('loading')
    ;(async () => {
      try {
        const [VerovioModule, meiResp] = await Promise.all([
          createVerovioModule(),
          fetch(`/api/job/${jobId}/mei`),
        ])
        if (cancelled) return
        if (!meiResp.ok) {
          throw new Error(`MEI fetch failed: HTTP ${meiResp.status}`)
        }
        const text = await meiResp.text()
        const toolkit = new VerovioToolkit(VerovioModule)
        toolkit.setOptions(RENDER_OPTIONS)
        if (!toolkit.loadData(text)) {
          throw new Error('Verovio.loadData returned false')
        }
        const xmlDoc = new DOMParser().parseFromString(text, 'application/xml')
        const parserError = xmlDoc.getElementsByTagName('parsererror')[0]
        if (parserError) throw new Error('MEI parse error: ' + parserError.textContent)
        const facs = parseFacsimile(xmlDoc)
        toolkitRef.current = toolkit
        setMeiText(text)
        setMeiDoc(xmlDoc)
        setFacsimile(facs)
        setScorePageCount(toolkit.getPageCount())
        setScorePage(1)
        setImagePage(facs.surfaceOrder[0] ?? 1)
        setStatus('ready')
      } catch (e) {
        console.error(e)
        if (!cancelled) {
          setError((e as Error).message)
          setStatus('error')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [jobId])

  // Re-render score whenever page or selection changes.
  useEffect(() => {
    const tk = toolkitRef.current
    const el = svgContainerRef.current
    if (!tk || !el || status !== 'ready') return
    el.innerHTML = tk.renderToSVG(scorePage)
    if (selectedMeasureId) {
      const target = el.querySelector(`#${CSS.escape(selectedMeasureId)}`)
      target?.classList.add('measure-selected')
      target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }, [scorePage, status, selectedMeasureId, meiText])

  const handleMeasureSelect = useCallback(
    (measureId: string) => {
      setSelectedMeasureId(measureId)
      if (!facsimile) return
      const zoneId = facsimile.measureToZone.get(measureId)
      if (!zoneId) return
      const surfaceN = facsimile.zoneToSurface.get(zoneId)
      if (surfaceN && surfaceN !== imagePage) setImagePage(surfaceN)
    },
    [facsimile, imagePage],
  )

  const onSvgClick = (e: React.MouseEvent<HTMLDivElement>) => {
    let target = e.target as HTMLElement | null
    while (target && target !== e.currentTarget) {
      const cls = target.getAttribute('class') ?? ''
      if (target.id && cls.split(' ').includes('measure')) {
        handleMeasureSelect(target.id)
        return
      }
      if (target.id && (cls.includes('note') || cls.includes('rest') || cls.includes('chord'))) {
        let p = target.parentElement
        while (p && p !== e.currentTarget) {
          const pc = p.getAttribute('class') ?? ''
          if (p.id && pc.split(' ').includes('measure')) {
            handleMeasureSelect(p.id)
            return
          }
          p = p.parentElement
        }
      }
      target = target.parentElement
    }
  }

  const onZoneClick = (zoneId: string) => {
    if (!facsimile) return
    const measureId = facsimile.zoneToMeasure.get(zoneId)
    if (!measureId) return
    handleMeasureSelect(measureId)
    // try to scroll Verovio to the page containing this measure
    const tk = toolkitRef.current
    // verovio API has getPageWithElement but typings are minimal; cast through unknown
    const fn = (tk as unknown as { getPageWithElement?: (id: string) => number } | null)
      ?.getPageWithElement
    if (typeof fn === 'function') {
      const page = fn.call(tk, measureId)
      if (page && page !== scorePage) setScorePage(page)
    }
  }

  const totalSurfaces = facsimile?.surfaceOrder.length ?? 0
  const totalMeasures = measureIds.length
  const assignedCount = facsimile?.measureToZone.size ?? 0

  return (
    <div className="app">
      <header className="topbar">
        <strong>OMR Editor</strong>
        {jobId && <span className="muted">job: <code>{jobId.slice(0, 8)}…</code></span>}
        {totalMeasures > 0 && (
          <span className="muted">
            {totalMeasures} measures · {assignedCount} zones · {totalSurfaces} pages
          </span>
        )}
        {selectedMeasureId && (
          <span className="muted accent">selected: {selectedMeasureId}</span>
        )}
        <a className="muted right" href="/">← New conversion</a>
      </header>

      {status === 'loading' && <div className="banner">Loading MEI and Verovio…</div>}
      {status === 'error' && <div className="banner banner-error">{error}</div>}

      <div className="panes">
        <section className="pane pane-image">
          <div className="pane-header">
            <span>Source</span>
            {totalSurfaces > 1 && facsimile && (
              <span className="pager">
                <button
                  onClick={() => {
                    const idx = facsimile.surfaceOrder.indexOf(imagePage)
                    if (idx > 0) setImagePage(facsimile.surfaceOrder[idx - 1])
                  }}
                  disabled={facsimile.surfaceOrder.indexOf(imagePage) <= 0}
                >‹</button>
                <span>p. {imagePage} / {totalSurfaces}</span>
                <button
                  onClick={() => {
                    const idx = facsimile.surfaceOrder.indexOf(imagePage)
                    if (idx < facsimile.surfaceOrder.length - 1) {
                      setImagePage(facsimile.surfaceOrder[idx + 1])
                    }
                  }}
                  disabled={facsimile.surfaceOrder.indexOf(imagePage) >= totalSurfaces - 1}
                >›</button>
              </span>
            )}
          </div>
          <div className="pane-body">
            {currentSurface && currentSurface.graphicURL ? (
              <div className="image-wrap">
                <img
                  ref={imageRef}
                  src={currentSurface.graphicURL}
                  alt={`source page ${currentSurface.n}`}
                  draggable={false}
                />
                {Array.from(currentSurface.zones.entries()).map(([zoneId, b]) => {
                  const w = currentSurface.width
                  const h = currentSurface.height
                  if (!w || !h) return null
                  const isSelected = zoneId === selectedZoneId
                  return (
                    <button
                      key={zoneId}
                      onClick={() => onZoneClick(zoneId)}
                      title={zoneId}
                      className={isSelected ? 'zone zone-selected' : 'zone'}
                      style={{
                        left: `${(b.ulx / w) * 100}%`,
                        top: `${(b.uly / h) * 100}%`,
                        width: `${((b.lrx - b.ulx) / w) * 100}%`,
                        height: `${((b.lry - b.uly) / h) * 100}%`,
                      }}
                    />
                  )
                })}
              </div>
            ) : (
              <div className="empty">(no image)</div>
            )}
          </div>
        </section>

        <section className="pane pane-score">
          <div className="pane-header">
            <span>Score</span>
            {scorePageCount > 1 && (
              <span className="pager">
                <button onClick={() => setScorePage(p => Math.max(1, p - 1))}
                        disabled={scorePage <= 1}>‹</button>
                <span>p. {scorePage} / {scorePageCount}</span>
                <button onClick={() => setScorePage(p => Math.min(scorePageCount, p + 1))}
                        disabled={scorePage >= scorePageCount}>›</button>
              </span>
            )}
          </div>
          <div className="pane-body">
            <div ref={svgContainerRef} className="svg-host" onClick={onSvgClick} />
          </div>
        </section>
      </div>
    </div>
  )
}

export default App
