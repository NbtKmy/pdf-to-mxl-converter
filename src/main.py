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
    mxl_path = _find_one(output_dir, f'{naked}.mxl')

    if result.exit_code != 0 or mxl_path is None:
        flash('Audiveris could not produce a MusicXML file.')
        if result.exit_code != 0:
            flash(f'audiveris exited with code {result.exit_code}')
        else:
            flash(
                'Transcription likely failed (e.g. unrecognised rhythm). '
                'Try a simpler score; the .log file in /output has details.'
            )
        return redirect(url_for('main'))

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


@server.route('/health')
def health():
    return jsonify(status='ok')


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8888, threaded=True)
