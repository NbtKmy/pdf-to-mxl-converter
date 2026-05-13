"""Flask front-end for the PDF → MEI / MusicXML converter."""
from __future__ import annotations

import glob
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.utils import secure_filename

from converter import (
    AudiverisResult,
    IIIFError,
    IIIFManifest,
    download_image,
    fetch_manifest,
    images_to_pdf,
    inject_facsimile,
    inject_meihead_metadata,
    merge_mei_movements,
    musicxml_to_mei,
    parse_omr,
    pdf_to_pngs,
    run_audiveris,
)
from formTemplate import imageUpload

ALLOWED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg'}
ALLOWED_PDF_EXTS = {'.pdf'}
ALLOWED_EXTS = ALLOWED_IMAGE_EXTS | ALLOWED_PDF_EXTS


def _natural_key(name: str):
    """Sort key that treats digit runs as integers: page2 < page10."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r'(\d+)', name)]


def _check_csrf() -> bool:
    try:
        validate_csrf(request.form.get('csrf_token'))
    except CSRFError:
        return False
    return True

# Paths inside the flask container. The named docker volumes share these with
# the audiveris container, where they appear as /input and /output.
UPLOAD_FOLDER = '/code/src/mediafiles'
OUTPUT_FOLDER = '/code/src/output'
AUDIVERIS_INPUT_ROOT = '/input'
AUDIVERIS_OUTPUT_ROOT = '/output'
EDITOR_DIST = Path(__file__).parent / 'editor' / 'dist'

server = Flask(__name__)
server.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
server.config['SECRET_KEY'] = 'your_secret_key'


def _job_input_dir(job_id: str) -> Path:
    return Path(UPLOAD_FOLDER) / job_id


def _job_output_dir(job_id: str) -> Path:
    return Path(OUTPUT_FOLDER) / job_id


def _attach_token(response, token: str):
    if token:
        response.set_cookie('download_token', token, max_age=60, path='/')
    return response


def _find_one(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def _extract_log_tail(log: str, n: int = 3) -> str:
    """Pick the most diagnostic lines from Audiveris stdout for user display.

    Prefer lines that look like errors; fall back to the last few non-empty
    lines if no error keywords match.
    """
    if not log:
        return ''
    lines = [line.rstrip() for line in log.splitlines() if line.strip()]
    if not lines:
        return ''
    keywords = ('ERROR', 'Exception', 'SEVERE', 'FATAL', 'Caused by')
    flagged = [line for line in lines if any(kw in line for kw in keywords)]
    picked = (flagged or lines)[-n:]
    return ' | '.join(picked)


def _diagnose_audiveris_output(
    result: AudiverisResult,
    output_dir: Path,
    job_id: str,
) -> list[str] | None:
    """Return a list of user-facing flash messages, or None on success.

    Distinguishes:
    - Audiveris crashed (non-zero exit)
    - Audiveris ran but produced no .mxl at all (unreadable score)

    The "multi-section split" case (Audiveris emits ``<naked>.mvtN.mxl``
    instead of one ``<naked>.mxl``) is no longer an error — the convert
    handler stitches those into a multi-mdiv MEI.
    """
    if result.exit_code != 0:
        msgs = [f'Audiveris crashed (exit code {result.exit_code}).']
        tail = _extract_log_tail(result.log)
        if tail:
            msgs.append(f'Last lines: {tail}')
        msgs.append(f'Full log saved under src/output/{job_id}/ (look for *.log).')
        return msgs

    if not list(output_dir.glob('*.mxl')):
        return [
            'Audiveris finished without crashing but could not extract any musical notation.',
            'This usually means hand-written, pre-modern (mensural / neumatic), '
            'or severely degraded scores. Preprocessing rarely helps in these cases.',
            f'Full log saved under src/output/{job_id}/ (look for *.log).',
        ]

    return None


def _collect_mxl_files(output_dir: Path, naked: str) -> list[Path]:
    """Return Audiveris's ``.mxl`` outputs in book reading order.

    Single-movement scores produce one ``<naked>.mxl``. Multi-section scores
    produce ``<naked>.mvt1.mxl``, ``<naked>.mvt2.mxl``, ... — sorted here by
    integer mvt number (so mvt10 follows mvt9, not mvt1).
    """
    single = output_dir / f'{naked}.mxl'
    if single.is_file():
        return [single]
    pattern = re.compile(rf'^{re.escape(naked)}\.mvt(\d+)\.mxl$')
    candidates: list[tuple[int, Path]] = []
    for path in output_dir.glob(f'{naked}.mvt*.mxl'):
        m = pattern.match(path.name)
        if m:
            candidates.append((int(m.group(1)), path))
    candidates.sort()
    return [path for _, path in candidates]


@server.route('/', methods=['GET'])
def main():
    form = imageUpload()
    return render_template('index.html', form=form)


@server.route('/convert', methods=['POST'])
def convert():
    form = imageUpload()
    if not form.validate_on_submit():
        return render_template('index.html', form=form)

    uploads = [u for u in (form.img.data or []) if u and u.filename]
    if not uploads:
        flash('No file selected.')
        return redirect(url_for('main'))

    job_id = uuid.uuid4().hex
    input_dir = _job_input_dir(job_id)
    output_dir = _job_output_dir(job_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for upload in uploads:
        safe = secure_filename(upload.filename) or 'upload'
        ext = Path(safe).suffix.lower()
        if ext not in ALLOWED_EXTS:
            flash(f'Unsupported file type "{ext}". Allowed: PDF, PNG, JPG.')
            return redirect(url_for('main'))
        dest = input_dir / safe
        upload.save(dest)
        saved.append(dest)

    saved.sort(key=lambda p: _natural_key(p.name))

    pdf_inputs = [p for p in saved if p.suffix.lower() in ALLOWED_PDF_EXTS]
    img_inputs = [p for p in saved if p.suffix.lower() in ALLOWED_IMAGE_EXTS]

    if pdf_inputs and img_inputs:
        flash('Mixing PDFs and images in one upload is not supported. '
              'Send a single PDF, or one or more images.')
        return redirect(url_for('main'))
    if len(pdf_inputs) > 1:
        flash('Only one PDF per upload is supported. '
              'Convert each PDF separately, or send the pages as images.')
        return redirect(url_for('main'))

    if pdf_inputs:
        pdf_path = pdf_inputs[0]
        flash(f'{pdf_path.name} uploaded.')
    else:
        # Wrap images into a synthetic PDF so the downstream pipeline stays
        # a single code path (Audiveris + pymupdf re-render at OMR dims).
        pdf_path = input_dir / f'{job_id}.pdf'
        try:
            images_to_pdf(img_inputs, pdf_path)
        except Exception as exc:
            flash(f'Failed to bundle {len(img_inputs)} images into a PDF: {exc}')
            return redirect(url_for('main'))
        # Remove individual image files so Audiveris's input glob only sees
        # the bundled PDF.
        for img in img_inputs:
            img.unlink(missing_ok=True)
        flash(f'Bundled {len(img_inputs)} image(s) into one document.')

    output_format = form.output_format.data or 'mei'
    token = request.form.get('download_token', '')
    return _run_pipeline(job_id, pdf_path, output_format, token)


def _run_pipeline(
    job_id: str,
    pdf_path: Path,
    output_format: str,
    token: str,
    iiif_manifest: IIIFManifest | None = None,
    iiif_manifest_url: str | None = None,
):
    """Drive Audiveris → MEI → response for a job whose input PDF is staged.

    Called by both the file-upload (``/convert``) and IIIF (``/iiif/convert``)
    routes once they've assembled a single PDF in the job's input dir. The
    IIIF route passes ``iiif_manifest`` so the resulting MEI can carry
    provenance metadata (label, rights, provider, attribution) in meiHead.
    """
    output_dir = _job_output_dir(job_id)
    naked = os.path.splitext(pdf_path.name)[0]

    result = run_audiveris(
        input_dir=f'{AUDIVERIS_INPUT_ROOT}/{job_id}',
        output_dir=f'{AUDIVERIS_OUTPUT_ROOT}/{job_id}',
    )

    diagnosis = _diagnose_audiveris_output(result, output_dir, job_id)
    if diagnosis is not None:
        for line in diagnosis:
            flash(line)
        return redirect(url_for('main'))

    mxl_files = _collect_mxl_files(output_dir, naked)
    if not mxl_files:
        flash('Audiveris produced MusicXML, but under an unexpected filename. '
              f'Check src/output/{job_id}/.')
        return redirect(url_for('main'))

    if output_format == 'mxl':
        if len(mxl_files) > 1:
            flash('This score was split into multiple movements; bundled MXL '
                  'download is not supported. Pick MEI or the editor instead.')
            return redirect(url_for('main'))
        return _attach_token(
            make_response(send_file(
                mxl_files[0],
                as_attachment=True,
                mimetype='application/vnd.recordare.musicxml',
            )),
            token,
        )

    try:
        if len(mxl_files) == 1:
            mei_xml = musicxml_to_mei(mxl_files[0])
        else:
            mei_xml = merge_mei_movements(
                [musicxml_to_mei(p) for p in mxl_files]
            )
            flash(f'Stitched {len(mxl_files)} movements into one MEI.')
    except Exception as exc:
        flash(f'Failed to build MEI: {exc}')
        return redirect(url_for('main'))

    omr_path = _find_one(output_dir, f'{naked}.omr')
    if omr_path is not None:
        try:
            omr_data = parse_omr(omr_path)
            rendered = pdf_to_pngs(pdf_path, omr_data, output_dir)
            sheet_image_urls = {
                num: url_for('job_source', job_id=job_id, filename=path.name)
                for num, path in rendered.items()
            }
            mei_xml = inject_facsimile(mei_xml, omr_data, sheet_image_urls)
        except Exception as exc:
            flash(f'Facsimile zones could not be injected ({exc}); '
                  'returning MEI without zones.')

    if iiif_manifest is not None:
        try:
            mei_xml = inject_meihead_metadata(
                mei_xml, iiif_manifest, iiif_manifest_url
            )
        except Exception as exc:
            flash(f'IIIF metadata could not be added to meiHead ({exc}); '
                  'returning MEI without provenance.')

    mei_path = output_dir / f'{naked}.mei'
    mei_path.write_text(mei_xml, encoding='utf-8')

    if output_format == 'edit':
        return _attach_token(
            make_response(redirect(url_for('editor_index', job_id=job_id))),
            token,
        )

    return _attach_token(
        make_response(send_file(
            mei_path,
            as_attachment=True,
            mimetype='application/vnd.mei+xml',
        )),
        token,
    )


@server.route('/iiif/load', methods=['POST'])
def iiif_load():
    if not _check_csrf():
        abort(400, description='CSRF check failed')
    url = (request.form.get('manifest_url') or '').strip()
    if not url:
        flash('Please paste a IIIF manifest URL.')
        return redirect(url_for('main'))
    try:
        manifest = fetch_manifest(url)
    except IIIFError as exc:
        flash(f'IIIF manifest could not be loaded: {exc}')
        return redirect(url_for('main'))
    if not manifest.canvases:
        flash('That manifest contains no canvases (pages) to OMR.')
        return redirect(url_for('main'))
    return render_template(
        'iiif_select.html',
        form=imageUpload(),    # only used for csrf_token rendering
        manifest=manifest,
        manifest_url=url,
    )


@server.route('/iiif/convert', methods=['POST'])
def iiif_convert():
    if not _check_csrf():
        abort(400, description='CSRF check failed')
    url = (request.form.get('manifest_url') or '').strip()
    try:
        indices = sorted({int(v) for v in request.form.getlist('canvas')})
    except ValueError:
        flash('Invalid canvas selection.')
        return redirect(url_for('main'))
    if not url or not indices:
        flash('Pick at least one page to OMR.')
        return redirect(url_for('main'))

    output_format = request.form.get('output_format') or 'edit'
    token = request.form.get('download_token', '')

    try:
        manifest = fetch_manifest(url)
    except IIIFError as exc:
        flash(f'IIIF manifest could not be loaded: {exc}')
        return redirect(url_for('main'))

    by_index = {c.index: c for c in manifest.canvases}
    selected = [by_index[i] for i in indices if i in by_index]
    if not selected:
        flash('Selected canvases were not found in the manifest.')
        return redirect(url_for('main'))

    job_id = uuid.uuid4().hex
    input_dir = _job_input_dir(job_id)
    output_dir = _job_output_dir(job_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    for canvas in selected:
        ext = Path(urlparse(canvas.image_url).path).suffix.lower() or '.jpg'
        if ext not in ALLOWED_IMAGE_EXTS:
            ext = '.jpg'
        dest = input_dir / f'canvas-{canvas.index:04d}{ext}'
        try:
            download_image(canvas, dest)
        except IIIFError as exc:
            flash(f'Image download failed: {exc}')
            return redirect(url_for('main'))
        image_paths.append(dest)

    pdf_path = input_dir / f'{job_id}.pdf'
    try:
        images_to_pdf(image_paths, pdf_path)
    except Exception as exc:
        flash(f'Failed to bundle IIIF images into a PDF: {exc}')
        return redirect(url_for('main'))
    for img in image_paths:
        img.unlink(missing_ok=True)
    flash(f'Downloaded {len(selected)} page(s) from {manifest.label}.')

    return _run_pipeline(
        job_id,
        pdf_path,
        output_format,
        token,
        iiif_manifest=manifest,
        iiif_manifest_url=url,
    )


def _resolve_job_artifact(job_id: str, suffix: str) -> Path:
    output_dir = _job_output_dir(job_id)
    if not output_dir.is_dir():
        abort(404)
    match = _find_one(output_dir, f'*{suffix}')
    if match is None:
        abort(404)
    return match


@server.route('/api/job/<job_id>/mei')
def job_mei(job_id: str):
    return send_file(_resolve_job_artifact(job_id, '.mei'),
                     mimetype='application/vnd.mei+xml')


@server.route('/api/job/<job_id>/download.mei')
def job_download_mei(job_id: str):
    return send_file(_resolve_job_artifact(job_id, '.mei'),
                     mimetype='application/vnd.mei+xml',
                     as_attachment=True)


@server.route('/api/job/<job_id>/download.mxl')
def job_download_mxl(job_id: str):
    return send_file(_resolve_job_artifact(job_id, '.mxl'),
                     mimetype='application/vnd.recordare.musicxml',
                     as_attachment=True)


@server.route('/api/job/<job_id>/source/<path:filename>')
def job_source(job_id: str, filename: str):
    output_dir = _job_output_dir(job_id)
    if not output_dir.is_dir():
        abort(404)
    return send_from_directory(output_dir, filename)


@server.route('/edit/<job_id>')
def editor_index_no_slash(job_id: str):
    # Trailing slash so the SPA's relative asset paths resolve to /edit/<job_id>/assets/...
    return redirect(url_for('editor_index', job_id=job_id))


@server.route('/edit/<job_id>/')
def editor_index(job_id: str):
    if not _job_output_dir(job_id).is_dir():
        abort(404)
    index_path = EDITOR_DIST / 'index.html'
    if not index_path.is_file():
        abort(503, description=(
            'Editor build is missing. Run `bun run build` inside src/editor/.'
        ))
    return send_file(index_path, mimetype='text/html')


@server.route('/edit/<job_id>/assets/<path:filename>')
def editor_assets(job_id: str, filename: str):
    # job_id is intentionally unused — assets are shared across all jobs.
    del job_id
    assets_dir = EDITOR_DIST / 'assets'
    if not assets_dir.is_dir():
        abort(404)
    return send_from_directory(assets_dir, filename)


@server.route('/edit/<job_id>/osd-images/<path:filename>')
def editor_osd_images(job_id: str, filename: str):
    # OpenSeadragon's UI sprite images (zoom/home/fullscreen buttons).
    # Bundled into editor/public/osd-images so they don't depend on a CDN.
    del job_id
    images_dir = EDITOR_DIST / 'osd-images'
    if not images_dir.is_dir():
        abort(404)
    return send_from_directory(images_dir, filename)


@server.route('/health')
def health():
    return jsonify(status='ok')


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8888, threaded=True)
