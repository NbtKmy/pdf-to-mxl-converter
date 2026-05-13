import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import OpenSeadragon from 'openseadragon'
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
type Provenance = {
  title: string | null
  provider: string | null
  rights: string | null
  attribution: string | null
  manifestUrl: string | null
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

/** Direct-child lookup by MEI-namespace local name. */
function childByTag(parent: Element | null, tag: string): Element | null {
  if (!parent) return null
  for (let i = 0; i < parent.children.length; i++) {
    const c = parent.children[i]
    if (c.namespaceURI === MEI_NS && c.localName === tag) return c
  }
  return null
}

function textOf(el: Element | null): string | null {
  const t = el?.textContent?.trim() ?? ''
  return t || null
}

/** Pull IIIF-derived provenance out of the meiHead/fileDesc/sourceDesc/source
 *  block injected by the backend's ``inject_meihead_metadata``. */
function parseProvenance(doc: XMLDocument): Provenance | null {
  const source = doc.getElementsByTagNameNS(MEI_NS, 'source')[0] ?? null
  if (!source) return null

  const srcTitleStmt = childByTag(source, 'titleStmt')
  const title = textOf(childByTag(srcTitleStmt, 'title'))

  const respStmt = childByTag(srcTitleStmt, 'respStmt')
  const corp = childByTag(respStmt, 'corpName')
  const provider = textOf(corp)

  const pubStmt = childByTag(source, 'pubStmt')
  const availability = childByTag(pubStmt, 'availability')
  const rights = textOf(childByTag(availability, 'useRestrict'))

  let attribution: string | null = null
  if (pubStmt) {
    for (let i = 0; i < pubStmt.children.length; i++) {
      const c = pubStmt.children[i]
      if (c.namespaceURI === MEI_NS && c.localName === 'respStmt') {
        const name = childByTag(c, 'name')
        if (name) {
          attribution = textOf(name)
          break
        }
      }
    }
  }

  const bibl = childByTag(source, 'bibl')
  const ref = childByTag(bibl, 'ref')
  const manifestUrl = ref?.getAttribute('target') ?? null

  if (!title && !provider && !rights && !attribution && !manifestUrl) return null
  return { title, provider, rights, attribution, manifestUrl }
}

function App() {
  const [status, setStatus] = useState<Status>('init')
  const [error, setError] = useState<string | null>(null)
  const [meiText, setMeiText] = useState<string | null>(null)
  const [meiDoc, setMeiDoc] = useState<XMLDocument | null>(null)
  const [facsimile, setFacsimile] = useState<Facsimile | null>(null)
  const [provenance, setProvenance] = useState<Provenance | null>(null)
  const [scorePage, setScorePage] = useState(1)
  const [scorePageCount, setScorePageCount] = useState(0)
  const [imagePage, setImagePage] = useState(1)
  const [selectedMeasureId, setSelectedMeasureId] = useState<string | null>(null)
  const toolkitRef = useRef<VerovioToolkit | null>(null)
  const svgContainerRef = useRef<HTMLDivElement | null>(null)
  const osdHostRef = useRef<HTMLDivElement | null>(null)
  const osdViewerRef = useRef<OpenSeadragon.Viewer | null>(null)
  const overlayElsRef = useRef<Map<string, HTMLDivElement>>(new Map())

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
        setProvenance(parseProvenance(xmlDoc))
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

  // Initialize OSD viewer once when the host div is mounted.
  useEffect(() => {
    if (!osdHostRef.current || osdViewerRef.current) return
    osdViewerRef.current = OpenSeadragon({
      element: osdHostRef.current,
      prefixUrl: 'osd-images/',
      tileSources: undefined,
      showNavigationControl: true,
      showNavigator: false,
      navigationControlAnchor: OpenSeadragon.ControlAnchor.TOP_RIGHT,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
      minZoomImageRatio: 0.8,
      maxZoomPixelRatio: 4,
      visibilityRatio: 0.6,
      animationTime: 0.4,
      springStiffness: 7,
      immediateRender: true,
    })
    return () => {
      osdViewerRef.current?.destroy()
      osdViewerRef.current = null
    }
  }, [])

  // Load the current surface's image into OSD and (re)draw zone overlays.
  useEffect(() => {
    const viewer = osdViewerRef.current
    if (!viewer || !currentSurface || !currentSurface.graphicURL) return

    overlayElsRef.current.clear()
    viewer.clearOverlays()

    const onOpen = () => {
      if (!currentSurface) return
      const { width: imgW, height: imgH } = currentSurface
      if (!imgW || !imgH) return
      currentSurface.zones.forEach((b, zoneId) => {
        const el = document.createElement('div')
        el.className = 'osd-zone'
        el.title = zoneId
        el.dataset.zoneId = zoneId
        el.addEventListener('click', (ev) => {
          ev.stopPropagation()
          if (!facsimile) return
          const mid = facsimile.zoneToMeasure.get(zoneId)
          if (!mid) return
          handleMeasureSelect(mid)
          const tk = toolkitRef.current
          const fn = (tk as unknown as { getPageWithElement?: (id: string) => number } | null)
            ?.getPageWithElement
          if (typeof fn === 'function') {
            const page = fn.call(tk, mid)
            if (page && page !== scorePage) setScorePage(page)
          }
        })
        overlayElsRef.current.set(zoneId, el)
        const rect = viewer.viewport.imageToViewportRectangle(
          b.ulx,
          b.uly,
          b.lrx - b.ulx,
          b.lry - b.uly,
        )
        viewer.addOverlay({ element: el, location: rect })
      })
      applySelectedZoneClass()
    }
    viewer.addOnceHandler('open', onOpen)
    // OSD accepts a tile-source instance at runtime; the typings demand the
    // ``{tileSource}`` wrapper, so cast through unknown.
    viewer.open(
      new OpenSeadragon.ImageTileSource({ url: currentSurface.graphicURL }) as unknown as Parameters<typeof viewer.open>[0],
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSurface?.graphicURL, currentSurface?.n])

  const applySelectedZoneClass = useCallback(() => {
    overlayElsRef.current.forEach((el, zoneId) => {
      el.classList.toggle('osd-zone-selected', zoneId === selectedZoneId)
    })
  }, [selectedZoneId])

  useEffect(() => {
    applySelectedZoneClass()
  }, [selectedZoneId, applySelectedZoneClass])

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

      {provenance && (
        <div className="provenance">
          {provenance.title && (
            <span className="prov-title" title={provenance.title}>{provenance.title}</span>
          )}
          {provenance.provider && (
            <span className="prov-chip">
              <span className="prov-label">Provider</span>
              <span>{provenance.provider}</span>
            </span>
          )}
          {provenance.rights && (
            <span className="prov-chip">
              <span className="prov-label">Rights</span>
              {provenance.rights.startsWith('http') ? (
                <a href={provenance.rights} target="_blank" rel="noreferrer">
                  {provenance.rights.replace(/^https?:\/\//, '')}
                </a>
              ) : (
                <span>{provenance.rights}</span>
              )}
            </span>
          )}
          {provenance.attribution && (
            <span className="prov-chip prov-attr" title={provenance.attribution}>
              {provenance.attribution}
            </span>
          )}
          {provenance.manifestUrl && (
            <a
              className="prov-manifest"
              href={provenance.manifestUrl}
              target="_blank"
              rel="noreferrer"
            >
              IIIF manifest ↗
            </a>
          )}
        </div>
      )}

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
          <div className="pane-body pane-body-osd">
            <div ref={osdHostRef} className="osd-host" />
            {!currentSurface?.graphicURL && <div className="empty osd-empty">(no image)</div>}
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
