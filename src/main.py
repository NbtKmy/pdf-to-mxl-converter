"""Flask front-end for the PDF → MEI / MusicXML converter."""
from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

from flask import (
    Flask,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from converter import musicxml_to_mei, run_audiveris
from formTemplate import imageUpload

UPLOAD_FOLDER = '/code/src/mediafiles'
OUTPUT_FOLDER = '/code/src/output'

server = Flask(__name__)
server.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
server.config['SECRET_KEY'] = 'your_secret_key'


def _wipe_workdirs() -> None:
    for f in glob.glob(os.path.join(UPLOAD_FOLDER, '*')):
        if os.path.isfile(f):
            os.remove(f)
    for entry in glob.glob(os.path.join(OUTPUT_FOLDER, '*')):
        if os.path.isdir(entry):
            shutil.rmtree(entry)
        elif os.path.isfile(entry):
            os.remove(entry)


def _attach_token(response, token: str):
    if token:
        response.set_cookie('download_token', token, max_age=60, path='/')
    return response


@server.route('/', methods=['GET', 'POST'])
def main():
    form = imageUpload()

    if not form.validate_on_submit():
        return render_template('index.html', form=form)

    _wipe_workdirs()

    upload = form.img.data
    filename = secure_filename(upload.filename)
    pdf_path = Path(UPLOAD_FOLDER) / filename
    upload.save(pdf_path)
    flash(f'{filename} uploaded.')

    output_format = form.output_format.data or 'mei'
    token = request.form.get('download_token', '')

    result = run_audiveris()
    naked = os.path.splitext(filename)[0]
    mxl_files = sorted(glob.glob(os.path.join(OUTPUT_FOLDER, f'{naked}.mxl')))

    if result.exit_code != 0 or not mxl_files:
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
        response = make_response(send_file(
            mxl_files[0],
            as_attachment=True,
            mimetype='application/vnd.recordare.musicxml',
        ))
        return _attach_token(response, token)

    try:
        mei_xml = musicxml_to_mei(Path(mxl_files[0]))
    except Exception as exc:
        flash(f'Failed to build MEI: {exc}')
        return redirect(url_for('main'))

    mei_path = Path(OUTPUT_FOLDER) / f'{naked}.mei'
    mei_path.write_text(mei_xml, encoding='utf-8')
    response = make_response(send_file(
        mei_path,
        as_attachment=True,
        mimetype='application/vnd.mei+xml',
    ))
    return _attach_token(response, token)


@server.route('/health')
def health():
    return jsonify(status='ok')


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8888, threaded=True)
