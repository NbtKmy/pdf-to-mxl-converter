"""Flask front-end for the PDF → MEI / MusicXML converter."""
from __future__ import annotations

import glob
import os
import uuid
from pathlib import Path

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
from werkzeug.utils import secure_filename

from converter import (
    AudiverisResult,
    inject_facsimile,
    musicxml_to_mei,
    parse_omr,
    pdf_to_pngs,
    run_audiveris,
)
from formTemplate import imageUpload

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
    naked: str,
    job_id: str,
) -> list[str] | None:
    """Return a list of user-facing flash messages, or None on success.

    Distinguishes:
    - Audiveris crashed (non-zero exit)
    - Audiveris ran but produced no .mxl at all (unreadable score)
    - Audiveris produced .mxl files under unexpected names (multi-section split)
    """
    if result.exit_code != 0:
        msgs = [f'Audiveris crashed (exit code {result.exit_code}).']
        tail = _extract_log_tail(result.log)
        if tail:
            msgs.append(f'Last lines: {tail}')
        msgs.append(f'Full log saved under src/output/{job_id}/ (look for *.log).')
        return msgs

    all_mxl = sorted(output_dir.glob('*.mxl'))
    if not all_mxl:
        return [
            'Audiveris finished without crashing but could not extract any musical notation.',
            'This usually means hand-written, pre-modern (mensural / neumatic), '
            'or severely degraded scores. Preprocessing rarely helps in these cases.',
            f'Full log saved under src/output/{job_id}/ (look for *.log).',
        ]

    expected = output_dir / f'{naked}.mxl'
    if not expected.is_file():
        names = ', '.join(p.name for p in all_mxl)
        return [
            f'Audiveris split this score into {len(all_mxl)} sections: {names}.',
            'Multi-section scores (separate movements, parallel parts, or '
            'multiple works in one book) are not yet supported in this version.',
            'For now try converting one page or one work at a time.',
        ]
    return None


@server.route('/', methods=['GET'])
def main():
    form = imageUpload()
    return render_template('index.html', form=form)


@server.route('/convert', methods=['POST'])
def convert():
    form = imageUpload()
    if not form.validate_on_submit():
        return render_template('index.html', form=form)

    job_id = uuid.uuid4().hex
    input_dir = _job_input_dir(job_id)
    output_dir = _job_output_dir(job_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    upload = form.img.data
    filename = secure_filename(upload.filename)
    pdf_path = input_dir / filename
    upload.save(pdf_path)
    flash(f'{filename} uploaded.')

    output_format = form.output_format.data or 'mei'
    token = request.form.get('download_token', '')

    result = run_audiveris(
        input_dir=f'{AUDIVERIS_INPUT_ROOT}/{job_id}',
        output_dir=f'{AUDIVERIS_OUTPUT_ROOT}/{job_id}',
    )
    naked = os.path.splitext(filename)[0]

    diagnosis = _diagnose_audiveris_output(result, output_dir, naked, job_id)
    if diagnosis is not None:
        for line in diagnosis:
            flash(line)
        return redirect(url_for('main'))

    mxl_path = output_dir / f'{naked}.mxl'

    if output_format == 'mxl':
        return _attach_token(
            make_response(send_file(
                mxl_path,
                as_attachment=True,
                mimetype='application/vnd.recordare.musicxml',
            )),
            token,
        )

    try:
        mei_xml = musicxml_to_mei(mxl_path)
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


@server.route('/health')
def health():
    return jsonify(status='ok')


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8888, threaded=True)
